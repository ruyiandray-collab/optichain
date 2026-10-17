// Cloudflare Pages Function：POST /api/ask {question}
// 流程：合规拦截（确定性规则）→ 快照内检索 → DeepSeek 作答（只能用上下文）→ 引用与数字校验 → 返回。
import { compliance, buildIndex, search, coverage, uncheckedNumbers, uncitedNumbers, uncitedSentences, isNoInfoAnswer, SYSTEM } from "../_lib/qa.js";

const MIN_COVERAGE = 0.3; // 问题实词被最佳文档命中的比例下限；低于它直接拒答，高于它交给 LLM（“上下文没有就说没有”）兜底

let cache = null;

async function load(env, url) {
  if (cache) return cache;
  const get = async (p) => (await env.ASSETS.fetch(new URL(p, url))).json();
  const [rules, idx] = await Promise.all([get("/data/compliance.json"), get("/data/qa_index.json")]);
  cache = { rules, index: buildIndex(idx.docs), version: idx.version, cutoff: idx.data_cutoff };
  return cache;
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), { status, headers: { "content-type": "application/json; charset=utf-8" } });

// 简单限流：同一 IP 每 60 秒最多 RATE_MAX 次（按 Worker 实例的内存计数，尽力而为；更严格要用 KV / Durable Objects）
const RATE_MAX = 6, RATE_WINDOW_MS = 60_000;
const hitsByIp = new Map();
export function rateLimited(ip, now = Date.now()) {
  const arr = (hitsByIp.get(ip) || []).filter((t) => now - t < RATE_WINDOW_MS);
  arr.push(now);
  hitsByIp.set(ip, arr);
  if (hitsByIp.size > 5000) hitsByIp.clear(); // 防止内存无限增长
  return arr.length > RATE_MAX;
}

export async function onRequestPost({ request, env }) {
  const ip = request.headers.get("cf-connecting-ip") || "local";
  if (rateLimited(ip)) {
    return json({ refused: true, reason: "rate_limited", answer: "提问太频繁了，请稍等一分钟再试。", citations: [] }, 429);
  }
  let question = "";
  try {
    question = String((await request.json()).question || "").trim().slice(0, 300);
  } catch {
    return json({ refused: true, reason: "bad_request", answer: "请求格式错误。", citations: [] }, 400);
  }
  if (!question) return json({ refused: true, reason: "empty", answer: "请输入问题。", citations: [] }, 400);

  const { rules, index, version, cutoff } = await load(env, request.url);
  const c = compliance(question, rules);
  if (c.refused) return json({ refused: true, reason: c.rule, answer: c.reply, citations: [], version });

  const hits = search(index, question, 8);
  const cov = Math.max(0, ...hits.slice(0, 3).map((h) => coverage(index, question, h)));
  if (!hits.length || hits[0].s < 2 || cov < MIN_COVERAGE) {
    return json({ refused: true, reason: "no_evidence", answer: rules.no_evidence_reply, citations: [], version });
  }
  const context = hits.map(({ d }) => `[${d.id}] ${d.title}${d.date ? `（${d.date}）` : ""}：${d.text}`).join("\n");
  if (!env.DEEPSEEK_API_KEY) {
    return json({ refused: true, reason: "llm_unavailable", answer: "问答服务暂不可用（未配置模型密钥）。", citations: [], version });
  }

  let out;
  try {
    const r = await fetch("https://api.deepseek.com/chat/completions", {
      method: "POST",
      headers: { authorization: `Bearer ${env.DEEPSEEK_API_KEY}`, "content-type": "application/json" },
      body: JSON.stringify({
        model: env.DEEPSEEK_MODEL || "deepseek-flash", temperature: 0, max_tokens: 4000,
        response_format: { type: "json_object" },
        messages: [{ role: "system", content: SYSTEM },
                   { role: "user", content: `【上下文】（快照 ${version}，数据截止 ${cutoff}）\n${context}\n\n【问题】${question}` }],
      }),
    });
    if (!r.ok) throw new Error(`LLM ${r.status}`);
    const body = await r.json();
    out = JSON.parse(body.choices?.[0]?.message?.content || "{}");
  } catch (e) {
    return json({ refused: true, reason: "llm_error", answer: "问答服务暂时出错，请稍后再试。", citations: [], version }, 502);
  }

  const answer = String(out.answer || "");
  const allowed = new Set(hits.map(({ d }) => d.id));
  // 模型有时只写编号的后半段（如 300308.SZ）：唯一匹配到上下文编号时才映射，否则丢弃
  const resolve = (id) => {
    if (allowed.has(id)) return id;
    const m = [...allowed].filter((a) => a.endsWith(`:${id}`) || a.endsWith(id));
    return m.length === 1 ? m[0] : null;
  };
  const inline = [...answer.matchAll(/\[([^\]]+)\]/g)].map((x) => x[1]);
  let cites = [...new Set([...(out.citations || []), ...inline].map(resolve).filter(Boolean))];
  // 不再替模型补引用（交叉核对发现：无引用的回答会被自动配上检索结果当依据）
  if (isNoInfoAnswer(answer)) {  // 模型判断快照里没有：返回统一的“没有证据”话术，不展示模型的附带说明
    return json({ refused: true, reason: "no_evidence", answer: rules.no_evidence_reply, citations: [], version });
  }
  const noCite = uncitedSentences(answer, resolve);
  if (!cites.length || noCite.length) {
    return json({ refused: true, reason: "answer_blocked_uncited_claims", uncited_sentences: noCite.map((x) => x.slice(0, 80)),
                  answer: "生成的回答里有结论没有标注依据，已拦截。请查看下方检索到的证据原文。",
                  citations: hits.slice(0, 3).map(({ d }) => ({ id: d.id, title: d.title, ref: d.ref, evidence_id: d.kind === "evidence" ? d.id : undefined })),
                  version });
  }
  // 输出侧再做两道确定性校验：答案不能有投资建议措辞；答案里的数字必须都出现在上下文里
  if (compliance(answer, rules).refused) {
    return json({ refused: true, reason: "answer_blocked_advice", answer: rules.rules[0].reply, citations: [], version });
  }
  const bad = uncheckedNumbers(answer, context, (id) => Boolean(resolve(id)));
  if (bad.length) {
    return json({ refused: true, reason: "answer_blocked_numbers", unchecked_numbers: bad,
                  answer: "生成的回答里有数字在检索到的证据中找不到，已拦截。请查看下方引用的证据原文。",
                  citations: hits.slice(0, 3).map(({ d }) => ({ id: d.id, title: d.title, ref: d.ref, evidence_id: d.kind === "evidence" ? d.id : undefined })),
                  version });
  }
  const byId = Object.fromEntries(hits.map(({ d }) => [d.id, d]));
  // 逐句：每个数字都要有引用，并出现在被引用的那份文档里
  const uncited = uncitedNumbers(answer, resolve, (id) => `${byId[id].title} ${byId[id].text}`);
  if (uncited.length) {
    return json({ refused: true, reason: "answer_blocked_uncited_numbers", uncited_numbers: uncited,
                  answer: "生成的回答里有数字没有对应引用（或引用的文档里找不到这个数字），已拦截。请查看下方检索到的证据原文。",
                  citations: hits.slice(0, 3).map(({ d }) => ({ id: d.id, title: d.title, ref: d.ref, evidence_id: d.kind === "evidence" ? d.id : undefined })),
                  version });
  }
  return json({
    refused: false, answer, version, data_cutoff: cutoff,
    numbers_verified: true, // 已通过 uncheckedNumbers：答案里的每个数字都出现在检索上下文中
    citations: cites.map((id) => ({ id, title: byId[id].title, ref: byId[id].ref,
                                    evidence_id: byId[id].kind === "evidence" ? id : undefined, url: byId[id].url })),
    retrieved: hits.map(({ d, s }) => ({ id: d.id, score: Math.round(s * 100) / 100 })),
  });
}
