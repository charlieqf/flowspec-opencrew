# 02 · 流程定义规范（Process Definition）

> 如何声明"这条业务流程由哪些步骤、什么顺序、什么依赖、什么策略组成"。立场见 [00·P1/P9](./00_Overview.md)：**声明式，不写在命令式代码里**。

## A. 概念层

### A1. 流程 = 步骤清单 + 顺序 + 依赖 + 策略

一个 Process 定义回答四问：

1. **有哪些步骤？** —— 一组 Tool 引用（每个引用 = 一个步骤定义）。
2. **怎么展示？** —— stage/阶段 + 步骤序号只给出稳定展示顺序；执行顺序完全由依赖决定。
3. **什么依赖？** —— 三种依赖，**用途分明**（采纳团队审核 [00·P9]）：
   - **数据依赖** = `reads`（读变量）+ `consumes`（消费上游产物）——有数据流的地方用它。
   - **流程内控制依赖** = `depends_on`（"S2 要等 S1 completed"）——**没有数据产物、纯控制顺序**时用它，而**不是**滥用命名条件。
   - **外部/跨流程信号** = `conditions`/`signals`（命名条件池）——**只**表达外部事件或跨流程依赖，见 [06·C](./06_Runtime_Observability.md)。
4. **什么策略？** —— 简单 `when` 分支、重试/恢复、资源需求、SLA、人工卡点、是否默认启用。

### A2. 顺序 vs 依赖：两套约束，依赖为准

- **stage/序号** 是**书写顺序**，只给人读、给 UI 排；不存在隐式线性执行或“序号回退”。
- **依赖（`depends_on` / `consumes` / `reads`）** 是**执行顺序**的真正决定者。一步只要它的输入齐了就能跑，哪怕序号靠后。

> 规则：**永远以依赖为准，序号只是默认与展示**。不要用序号隐式表达依赖——那样重排就会出错。
> **不要用命名条件表达流程内顺序**——流程内控制依赖用 `depends_on`，命名条件留给跨流程/外部信号（否则静态校验、血缘、防陈旧条件误触发都变弱）。

### A2.1 非单链条边界：一个 Run = 一个 DAG

完整业务生命周期可以有“退回→修订→重审”的回环；v0.4 把每次可调度执行展开为一个 Run。Run 内支持多根节点、并行 fork、多前置 AND-join、`any_of` OR-join，以及最小 `when` guard；**不允许原始回边**。

- 技术瞬态失败：`retry.policy=on_transient`。
- 集合逐项处理：`fanout`（仍为 `[proposed]`）。
- 补件、返工、数据修正：在同一 `session + task` 下创建新 Run，用 `supersedes_run_id` 关联旧 Run（`[proposed]`）。
- 未激活或已不可能满足的分支按 `branch_closure=skip_unreachable` 递归成为 `skipped`，避免分支终点永久 pending。
- Run 以 `completion.mode=exactly_one_outcome` 收口；一个 outcome 同时声明业务 guard、终点 Step 与必需 Artifact。拒绝、补件、修订请求都可以是完整正常结果。
- v0.4 不引入 `repeat_until`、循环 Gateway 或任意表达式 DSL。真正出现必须在 Run 内迭代的已验证需求时，再设计有最大轮数和逐轮产物隔离的结构化循环。

### A2.2 Illustrative 与 Executable 不是同一承诺

- `contract_level=illustrative`：只用于说明拓扑与概念，可省略部署时才有的 Registry、Schema、预算与完成契约；不能交给 Runtime 执行。
- `contract_level=executable`：除 DAG 外，必须冻结 `schema_version`、`tool_registry_ref`、`context_schema_ref`、typed `artifact_contracts`、`branch_closure`、`completion` 与 `run_budget`。编译/启动时必须检查 Process 与 Tool Registry 的 `type/side_effect_class/reads/consumes/produces/writes` 不漂移。

Run 启动后还要保存 Process、Tool Registry、全部 AI Profile 的版本与 digest；恢复使用快照，不静默采用“最新配置”。四套可运行实例见 [12](./12_ExecutableDemos.md)。

这条边界保留拓扑排序、血缘、关键路径和断点恢复的简单语义，也避免把业务返工与技术重试混为一谈。

### A3. 继承（Folder 模型，借 Control-M）`[roadmap 🆕]`

流程可分组（folder / sub-process）。**分组级声明的策略被组内步骤继承**：日程、资源、前置条件、模型选型等可在组上定义一次，组内步骤默认继承，可显式覆盖（`USE_PARENT` 转义与 per-step override）。这让上千步骤的编排保持 DRY。
> **状态**：Folder 继承与 `USE_PARENT` **当前未实现**，是从 Control-M 借的路线图能力。

### A4. 计划与确认（Plan & Confirm，人工卡点的一等形态）`[partial / proposed]`

许多业务流程不是"定义好就自动全跑"，而是**先生成一个执行计划、由人确认后再执行**（审批流、尽调抽样计划、视频分镜方案）。规范把这抽象为：

- **Plan**：由某个"规划步骤"产出的、结构化的待执行方案（`plan_json`）。
- **Confirm**：一个人工卡点——计划在被确认前，下游执行步骤不放行。

这对应 BPMN 的 User Task / Control-M 的 Confirm。**人工介入是显式步骤，不是外挂的等待。**

> **状态（团队审核）**：`workflow_plans.confirmed_at` **有表字段**，但**未找到通用确认流程的读写闭环**；口播的 `binding_status` 是**输入哈希绑定状态**，**不是**人工确认状态。所以"通用 Plan Confirm + `waiting(user)`"是 `[proposed]`；当前口播的"先生成后执行"（`VideoPlanGenerator` → `VideoPlanExecutor`）**并非**由一个通用确认卡点驱动。

### A5. 静态可校验性（声明式的红利）

因为流程是数据不是代码，定义**理应**可在运行前被校验。但要分清现状：

| 校验 | 状态 |
|---|---|
| 依赖 token 分类 + 无法解析报错（`RegistryNormalizationError`） | `[implemented]`（`registry_normalizer.py:235` 目前**只做**这个） |
| step ID 唯一、依赖引用存在、依赖图无环、产物/资源引用存在、展开 defaults 后重试安全 | FlowSpec `schema/proposed/lint_process.py` 已实现；OpenCrew normalizer/运行时尚未接入，故执行侧仍为 `[proposed]` |
| 每步 `writes` 字段在 Variables Schema 内 | `[proposed]`（未做） |
| 跨步 `writes` 所有权唯一（owner 冲突） | `[proposed]`（未做，见 [04·A4](./04_VariablesAndState.md)） |
| 每步 `reads` 有生产者或初始播种 | `[proposed]`（未做） |
| executable Artifact contract 唯一解析、fanout stable key、completion outcome 引用与分支闭包 | FlowSpec v0.4 linter + demo 端到端测试已实现；OpenCrew Runtime 尚未接入 |

> 更正（团队审核）：OpenCrew 的 `registry_normalizer.py` **只做 token 分类与 unresolved 检查**。本规范仓内的轻量 linter 让目标 Process 文档可以先行做图语义回归，但它尚未接入 OpenCrew 执行入口；Variables 字段和 owner 冲突检查也仍未实现。

---

## B. 可实现绑定

### B1. 流程注册表 Schema（Process Registry）

一个 Process = 一个注册表文件（JSON）。以下是**目标 Process Schema `[proposed]`**——**不是** `registry_normalizer.py` 的真实输出（后者从 `registry_normalizer.py:327` 起，是"Normalized **Tool Library** registry"结构：`{schema_version, tools[], unresolved_dependencies}`，并**不含** stages 顺序/流程编排，口播的流程顺序仍**硬编码**在 `koubo_storyboard/constants.py`）。`tool` 引用可执行能力；依赖、条件、资源和人工卡点属于 Process Step。示例同时物化有效 I/O、`side_effect_class` 与成本画像，以便不依赖运行时注册表也能完成静态安全校验：

```jsonc
{
    "schema_version": "0.4",
    "contract_level": "illustrative",           // executable 还必须冻结 Registry/Schema/outcome/budget
    "process_id": "loan_approval",              // 稳定标识；版本只放 version，不编码进 ID
  "version": "1.0.0",
  "title": "个人消费贷审批流程",
  "failure_propagation": "stop_run",          // stop_run | continue_independent（Run 级，见 06·E）
  "defaults": {                                // A3 继承：组级默认，步骤可覆盖
    "retry": { "policy": "fail_fast" },        // 见 06 重试取向
    "on_error": [],                            // On-Do 恢复策略，见 06
    "resources": []                            // 资源需求，见 06·D
  },
  "stages": ["intake", "risk", "decision", "disburse"],  // 阶段顺序（展示/默认）
  "steps": [
    {
      "id": "S1_intake_validate",              // 逻辑步骤稳定标识；物理目录名由实现映射
      "tool": "form_validator",                // 引用的 Tool（见 03）
      "stage": "intake",
      "reads": ["applicant_id"],               // 数据依赖：读变量 token（见 04）
      "consumes": [],                          // 数据依赖：消费上游产物（见 05）
      "produces": ["ValidatedApplication.json"], // 主产物（写入 OutputManifest）
      "writes": ["application_valid"],         // 允许写回的变量字段（白名单，见 04）
      "depends_on": [],                        // [proposed] 流程内控制依赖：[{ "step_id":"S1","statuses":["completed"] }]；OR-join 用 [{ "any_of":[...] }]
      "conditions_in": { "op":"AND", "of":["external.credit_ready"] }, // [roadmap 🆕] 仅外部/跨流程命名信号（见 06·C），勿用于流程内顺序
      "conditions_out": [],                    // [roadmap 🆕] 完成后发布的跨流程信号
      "side_effect_class": "pure",            // 必填；重试策略据此校验
      "when": { "variable":"application_enabled", "not_equals":false }, // 可选最小 guard
      "required_by_default": true,
      "cost_level": "low",
      "uses_llm": false,
      "human_gate": null                       // 或 { "type":"confirm", "form":"..." } 见 A4
    }
    // ... 更多步骤
  ]
}
```

### B2. 依赖求值规则（Runtime 契约）

一步 `S` 的目标求值顺序如下（`[proposed]`；现行 runner 仅实现 `reads`/`consumes` 的子集，见 [04·A5](./04_VariablesAndState.md)）：

```text
1. activation：求值 when
   - equals/not_equals/in 缺变量 → waiting(variable)
   - exists 直接求值
   - false → skipped(when_false)，不创建 Attempt、不调 Tool
2. control/data prerequisites：分别求值 depends_on、reads、consumes、外部 signal
   - 生产者仍可能完成 → waiting(具体原因)
   - 所有相关生产者均终态且已不可能满足 → skipped(*_unreachable)
3. resource claim：以一次原子操作获取 mutex/semaphore/rate-limit claim
   - 不能先“检查 available”再另一步 acquire，否则存在 TOCTOU 竞态
4. dispatch：创建 Step Attempt 并调用对应 Adapter
5. finalize：校验 ToolResult、Context Patch、Artifact 与 Usage 后原子发布可见结果
```

`depends_on` 每项是普通依赖 `{step_id,statuses}` 或 OR-join `{any_of:[...]}`。一个普通依赖在上游进入终态且状态不在 `statuses` 时变为 impossible；`any_of` 只有所有子项都 impossible 才 impossible。Artifact/Variable 也用同样的“仍可到达 / 已不可到达”区分。这样运行时能等待真正可能到来的输入，同时把另一条已关闭分支递归标成 `skipped`，而不是死锁。

`human_gate` **不参与 Confirm Step 的 dispatch 前置谓词**。Confirm Step 被调度后才创建 durable work item，并走 `running → waiting(user)`；提交决定时校验 allowed decision、actor role、reason、input snapshot hash 与 expected revision，CAS 成功后恢复同一 Attempt、发布决定 Artifact/Context，再进入 `completed`。Suspend Step 对外部 callback 使用同一模式。若把“已确认”写成 dispatch 前置条件，会形成“未 dispatch 就没有 work item、没有 work item 就无法确认”的循环依赖。

`when` 只允许 `{variable, equals|not_equals|in|exists}` 四种结构；不执行脚本、不访问网络、不支持任意表达式。等待原因必须可查询。**现行实现**：不满足即 `blocked`，无 `waiting/skipped`（见 [04·C](./04_VariablesAndState.md)）。

分支汇合若语义上允许可选上游，应显式把 `skipped` 写进期望状态，例如 `"statuses":["completed","skipped"]`；若依赖只接受 `completed`，上游 skipped 后该下游按 `skip_unreachable` 关闭。

所有 Step 终态后（或每次状态变化时）求值 `completion.outcomes`：必须**恰好一个** guard 为真，并且其 `terminal_steps` 均 completed、`required_artifacts` 均 valid，Run 才能 `completed`。零个匹配是定义/输入不完整；多个匹配是 outcome 互斥性错误；二者都必须失败而非任选一条。

参考实现：`runner.py:check_dependencies`（`reads_session_context` + `consumes_outputs` 求值）；`runner.py:run_registry_step` 调度。

### B3. 继承的绑定

- 组级 `defaults.*` 与步骤级同名字段做**浅合并**，步骤级优先。
- 显式写 `"schedule": "USE_PARENT"` 表示强制取组级。
- 校验期展开继承后再做静态检查。
- `failure_propagation` 是 Process/Run 级策略，不参与步骤级继承：`stop_run` 停止启动任何新步骤；`continue_independent` 只允许 DAG 上不可达于失败步骤且其余前置仍满足的分支继续。

### B4. 计划确认的绑定（用 depends_on + consumes，`[proposed]`）

```jsonc
// 规划步骤产出 plan；确认步骤消费它并 depends_on 它；执行步骤 depends_on 确认步骤
{ "id": "S6_plan_generate", "tool": "risk_plan_generator",
  "side_effect_class":"pure", "produces": ["ApprovalPlan.json"] },

{ "id": "S7_plan_confirm", "tool": "human_confirm", "type": "confirm",
  "depends_on": [{ "step_id": "S6_plan_generate", "statuses": ["completed"] }],
  "side_effect_class":"pure",
  "consumes": ["ApprovalPlan.json"],           // 消费待审计划
  "produces": ["ApprovalDecision.json"], "writes": ["approval_decision"],
  "human_gate": { "type": "confirm", "form": "approval_review_form",
                  "roles": ["risk_officer"], "sla_seconds": 86400,
                  "allowed_decisions": ["approved", "rejected", "revision_requested"],
                  "bind_to_input_hash": true, "require_reason": true } },

{ "id": "S8_disburse", "tool": "disbursement",
  "side_effect_class":"reconcilable",
  "depends_on": [{ "step_id": "S7_plan_confirm", "statuses": ["completed"] }] }
  // S7 dispatch 后创建 work item 并 waiting(user)；S8 等 S7 completed（现行通用闭环未实现）
```
> `binding_status`（口播）是**输入哈希绑定状态**，不是人工确认状态；通用 Plan Confirm 见 A4，属 `[proposed]`。

参考实现：`workflow_plans.confirmed_at` **只有表字段**，当前**未形成**通用"确认状态"的读写与 UI 回传闭环（`binding_status` 是输入哈希绑定状态，不承担人工确认语义）。

### B5. 流程作者检查清单

- [ ] 每步的 `reads` 都有生产者或初始播种？
- [ ] 每步的 `consumes` 都指向某上游 `produces`？
- [ ] `writes` 字段所有权唯一、在变量 Schema 内？
- [ ] 依赖图无环？（跑一次拓扑排序）
- [ ] step ID 唯一，所有 `depends_on`、`consumes`、resource pool 引用都存在？
- [ ] `when` 是否只用了最小四操作，并正确处理了下游的 `skipped`？
- [ ] `branch_closure=skip_unreachable` 后是否仍会出现无生产者、无 outcome 的死路？
- [ ] completion outcomes 是否互斥且完备，并同时引用终点 Step 与必需 Artifact？
- [ ] executable 定义是否冻结 Tool Registry、Context/Artifact Schema、Run budget；Process/Registry I/O 是否无 drift？
- [ ] 昂贵步骤（GPU/LLM/多模态）声明了资源、重试取向与 `ai_profile_ref`？预算和逐 Model Invocation Usage/Cost 是否可落账？
- [ ] `failure_propagation` 是否明确？若允许 `continue_independent`，独立性是否由 DAG 推导而不是人工列表？
- [ ] 人工卡点声明了 allowed decisions、roles、reason、input-hash binding、expected revision 与 SLA？确认 Step 是否先 dispatch 创建 work item，而非反向等待一个不存在的确认？
- [ ] 顺序仅用于展示，真实约束都落在依赖上？

下一步：[03 · 工具契约](./03_ToolContract.md)。
