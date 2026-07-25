# FlowSpec executable demos

- 定位：`examples/*.md` 解释四场景的领域模型，`demos/<scenario>/` 将其物化为可校验 Process、Mock、Run 与 UI；Demo 执行事实以并列 JSON/NDJSON 为准，HTML 只是投影。
- 重建：`backend/.venv/bin/python docs/WorkflowSpec/demos/build_all.py`
- 验收：`backend/.venv/bin/python -m pytest docs/WorkflowSpec/schema/test_demo_contracts.py -q`
- 增加第 5 个场景：沿用 `loan-approval/` 的目录契约，并参见[第 12 章](../12_ExecutableDemos.md)。
