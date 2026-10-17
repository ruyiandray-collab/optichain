# 打印某个 probe 的返回正文（解开 MCP content.text 里的 JSON）
import json, sys
for pid in sys.argv[1:]:
    d = json.load(open(f'data/probe_raw/{pid}.json', encoding='utf-8'))
    content = d['response']['data']['result']['content']
    text = '\n'.join(c.get('text', '') for c in content)
    try:
        inner = json.loads(text)
        data = inner.get('data')
        out = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)
        print(f'===== {pid} code={inner.get("code")} msg={inner.get("msg")} keys={list(data.keys()) if isinstance(data, dict) else type(data).__name__}')
    except Exception:
        out = text
        print(f'===== {pid} (raw text)')
    lim = int(__import__('os').environ.get('LIM', '2500'))
    print(out.replace('\n', '\n')[:lim])
