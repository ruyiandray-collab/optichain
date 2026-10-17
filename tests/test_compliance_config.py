"""合规配置结构校验：每条规则都必须有 id、patterns 和它自己的 reply，不能有重复键。
（交叉核对发现：插入新规则时把“投资建议”的 reply 挤到了别的规则下，YAML 重复键静默覆盖，测试没抓到）"""
from pathlib import Path

import yaml

CFG = Path(__file__).resolve().parent.parent / "config" / "compliance.yaml"


class _NoDupLoader(yaml.SafeLoader):
    pass


def _no_dup(loader, node, deep=False):
    keys = [loader.construct_object(k, deep=deep) for k, _ in node.value]
    dup = {k for k in keys if keys.count(k) > 1}
    assert not dup, f"compliance.yaml 第 {node.start_mark.line + 1} 行附近有重复键：{dup}"
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_NoDupLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_dup)


def test_every_rule_has_its_own_reply_and_no_duplicate_keys():
    d = yaml.load(CFG.read_text(encoding="utf-8"), Loader=_NoDupLoader)
    for r in d["rules"]:
        assert r.get("id") and r.get("patterns") and r.get("reply"), r.get("id")
    replies = {r["id"]: r["reply"] for r in d["rules"]}
    assert "投资建议" in replies["investment_advice"]
    assert "绕过产品规则" in replies["prompt_injection"]
    assert "全文" in replies["full_text"]
    assert "非实时" in replies["realtime"] or "不是实时" in replies["realtime"]
