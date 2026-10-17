# 只打印 answer/tables 部分（去掉 indicators_params 噪声），并打印 indicators_params 的键数
import json, sys, os
lim = int(os.environ.get('LIM', '1800'))
for pid in sys.argv[1:]:
    d = json.load(open(f'data/probe_raw/{pid}.json', encoding='utf-8'))
    text = '\n'.join(c.get('text', '') for c in d['response']['data']['result']['content'])
    try:
        inner = json.loads(text)
    except Exception:
        print(f'===== {pid} RAW', text[:lim]); continue
    data = inner.get('data')
    print(f'===== {pid} [{d["ms"]}ms] code={inner.get("code")} msg={inner.get("msg")} subMsg={inner.get("subMsg")}')
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ('trade_time_description',): continue
            if k == 'indicators_params':
                print('  indicators_params:', json.dumps(v, ensure_ascii=False)[:400]); continue
            s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            print(f'  [{k}]', s[:lim])
    else:
        print(str(data)[:lim])
