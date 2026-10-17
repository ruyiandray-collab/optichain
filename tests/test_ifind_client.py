"""客户端异常路径：429 包在 ok 里、空结果、参数错误、超时、缓存。用假 transport，不发真实请求。"""
import json
from pathlib import Path

import pytest
import requests

from pipeline.ifind import IfindClient, classify

RPC = json.loads((Path(__file__).parent / "fixtures" / "rpc.json").read_text(encoding="utf-8"))

# 2026-10-17 并发压测实际收到的形态（burst_6.json）：HTTP 200、无 error 字段、文本里是 429
RATE_LIMITED = {"jsonrpc": "2.0", "id": 5, "result": {"content": [{"type": "text", "text":
    'call failed, status: 429, response: {"answer":"请求过于频繁，请降低频率；个人轻量级用户可通过升级权益获取高频/实时行情独立额外限流池"}'}]}}


def test_classify_ok():
    status, _, inner, _ = classify(RPC["ok"])
    assert status == "ok" and inner["code"] == 1


@pytest.mark.parametrize("key", ["empty", "empty_news"])
def test_classify_empty(key):
    assert classify(RPC[key])[0] == "empty"


def test_classify_rate_limited_inside_ok():
    assert classify(RATE_LIMITED)[0] == "rate_limited"


def test_classify_bad_args():
    status, _, _, err = classify(RPC["bad_args"])
    assert status == "error" and "time_scope" in err


class FakeTransport:
    """按脚本返回响应；initialize 总是成功。"""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, server, payload, timeout):
        if payload.get("method") == "initialize":
            return 200, {"Mcp-Session-Id": "sid"}, {"result": {}}
        if payload.get("method") == "notifications/initialized":
            return 202, {}, None
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return 200, {}, r


def make(tmp_path, responses):
    t = FakeTransport(responses)
    c = IfindClient(token="x", transport=t, cache_dir=tmp_path / "cache", call_log=tmp_path / "calls.jsonl",
                    min_interval=0, sleep=lambda s: None)
    return c, t


def test_retry_after_429_then_ok(tmp_path):
    c, t = make(tmp_path, [RATE_LIMITED, RATE_LIMITED, RPC["ok"]])
    r = c.call("stock", "get_stock_financials", {"query": "q"})
    assert r.status == "ok" and r.attempts == 3 and t.calls == 3
    log = [json.loads(l) for l in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["status"] == "ok" and log[-1]["attempts"] == 3


def test_gives_up_after_max_retries(tmp_path):
    c, t = make(tmp_path, [RATE_LIMITED] * 3)
    r = c.call("stock", "get_stock_financials", {"query": "q"})
    assert r.status == "rate_limited" and t.calls == 3
    assert not (tmp_path / "cache").exists()  # 失败不缓存


def test_timeout_then_ok(tmp_path):
    c, _ = make(tmp_path, [requests.Timeout(), RPC["ok"]])
    assert c.call("stock", "t", {"query": "q"}).status == "ok"


def test_bad_args_not_retried(tmp_path):
    c, t = make(tmp_path, [RPC["bad_args"], RPC["ok"]])
    r = c.call("news", "search_trending_news", {"size": 5})
    assert r.status == "error" and t.calls == 1


def test_cache_hit_skips_network(tmp_path):
    c, t = make(tmp_path, [RPC["ok"]])
    a = c.call("stock", "t", {"query": "q"})
    b = c.call("stock", "t", {"query": "q"})
    assert t.calls == 1 and b.from_cache and a.call_id == b.call_id
    assert b.data == a.data
