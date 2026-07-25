# 07 · 可实现绑定（Implementation Binding）

> 把前面各章散落的 Schema、命名约定、目录约定、状态枚举、事件契约**汇总成一处**，可直接照着写代码。这是"下层可替换"的具体形态——你可用任意语言/存储实现，只要遵守这些契约。

## 1. 命名约定（Naming Conventions）

| 对象 | 约定 | 示例 |
|---|---|---|
| Process ID | 稳定 `snake_case`，**不编码版本** | `loan_approval`；版本另存 `version:1.1.0` |
| session ID（业务实例身份，拥有 Workspace） | `{domain}_{bizkey}` | `loan_2024_00123` |
| task ID（配对业务记录，1:1） | `{session_id}_t` 或独立键 | `loan_2024_00123_t` |
| Run ID | `run_<ms>_<uuid12>`（时间可排序） | `run_1737019200000_a1b2c3d4e5f6` |
| Step Attempt No | Run 内、Step 内递增整数 | `2` |
| Step ID | 流程版本内稳定、可读、不得依赖展示顺序解释语义 | `risk_decision` 或兼容期 `S4_risk_decision` |
| Step 物理目录 | 可含展示 index，但必须由 Step ID 显式映射 | `S4_risk_decision/` |
| Tool ID | `snake_case` | `credit_bureau_pull` |
| 变量字段 | `snake_case`，`*_ref` 表文件引用，`*_path` 表相对路径 | `risk_score` / `source_video_ref` |
| 产物名 | `PascalCase.ext`，下游按此名 consumes | `CreditReport.json` |
| 条件名（跨流程信号） | `{producer}_ready` 或 `{session}:{token}` | `credit_ready` / `loan_00123:S1_done` |
| 控制依赖 | `depends_on:[{step_id,statuses}]`（流程内，见 [02](./02_ProcessDefinition.md)） | `S6 depends_on S5:completed` |
| 资源池名 | `snake_case` | `gpu` / `tts_api` |
| 事件 kind | `{scope}.{verb}` | `step.completed` / `run.failed` |

> OpenCrew 参考：Run ID `tus_<ms>_<uuid12>`（`paths.py:create_tool_use_session_id`）；步骤目录 `S{index}_{tool}`。

## 2. 目录与存储约定（OpenCrew-compatible Canonical Layout）

```
<workspace_dir>/                                      # 1 session；task 引用它，不另建 task 根
├── inbox/                                             # [recommended] 原始输入
├── SessionContext/                                    # session 级输入/兼容上下文
├── SessionOutput/                                     # session-latest 投影（非 Artifact 权威）
├── SessionReport/                                     # 领域兼容报告
├── tool_use_sessions/<run_id>/                        # 1 Run
│   ├── 0_SessionContext/{Variables.json,InputManifest.json,<inputs...>}
│   ├── S{index}_{tool}/                               # 1 Step 私有根
│   │   ├── State.json                                # 仍在 Step 根，不迁移
│   │   ├── Working/Attempts/A{no}_{attempt_id}/       # [proposed/additive]
│   │   ├── Report/Attempts/A{no}_{attempt_id}/        # diagnostic.ndjson/stdout/stderr/error
│   │   ├── Prompt/Attempts/A{no}_{attempt_id}/        # 实际 prompt / transcript
│   │   └── Output/{.staging/A.../,OutputManifest.json,<published artifacts...>}
│   ├── SessionReport/{SessionRunSummary.json,Runtime.ndjson}
│   └── SessionOutput/{manifests,media,reports,json,...}
└── SessionScratch/                                    # 可清；不得保存唯一副本
```

兼容规则：保持 `tool_use_sessions`、`0_SessionContext`、Step 根、`State.json`、四个 Step 子目录和 canonical `Output/` 不变；Attempt 分区只是子目录扩展。Step 输入默认通过 Run `InputManifest` 与上游 `OutputManifest` 引用，不为“每步一个目录”而复制相同大文件。`SessionOutput` 是可重建的对外投影，不能取代 Step `OutputManifest`。完整输入/输出定位矩阵、path base 与安全解析见 [05·B1](./05_Workspace.md)；日志分层、级别和落点见 [06·F5](./06_Runtime_Observability.md)。

本地 OpenCrew 的解析链是 `OPENCREW_DATA_DIR`（默认 `~/.opencrew`）→ `sessions/<session_ref>/workspace` → DB `sessions.workspace_dir`；业务代码应从 DB/storage resolver 取 Workspace，而不是硬编码默认绝对路径。`ArtifactPathBase` 只含 `workspace|run|step|step_attempt|external`，用于正式 Artifact 的安全相对路径或外部版本 URI；`StorageIndexBase` 是它的索引超集，另加 `run_bundle|database|platform`，分别定位便携证据包、数据库事实和 Workspace 外的服务/进程日志 sink。后三者不是 Artifact 文件路径。

## 3. 核心 Schema 汇总

> ✅ **正式机器可校验 Schema 已就位**：[`schema/`](./schema/) 根目录下的 `*.schema.json` 由 [`schema/_generate.py`](./schema/_generate.py) **从真实 `models.py` 自动导出**；`schema/proposed/` 是目标契约。[`schema/test_schema_examples.py`](./schema/test_schema_examples.py) 做模型 drift/示例回归，[`schema/test_demo_contracts.py`](./schema/test_demo_contracts.py) 则跨文件验证四套定义、Run、Artifact、Usage、Budget 与 HTML。下面的 JSONC 是**人读速记**。
>
> **两组分开看**：3.A 是 **`[implemented]`** —— 对应 `models.py` 真实模型（`ContractModel` 全部 `extra="forbid"` + `schema_version:"1.0"`）；3.B 是 **`[proposed]`** —— 规范想要但当前模型**没有**的字段/结构。**实现以 3.A / `schema/` 为准**；3.B 需先扩模型。Process/Tool Registry（3.1/3.2）是 `[proposed]` 目标形态，真实归一化结构见 [02·B1](./02_ProcessDefinition.md) 说明。

---

### 3.A 现行契约 `[implemented]`（源：`tool_sessions/schemas/models.py`）

#### 3.A.1 Variables（`models.py:17`，`extra=forbid`）
```jsonc
// 字段是固定白名单——业务字段塞不进来（extra=forbid），业务数据请落 workspace 文件或独立业务存储
{ "schema_version":"1.0",
  "tool_use_session_id":"tus_...",        // ← Run 的 ID（不是 run_id）
  "workflow_id":"", "task_id": 0,          // task_id 是 int|None
  "opencrew_session_id": 0,                // int|None（不是字符串 session_id）
  "opencode_session_id":"",
  "workspace_dir":"", "tool_session_root":"",
  "current_attempt_id":0, "attempt_no":0,
  "workflow_plan_id":0, "workflow_plan_run_id":0,
  "current_prompt_version_id":0, "current_runtime_version_id":0,
  "prompt_model_provider":"", "prompt_model_id":"",
  "run_model_provider":"", "run_model_id":"",
  "provider_mode":"local_box", "billing_mode":"local_usage_only",
  "source_video_path":"", "clip_mode":"", "selected_scheme":"",
  "cloud_asr_data_transfer_allowed": false, "cloud_asr_data_transfer_scope":"",
  "cloud_asr_data_transfer_authorized_at":"",
  "created_at":"", "updated_at":"" }
// 正式机器可校验版本：schema/Variables.schema.json（由 models.py 自动导出，共 28 字段）
```

#### 3.A.2 OutputManifest（`models.py:78`）
```jsonc
{ "schema_version":"1.0", "tool_use_session_id":"", "step_id":"", "tool_id":"",
  "status":"completed",
  "files": [ { "path":"", "kind":"artifact", "schema_name":"",
               "sha256":"", "size":0, "visibility":"internal",
               "sensitivity":"normal", "downloadable":0 } ] }
// 注意：是 files[] 且键为 path/kind/schema_name/size；没有 name/media_type/role
// path 当前相对 Tool Use Session/Run root（runner 以 self.paths.root 解析），不是 session workspace 根
// status 在 Pydantic 模型中是普通 str；"completed" 是默认值，不是 Schema 强制枚举
```

#### 3.A.3 State（`models.py:86`）
```jsonc
{ "schema_version":"1.0", "tool_use_session_id":"", "attempt_id":0,
  "workflow_plan_run_id":0, "step_index":0, "step_id":"", "tool_id":"", "tool_name":"",
  "status":"not_started",                 // 普通 str；观察值见下，Schema 并未把它限制成枚举
  "started_at":"", "updated_at":"", "finished_at":"", "heartbeat_at":"",
  "retry_count":0, "idempotency_key":"", "input_snapshot_hash":"",
  "output_manifest_path":"Output/OutputManifest.json",
  "error_summary": null }
// 注意：没有 waiting/wait_reason/orphaned 字段，没有 heartbeat_timeout_seconds，没有 result；缺依赖时用 DependencyCheckResult 表达（见 3.A.6）
// 当前通用 runner 观察值：not_started/running/completed/failed/blocked/stale_running；口播另有 orphaned 标志
```

#### 3.A.4 ToolResult（`models.py:164`）与 SessionContextPatch（`models.py:177`）
```jsonc
// 工具返回的 ToolResult：context_patch 只是一个 dict
{ "schema_version":"1.0", "tool_id":"", "tool_name":"", "step_id":"", "status":"",
  "outputs":{}, "warnings":[], "errors":[], "result_paths":[], "metrics":{},
  "context_patch": { /* 纯 patch 字典 */ } }
// runner 再把它包成 SessionContextPatch 去做白名单合并（runner.py:510）：
{ "schema_version":"1.0", "tool_id":"", "step_id":"", "patch": {} }
```

`OutputManifest.status`、`State.status` 与 `ToolResult.status` 在现行 Pydantic 模型里都是普通 `str`。下文列出的值是当前代码路径的**观察值/控制层约束**，不是 JSON Schema 已封闭的枚举；只有 `DependencyCheckResult.status` 明确使用 `Literal["ready","blocked"]`。这保持“代码出现过某值 ≠ 契约已限制为该值”的精度。

#### 3.A.5 InputManifest（`models.py:59`）
```jsonc
{ "schema_version":"1.0", "tool_use_session_id":"", "attempt_id":0,
  "files": [ { "path":"", "source_kind":"workspace_file", "source_ref":"",
               "sha256":"", "size":0, "visibility":"internal", "sensitivity":"normal" } ] }
```

#### 3.A.6 DependencyCheckResult（`models.py:115`）—— 只有两态，没有 waiting
```jsonc
{ "schema_version":"1.0",
  "status":"ready|blocked",               // ← 只有这两个是 Literal 枚举；缺依赖 = blocked
  "missing_dependencies": [ { "kind":"",  // ← kind 在模型里是普通 str，不是 Schema 强制枚举
                              "required_from":"", "required_path":"", "suggested_action":"" } ] }
// kind 的当前观察值：session_context / tool_output / runtime_dependency / python_package / data_asset（非受约束枚举）
```

#### 3.A.7 PromptManifest / ModelCallAudit（`models.py:128-161`）

`[implemented]` `PromptManifest` 按 Tool Step 聚合 `prompts/references/model_calls`；`ModelCallAudit` 记录 provider/model、业务/执行关联、system/user prompt 路径、输入文件、request/usage ID、部署/计费模式、脱敏状态、usage summary 与 idempotency key。正式 Schema：[`PromptManifest.schema.json`](./schema/PromptManifest.schema.json)、[`ModelCallAudit.schema.json`](./schema/ModelCallAudit.schema.json)。

边界：这些字段已经为 AI 调用审计打底，但开放的 `dict` 还不能替代 [10](./10_AI_ModelAndAgent_Profile.md) 的冻结 Profile 与逐 Invocation typed Usage 契约。

#### 3.A.8 Local Usage Ledger（`db/schema.py:158-188`）

`[implemented]` `local_usage_log` 已是一项重要的一等成本基础：记录 request/task/attempt/step/idempotency、provider/model/modality、`units_json`、`est_cost_micros`、provider 实际成本列（`actual_cost_micros/currency/source/raw`）、pricebook version 与 billing reconciliation time；`idempotency_key` 有唯一索引。`LocalUsageRecorder.record_with_result` 在事务内以 `ON CONFLICT DO NOTHING` 去重（`services/local_usage.py:28-126`），`local_metering.py` 能归一 input/output token、字符、音频/视频秒和图像等单位并汇总未计价警告。
> **精度说明**：默认计量路径写**估算成本**（`est_cost_micros`）而将 `actual_cost_micros` 留空；当前 `asset_video_generation_services.py` 的 xAI 视频路径是已核实的例外，会从 provider usage 计算并写入 `actual_cost_micros`、source 与 raw。它仍只是特定响应口径，不代表所有调用路径都有实际费用，更不能直接标成 invoice-final。pricebook 本体是代码配置（`local_metering.py:MODEL_PRICEBOOK/DEFAULT_PRICEBOOK`），表里只存 `pricebook_version` 引用。customer/internal charge 在报表期由 estimate/actual ×倍率计算（`local_metering.py:summarize_usage_row`），**不落表**、无 `charge_status/settlement` 列。

与目标 `AIUsageRecord` 的差距：当前用量 units/status 是开放结构；没有统一 `model_invocation_id/agent_execution_id/run_id/step_attempt_id/step_attempt_no`；不同调用路径覆盖程度不一；尚无通用预算预留/结算状态机。现行金额用整数 micros，目标 Schema 用 decimal string + currency；二者都避免二进制浮点，可在绑定层无损映射。

---

### 3.B 目标扩展 `[proposed]`（当前模型没有，需扩 Schema）

- **Variables 承载业务字段**：现行 `extra=forbid` 且字段固定；示例里的 `applicant_valid/risk_score` 等业务字段**当前无法**进 Variables，规范目标是引入一个可扩展的 `business` 子文档或独立业务上下文。
- **State 的 `status="waiting"` + `wait_reason`**：现行只有 `blocked`（且原因在 `DependencyCheckResult.missing_dependencies` 里，不在 State 上）。把"卡在什么上"提升为一等、可查的 State 字段是目标。
- **OutputManifest 的 `role/media_type` 语义**：现行只有 `kind/schema_name`。
- **Artifact validity/binding/provenance**：目标只治理 canonical Artifact，按 `name+sha256+producer_attempt.input_snapshot_hash` 判断完整性/陈旧；仅在 Artifact contract 声明 `binding_keys` 时校验业务 binding。Run 级 `input_revision_hash` 不复制到每条 Artifact，Working/日志/Prompt/staging/投影不升级成业务 Artifact。
- **`orphaned` 作为通用 State 值**：现行仅口播子系统用作标志（`failed + orphaned:true`），非通用状态机取值。
- **`heartbeat_timeout_seconds` 落在 State**：现行是 runner 侧配置，不在 State 文档里。
- **Mutable snapshot revision**：Variables/State 增加单调 `revision` 与 `writer_id`，CAS 写入；已发布 Manifest/Checkpoint 保持不可变，用 provenance 而非可变 revision。
- **结构化 Error 与 Checkpoint**：目标 Schema 分别见 [`Error.schema.json`](./schema/proposed/Error.schema.json) 与 [`Checkpoint.schema.json`](./schema/proposed/Checkpoint.schema.json)；现行 `ToolResult.errors` 仍是字符串列表且无通用 checkpoint。
- **日志与物理位置索引**：目标 Schema 见 [`DiagnosticLogRecord.schema.json`](./schema/proposed/DiagnosticLogRecord.schema.json) 与 [`StorageIndex.schema.json`](./schema/proposed/StorageIndex.schema.json)。前者规范每行诊断记录的 level/channel/Attempt 身份/可见性，后者显式登记 DB、文件、对象 URI 与投影的 authority/base/path；现行没有通用同形契约。
- **AI Profile + typed Usage/预算**：目标 Schema 见 [`AIExecutionProfile.schema.json`](./schema/proposed/AIExecutionProfile.schema.json)、[`AIUsageRecord.schema.json`](./schema/proposed/AIUsageRecord.schema.json) 与 [`BudgetLedger.schema.json`](./schema/proposed/BudgetLedger.schema.json)；现行已有持久 `local_usage_log`、operation 去重、估算成本、`pricebook_version` 与 reconciliation time。`actual_cost_micros` 默认留空但 xAI 视频路径已有特定写入例外；pricebook 本体和报表期 charge 均不在表内。尚缺统一 Invocation 身份、typed 状态、不可变纠错记录、预算预留与所有调用路径覆盖。

### 3.1 Process Registry（`[proposed]` 目标形态）—— 正式 Schema 见 [`schema/proposed/Process.schema.json`](./schema/proposed/Process.schema.json)
```jsonc
{ "schema_version":"0.4", "contract_level":"executable",
  "process_id":"loan_approval", "version":"1.1.0", "title":"...",
  "tool_registry_ref":"tool_registry.json", "context_schema_ref":"schemas/context.schema.json",
  "branch_closure":"skip_unreachable",
  "failure_propagation":"stop_run|continue_independent",
  "artifact_contracts": { "CreditReport.json": {
      "media_type":"application/json", "schema_ref":"schemas/artifacts.schema.json#/$defs/creditReport",
      "classification":"restricted", "binding_keys":["application_id"] } },
  "completion": { "mode":"exactly_one_outcome", "outcomes":[
      { "id":"approved_and_disbursed", "when":{"variable":"decision","equals":"approved"},
        "terminal_steps":["S8_disburse"], "required_artifacts":["DisbursementReceipt.json"] } ] },
  "run_budget": { "max_cost":"5.00", "currency":"USD", "reservation_policy":"required" },
  "defaults": { "retry","on_error","resources" },
  "stages": ["..."],
  "resource_pools": { "gpu": { "type":"semaphore", "limit":4 },      // [06·D]：按 kind 定型
                      "tts_api": { "type":"rate_limit", "per_minute":8 } },
  "steps": [ /* 每步引用 Tool，并声明本次流程中的依赖、I/O 投影与策略——见 3.2 */ ] }
// 正式 Schema：schema/proposed/Process.schema.json（四个 executable demo 已过 Schema+linter+运行回归）
```

> **边界**：`tool` 引用 Tool Registry 里的可执行能力；`depends_on/when/conditions_*/resources/human_gate/on_error` 只属于 Process Step。为让流程文件能独立做安全校验，目标 Process Schema 还要求/允许写出本次调用的有效 I/O、`side_effect_class` 与成本画像；它们不是第二份可执行体定义，后续编译器应与 Tool Registry 做一致性校验。`name/script/expected_seconds` 等 Tool 固有字段不进入 Process Step。

### 3.2 Tool Registry（`[proposed]` 可执行目标形态）
```jsonc
{ "schema_version":"1.0", "registry_id":"loan_tools", "version":"1.1.0",
  "tools":[{
    "tool_id":"credit_bureau_pull", "version":"1.0.0",
    "entrypoint":"tools:credit_bureau_pull",
    "type":"service", "side_effect_class":"idempotent",
    "reads":["application_id"], "consumes":[],
    "produces":["CreditReport.json"], "writes":["credit_score"],
    "mock":true, "supports_reconciliation":false, "supports_checkpoint":false
  }] }
```
> 正式目标见 [`ToolRegistry.schema.json`](./schema/proposed/ToolRegistry.schema.json)。Process Step 的 I/O 与 side-effect 是本次调用的静态投影，编译时必须与 Registry 完全一致；Registry 才保存版本化 entrypoint。现行 OpenCrew 注册表仍以 `registry_normalizer.py` 归一化结构为准——产物字段名是 **`produces_outputs`**（由 `main_outputs` 转成），依赖为 `reads_session_context/consumes_outputs` + 依赖桶；它尚未消费上面的目标 Registry。流程拓扑、分支、资源、人工卡点和失败动作只属于 Process Step。

### 3.3 Run 修订链（`[proposed]`，业务返工而非图内循环）

```jsonc
{ "schema_version":"1.0", "run_id":"run_...",
  "session_id":"loan_00123", "task_id":"loan_00123_t",
  "run_sequence":2,
  "supersedes_run_id":null,                 // 补件/返工或显式整 Run 重跑时指向前次 Run
  "process_snapshot": {
    "process_id":"loan_approval", "version":"1.1.0", "digest":"sha256:...",
    "tool_registry_id":"loan_tools", "tool_registry_version":"1.1.0",
    "tool_registry_digest":"sha256:...", "profiles":[{"profile_id":"...","version":"...","digest":"sha256:..."}] },
  "input_revision_hash":"sha256:...",      // 本次业务输入版本
  "status":"completed", "outcome":"approved_and_disbursed",
  "context":{}, "steps":[], "artifacts":[], "usage_records":[],
  "human_tasks":[], "events":[], "budget_summary":{} }
```

一个 Run 内拓扑必须无环；自动技术重试留在本 Run/Step 的 Step Attempt。业务输入变化创建新 Run；Run 已终止后的显式整 Run 重跑也可创建新 Run，是否为业务修订由 `input_revision_hash` 是否变化区分。正式 demo 绑定见 [`RunRecord.schema.json`](./schema/proposed/RunRecord.schema.json)；它把 Process/Registry/Profile snapshot、步骤尝试、Artifact、Human Task、事件和预算聚合到一份可校验记录。当前 OpenCrew 尚无这一通用 Run 信封。

Run 是成本/用量的聚合范围，不是统一计费边界。收费与去重落在带稳定 `operation_idempotency_key` 的 billable operation；一次 Step Attempt 可能没有收费、收费一次，或因 fanout 含多个计费子项。

### 3.3.1 AI Execution / Usage（`[proposed]`）

- Step/Tool 用 `ai_profile_ref` 引用冻结的模型选择、预算、数据、输出、流式与 Agent 权限策略；Profile 不存凭证或 prompt 正文。
- 每个 Model Invocation 至少写一条 [`AIUsageRecord`](./schema/proposed/AIUsageRecord.schema.json) 观察记录，并直接关联稳定 `step_attempt_id`（序号只用于展示）。`operation_idempotency_key` 标识同一计费操作并跨 transport retry 稳定；`observation_idempotency_key` 标识本次不可变观察/纠错，使 superseding record 能与原记录并存。
- `invocation_status=completed|failed|ambiguous|cancelled` 与 measurement/cost 状态正交。`usage.measurement_status=unavailable` 时禁止伪填 0；`cost.status=estimated|provisional` 必须带来源，按 price/tariff 计算时还必须绑定 `price_snapshot_ref`。只有 provider reported 或 invoice reconciled 的金额可标 `final`。
- [`BudgetLedger`](./schema/proposed/BudgetLedger.schema.json) 用 `reserve → settle + release`（必要时 `adjust`）记录逐 operation 预算变化；`adjust` 必须声明 `debit`（追加占用）或 `credit`（退回额度），不能靠正负金额猜方向。它是目标 Run 预算账本，不等于 OpenCrew 现行报表期 customer/internal charge 计算。
- `ModelCallAudit` 是 `[implemented]` 的调用审计；AIUsageRecord 是更严格的目标计量/成本事实。实现可关联二者，但不能靠日志文本反推账单。

### 3.4–3.7 变量/状态/结果/清单

**以 3.A 为唯一现行契约**（Variables/OutputManifest/State/ToolResult/SessionContextPatch/DependencyCheckResult）。v0.1 曾在此处给出 `run_id/artifacts[]/wait_reason/result` 等字段——**均与真实模型不符，已删除**，其目标形态见 3.B。

### 3.8 Event Envelope（[proposed]）
```jsonc
// 目标标准信封；当前 session_events 表/Variables 仍沿用 opencrew_session_id、tool_use_session_id、attempt_id 等历史字段
{ "schema_version":"1.1", "event_id":"evt_...", "cursor":42, "kind":"step.completed",
  "session_id":"...", "task_id":"...", "run_id":"...",
  "step_id":"...", "tool_id":"...", "step_attempt_id":"sa_...", "step_attempt_no":1,
  "correlation_id":"run_...", "actor":{"type":"system","id":"runner"},
  "payload": {}, "at":"2026-07-24T09:00:00Z" }
```

Run 级事件的 `step_id/tool_id/step_attempt_id/step_attempt_no` 可为 `null`；`step.skipped` 因未创建 Attempt 也使用 `null/null`。其余 Step、Artifact、Human Task、callback、模型与预算事件必须直接关联实际 Attempt，且稳定 ID 与展示序号同时为空或同时有值。身份、cursor、correlation 与 actor 仍不可省。Human Task 决策事件的 actor 还应记录授权角色，payload 记录 `decision_id/decision/reason/expected_revision/revision`，不能只发一条“approved”文本。

### 3.9 Snapshot / Command Envelope（`[proposed]`）

```jsonc
// 服务端状态快照：revision 管聚合状态版本，event_cursor 接续增量事件
{ "session_id":"...", "task_id":"...", "run_id":"...",
  "revision":18, "event_cursor":10432, "generated_at":"...", "state":{} }

// 所有会改变运行/变量的命令：幂等 + CAS + 审计 actor
{ "command_id":"cmd_...", "idempotency_key":"...", "expected_revision":18,
  "actor_id":"user_42",
  "kind":"start_run|rerun|diagnostic_step|pause_before_step|pause_after_step|resume|stop_after_current|terminate|context_override",
  "payload":{
    "source_run_id":"run_...",              // rerun 必填；初次 start_run 省略
    "scope":{
      "mode":"full|through_step|from_step|only_step",
      "target_step_id":"S6",                // through_step / only_step
      "start_step_id":"S4",                 // from_step
      "publish_mode":"canonical|diagnostic",
      "boundary_behavior":"continue_to_outcome|pause_after_target|finish_diagnostic",
      "reuse_policy":"validate_input_hash_schema_binding",
      "downstream_policy":"recompute_affected|not_applicable"
    },
    "reason":"user_requested"
  } }
```

冲突返回 409 + 最新 revision/snapshot；SSE 与轮询均从 `event_cursor` 续接。可人工编辑的业务文档另用内容 sha256 做乐观并发，不能拿 `updated_at` 替代。

`scope` 不是 Step ID 切片器，而是交给 Process compiler 的图查询：

| 意图 | 必需字段 | 编译结果 |
|---|---|---|
| 全链运行 | `kind=start_run, mode=full, publish_mode=canonical` | 所有激活根节点到 exactly-one outcome |
| 运行到某步 | `kind=start_run, mode=through_step, target_step_id, boundary_behavior=pause_after_target` | 目标的传递前置闭包；目标完成后 Run 为 `waiting(user)`，不生成业务 outcome |
| 从某步重跑 | `kind=rerun, source_run_id, mode=from_step, start_step_id, downstream_policy=recompute_affected` | 所选节点 + 受影响后继闭包；独立上游仅在 hash/schema/binding/definition 验证通过后复用 |
| 单步重跑 | `kind=diagnostic_step, mode=only_step, target_step_id, publish_mode=diagnostic` | 隔离 Step Attempt；不更新 canonical Context/Manifest。要正式采用结果必须改用 `from_step` |

所有范围都要写 `command.accepted|rejected`、`run.scope_compiled` 及执行事件；编译结果至少回传 `execute_step_ids/reuse_step_ids/invalidated_step_ids/required_external_inputs` 和选择理由，供 UI 展示“为什么这些步骤会跑”。`through_step` 的 canonical Run 只能等待后续 resume 或显式取消，不能因边界已到就写 `completed`。独立 production `RunCommand/Snapshot` Schema 仍待从 demo 聚合契约中拆出，因此本节整体保持 `[proposed]`。

> **OpenCrew 当前映射 `[implemented/domain-specific]`**：`OpenClipAnalysisV1RunPayload` 已有 `mode/start_step_id/end_step_id/run_only_step_id/selected_step_ids/previous_attempt_id/pause_before_step_id`；`ANALYSIS_V1_RUN_MODES` 支持 `run_all/run_range/run_from_step/run_only_step/run_selected_steps/rerun_all/rerun_failed/rerun_from_step`，前端也投影了“运行至此步 / 从此步开始运行 / 单独运行此步 / 重跑此步及后续”。它尚未使用上述统一命令信封：没有 command id、expected revision/CAS 或通用 DAG scope compiler；`run_range` 终态为领域 attempt completed，`run_only_step` 虽标 `billing_scope=diagnostic` 仍会生成 result manifest/同步文件，边界前复用也尚未以 hash 门控。不能把这些领域入口标成通用 FlowSpec Runtime 已完成。

## 4. 枚举常量

```
# —— [implemented] 现行代码里实际观察到的取值；除 DepStatus 外并非 Schema 枚举 ——
StateStatusObserved = not_started | running | completed | failed | blocked | stale_running # State.status 是 str；口播另有 orphaned 标志位
DepStatus   = ready | blocked                                                         # DependencyCheckResult（无 waiting）
RunStatusObserved = running | completed | failed | blocked | cancelled | {completed|failed|blocked|cancelled}_with_sync_error # SessionRunSummary.status 也是 str，runner 控制层校验终态
CostLevel   = very_low | low | medium | high | very_high | external_skill             # ToolLibrary/*/tool_registry.json
UITone      = done | pending | disabled | running | failed                            # 口播 slot_state_services.py

# —— [proposed] 规范目标，当前未实现 ——
StepStatus(+) 增补 waiting、skipped（when=false）、orphaned（作为通用状态而非口播标志）
WaitKind    = step | variable | artifact | resource | condition | user | host | external_callback # step=depends_on 未满足；external_callback=Suspend/回调挂起；现行原因在 DependencyCheckResult.missing_dependencies.kind
RetryPolicy = fail_fast | on_transient                                               # on_error[] 是动作表，不是 retry policy
FailurePropagation = stop_run | continue_independent                                 # Run 级；与 RetryPolicy 正交
SideEffectClass = pure | idempotent | reconcilable | non_idempotent                   # 取代布尔 idempotent，重试据此校验
ToolType    = script | command | service | model | agent | confirm | suspend | subprocess | noop # 现仅 subprocess/noop 有通用 adapter
EventKind   = step.* | run.* | plan.*
ArtifactValidity = missing | valid | stale | unbound | corrupt
UsageMeasurement = provider_reported | locally_measured | estimated | unavailable
CostStatus  = unknown | estimated | provisional | final
DiagnosticLogLevel = debug | info | warning | error | critical
DiagnosticLogChannel = runtime | adapter | tool | stdout | stderr | model_adapter
ArtifactPathBase = workspace | run | step | step_attempt | external                     # RunRecord Artifact.path_base
StorageIndexBase = ArtifactPathBase | run_bundle | database | platform                 # StorageIndex 索引超集；platform=Workspace 外服务/进程日志 sink
StorageAuthority = database | file | object_store | log_service | projection

# —— [roadmap 🆕] ——
ResourceKind = mutex | semaphore | rate_limit                                         # 三原语，见 [06·D]
```

## 5. API 端点约定（供 UI 消费）

| 状态 | 用途 | 方法 + 路径 | 返回 |
|---|---|---|---|
| `[implemented]` | 业务详情（按 task） | `GET /api/{domain}/tasks/{task_id}` | 任务详情聚合 |
| `[implemented]` | **执行进度快照（弹窗实际用）** | 专用端点 `.../video-plan/execution`、`.../image-plan/execution`、`.../video-only-plan/execution` | `video_plan_execution_payload`（plan_hash/artifact/execution/binding 状态） |
| `[implemented]` | 事件增量轮询（按 session） | `GET /api/sessions/{session_id}/events?since=<cursor>` | Event[] |
| `[implemented]` | 事件流（SSE） | `GET /api/sessions/{session_id}/events/stream` | text/event-stream |
| `[implemented/domain-specific]` | Analysis_V1 范围执行 | `POST .../analysis-v1/run-to-storyboard` | 支持 full/range/from/only/selected/rerun；领域 attempt 语义，不是通用 Process Run |
| `[proposed]` | 通用人工确认 | `POST .../steps/{step_id}/confirm` | 当前**不存在**通用确认端点；口播只有 `workflow_plans.confirmed_at` 表字段，无通用读写闭环 |
| `[proposed]` | 通用重跑 | `POST .../runs` | 当前**不存在**通用重跑端点；重跑逻辑在口播 router 内 |
| `[proposed]` | 通用运行控制/override | `POST .../runs/{run_id}/commands` | 请求必须带 command/idempotency/expected_revision/actor；当前无统一命令信封 |

> OpenCrew 参考：`routes/sessions.py`（events + `/events/stream`）、`koubo_storyboard/task_routes.py`（`GET .../tasks/{task_id}`）。
> ⚠️ **前端事实（[implemented]）**：口播三个执行弹窗**轮询各自的专用执行快照端点**（video-plan / image-plan / video-only-plan execution），**不是**简单的 task detail GET，也**不是** SSE——见 `kouboStoryboardApi.js:184` 与 `KouboVideoPlanModal.jsx:396`（每秒）等。平台 SSE 端点虽在，这些弹窗未用。

## 6. 参考实现映射表（规范 → OpenCrew 代码）

| 规范要素 | OpenCrew 文件:符号 |
|---|---|
| Process Registry（**partial**） | `tool_registry.json` + `registry_normalizer.py` 只归一化**工具库**（`produces_outputs`+依赖桶）；**流程顺序仍领域硬编码**（`koubo_storyboard/constants.py`），完整 Process Registry 为 `[proposed]` |
| 目标 Process / Tool Registry | `docs/WorkflowSpec/schema/proposed/{Process,ToolRegistry}.schema.json` + `lint_process.py` 已用于四套 demo 的静态编译检查；尚未接入 OpenCrew normalizer/执行入口 |
| 目标 Run / Budget / Artifact 聚合 | `schema/proposed/{RunRecord,BudgetLedger}.schema.json` 已校验 definition snapshot、Step Attempt、Human Task、Event、Artifact、Usage 与预算账本；OpenCrew 尚无同形通用 Run 信封 |
| 目标日志 / 物理位置索引 | `schema/proposed/{DiagnosticLogRecord,StorageIndex}.schema.json` + 四套 demo 的 `logs/`/`storage-index.json`；仅为规范证据，未改变 OpenCrew 目录 |
| 文档内 Mock Runtime | `docs/WorkflowSpec/demos/demo_runtime.py` 只用于确定性物化目标契约和 UI 验证，**不是** OpenCrew runner 的现行能力，也不实现分布式 claim/lease |
| 用户选择的范围执行（领域实现） | `koubo/router.py:ANALYSIS_V1_RUN_MODES + analysis_v1_compile_plan` 与 `AnalysisV1Module.jsx:runStepAction` 已支持 full/range/from/only/selected/rerun；按线性领域步骤表编译，边界前 reuse 尚未 hash-gated；通用 DAG scope compiler / Command CAS 为 `[proposed]` |
| 结构化 Error / Checkpoint | `docs/WorkflowSpec/schema/proposed/{Error,Checkpoint}.schema.json` 为目标契约；现行 Error 为 `string[]`、无通用 checkpoint |
| AI Profile / Usage & Cost | `schema/proposed/{AIExecutionProfile,AIUsageRecord}.schema.json` 为目标；现行 `local_usage_log` + `LocalUsageRecorder/local_metering` 已持久计量和成本、唯一键去重，`PromptManifest/ModelCallAudit/ModelBroker` 提供调用审计；统一 Invocation/typed status/预算仍为 partial |
| 依赖归一化 | `registry_normalizer.py:DependencyBuckets` |
| Adapter 执行 | `runner.py:ToolAdapter / SubprocessToolAdapter / NoopTool`（现行只有单一 `run`；`prepare/run/finalize` 与 confirm/service/suspend adapter 为 [proposed]） |
| ~~调度循环~~ **单步执行器** | `runner.py:run_registry_step + check_dependencies`（当前是**单步**执行器，非完整声明式调度循环；"自动从首个未完成步骤恢复"为 [proposed]） |
| Variables | `schemas/models.py:Variables`（`extra=forbid`）+ `prepare.py:prepare_session_variables` |
| 白名单合并 | `runner.py:merge_context_patch` + `schemas/models.py:SessionContextPatch` |
| State / Manifest | `schemas/models.py:State / OutputManifest` |
| 目录布局 | `paths.py:ensure_tool_session_layout` + `koubo_storyboard/constants.py` |
| Workspace 解析 | `koubo_storyboard/runtime.py:workspace_for` |
| 诊断日志（现状分裂） | 通用 runner 只 `capture_output`，尚未落 Step `Report/`；Analysis_V1 领域路径为 `SessionReport/tool_runs/attempt_<id>/logs/<step>.{stdout|stderr}.log`；服务进程 `/tmp/opencrew-*.log` 只是本地部署绑定 |
| 心跳/恢复 | `runner.py:recover_stale_running_steps`；`video_plan_execution_state_services.py`（orphan） |
| 事件/SSE | `session_events` 表；`runner.py:RunnerEventSink`；`routes/sessions.py` |
| OpenCode Agent | `adapters/opencode.py:OpenCodeSessionClient` + `koubo_storyboard/agent_chat_*`；当前是领域能力，OpenCode Session 不映射为业务 session/Run |
| 多客户端 snapshot/command CAS | session events/SSE 与领域轮询快照为 partial；统一 revision、command envelope、409 回传尚为 `[proposed]` |
| UI slot 状态 | `koubo_storyboard/slot_state_services.py` |
| 计划确认（partial/proposed） | `workflow_plans.confirmed_at` 有表字段但无通用读写闭环；`binding_status` 是**输入哈希绑定状态**，非人工确认状态 |
| 资源池 / SLA / On-Do / 命名条件池 | `[roadmap 🆕]` 尚未实现，见 [06](./06_Runtime_Observability.md) |
| Folder 继承 / Named-Pool·Global 变量 / 通用 Plan Confirm | `[proposed]` 见 [02](./02_ProcessDefinition.md) / [04](./04_VariablesAndState.md) |

下一步：[08 · 成熟体系对照](./08_PriorArt_CrossReference.md)。
