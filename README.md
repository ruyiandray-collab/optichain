# OptiChain · AI 算力光互联产业研究台

> 进行中（M1）。M6 时补全目标用户、核心设计、AI 角色、数据使用、已知边界和未做事项。
> 本产品不构成投资建议；所有数据非实时，均标注数据截止日。

## 目录

```
config/      universe.yaml（公司全集）、segments.yaml（产业链口径与产品词表）
pipeline/    ifind.py（iFinD 客户端）、tables.py（表格解析）、fiscal.py（财年映射）
             ingest.py（M1 采集）、sec.py（SEC 第二来源）、checks.py（确定性校验）
tests/       单元测试（夹具为真实返回的节选）
docs/        data_probe.md、proposal.md、risks.md、ai_log.md
data/staging records.jsonl（规范化记录）、api_calls.jsonl（调用元数据）、checks.json
```

## 运行

```bash
pip install -r requirements.txt
python -m pytest -q
python -m pipeline.ingest     # 需要 iFinD 密钥（IFIND_CONFIG 指向 mcp_config.json）
python -m pipeline.checks
```
