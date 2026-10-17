"""对比：规则按句回退抽取 vs LLM 抽取 + 规则裁定，逐家输出等级差异（写入 extractor_comparison.json）。"""
import json, sys
sys.path.insert(0, '.')
import yaml
from pipeline import grade
from pipeline.m2 import _jsonl, search_scope
U = yaml.safe_load(open('config/universe.yaml', encoding='utf-8'))
ev, recs, llm_claims = _jsonl('evidence_raw.jsonl'), _jsonl('records.jsonl'), _jsonl('claims.jsonl')
rule_claims = [cl for e in ev for c in grade.CONCEPTS for cl in grade.fallback_claims(e, c)]
rows = []
for c in U['cn']:
    evs = [e for e in ev if e['entity'] == c['code']]; ids = {e['evidence_id'] for e in evs}
    for concept in grade.CONCEPTS:
        a = grade.grade_entity(c['code'], concept, evs, [x for x in rule_claims if x['evidence_id'] in ids], recs, search_scope(c))
        b = grade.grade_entity(c['code'], concept, evs, [x for x in llm_claims if x['evidence_id'] in ids], recs, search_scope(c))
        rows.append({'name': c['name'], 'concept': concept, 'rule_only': a['grade'], 'llm_plus_rule': b['grade'],
                     'rule_only_quote': (a.get('best') or {}).get('quote'), 'llm_quote': (b.get('best') or {}).get('quote')})
dist = lambda k, con: {g: sum(1 for r in rows if r['concept'] == con and r[k] == g) for g in grade.GRADES}
out = {'distribution': {con: {'rule_only': dist('rule_only', con), 'llm_plus_rule': dist('llm_plus_rule', con)} for con in grade.CONCEPTS},
       'changed': [r for r in rows if r['rule_only'] != r['llm_plus_rule']]}
json.dump(out, open('data/staging/extractor_comparison.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(out['distribution'], ensure_ascii=False))
for r in out['changed']:
    print(f"{r['name']:5s} {r['concept'][:4]} {r['rule_only']}→{r['llm_plus_rule']} | 规则引文: {(r['rule_only_quote'] or '')[:50]}")
