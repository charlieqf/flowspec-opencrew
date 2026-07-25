# FlowSpec JSON Schema：现行导出与目标契约

根目录与 `proposed/` 必须分开理解：根目录 `*.schema.json` 是**从真实 Pydantic 契约模型自动导出的 `[implemented]` Schema**；`proposed/*.schema.json` 是 FlowSpec v0.4 的**目标工程契约**，虽可机器校验并已被 demo 物化，但 OpenCrew 通用 runner 尚未接入。

- **唯一事实来源**：`backend/opcrew_backend/tool_sessions/schemas/models.py`
- **重新生成**：`backend/.venv/bin/python docs/WorkflowSpec/schema/_generate.py`
- **现行 drift/示例测试**：`backend/.venv/bin/python -m pytest docs/WorkflowSpec/schema/test_schema_examples.py`

| 文件 | 对应模型 |
|---|---|
| `Variables.schema.json` | `Variables`（`extra=forbid`，28 字段固定白名单——业务字段会被拒绝） |
| `OutputManifest.schema.json` | `OutputManifest`（`files[]`，键 `path/kind/schema_name/sha256/size/...`） |
| `State.schema.json` | `State`（无 `waiting/wait_reason/result`） |
| `ToolResult.schema.json` | `ToolResult`（`context_patch` 为纯 dict） |
| `SessionContextPatch.schema.json` | `SessionContextPatch`（`{tool_id,step_id,patch}`，runner 侧包装用） |
| `DependencyCheckResult.schema.json` | `DependencyCheckResult`（`status: ready\|blocked`，无 waiting） |
| `InputManifest.schema.json` | `InputManifest` |
| `PromptManifest.schema.json` | `PromptManifest`（prompt/reference/model call 的现行开放结构） |
| `ModelCallAudit.schema.json` | `ModelCallAudit`（模型/provider/request/usage/脱敏/幂等审计） |

`examples/` 下的 `*.valid.json` 必须通过校验；`*.invalid-*.json` 必须被拒绝（例如 `Variables.invalid-extra-field.json` 往 Variables 里塞 `applicant_valid/risk_score` 业务字段，验证 `extra=forbid` 会拒绝——这正是 [07·3.B](../07_ImplementationBinding.md) 说"业务字段当前塞不进 Variables"的可执行证据）。

## proposed/ —— 目标绑定的正式 Schema `[proposed]`

`proposed/` 是**手写**的目标绑定（均非现行 Pydantic 契约）：

| 文件 | 目标契约 |
|---|---|
| `Process.schema.json` | illustrative/executable Process：DAG、最小 guard、typed Artifact、branch closure、exactly-one outcome、Human Gate、fanout/reduce、资源、预算与有界重试 |
| `Error.schema.json` | prepare/run/finalize/control 阶段的安全结构化错误；`retryable` 是分类提示而非调度命令 |
| `Checkpoint.schema.json` | Tool 安全完成边界发布的不可变恢复产物，绑定 Tool 版本、输入哈希、恢复 token 与状态文件 sha256 |
| `AIExecutionProfile.schema.json` | Model/Agent 的模型选择、预算、数据边界、输出校验、流式与 Agent 能力政策 |
| `AIUsageRecord.schema.json` | 每个 billable Model Invocation 的 token/媒体单位、费用状态与 operation 去重记录 |
| `ToolRegistry.schema.json` | 版本化 Tool entrypoint、I/O、side-effect、mock/reconciliation/checkpoint 能力 |
| `RunRecord.schema.json` | Run/Step Attempt、definition snapshot、canonical Artifact、Human Task、Event、Usage 与预算汇总；派生摘要存 producer Attempt，Artifact 不重复 Run 级输入 hash |
| `BudgetLedger.schema.json` | Run 内逐 operation 的 reserve/settle/release/adjust 预算流水 |
| `DiagnosticLogRecord.schema.json` | Step Attempt 诊断日志记录：级别、channel、关联身份、可见性与敏感度 |
| `StorageIndex.schema.json` | session/task/Run/Step/Attempt 的存储基准、物理 locator、事实权威与物化状态索引 |

- `proposed/examples/*.valid.json` 覆盖 Process、Error、Checkpoint、AI Profile 与 AI Usage 目标契约。
- `proposed/lint_process.py` 补 JSON Schema 不适合处理的跨步骤检查：step ID 唯一、依赖存在、DAG 无环、typed Artifact 引用、fanout stable key/并发/失败/reduce、Human Gate、outcome 完备性与 retry 安全。
- `test_schema_examples.py` 直接抽取四份概念示例和 `02` 的 Process JSONC，同时运行 Schema + linter；另有结构与图语义反例。
- `test_demo_contracts.py` 跨文件验证综述链接、四套 executable Process/Registry/Profile、双 Run 修订链、Context、Artifact hash/binding/provenance、Human Task revision、Event cursor/WaitKind、Usage、Budget、每 Attempt 至多一个 Agent Execution 及其多轮归组、ArtifactPathBase/StorageIndexBase 边界、definition digest、fork/join、四种运行范围说明、StorageIndex/诊断日志、确定性重建，以及“仅保留综述、Demo 入口和四场景”6 个 purpose-built HTML 的目录约束。

```bash
backend/.venv/bin/python docs/WorkflowSpec/schema/proposed/lint_process.py process.json

# 运行现行 Schema + 目标 Schema + 四套 demo 的全部回归
backend/.venv/bin/python -m pytest docs/WorkflowSpec/schema -q
```

> 这里的“executable demo”指文档内 Mock Runtime 能从冻结定义物化完整 Run 并通过端到端契约测试；OpenCrew normalizer/生产运行时尚未接入该 Process Schema/linter，不能据此宣称目标流程已在 OpenCrew 实现。

RunRecord 已为 demo 定义 Human Task/Event/Artifact 的聚合记录，DiagnosticLogRecord 与 StorageIndex 已拆为独立目标 Schema；但 production Artifact/Event/HumanTask/Snapshot/Command Schema 仍待拆出，`wait_reason`、Variables 业务子文档和分布式 claim/lease/fencing 也尚未形成最终绑定。不要把 demo 聚合 `$defs` 或 proposed Schema 误写成生产 API 已冻结。
