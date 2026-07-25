# 11 · 四场景综合验证与规范边界

> 本章不再发明第五套流程，而是用四个专业化案例反向检验：哪些能力必须进入 FlowSpec 核心，哪些只属于 AI Profile，哪些应留给领域实现。`examples/` 是领域特点与理想模型的 Markdown 概念压力测试，`demos/` 是把同一组场景收敛为 Process/Registry/Mock/Run/UI 的可执行验收包；二者不是两套并列实现。概念层见 [贷款审批](./examples/loan-approval.md)、[银行数据报告](./examples/bank-data-report.md)、[尽调材料](./examples/due-diligence.md)、[OpenCrew 视频](./examples/opencrew-video-creation.md)，可交互、带双 Run 记录的验证入口见[四场景 demo](./demos/index.html)。

## 1. 四个业务实例到底在交付什么

| 场景 | 业务实例（session + task） | 权威业务产物 | AI 的合适角色 | AI 不可越过的边界 |
|---|---|---|---|---|
| 消费贷审批 | 一笔 application | 输入/外部报告、feature snapshot、policy decision、审批/接受、放款回执 | 材料抽取、摘要、意见草稿 | 不改来源数字、不替代政策引擎/授权审批、不盲重发放款 |
| 月度银行风险报告 | 法人+组合+期间+data cut | 冻结数据集、DQ/对账、指标、lineage、签署报告与分发回执 | 异常解释、grounded narrative | 不重算/覆盖指标，不把 waiver 伪装成 DQ 通过 |
| 买方尽调 | 一个 matter/标的+scope revision | inventory、evidence、conflict/finding、专业复核、报告版本 | OCR/抽取、候选冲突、finding/报告草稿 | 不跨 matter、不直接定法律/交易结论、不丢 evidence |
| AI 视频创作 | 一项创作/交付 | brief、storyboard、按 key 绑定的媒体、QA、选定成片与发布回执 | 创意 Agent、TTS/图像/视频生成、多模态 QA | 不直接发布、不越权读写、不把流式/未绑定资产当 Final |

这四个实例的共同点不是“都有一条链”，而是**都要把高成本、不完全可靠的外部/AI 执行，收敛成可审计的业务产物和人工责任**。

### 1.1 可执行 demo 的反向验证结果

四个页面都由 `contract_level=executable` 的 Process、版本化 Tool Registry 和确定性 Mock Tool 物化；每个场景用同一 `session + task` 生成两次有修订关系的 Run。Mock 不联网、不调用真实模型，金额也明确是 estimated provider cost；它验证的是契约自洽、拓扑闭合与确定性重建，不是模型效果、生产吞吐或 claim / lease / fencing / 崩溃恢复等分布式正确性。

| 场景与页面 | Run 1 → Run 2 outcome | Step | 最新 Run fanout / Human Task | 最新 Run Usage / provider cost | 主要被迫收紧的契约 |
|---|---|---:|---:|---:|---|
| [消费贷审批](./demos/loan-approval/index.html) | rejected_with_notice → approved_and_disbursed | 12 | 0 / 1 | 1 / US$0.009 | 人工决定的批准/拒绝/退回修改均有闭合 outcome；绑定输入与 revision；放款 operation 可对账 |
| [银行风险报告](./demos/bank-risk-report/index.html) | data_remediation_requested → report_distributed | 15 | 0 / 1 | 1 / US$0.021 | data cut 与三路 AND-join；DQ remediation 与报告修订分开；指标与叙述/签署分权 |
| [交易尽调](./demos/due-diligence/index.html) | supplement_requested → report_issued | 10 | 4 / 1 | 6 / US$0.111 | 文档 item key、逐项 Usage/Artifact、evidence binding 和确定性 fan-in |
| [OpenCrew AI 视频](./demos/opencrew-video/index.html) | qa_revision_required → delivered | 16 | 15 / 2 | 17 / US$1.637 | Agent 内循环与 Process 分层；同一 Agent Step 的两轮 Invocation 以 execution ID 归组；音频/图像/视频按 asset key 汇合；多模态预算与发布 Gate |

运行记录进一步证明：业务拒绝、补件和返修并不等于技术失败；它们可以是 `Run.status=completed` 下互斥且可审计的 outcome。未激活分支必须递归收敛为 `skipped`，否则四个图都会残留永久 pending。

## 2. 非单链条拓扑验证

```text
贷款：材料 → [征信 | 欺诈 | 身份/收入核验] → 风险/政策 → [直通 | 人审] → 接受 → 放款
银行：[账户 | 交易 | 客户 | 总账] → 对账/DQ → 指标 → [叙述 | 附件] → 签署/分发
尽调：inventory → 文档 fanout → [法律核验 | 财务核验] → findings → 专业复核 → 报告
视频：方案确认 → [音频链 | 视觉链] → 按 asset key 汇合 → 合成/QA → 发布批准
```

四套可执行页面已把这些拓扑从文字变成由 `process.json.depends_on` 生成的箭头图：每个 Step 带稳定编号，fork / AND-join / guard 显式标注，手机端投影为“前置 → 本步”清单；浏览器回归要求渲染边数与声明边数逐条相等。页面也用场景实例解释 `full/through_step/from_step/only_step`，但 Mock Runtime 只执行完整 Run，局部范围仍是目标控制契约。

FlowSpec 可表达的部分：

- 多根、多分支、AND-join 用多个 `depends_on/consumes`；OR-join 用 `any_of`。
- 同构集合用 `fanout`；父 Step 的 completed 必须定义为全部子项 `completed` 或按明确隔离政策收敛。
- 条件分支只用最小 `when`，不引入任意脚本表达式。
- 失败后的后代阻断；`continue_independent` 只放行 DAG 可证明独立的分支。
- 补件、数据重述、尽调追加材料、脚本/音色/参考图修改都创建新 Run；Run 内不画回边。

**仍需在 OpenCrew Runtime 实现而非继续扩 DSL 的部分**：目标 Schema 与 demo 已物化 fanout 稳定 item key、逐项 State/Usage/Artifact 和确定性 reduce，但现行 OpenCrew 通用 runner 尚未消费这些契约；生产实现仍缺原子子项 claim、Checkpoint、隔离阈值、typed fan-in 与 scoped rerun。它们是运行时/物化模型缺口，不需要 `while`、循环 Gateway 或通用表达式解释器。

## 3. 跨场景能力矩阵

| 能力 | 贷款 | 银行报告 | 尽调 | 视频 | 规范归属 |
|---|:---:|:---:|:---:|:---:|---|
| 每 Run DAG + 跨 Run 修订链 | ✓ | ✓ | ✓ | ✓ | Core |
| canonical Artifact hash/provenance/按需 binding/validity | ✓ | ✓ | ✓ | ✓ | Core；临时文件排除 |
| AND/OR join + 最小 guard | ✓ | ✓ | ✓ | ✓ | Core |
| fanout + checkpoint/scoped rerun | 少量 | 来源/组合 | 大规模 | 对白/镜头 | Core runtime target |
| 人工 Gate + override/CAS/审计 | ✓ | ✓ | ✓ | ✓ | Core |
| reconcilable 外部副作用 | 放款/征信 | 分发 | 外部数据源 | 媒体生成/发布 | Core |
| Model/Agent 权限与数据政策 | ✓ | ✓ | ✓ | ✓ | AI Profile |
| Model Invocation Usage/Cost | ✓ | ✓ | **高** | **很高** | Core concept + AI binding |
| 领域 policy/metric/finding/media Schema | 专属 | 专属 | 专属 | 专属 | Domain，不进 Core |

## 4. 由四场景共同推出的规范要求

1. **Artifact 必须强于文件存在，但只治理 canonical output**：贷款报告、银行 data cut、尽调 evidence、视频 asset 都确有跨步/跨 Run 对齐价值，所以声明业务 binding；Runtime 自动生成版本 ID/内容 checksum，并在 producer Attempt 保存一次 `input_snapshot_hash`。Working、日志、Prompt、staging 和投影不承担这套元数据；无 join/re-bind 需要的一次性正式产物也可省略 binding。
2. **AI 输出是候选业务产物**：模型完成不等于业务完成；必须经过结构/引用/媒体校验和相应人工/规则 Gate 后才能 finalize。
3. **用量与费用是一等事实**：每个 Model Invocation 单独记录 token/媒体单位和费用状态；Agent、fanout 和格式修复都可能在一个 Step 内产生多条记录。Run/session/租户只是聚合视图。
4. **“结果未知”是一等故障**：放款、报告分发、尽调数据源请求、图像/视频生成都可能已发生但响应丢失；统一走 reconcilable + provider/business key 对账，不盲重发。
5. **人改数据也要受控**：DQ waiver、审批 override、finding disposition、选定媒体版本都用 command/patch + actor/reason/expected revision，并级联失效后代。
6. **权限来自业务范围，不来自 Agent 能力**：模型/Agent 只能读取本 Run/Profile 授权的输入；任何影响权威状态的动作经过 Adapter finalize 或业务 API。
7. **完成是业务契约，不是状态计数**：Runtime 先关闭不可达分支，再要求 `exactly_one_outcome` 的 guard、终点 Step 和必需 Artifact 同时成立；“所有 Step completed”会把正常拒绝/返修逼成假失败。
8. **Step 不是一次真实调用**：一个逻辑 Step 可能因 guard 不执行、因 Confirm 等待人工，或因 fanout/Agent 包含多个 operation；Step Attempt、Tool Call、Model Invocation 与计费 operation 必须各有身份。

## 5. 明确不吸收到通用核心

- 不提供通用信贷规则语言、监管报表公式语言、法律 finding ontology 或视频 timeline DSL；由领域 Schema/Tool 负责。
- 不把 OpenCode Session、provider interaction ID、数仓 job ID、征信 enquiry ID 变成 FlowSpec 身份主词；它们都是外部执行引用。
- 不保证 LLM 可复现文本；保证输入/模型/prompt/调用/用量/结果版本可审计，并对副作用幂等/对账。
- 不用一个万能 `retry_count` 同时表达 transport retry、Step Attempt、Checkpoint、业务重审、模型修复和计费次数。
- 不因四场景都有人审就设计完整 BPM/组织权限系统；核心只规定 Gate/actor/role/revision/audit 契约，复杂委派与组织策略由身份服务提供。

## 6. 结论与落地优先级

四场景没有迫使 FlowSpec 放弃“每 Run 一个 DAG”，也不需要通用循环 DSL。以下是**建设依赖顺序**，不是业务价值排名；后层都要引用前层身份与事实，因此不应仅因 AI 费用紧迫就先建一套脱离 Run/Attempt 的旁路账本。

| 优先级 | 建设包 | 最小退出条件 | 为什么在这里 |
|---|---|---|---|
| P0-A | 可执行定义编译 + Run 基座 | Process/Tool Registry 校验与 digest；Run/Step/Attempt 身份；原子 claim；side-effect/retry 校验；branch closure；exactly-one outcome | 所有 Artifact、Human Task、Usage 与 fanout 都必须先有稳定挂载点；否则后续只能靠日志猜关联 |
| P0-B | Typed Context + Artifact finalize | Context Schema/白名单 patch；仅 canonical Artifact 做 Runtime-generated ID/hash、producer Attempt provenance、按需 binding、validity；Attempt 私有写入与原子发布 | 保住“产物属于谁、由何输入产生、是否仍可消费”，同时不给临时文件和 Tool 作者增加普遍负担 |
| P0-C | Human Task / callback | dispatch 后创建 work item；`waiting(user|external_callback)`；actor/role/reason/input hash/expected revision；幂等回调和超时 | 四场景都有责任移交；若继续用 blocked/按钮布尔值，会丢失责任人和并发控制 |
| P1-A | AI Profile + Invocation Usage + Budget | Profile enforcement；统一 Model/Agent Invocation；operation/observation 双幂等；typed measurement/cost；所有调用路径覆盖；reserve/settle/release | AI 治理业务价值高，但必须依赖 Run/Attempt/Artifact 身份。现行 `local_usage_log` 已有持久计量、估价与唯一键，可增量迁移而非从零建设 |
| P1-B | Typed fanout/fan-in | stable item key；逐项 Attempt/Artifact/Usage；并发上限/失败阈值；确定性 reduce；scoped resume | 尽调与视频证明它不可省，但应复用 P0/P1-A 的身份、产物和费用原语，而不是另建子任务体系 |
| P2 | 对账、资源与运营控制面 | reconcilable worker；持久 operation key/receipt；semaphore/rate limit/fencing；snapshot/event cursor/command CAS；SLA overlay | 生产韧性与规模化所需；可在 P0 先做关键副作用的领域对账，P2 再平台化 |

建议将 P0-A/P0-B 作为任何新流程接入的硬 Gate；P0-C 与 P1-A 可在基座稳定后按业务流并行落地。路线图不再优先增加通用表达式、循环语法或完整 BPM 组织模型。

> **上线 Gate 不按编号机械后置**：P2 表示通用平台化的建设依赖顺序，不表示可以先把分布式风险带入生产。若首批场景已经使用多 worker、长时昂贵任务或 `reconcilable/non_idempotent` 外部副作用，则对应的 lease/TTL/fencing、operation receipt/reconcile、checkpoint/心跳恢复必须前移，与该场景的 P0/P1 一起成为上线退出条件；没有这些保证时只能维持单 worker、受限流量或人工对账的明确运行边界。
