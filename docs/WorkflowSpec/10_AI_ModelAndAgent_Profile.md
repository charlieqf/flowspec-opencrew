# 10 · AI / Model / Agent 执行 Profile

> **定位**：FlowSpec 的主要落地域通常与 AI 模型紧密结合。本章是建立在核心概念之上的**可选 Profile**：它约束 LLM/多模态模型调用和 Agent 内循环，但不把任何供应商、SDK 或 OpenCode Session 升格为业务流程核心概念。
>
> **状态**：本章规则为 `[proposed]`；OpenCrew 已实现其中一部分模型选型、调用审计、敏感度标记、幂等键及 OpenCode 工具禁用，逐条在 §G 标出。

## A. 两种 AI 执行形态

| 形态 | FlowSpec 边界 | 典型用途 | 内部活动 |
|---|---|---|---|
| **Model Tool** | 一个 Step 内的一次或有界多次 Model Invocation | 分类、抽取、摘要、结构化生成、图像/音频/视频生成 | prompt 渲染 → 模型请求 → 输出校验；格式修复是新的 Invocation |
| **Agent Tool** | 一个 Step 内的一次有界 Agent Execution | 研究、规划、多轮评议、基于获准工具的材料处理 | 多个 Model Invocation + Tool Call，可有内部循环 |

```text
session + task → Run → Step → Step Attempt
                              ├── Model Invocation (0..N，实际计费操作)
                              │     └── UsageRecord (token/媒体单位/费用)
                              └── Agent Execution (0..1；fanout 时每个 item Attempt 各自 0..1)
                                    ├── Model Invocation (1..N)
                                    └── Tool Call (0..N，受能力策略约束)
```

**关键边界**：Agent 的 `think → call tool → observe → think` 是 Tool 内部的有界执行循环，不是 Process 回边。一次 Agent Tool 的 Step Attempt 至多建立一个 `agent_execution_id`；fanout 时这个约束作用于每个 item Attempt。多个需要独立授权、失败处理或恢复的 Agent 应拆成不同 Step/item Attempt。编排器只接收该 Step 的最终、经校验结果；如果业务输入、人工决定或目标发生变化，创建新 Run。普通 Model/Tool 失败产生新 Step Attempt 或走 [06·E](./06_Runtime_Observability.md) 的恢复模型。

OpenCode Session 是一种**可选 Agent 对话上下文**。它可以绑定 `step_attempt`、`run` 或 `business_session`，但不等于 FlowSpec `session`、`task` 或 `Run`，也不能成为业务状态的唯一权威。

### A.1 Profile v1.1 的政策边界

[`AIExecutionProfile.schema.json`](./schema/proposed/AIExecutionProfile.schema.json) 的 `schema_version=1.1` 把调用前政策收敛为六组；`profile_id + version + digest` 随 Run 冻结，Profile 版本与模型版本是两件事：

| 政策组 | 必须回答的问题 | 关键约束 |
|---|---|---|
| model | 最终用哪个 provider/model/deployment/region/runtime？ | 记录 model revision 与选择来源；fallback 默认 deny，显式候选也要重新过政策 |
| budget | 最多运行多久、消耗多少 token/媒体单位/金额？ | 文本可限 input/output token；图像、音频、视频用 `max_media_units`；Agent 另限 turns/tool calls |
| data_policy | 数据能否出境、驻留在哪里、provider 能否留存或训练？ | sensitivity、transfer/consent、residency、retention、training、purpose/deletion 分开声明，缺授权 fail-closed |
| output | 什么输出才可成为候选 Artifact？ | Schema validation、最多修复次数、safety/moderation、grounding/evidence 均是调用前政策 |
| streaming | 流式内容是否可直接成为权威结果？ | `partial_output_authoritative` 固定为 false |
| agent | Agent 能用哪些工具、目录和网络？ | Tool grant 绑定版本、approval、max calls 与参数政策；Workspace 仅 none/read-only/step-private，网络默认 deny_all |

Profile 不保存 secret、prompt 正文、供应商响应或 UI 配置。Run snapshot 记录它的 digest；Invocation 再记录实际解析出的 provider/model/deployment/region，二者共同证明“获准的政策”和“真实执行”没有漂移。

## B. 可复现与审计：记录输入，不假装确定性

AI 步骤不要求相同输入产生相同输出，但每次执行必须能回答“当时到底把什么交给了哪个模型”：

- 解析后的 `provider + model_id + model/runtime version`，以及选择来源；禁止运行中静默换模型。
- system/developer/user prompt 的模板引用、版本和内容哈希；实际渲染 prompt 或受控引用。
- Tool schema、检索结果、输入 Artifact 的 ID/校验和/敏感度；不能只记最终拼接字符串。
- sampling/seed/response format 等会影响结果的参数。
- 输出引用、finish reason、校验结果、实际 token/媒体用量、费用、provider/gateway request ID。
- `model_invocation_id`、`agent_execution_id` 与父 `run_id/step_id/step_attempt_id` 的关联；`step_attempt_no` 只是便于阅读的序号，不能替代稳定连接键。

每个 Model Invocation 还必须产生独立 [`AIUsageRecord`](./schema/proposed/AIUsageRecord.schema.json)。input/output/cache/reasoning token 是核心字段，图像张数、音频秒、视频秒等放在 provider units；用量来源显式区分 provider 报告、本地计量、估算和不可得。Step/Run/session/tenant 成本由这些记录聚合，不能用一个 `cost_level` 或 Run 总数替代。

AIUsageRecord v1.2 有两类不可混用的幂等身份，并强制每条记录直接关联稳定 `step_attempt_id`：

- `operation_idempotency_key` 标识同一个可能收费的操作；transport retry、响应补录和后续对账都保持不变，用来防重复请求/重复计费。
- `observation_idempotency_key` 标识一条不可变的计量观察；纠错创建新 record、新 observation key，并用 `supersedes_usage_record_id` 指向旧记录，不能原地改账。

`invocation_status=completed|failed|ambiguous|cancelled` 与用量/金额状态正交。`measurement_status=unavailable` 时**不得**附带 token 或媒体数字，尤其不能填 0。Provider cost 区分 `unknown/estimated/provisional/final`：estimated/provisional 必须带金额、币种和来源；pricebook/tariff 计算还必须绑定 `price_snapshot_ref`；只有 provider reported 或 invoice reconciled 的金额可标为 final。可选 customer/internal `charge` 是另一栏，绝不能覆盖 provider `cost`；OpenCrew 现行报表期倍率 charge 不落表，不能误写为已有逐调用 charge ledger。

Prompt/response 可能包含个人信息、商业秘密或第三方版权内容。审计存储应优先保存哈希、受控路径和脱敏摘要；原文的访问、保留与删除策略继承输入数据分类，日志不得成为旁路数据湖。

## C. 模型选择、降级与数据边界

1. **Run 开始时解析并冻结选择**：Step 可从 `ai_profile_ref` 取默认值，Run 输入可在授权范围内覆盖；最终选择进入 Run/Invocation 快照。
2. **无静默 fallback**：默认 `fallback_policy=deny`。需要降级时只能列出有序的 `explicit_list`，并在调用前重新检查模态能力、区域、数据政策、成本预算与输出 Schema；实际降级必须发审计事件。
3. **凭证只用引用**：Profile/Context/Artifact/事件不存 API key；`auth_ref` 交给 secret broker 或部署层解析。
4. **外部传输先判定**：输入敏感度、模型部署位置、允许的数据范围和授权证据共同决定能否调用。`consent_required` 没有 `consent_ref` 时 fail-closed。
5. **本地不等于可信**：本机 OpenCode/模型仍是外部执行依赖，必须有超时、健康状态、版本与访问边界；故障不能悄悄切到云端。

## D. 幂等、重试与“请求结果未知”

AI 调用应区分四种情况：

| 情况 | 是否可重发 | 处理 |
|---|---|---|
| 请求尚未被 provider 接受的连接失败 | 在有界策略内可以 | 保持同一 `operation_idempotency_key` |
| provider 明确支持幂等键，且键仍有效 | 可以 | 同键重发并对账 request ID |
| 已提交但响应丢失、provider 不支持可靠幂等 | **不盲目重发** | 标为 `ambiguous/reconcilable`，轮询 provider 状态或人工对账 |
| 输出不符合 JSON/业务 Schema | 不是 transport retry | 先本地校验；若调用模型修复，则记为新的、收费的 Model Invocation，且次数有上限 |

“temperature=0”不能替代幂等。模型服务、检索索引、系统 prompt 与 safety policy 都可能变化；P3 的目标是避免重复副作用和重复计费，不是承诺文本相同。

同一 operation 的请求结果若从 ambiguous 经对账变成 completed，应追加 superseding UsageRecord；不能换一个 operation key 后“当成新调用”，也不能覆盖原来的 ambiguous 证据。输出 Schema 修复则相反：它确实触发了新的模型计算与收费，因此必须使用新的 `operation_id/model_invocation_id`，并受修复次数和预算共同限制。

## E. Agent 能力边界与提交协议

Agent 默认视为**不可信的建议执行体**，权限按能力显式授予：

- 默认 `deny_all`，确需调用工具时采用 allowlist；每个 Tool Call 仍要有参数 Schema、超时、资源/网络策略和审计 span。
- Workspace 默认 `none` 或 `read_only`；需要写临时文件时只给 Step 私有 Working，不给 SessionOutput 或其他 Step 目录。
- prompt、网页、上传文件和检索文本都是不可信数据，不得把其中的指令自动提升为系统权限。
- Agent 不能直接更新权威 Variables、State、OutputManifest 或业务数据库。它只可返回候选结果；Adapter `finalize` 校验后发布 Artifact/Context Patch，或调用受审计的业务 Command/API。
- 业务会话级聊天可以在 Run 外存在；它若要“应用建议”“生成版本”“批准方案”，必须创建 Run/命令，并记录 actor、expected revision 与幂等键。
- 检测到越权 Tool Call 时应中止或拒绝该调用并记录安全事件，不能只在 UI 隐藏。

## F. 预算、流式输出与运行控制

每个 AI Profile 必须给硬上限：wall time，以及 token、媒体单位或金额中的至少一种；Agent 还必须给 `max_turns/max_tool_calls`。达到任何上限都返回结构化 `budget_exhausted`，不把截断内容冒充完成结果。预算是调用前的调度约束；UsageRecord 是调用后的事实。provider 未返回 usage/cost 时分别记 `unavailable/unknown`，不能填 0。

`contract_level=executable` 的 Process 还必须声明 Run 级金额上限和 reservation policy。目标 [`BudgetLedger.schema.json`](./schema/proposed/BudgetLedger.schema.json) 按 billable operation 记录 `reserve → settle + release`，必要时追加 `adjust`：调用前原子预留，调用完成后以 UsageRecord 的可解释金额结算并释放差额；修正流水必须显式写 `direction=debit|credit`，金额始终非负。ambiguous 调用保持可对账占用或显式转 provisional，不能悄悄释放后再次调用。Profile 上限、Run 预算、租户配额分别是单次政策、一次执行和组织治理边界，不能互相替代。

流式 delta、思考中状态与中途文件只是**观测数据**：可以驱动 UI，但不是权威 Output。只有收到明确终止、完成输出校验、写入 Artifact/Manifest 且 `finalize` 成功后，Step 才能 `completed`。客户端断线不自动取消后台调用；`Terminate-Current` 必须尝试 provider/Agent abort，并将“是否真的取消”作为可对账状态。

速率治理同时需要：并发 `semaphore`（并发请求/本地 GPU slot）、`rate_limit`（requests/tokens/媒体秒每分钟）和预算（Run/租户/业务实例费用）。三者不能用一个 `cost_level` 替代。

正式目标 Schema：[`schema/proposed/AIExecutionProfile.schema.json`](./schema/proposed/AIExecutionProfile.schema.json)、[`AIUsageRecord.schema.json`](./schema/proposed/AIUsageRecord.schema.json) 与 [`BudgetLedger.schema.json`](./schema/proposed/BudgetLedger.schema.json)。前者是调用前政策，后两者是调用后事实/预算流水；三者不能合并成一个可变“AI 调用对象”。

## G. OpenCrew 当前映射

| Profile 要求 | OpenCrew 证据与差距 |
|---|---|
| 模型/provider/部署/计费快照 | `[implemented]` `Variables` 已有 prompt/run provider+model、`provider_mode/billing_mode`（`tool_sessions/schemas/models.py:29-36`）；尚无统一冻结的 AIExecutionProfile |
| 文件敏感度与云传输授权 | `[implemented/partial]` Input/Output 文件带 `sensitivity/visibility`（`models.py:47-75`），Variables 有 cloud ASR 授权字段（`:40-42`）；尚未通用于每种模型模态 |
| Prompt 与模型调用审计 | `[implemented/partial]` `PromptManifest` 与 `ModelCallAudit`（`models.py:128-161`）；字段中多处仍为开放字典，尚未覆盖完整参数与 prompt hash |
| 调用幂等/用量/费用 | `[implemented/partial]` `local_usage_log` 持久保存 task/attempt/step、幂等键、provider/model/modality、units、**估算成本 `est_cost_micros`**、币种、**pricebook version** 与 reconciliation time，并有唯一幂等索引（`db/schema.py:local_usage_log`）；`LocalUsageRecorder` 用事务插入和 conflict 去重。`ModelBroker` 也强制 idempotency key 并写审计，但其完整 response cache 仅在进程内。**注意**：默认路径不写 `actual_cost_micros`；当前 xAI 视频路径会从 provider usage 写入 actual cost/source/raw，但覆盖面有限且不是发票对账结果。pricebook 本体是代码配置（`local_metering.py:MODEL_PRICEBOOK`）非表；customer/internal charge 在报表期计算不落表。目标差距是统一 Model/Agent Invocation 身份、typed measurement/cost status、预算预留与所有调用路径覆盖 |
| OpenCode Agent 上下文 | `[implemented]` `OpenCodeSessionClient.prompt_async` 支持显式 model、agent、system、tools、parts（`adapters/opencode.py:239-271`）；它是执行上下文，不是 FlowSpec 业务 session |
| Agent 权限与事件脱敏 | `[implemented/partial]` 口播 Agent 禁用 bash/read/write/web 等工具并白名单化事件字段（`agent_chat_common.py:6-25,36-100`）；检测到 Tool Use 会 abort 并记事件（`agent_chat_routes.py:1585-1602`）。这还是领域路由策略，尚非通用 Profile enforcement |
| Agent 流式观测 | `[implemented/partial]` 口播 Agent 通过 OpenCode event stream 向客户端转发清洗事件（`agent_chat_routes.py:1610-1625`）；最终 Artifact 发布与流式完成尚未统一成 Tool finalize 契约 |

## H. 四场景验证关注点

- **贷款审批**：模型只能生成/辅助风险理由与材料摘要，授信政策计算和最终批准必须由版本化规则/有权限的人完成；PII/征信数据出境默认受限。
- **银行报告**：LLM 负责基于已核验指标生成叙述与异常解释，不能重算或覆盖监管数字；数字引用需带 metric lineage。
- **尽调**：LLM/OCR 可扇出抽取和发现冲突，必须保留页码/段落 evidence；高风险法律判断由专业人员确认。
- **OpenCrew 视频**：Agent 可提出创意、分镜和 prompt 候选；TTS/图像/视频模型调用按媒体用量计费，生成结果需媒体校验、内容政策结果、绑定 key 与人工选定版本。

下一步：直接打开[四场景可执行 demo](./demos/index.html)，再结合 [examples/](./examples/) 看领域边界；回到工具契约看 [03](./03_ToolContract.md)。
