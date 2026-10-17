"""iFinD MCP HTTP 客户端。

和官方 skill 的 call.py 协议一致（JSON-RPC initialize → tools/call），另外加了：
- 串行限频：两次请求间隔 >= MIN_INTERVAL 秒（免费档 2 req/s，实测并发 6 有 5 个 429）
- 识别被包在 ok:true 文本里的 429 / call failed（data_probe.md 4.6）
- 超时 / 429 指数退避重试；最终失败不抛异常，返回 status，由上游记为“缺失”
- 按请求哈希缓存原始返回（data/cache，不进仓库）；调用元数据追加到 data/staging/api_calls.jsonl（可公开、可回放）
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache" / "ifind"
CALL_LOG = ROOT / "data" / "staging" / "api_calls.jsonl"
CST = timezone(timedelta(hours=8))

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
SERVERS = {
    "stock": f"{BASE}/hexin-ifind-ds-stock-mcp",
    "fund": f"{BASE}/hexin-ifind-ds-fund-mcp",
    "edb": f"{BASE}/hexin-ifind-ds-edb-mcp",
    "news": f"{BASE}/hexin-ifind-ds-news-mcp",
    "bond": f"{BASE}/hexin-ifind-ds-bond-mcp",
    "global_stock": f"{BASE}/hexin-ifind-ds-global-stock-mcp",
    "index": f"{BASE}/hexin-ifind-ds-index-mcp",
    "future": f"{BASE}/hexin-ifind-ds-futures-mcp",
}

MIN_INTERVAL = 0.7
MAX_RETRIES = 3
TIMEOUT = 60

EMPTY_MARKERS = ("查询结果为空", "结果为空", "未找到符合要求", "未能找到符合要求")
RATE_LIMIT_MARKERS = ("status: 429", "请求过于频繁")


def call_id_for(server: str, tool: str, params: dict) -> str:
    canon = json.dumps({"s": server, "t": tool, "p": params}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


@dataclass
class IfindResult:
    call_id: str
    server: str
    tool: str
    params: dict
    status: str  # ok | empty | rate_limited | error
    text: str = ""
    inner: Any = None  # 解开 content.text 后的业务 JSON
    called_at: str = ""
    ms: int = 0
    from_cache: bool = False
    error: str = ""
    attempts: int = 0
    raw: Any = field(default=None, repr=False)

    @property
    def data(self) -> Any:
        """业务数据体：inner['data']（可能是 dict / str / list）。"""
        if isinstance(self.inner, dict):
            return self.inner.get("data")
        return None


def classify(raw: Any) -> tuple[str, str, Any, str]:
    """把一次 JSON-RPC 响应分类为 (status, text, inner, error)。纯函数，便于测试。"""
    if not isinstance(raw, dict):
        return "error", str(raw)[:500], None, "non-json response"
    if "error" in raw:
        err = raw["error"]
        return "error", "", None, json.dumps(err, ensure_ascii=False)[:500]
    result = raw.get("result") or {}
    content = result.get("content") or []
    text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    if any(m in text for m in RATE_LIMIT_MARKERS):
        return "rate_limited", text[:500], None, "429 rate limited (wrapped in ok response)"
    if text.startswith("call failed") or result.get("isError"):
        return "error", text[:500], None, text[:200]
    try:
        inner = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "error", text[:500], None, "content.text is not json"
    if isinstance(inner, dict) and inner.get("code") not in (1, "1", None):
        return "error", text[:500], inner, f"business code={inner.get('code')} msg={inner.get('msg')}"
    body = inner.get("data") if isinstance(inner, dict) else inner
    body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    # 选股接口空结果是 selectedSecuritiesCount=0
    if isinstance(body, dict) and body.get("selectedSecuritiesCount") == 0:
        return "empty", text, inner, ""
    if any(m in body_text[:300] for m in EMPTY_MARKERS):
        return "empty", text, inner, ""
    return "ok", text, inner, ""


def _default_token() -> str:
    path = os.environ.get("IFIND_CONFIG") or str(Path.home() / ".claude/skills/ifind-finance-data/mcp_config.json")
    return json.loads(Path(path).read_text(encoding="utf-8"))["auth_token"]


class IfindClient:
    def __init__(self, token: str | None = None, transport: Callable | None = None,
                 cache_dir: Path = CACHE_DIR, call_log: Path | None = CALL_LOG,
                 min_interval: float = MIN_INTERVAL, sleep: Callable[[float], None] = time.sleep):
        self._token = token
        self._transport = transport or self._http_post
        self.cache_dir = cache_dir
        self.call_log = call_log
        self.min_interval = min_interval
        self._sleep = sleep
        self._sessions: dict[str, str] = {}
        self._req_id = 0
        self._last_call = 0.0

    # ---- 传输层 -------------------------------------------------------
    @property
    def token(self) -> str:
        if self._token is None:
            self._token = _default_token()
        return self._token

    def _headers(self, server: str) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "Authorization": self.token}
        if server in self._sessions:
            h["Mcp-Session-Id"] = self._sessions[server]
        return h

    def _http_post(self, server: str, payload: dict, timeout: float) -> tuple[int, dict, Any]:
        resp = requests.post(SERVERS[server], json=payload, headers=self._headers(server), timeout=timeout)
        data = None
        if resp.text.strip():
            try:
                data = resp.json()
            except ValueError:
                data = resp.text
        return resp.status_code, dict(resp.headers), data

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _init(self, server: str) -> None:
        if server in self._sessions:
            return
        payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
                   "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                              "clientInfo": {"name": "optichain", "version": "0.1"}}}
        status, headers, data = self._transport(server, payload, 30)
        sid = {k.lower(): v for k, v in headers.items()}.get("mcp-session-id")
        if status >= 400 or not sid:
            raise RuntimeError(f"initialize failed for {server}: {status} {str(data)[:200]}")
        self._sessions[server] = sid
        self._transport(server, {"jsonrpc": "2.0", "method": "notifications/initialized"}, 10)

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            self._sleep(wait)
        self._last_call = time.monotonic()

    # ---- 对外接口 -----------------------------------------------------
    def call(self, server: str, tool: str, params: dict, refresh: bool = False) -> IfindResult:
        cid = call_id_for(server, tool, params)
        cache_file = self.cache_dir / f"{cid}.json"
        if not refresh and cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            status, text, inner, err = classify(cached["raw"])
            if status in ("ok", "empty"):
                return IfindResult(cid, server, tool, params, status, text, inner, cached["called_at"],
                                   cached["ms"], True, err, 0, cached["raw"])

        res = IfindResult(cid, server, tool, params, "error")
        for attempt in range(1, MAX_RETRIES + 1):
            res.attempts = attempt
            self._throttle()
            t0 = time.monotonic()
            res.called_at = datetime.now(CST).isoformat(timespec="seconds")
            try:
                self._init(server)
                payload = {"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                           "params": {"name": tool, "arguments": params}}
                http_status, _, raw = self._transport(server, payload, TIMEOUT)
                res.ms = int((time.monotonic() - t0) * 1000)
                res.raw = raw
                if http_status == 429:
                    res.status, res.error = "rate_limited", "HTTP 429"
                else:
                    res.status, res.text, res.inner, res.error = classify(raw)
            except requests.Timeout:
                res.ms = int((time.monotonic() - t0) * 1000)
                res.status, res.error = "error", f"timeout after {TIMEOUT}s"
            except (requests.RequestException, RuntimeError) as e:
                res.ms = int((time.monotonic() - t0) * 1000)
                res.status, res.error = "error", f"{type(e).__name__}: {e}"[:300]
                self._sessions.pop(server, None)  # 会话可能过期，下次重建
            if res.status in ("ok", "empty"):
                break
            # 业务错误（参数非法等）重试无意义；429 / 超时 / 网络错误退避重试
            if res.status == "error" and res.raw is not None and "error" in (res.raw or {}):
                break
            self._sleep(2 ** attempt)

        if res.status in ("ok", "empty"):
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({"server": server, "tool": tool, "params": params,
                                              "called_at": res.called_at, "ms": res.ms, "raw": res.raw},
                                             ensure_ascii=False), encoding="utf-8")
        self._log(res)
        return res

    def _log(self, res: IfindResult) -> None:
        if not self.call_log:
            return
        self.call_log.parent.mkdir(parents=True, exist_ok=True)
        raw_bytes = json.dumps(res.raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
        rec = {"call_id": res.call_id, "source": "iFinD MCP", "server": res.server, "tool": res.tool,
               "params": res.params, "called_at": res.called_at, "ms": res.ms, "status": res.status,
               "attempts": res.attempts, "error": res.error,
               "raw_sha256": hashlib.sha256(raw_bytes).hexdigest()[:16]}
        with self.call_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
