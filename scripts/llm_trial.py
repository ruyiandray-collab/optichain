"""LLM 模型对比试验：同一批证据分别用两个模型抽取，比较引文校验通过率、阶段、耗时与 token。"""
import json, sys, time, importlib
sys.path.insert(0, '.')
import pipeline.extract as X
names = {'300308.SZ': '中际旭创', '002281.SZ': '光迅科技', '002273.SZ': '水晶光电', '300394.SZ': '天孚通信'}
ev = [json.loads(l) for l in open('data/staging/evidence_raw.jsonl', encoding='utf-8')]
picked = [e for e in ev if e['entity'] in names and any(k in e['text'] for k in ('CPO', '共封装'))][:8]
key = X.api_key()
model = sys.argv[1]
X.MODEL = model
t0 = time.time(); tot = {'p': 0, 'c': 0}; out = []
for e in picked:
    ok, rej, meta = X.extract_one(e, names[e['entity']], key)
    tot['p'] += meta['usage'].get('prompt_tokens', 0); tot['c'] += meta['usage'].get('completion_tokens', 0)
    out.append({'eid': e['evidence_id'], 'name': names[e['entity']], 'src': e['source_name'][:24],
                'ok': [(c['concept'], c['llm_stage'], c['quote'][:70]) for c in ok], 'rej': [r['reason'] for r in rej]})
print(model, f'{time.time()-t0:.0f}s', tot)
for o in out:
    print('==', o['name'], o['src']); [print('   OK ', x) for x in o['ok']]; [print('   REJ', x) for x in o['rej']]
json.dump(out, open(f'data/staging/llm_trial_{model}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
