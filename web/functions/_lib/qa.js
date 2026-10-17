// 问答的纯逻辑（无网络）：合规拦截、检索（字符二元组 BM25）、答案数字校验。
// 由 functions/api/ask.js 调用，也由 web/functions-tests/qa.test.mjs 直接测试。


const norm = (s) => (s || "").toLowerCase().replace(/\s+/g, "");

export function bigrams(s) {
  const t = norm(s).replace(/[，。；：、（）()“”"'《》【】\[\]·,.;:!?！？|]/g, " ");
  const out = [];
  for (const seg of t.split(" ")) {
    if (!seg) continue;
    // 英文 / 数字按词，中文按二元组
    const words = seg.match(/[a-z0-9.%]+|[一-鿿]+/g) || [];
    for (const w of words) {
      if (/^[a-z0-9.%]+$/.test(w)) { if (w.length > 1) out.push(w); continue; }
      if (w.length === 1) out.push(w);
      for (let i = 0; i < w.length - 1; i++) out.push(w.slice(i, i + 2));
    }
  }
  return out;
}

export function buildIndex(docs) {
  const df = new Map();
  const toks = docs.map((d) => {
    const tf = new Map();
    for (const t of bigrams(`${d.title} ${d.entity_name || ""} ${d.text}`)) tf.set(t, (tf.get(t) || 0) + 1);
    for (const t of tf.keys()) df.set(t, (df.get(t) || 0) + 1);
    return tf;
  });
  const avg = toks.reduce((a, tf) => a + [...tf.values()].reduce((x, y) => x + y, 0), 0) / Math.max(docs.length, 1);
  return { docs, toks, df, avg, N: docs.length };
}

// 含虚词的二元组不算“实词”，用于判断问题是否在快照覆盖范围内
const FUNCTION_CHARS = /[的了吗呢么哪个是有在和与及或于把被从对为这那些什怎多少吧呀啊]/;

export function contentBigrams(question) {
  return [...new Set(bigrams(question))].filter((t) => !(t.length === 2 && FUNCTION_CHARS.test(t)));
}

// 最佳文档命中问题实词的比例；低于阈值视为“快照没有覆盖”
export function coverage(index, question, hit) {
  const q = contentBigrams(question);
  if (!q.length || !hit) return 0;
  const i = index.docs.indexOf(hit.d);
  return q.filter((t) => index.toks[i].has(t)).length / q.length;
}

export function search(index, question, k = 8) {
  const q = [...new Set(bigrams(question))];
  const k1 = 1.2, b = 0.75;
  const scored = index.docs.map((d, i) => {
    const tf = index.toks[i];
    const len = [...tf.values()].reduce((x, y) => x + y, 0);
    let s = 0;
    for (const t of q) {
      const f = tf.get(t);
      if (!f) continue;
      const idf = Math.log(1 + (index.N - index.df.get(t) + 0.5) / (index.df.get(t) + 0.5));
      s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + (b * len) / index.avg));
    }
    // 问题里直接出现公司名时，优先该公司的卡片和证据
    if (d.entity_name && question.includes(d.entity_name)) s *= 1.6;
    if (d.kind === "card" && d.title && question.includes(d.title.replace(/ 概况$/, ""))) s *= 1.6;
    return { d, s };
  });
  return scored.filter((x) => x.s > 0).sort((a, b) => b.s - a.s).slice(0, k);
}

// 答案里的数字（含小数、百分号）必须逐个出现在上下文里
export function numbersIn(s) {
  return s.match(/\d+(?:\.\d+)?%?/g) || [];
}

// ---- 归一化（合规和数字校验之前都先做）----
// 全角 → 半角（NFKC），清除零宽 / 格式控制字符，常见繁体 → 简体，去掉千分位。
// 审查发现：全角“９%”、繁体“可以買嗎”、空格“能 买 吗”、零宽字符“买​入”都能绕过原来的检查。
const TRAD = { 買: "买", 賣: "卖", 倉: "仓", 價: "价", 漲: "涨", 該: "该", 嗎: "吗", 適: "适", 會: "会", 際: "际",
  創: "创", 長: "长", 圖: "图", 實: "实", 進: "进", 對: "对", 現: "现", 標: "标", 優: "优", 評: "评", 級: "级", 資: "资",
  觀: "观", 點: "点", 億: "亿", 萬: "万", 兩: "两", 預: "预", 測: "测", 營: "营", 減: "减", 增: "增", 翻: "翻", 倍: "倍" };
// 用码点构造，避免源码里出现 U+2028 等会被当作换行的字符
const FORMAT_CHARS = new RegExp("[" + [[0xad, 0xad], [0x200b, 0x200f], [0x2028, 0x202f], [0x2060, 0x206f], [0xfeff, 0xfeff]]
  .map(([a, b]) => String.fromCharCode(a) + "-" + String.fromCharCode(b)).join("") + "]", "g");
export function normalizeText(s) {
  let t = String(s || "").normalize("NFKC").replace(FORMAT_CHARS, "");
  t = t.replace(/[一-鿿]/g, (c) => TRAD[c] || c);
  t = t.replace(/(\d),(?=\d{3}\b)/g, "$1"); // 1,234.5 → 1234.5
  return t.replace(/−/g, "-");
}

export function compliance(question, rules) {
  const q = normalizeText(question);
  const compact = q.replace(/\s+/g, "");
  for (const r of rules.rules) {
    for (const p of r.patterns) {
      const re = new RegExp(p, "i");
      if (re.test(q) || re.test(compact)) return { refused: true, rule: r.id, reply: r.reply };
    }
  }
  return { refused: false };
}

// ---- 数字校验 ----
// 每个数字按“数值 + 单位（区分币种）+ 显式符号 + 修饰词 + 所属指标”比对；中文数字、倍数表达必须原样出现在依据里。
const COUNT_AFTER = /^\s*(条|家|项|名|位|次|句|类|组|步|档|种|方面|季度|个季度|个月|个环节|个变量|个公司|个问题)/;
const UNIT = /^\s*(个百分点|pp|%|亿元|亿美元|万亿元|万亿|亿|百万美元|百万元|百万|万元|万美元|万|美元|元|倍|x|年|月|日|季度|Q)/i;
const UNIT_CANON = { "个百分点": "pp", "百万元": "百万元", "万亿元": "万亿元" };
const UNIT_FAMILY = { "亿元": "亿", "亿美元": "亿", "万亿元": "万亿", "百万美元": "百万", "百万元": "百万", "万元": "万", "万美元": "万" };
const MODIFIER = /(超过|超|逾|近|约|接近|将近|不到|不足|高达|仅|达)\s*$/;
const QUANT = /(翻了?[一二两三四五六七八九十\d]+(番|倍)|翻番|翻倍|[一二两三四五六七八九十]+倍|百分之[零一二三四五六七八九十百点]+|[一二三四五六七八九十两]+成|[零一二三四五六七八九十百千两]+(亿|万|元|倍|个百分点))/g;
// 指标同义组：回答里数字前面的指标，必须和依据里同一个数字前面的指标属于同一组（交叉核对发现“营收同比 99.0%”被写成“净利率 99.0%”）
const METRIC_GROUPS = [["营收", "营业收入", "收入"], ["毛利率"], ["净利率"], ["净利润", "归母净利"], ["毛利"], ["占比", "比例", "份额"],
  ["市值"], ["市盈率", "PE"], ["市销率", "PS"], ["Capex", "资本开支", "资本性支出"], ["出口"], ["汇率", "中间价"], ["研发费用"],
  ["同比"], ["环比"], ["敏感性"], ["境外"]];
const metricOf = (before) => {
  let best = null, pos = -1;
  for (const g of METRIC_GROUPS) for (const w of g) {
    const i = before.lastIndexOf(w);
    if (i > pos) { pos = i; best = g[0]; }
  }
  return best;
};

function tokens(text) {
  const t = normalizeText(text);
  const out = [];
  for (const m of t.matchAll(/([+-])?(\d+(?:\.\d+)?)/g)) {
    const after = t.slice(m.index + m[0].length);
    // 10-K / 20-F 等表单编号、字母夹着的代码不是数字事实（实测：“10-K”被当成数字 10）
    if (/^-[KQF](?![A-Za-z])/.test(after) || (/^[A-Za-z]/.test(after) && /[A-Za-z]$/.test(t.slice(0, m.index)))) continue;
    const u = (after.match(UNIT) || [""])[0].trim();
    // 修饰词与指标只在同一分句内找（实测发现：跨过逗号把“B 订单/量产 3 只”的 3 认成了“收入”）
    const before = t.slice(Math.max(0, m.index - 14), m.index).split(/[，,；;。：:、（()）【】\[\]/]/).pop();
    const unit = UNIT_CANON[u] || u.toLowerCase();
    out.push({ raw: m[0], value: parseFloat(m[2]), sign: m[1] || "", unit, family: UNIT_FAMILY[u] || unit,
               // 指标词必须紧挨数字（结尾距数字 ≤ 6 字）：线上实测“订单/量产/收入表述的 B 级为 3 只”被误绑到“收入”
               modifier: (before.match(MODIFIER) || [])[1] || "", metric: metricOf(before.slice(-8)),
               isCount: /^\d$/.test(m[2]) && (COUNT_AFTER.test(after) || before.endsWith("第")) });
  }
  return out;
}

export function numberSet(text) {
  const set = new Set();
  for (const k of tokens(text)) {
    for (const u of new Set([k.unit, k.family, "*"])) {
      set.add(`${k.value}|${u}`);
      if (k.sign) set.add(`${k.sign}${k.value}|${u}`);
      if (k.modifier) set.add(`${k.modifier}${k.value}|${u}`);
      if (k.metric) set.add(`${k.metric}@${k.value}|${u}`);
    }
  }
  return set;
}

function tokenOk(set, k) {
  const u = k.unit || "*";
  // 单位：带币种的必须同币种；只写“亿”的可以匹配同数量级的任意币种
  if (!set.has(`${k.value}|${u}`)) return false;
  // 显式符号：两个方向都校验（交叉核对发现 -27.2% → +27.2% 能通过）
  for (const [s, o] of [["+", "-"], ["-", "+"]]) {
    if (k.sign === s && !set.has(`${s}${k.value}|${u}`) && set.has(`${o}${k.value}|${u}`)) return false;
  }
  // 修饰词：“超 / 约 / 近 / 不到”会改变含义，必须与依据一致
  if (k.modifier && !set.has(`${k.modifier}${k.value}|${u}`)) return false;
  // 指标绑定：回答里写明了指标，依据里同一个数字前面也得是这个指标
  if (k.metric && !set.has(`${k.metric}@${k.value}|${u}`)) return false;
  return true;
}

function quantMissing(text, ctxText) {
  const ctx = normalizeText(ctxText);
  return [...normalizeText(text).matchAll(QUANT)].map((m) => m[0]).filter((x) => !ctx.includes(x));
}

// 只剥掉能解析成真实文档编号的方括号；其他方括号里的内容照常校验（审查发现：金额藏在方括号里能绕过）
export function stripCitations(text, isCitation) {
  return text.replace(/\[([^\]]*)\]/g, (all, id) => (isCitation(id) ? " " : ` ${id} `));
}

const describe = (k) => `${k.modifier}${k.sign}${k.value}${k.unit}${k.metric ? `（${k.metric}）` : ""}`;

export function uncheckedNumbers(answer, context, isCitation = () => false) {
  const set = numberSet(context);
  const body = stripCitations(answer, isCitation);
  // 全局检查只核对数值、单位、符号、修饰词；指标绑定在逐句检查里按被引用的文档做
  const bad = tokens(body).filter((k) => !k.isCount && !tokenOk(set, { ...k, metric: null })).map(describe);
  return bad.concat(quantMissing(body, context));
}

// 逐句校验：含数字的句子必须带引用编号，且每个数字都出现在该句所引用文档的原文里。
// resolve(id) 把模型写的编号映射成检索到的文档编号（映射不上返回 null）；docText(id) 返回文档全文。
export function sentences(answer) {
  // 句末标点之后紧跟的 [编号] 归属上一句
  // 按句号级切分（不按分号）：模型习惯在整句末尾标一次引用（实测发现分号切分会误拦）
  return answer.match(/[^。！？!?\n]+[。！？!?]?(?:\s*\[[^\]]+\])*/g) || [];
}

export function uncitedNumbers(answer, resolve, docText) {
  const problems = [];
  for (const s of sentences(answer)) {
    const ids = [...s.matchAll(/\[([^\]]+)\]/g)].map((m) => resolve(m[1])).filter(Boolean);
    const body = stripCitations(s, (id) => Boolean(resolve(id)));
    const cited = ids.map((id) => docText(id)).join(" | ");
    const set = numberSet(cited);
    for (const k of tokens(body)) {
      if (k.isCount || tokenOk(set, k)) continue;
      problems.push({ sentence: s.trim().slice(0, 80), number: describe(k), cited: ids });
    }
    for (const x of quantMissing(body, cited)) problems.push({ sentence: s.trim().slice(0, 80), number: x, cited: ids });
  }
  return problems;
}

// 每个实质性句子都要有引用（交叉核对发现：不含数字的断言“全球行业龙头”也能通过；接口还会替无引用答案补引用）
const NO_INFO = /快照中没有|没有检索到|无法回答|事实不足|不提供|不构成投资建议|未给出|未提供|没有给出|未披露|没有相关/;
// 模型自己表示“快照里没有”：以第一句为准（线上实测：这类回答常因附带说明没标引用被拦，用户只看到“已拦截”）
export function isNoInfoAnswer(answer) {
  const first = (sentences(answer)[0] || "").trim();
  // 必须是“快照中没有相关信息 / 没有检索到 / 无法回答”这类完整说法；“快照中没有 A 级公司”这种是正常结论（线上实测误判）
  return /^(快照(中|里)?没有(相关|这方面的?)?(信息|数据|证据|内容)|没有检索到|无法回答)/.test(first);
}
export function uncitedSentences(answer, resolve) {
  return sentences(answer).map((s) => s.trim()).filter((s) => {
    const plain = s.replace(/\[[^\]]*\]/g, "").replace(/[。；;！!？?\s]/g, "");
    if (plain.length < 10 || NO_INFO.test(s)) return false;
    return ![...s.matchAll(/\[([^\]]+)\]/g)].some((m) => resolve(m[1]));
  });
}

export const SYSTEM =`你是产业研究助手。只能依据【上下文】回答问题，回答使用中文，简洁。
规则：
1. 只使用上下文里出现过的事实和数字，不得自行计算、换算或估计任何数字；上下文没有的就说“快照中没有相关信息”。
2. 每个关键结论后面用 [文档编号] 标注来源，编号必须完整照抄上下文方括号里的编号（例如 [card:exposure:300308.SZ]），不要缩写；**每个句子末尾都必须带出处编号；数字（含单位、正负号、“约 / 超”等修饰词）必须原样出自该句所引用的文档，并且指标要对应（例如不能把“营收同比”的数字写成“净利率”）**。不要用中文数字或“翻倍”这类表达改写原文数字。
3. 不提供买卖、目标价、仓位、涨跌判断，不构成投资建议。
4. 输出 JSON：{"answer":"...","citations":["文档编号", ...]}。`;
