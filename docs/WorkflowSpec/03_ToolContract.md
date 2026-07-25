# 03 · 工具契约（Tool Contract）

> 如何定义一个"工具脚本"，使编排器能安全地调度它、传参给它、收它的产物、并在它失败时恢复。核心立场：**工具是声明式契约 + 子进程适配器，工具不感知编排**（[00·P3](./00_Overview.md)）。

## A. 概念层

### A1. 工具的三重身份

一个 Tool 有三个面：

1. **契约（Contract）**——声明式元数据：读什么、写什么、产什么、花多少钱、用不用 LLM、怎么重试，以及是否支持 checkpoint/结构化进度/安全终止。**编排器只依赖契约**来调度，不需要读工具源码。
2. **可执行体（Executable）**——真正干活的脚本/服务/人工任务本体。
3. **适配器（Adapter）**——把编排器的调用翻译成对可执行体的一次实际执行（起子进程、调 HTTP、派人工任务），并把结果翻译回结构化 `ToolResult`。

### A2. 工具不感知编排（关键纪律）

工具**不知道**：自己是第几步、上一步是谁、下一步是谁、整条流程长什么样。工具**只知道**：

- 自己拿到的**输入**（已声明要读的变量 + 已声明要消费的产物路径）。
- 自己的**私有工作目录**（`paths` 给出 workspace 根与本步目录）。
- 自己该**产什么**、该往 Context **写哪些字段**。

这条纪律换来的红利：工具可**复用**（换个流程照样用）、可**独立测试**（喂固定输入即可）、可**并行开发**（团队各写各的工具，只对齐契约）。

### A3. 工具怎么接触共享状态：只进不出的门

工具**不直接读写业务数据库、不直接改别人的变量**。它与外界的接口只有：

- **入**：读 `Context/Variables`（限自己 `reads` 声明的字段）+ 读上游 `OutputManifest` 指向的文件（限 `consumes`）。
- **出**：写自己私有目录的文件 → 登记进 `OutputManifest`；返回一个 `context_patch`（限 `writes` 声明拥有的字段）。

这就是 [04](./04_VariablesAndState.md) 的白名单模型在工具侧的体现。

### A4. 幂等而非确定性（[00·P3]，团队审核已澄清）

工具**允许**含随机、时间、网络、LLM 调用——这与 Temporal 的确定性约束相反。规范换取可重跑的方式是**幂等**。

> **澄清（避免误读）**：幂等**不是**"随机/LLM 步骤重算会得到等价输出"（那不可能）。幂等指**同一逻辑请求被去重 / 重放 / 对账**——不重复扣款、不重复下单、不重复发消息、不重复烧一次 GPU/LLM。

**每步须声明副作用类别**（`[proposed]`，替代含糊的 `idempotent:true`）：

| 类别 | 含义 | 重跑策略 |
|---|---|---|
| `pure` | 无副作用、输出仅由输入决定 | 可对瞬态错误做有界重试 |
| `idempotent` | 有副作用但以稳定 operation key 去重，重跑安全 | 可对瞬态错误做有界重试 |
| `reconcilable` | 副作用非幂等，但可从持久状态对账结果 | 不盲重试，丢响应走对账（见 [09·L4.2](./09_ProductionLessons.md)） |
| `non_idempotent` | 副作用不可逆且不可对账 | 默认 fail-fast，人工介入 |

**统一字段：`side_effect_class`**（`pure|idempotent|reconcilable|non_idempotent`），取代含糊的布尔 `idempotent:true`。**重试策略据它校验**：只有 `pure/idempotent` 可配 `on_transient`，且必须声明 `max_attempts`；`reconcilable` 只能走对账，`non_idempotent` 必须 fail-fast。v0.4 不提供宽泛的 retry-policy `on_error`（字段 `on_error[]` 是失败后的动作表，不是 retry policy）。

实现手段：以稳定的 `operation_idempotency_key` 为业务操作幂等键写外部系统；`attempt_no` 只标识第几次技术尝试。自动重试不得更换 operation key；只有输入修订或用户明确要求重新计算才产生新 key。产物采用内容寻址；副作用前先查是否已做过。

> **与 OpenCrew 现状的边界**：当前 runner 的 `idempotency_key={tool_use_session}:{step}:{retry_count}` 是 attempt 级键（[09·L4.5](./09_ProductionLessons.md)）。保留它用于执行记录可以，但接入付费/外部副作用时还需一个跨技术重试稳定的 operation key；不要直接把 attempt 级键当外部业务幂等键。

### A5. 成本与能力标注

工具须自报**成本画像**，供编排器做限流、预算、重试决策：`cost_level`、`uses_llm`、`uses_gpu`、预计耗时、是否有外部配额。目标域里 GPU/LLM 步骤又贵又限并发——没有这些标注就没法治理。但 `cost_level` 只是调度提示，**不能代替实际用量账本**。
> `cost_level` 实测取值（`[implemented]`）：`very_low | low | medium | high | very_high | external_skill`（不止 low/medium/high）。
> `uses_llm` 是现行注册表的粗粒度兼容标志，不能代表 TTS/图像/视频等全部模型调用；目标绑定以 `type=model|agent` + `ai_profile_ref` 为准。

`[proposed]` 每个模型 billable operation 必须产生独立 `AIUsageRecord`：记录 token/媒体单位、计量来源、费用状态/金额/币种、provider request ID，并关联 Run/Step/Step Attempt 与稳定 operation key。Agent Tool 内的每次 Model Invocation 分别记账；结构化输出修复、评议和 fallback 都是新的调用，不得折叠成 Step 一个总数。正式契约见 [`AIUsageRecord.schema.json`](./schema/proposed/AIUsageRecord.schema.json)，策略与预算见 [10](./10_AI_ModelAndAgent_Profile.md)。

### A6. 工具类型（采众家之长）

| 类型 | 干什么 | 对应 | 状态 |
|---|---|---|---|
| `Script` / `Command` | 跑一段脚本/命令 | Control-M Job:Script | `[implemented]`（`SubprocessToolAdapter`） |
| `Noop` | 占位/汇聚节点 | — | `[implemented]`（`NoopTool`） |
| `Service` / `Activity` | 调普通外部服务/API | Temporal Activity | `[proposed]` 无专用 adapter |
| `Model` | 一次或有界多次模型调用；逐 Invocation 记账 | AI Profile | `[implemented/partial]` 有领域调用/ModelBroker，无通用 adapter |
| `Agent` | Step 内有界多轮模型+获准 Tool Call | OpenCode 等 Agent runtime | `[implemented/partial]` 有领域路由，无通用 adapter |
| `Confirm` / `Gate` | 人工确认卡点 | BPMN User Task；Control-M Confirm | `[proposed]` |
| `Suspend` / `Wait` | 挂起等外部回调/信号 | SFN `.waitForTaskToken`；Argo Suspend | `[proposed]` |
| `SubProcess` | 调另一条子流程 | Control-M 子文件夹 | `[proposed]` |

> **现状**：真正实现的 adapter 只有 `SubprocessToolAdapter` 与 `NoopTool`（`runner.py:ToolAdapter`）。其余类型是目标形态。

### A7. `prepare → run → finalize` 生命周期 `[proposed]`

一次 Step Attempt 由三个**语义阶段**组成；实现可以是三个 hook，也可以由一个 adapter 内部完成，但阶段边界必须可观测，结构化错误须标明发生在哪一阶段：

1. **prepare**：解析并校验声明的变量与 Artifact，固定 `input_snapshot_hash`，准备 Step 私有 Working。大文件不强制复制，可使用只读内容寻址引用、受控挂载或物化；关键是输入在本次尝试内不可悄然漂移。
2. **run**：执行 Tool 逻辑，只访问获准输入、私有 Working/Prompt 与受控外部能力；transport retry 发生在本阶段且沿用稳定 operation key。
3. **finalize**：校验结果与 Artifact 完整性，发布不可变 OutputManifest/Checkpoint，提交 Context Patch，再由受信任 Runner 更新状态与事件。未完成 finalize 的临时文件不是正式产物。

生命周期不要求 Tool 感知流程拓扑；它只让输入冻结、执行和发布三个责任不再混在一个不透明 `run()` 中。Checkpoint 只能在 Tool 声明的安全完成边界发布，正式结构见 [`schema/proposed/Checkpoint.schema.json`](./schema/proposed/Checkpoint.schema.json)。

---

## B. 可实现绑定

### B1. 工具契约 Schema（Tool Registry Entry）

这里必须区分现行 OpenCrew registry 与目标 FlowSpec Registry，不能把两者字段拼成一个“仿佛已实现”的对象。

**OpenCrew 现行原始条目 `[implemented/partial]`**（字段随 ToolLibrary 有差异，归一化以 `registry_normalizer.py` 为准）：

```jsonc
{
  "id":"05_02", "name":"05_02_TalkingHeadVideoPlanExecutor",
  "script":"ToolLibrary/TalkingHead_V1/05_02_VideoPlanExecutor.py",
  "stage":"video_plan_execution", "required_by_default":false,
  "cost_level":"very_high", "uses_llm":false, "uses_video":true,
  "supports_resume":true,
  "hard_dependencies":["SessionOutput/storyboard/video_generation_plan.json"],
  "main_outputs":["SessionOutput/storyboard/video_plan_execution_result.json"]
}
```

Normalizer 把 `main_outputs` 变成 `produces_outputs`，并把 hard/soft/runtime 等依赖归入依赖桶；部分注册表还可提供 `reads_session_context/consumes_outputs/writes_session_context`。这些字段不等于目标 Registry 已支持 side-effect、AI Profile、Human Gate 或 RetryPolicy。

**FlowSpec v0.4 目标 Registry `[proposed]`**：

```jsonc
{
  "schema_version":"1.0", "registry_id":"video_tools", "version":"1.1.0",
  "tools":[{
    "tool_id":"generate_dialogue_video", "version":"1.0.0",
    "entrypoint":"tools:generate_dialogue_video", "type":"model",
    "side_effect_class":"reconcilable",
    "reads":["project_id","creative_revision"],
    "consumes":["StoryboardPlan.json","Image_*.png","VisualPrompt_*.json"],
    "produces":["RawVideo_{asset_key}.mp4"], "writes":[],
    "mock":true, "supports_reconciliation":true, "supports_checkpoint":false
  }]
}
```

正式目标见 [`ToolRegistry.schema.json`](./schema/proposed/ToolRegistry.schema.json)。**分层纪律**：Registry 保存 Tool 的版本化 entrypoint、固有 I/O、副作用类别与能力；`depends_on/when/retry/resources/human_gate/fanout/on_error/ai_profile_ref` 属于“这个流程如何使用工具”，只写在 Process Step。Process 为独立静态校验而物化本次调用的有效 I/O 与 `side_effect_class`，编译器必须与 Registry 做完全一致性检查；不得复制 entrypoint 等执行体定义。

### B2. 适配器接口（Adapter Protocol）

编排器通过统一接口执行任意工具。下面是目标三阶段协议；OpenCrew 当前 `runner.py:ToolAdapter` 只有单一 `run(...)`，所以 `prepare/finalize` hook 仍是 `[proposed]`：

```python
class ToolAdapter(Protocol):
    def prepare(self, *, tool, step, paths, tool_dir) -> PreparedInput: ...
    def run(self, *, tool, step, prepared, checkpoint=None) -> ToolExecution: ...
    def finalize(self, *, tool, step, execution, paths, tool_dir) -> ToolResult: ...
# 具体实现：
#   SubprocessToolAdapter —— 起子进程跑 tool.script，cwd=paths.workspace_dir，env 被 scrub
#   ServiceToolAdapter    —— 调外部服务/LLM（规范建议）
#   ConfirmToolAdapter    —— 派人工任务，挂起到 confirmed（规范建议）
#   NoopTool              —— 占位
```

**适配器职责边界**：prepare 固定并校验输入；run 注入声明输入、限定工作目录、scrub 环境变量、捕获 stdout/stderr/结构化进度；finalize 校验并发布正式产物、Patch 与 Manifest。任何阶段失败都返回结构化 Error；adapter 不自行决定是否重试，Runner 综合 Error 分类、`side_effect_class`、RetryPolicy 与成本后决策。

### B3. 工具返回契约（ToolResult）`[implemented]`

（真实模型：`schemas/models.py:164`。**注意 `context_patch` 只是一个纯 patch 字典**，不含 `tool_id/step_id`——runner 才把它包成 `SessionContextPatch(tool_id,step_id,patch=...)` 去做白名单合并，见 `runner.py:510`。）

```jsonc
{
  "schema_version": "1.0",
  "tool_id": "tts_builder_g", "tool_name": "", "step_id": "S3",
  "status": "completed",                 // 普通 str；以下是常见观察值，不是 Schema 枚举
  "outputs": { "tts_audio_path": "SessionOutput/tts/voice.wav" },
  "warnings": [], "errors": [],
  "result_paths": ["SessionOutput/tts/voice.wav"],
  "metrics": { "duration_ms": 41230 },
  "context_patch": { "tts_audio_path": "...", "tts_duration_ms": 41230 }  // ← 纯 dict，键须 ∈ writes_session_context
}
```
> 越权字段（不在 `writes_session_context`）会被 `merge_context_patch` 拒绝，且**整个步骤判 failed**（`runner.py:507-523`）。

编排器收到后：把 `result_paths` 写入本步 `OutputManifest.json`；把 `context_patch` 经**白名单校验**合并进 `Variables.json`（越权字段直接拒绝，见 04）。

#### B3.1 结构化 Error `[proposed]`

现行 `ToolResult.errors` 是 `string[]`，保留为 `[implemented]` 事实；目标契约改为结构化错误对象，正式 Schema 见 [`schema/proposed/Error.schema.json`](./schema/proposed/Error.schema.json)：

```jsonc
{
  "schema_version":"1.0", "error_code":"provider_rate_limited",
  "category":"rate_limit", "phase":"run",
  "retryable":true, "resume_supported":true, "user_action_required":false,
  "safe_message":"外部服务暂时限流，请稍后重试",
  "suggested_action":"按 Retry-After 等待后重试",
  "debug_ref":"report://S3/attempt-2/error-01"
}
```

- `retryable` 是产生错误的一方给出的**分类事实/提示**，不是自动重试命令；只有 `pure/idempotent + on_transient + 未超预算` 才可能重试。
- `resume_supported` 表示存在可校验 Checkpoint，不能仅凭布尔值恢复；还须核对 Tool 版本、输入哈希、完成边界与 checkpoint 文件校验和。
- `safe_message/suggested_action` 可给最终用户，不含绝对路径、secret、命令行或内部堆栈；诊断细节通过受权限控制的 `debug_ref` 间接访问。
- `phase=prepare|run|finalize|control` 用于区分依赖/输入失败、工具执行失败、发布同步失败和控制命令失败。

### B4. 工具目录布局（每步私有）

```
<workspace>/<run>/S{index}_{tool}/
  ├── Working/        # 工具的临时中间物（不对外）
  ├── Output/         # 对外产物 + OutputManifest.json
  ├── Report/         # stdout/stderr/日志（可观测）
  └── Prompt/         # 若用 LLM：本步实际 prompt（可审计）
```

参考实现：`paths.py` / `runner.py`（`S{step_index}_{tool_name}/{Working,Output,Report,Prompt}`）。

### B5. 工具开发者检查清单

- [ ] 只读了 `reads_session_context` 里声明的变量？只消费了 `consumes_outputs` 的产物？
- [ ] 只往 `writes_session_context` 白名单里的字段写 `context_patch`？
- [ ] 所有对外产物都登记进 `OutputManifest`（用声明的产物名）？
- [ ] 幂等：同一逻辑操作的技术重试是否沿用稳定 `operation_idempotency_key`，并用独立 `attempt_no` 记录尝试？
- [ ] 昂贵/外部调用步骤标了 `cost_level/uses_gpu/uses_llm/external_quota`？Model/Agent 是否有 AI Profile，且每次 Model Invocation 都写可去重 Usage/Cost？
- [ ] 长任务定期打心跳（否则会被判 `stale_running`）？
- [ ] `supports_resume` 是否绑定 checkpoint contract version，并在恢复前校验输入/Tool 版本/sha256？
- [ ] 错误是否使用结构化分类，且 `safe_message` 不泄露内部路径或 secret？
- [ ] prepare/run/finalize 的失败阶段是否可观察，未 finalize 的文件是否不会被发布为正式产物？
- [ ] 不感知编排：没有硬编码"我是第几步/下一步是谁"？
- [ ] 能被喂固定输入独立测试（有 fixtures）？

下一步：[04 · 变量与状态](./04_VariablesAndState.md)。
