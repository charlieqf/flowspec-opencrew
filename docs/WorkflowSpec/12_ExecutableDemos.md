# 12 · 四场景可执行 Demo 与验收

> **定位 `[proposed/executable]`**：这四套 demo 是 FlowSpec 目标契约的可执行验证集。它们由文档内的确定性 Mock Runtime 物化，证明 Process、Tool Registry、Run、Artifact、Human Task、Event、AI Usage 与 Budget 能闭合；它们**不是** OpenCrew 通用 runner 已实现这些能力的证据。

> **决策层演示口径**：页面证明的是契约自洽、拓扑闭合、确定性重建与 UI 投影一致；不证明分布式 claim / lease / fencing、真实并发、进程崩溃恢复或 reconcile worker 已完成。后者仍是 `[proposed]` 建设项。

## 1. 双受众阅读入口

统一入口：[demos/index.html](./demos/index.html)。四个页面均为单个自包含 HTML，不加载网络资源，直接用浏览器打开即可。

`examples/*.md` 与 `demos/<scenario>/` 覆盖同一组场景但分工不同：前者保留领域特点、专业边界和规模化设想，回答“为什么这样建模”；后者把其中可验收的设计物化为 Process/Registry/Schema、Mock Tool、输入 case、Run/Event/Log/Artifact/Usage 和 UI，回答“契约能否闭合并确定性重建”。二者不是两套并列实现，Demo 的执行事实以并列 JSON/NDJSON 与脚本为准。

HTML 只保留承担交互展示职责的 6 个页面：根综述、Demo 入口和四个场景页。规范章节、`examples/`、Schema 说明均以 Markdown 为唯一权威来源，不生成逐篇 HTML 镜像。

| 页面 | 决策层先看 | 开发团队继续看 |
|---|---|---|
| [消费贷审批](./demos/loan-approval/index.html) | 证据、政策、人责、拒绝/退回修改/放款 outcome 与修订链 | 并行核验、Human Task、reconcilable 放款、Artifact binding |
| [银行风险报告](./demos/bank-risk-report/index.html) | data cut、质量例外、指标/叙述/签署分权 | 三路 AND-join、DQ waiver、grounded AI、分发或报告修订回执 |
| [交易尽调](./demos/due-diligence/index.html) | 缺件先结束、海量材料仍可追溯到证据和责任人 | typed fanout/fan-in、document key、EvidenceIndex、逐项 Usage |
| [OpenCrew AI 视频](./demos/opencrew-video/index.html) | 创作可迭代，但成本、资产身份、QA 和发布均受控 | Agent 内循环、六类 AI Profile、15 个 fanout item、多模态预算账本 |

每页在流程图前提供三层解释：该场景的独特挑战；FlowSpec 提供什么支持；规范又禁止什么。页面还给出针对该场景的术语释义，而不是抽象字典。例如尽调把 fanout 解释为“按 `document_id` 拆成独立抽取任务”，视频把 fan-in 解释为“按 `asset_key` 汇合音频与视频片段”。

推荐阅读顺序：决策层看导读、四个结果指标、Run 修订链、流程和 outcome；开发团队再切换“运行记录 / Artifact / 存储与日志 / AI·成本治理 / 规范透视”，核对真实契约来源。

四页的“业务流程”标签都从 `process.json.depends_on` 自动生成一张连线 DAG：编号表示定义/阅读顺序，箭头表示真实执行依赖，节点显式标出一对多 fork、多上游 AND/OR join 与 guard；手机端改为逐 Step 的“前置 → 本步”依赖清单，避免密集连线失读。节点采用**中文业务动作和中文说明为主、稳定 Step ID/Tool key 为次级技术标识**的双层表达：中文文案来自 `demo.json` 的完整映射，不修改或翻译机器契约键。“规范透视”标签另有四张场景化范围卡，解释全链、运行至某步、从某步重跑和单步诊断；这些是**契约投影而非可执行按钮**。

## 2. 每套 demo 的目录契约

```text
demos/<scenario>/
├── index.html                    # 单页 UI；内嵌同源定义与两次 Run 投影
├── demo.json                     # 场景文案、中文 Stage/Step/outcome UI 映射、双受众导读与术语
├── process.json                  # contract_level=executable 的业务流程定义
├── tool_registry.json            # 版本化 mock entrypoint、I/O 与副作用契约
├── tools.py                      # 不联网的确定性业务 Tool 与 mock AI 输出
├── cases.json                    # 同一业务实例的两次 Run 输入/人工决定
├── run_demo.py                   # 单场景重建入口
├── profiles/*.json              # 冻结 AIExecutionProfile v1.1
├── schemas/*.schema.json         # 业务 Context 与 typed Artifact Schema
└── runs/<run_id>/
    ├── run.json                  # 聚合 RunRecord
    ├── context.json              # 最终 typed Context
    ├── events.ndjson             # cursor 单调、带 actor/correlation 的事实事件
    ├── usage.json                # 每 Invocation 的不可变 UsageRecord
    ├── budget-ledger.json        # reserve / settle / release 流水
    ├── storage-index.json        # 路径基准、权威、live layout 与便携证据位置
    ├── logs/
    │   ├── runtime.ndjson        # Run 级 mock 诊断，不替代审计 Event
    │   └── <step>/<attempt>/
    │       ├── diagnostic.ndjson # 结构化级别/channel/关联身份
    │       ├── stdout.log        # channel；不自动等于 info
    │       └── stderr.log        # channel；不自动等于 error
    ├── artifacts/*               # Mock Artifact 内容；媒体使用 *.mock.json envelope
    └── definition/               # Process/Registry/Profile 的冻结快照与 digest
```

`process.json` 与 `tool_registry.json` 都声明 I/O 和 `side_effect_class`，不是为了维护两份真相：前者是本流程调用投影，后者是版本化执行能力。构建前的 drift check 要求二者一致。HTML 只是展示投影；JSON/NDJSON 与 Tool 脚本才是可独立验证的材料。

便携 demo 的 `runs/<run_id>` 同时充当 `run_bundle`；`storage-index.json` 把这里真实存在的 mock 证据与 live Workspace 的目标 locator 分开标注。live 布局不另建 `tasks/` 或统一 `artifacts/` 根，而是保留 OpenCrew 的 `<workspace>/tool_use_sessions/<run_id>/S{index}_{tool}/{Working,Output,Report,Prompt}`，只在其下增量加入 `Attempts/` 与 `Output/.staging/`。因此 demo 能验证目标目录契约，但不会改写或迁移 OpenCrew 现有目录。

## 3. 两次 Run 的验收结果

| 场景 | Run 1 | Run 2（supersedes Run 1） | Step | Run 2 Artifact | Run 2 fanout item | Run 2 Usage | Run 2 estimated provider cost |
|---|---|---|---:|---:|---:|---:|---:|
| 消费贷 | `rejected_with_notice` | `approved_and_disbursed` | 12 | 11 | 0 | 1 | US$0.009 |
| 银行报告 | `data_remediation_requested` | `report_distributed` | 15 | 13 | 0 | 1 | US$0.021 |
| 尽调 | `supplement_requested` | `report_issued` | 10 | 11 | 4 | 6 | US$0.111 |
| AI 视频 | `qa_revision_required` | `delivered` | 16 | 24 | 15 | 17 | US$1.637 |

两次 Run 均保留旧记录，不原地重置 Step。Run 2 的 `supersedes_run_id`、新 `input_revision_hash` 和冻结 definition digest 共同说明“哪次修订采用了什么契约”。拒绝、补件、QA 返修都是完整业务 outcome，Run 可以正常 completed；不激活的另一条业务分支被关闭为 `skipped`。

## 4. Mock 的诚实边界

- `tools.py` 不访问模型、provider、银行、征信、数仓、数据室或发布渠道；固定输入产生固定输出，便于重建与契约回归。
- Mock AI 仍产生 Profile snapshot、Model Invocation、token/媒体单位、estimated cost 与预算流水；这验证治理路径，不代表真实模型质量或价格。
- `.wav/.png/.mp4` 等产物保存为带 `mock=true`、目标 media type 和业务 binding 的 JSON envelope，文件名为 `*.mock.json`；不会用文本文件冒充可播放媒体。
- Demo 只把 `process.artifact_contracts` 中经 finalize 发布的输出视为 canonical Artifact：Tool 只给业务 payload，Runtime 自动生成版本 `artifact_id` 和内容 `sha256`；派生摘要存一次在 producer Attempt（fanout 子 Attempt 同理），Artifact 以 `attempt_id` 关联。Run 级 `input_revision_hash` 不再复制到每条 Artifact；Working、日志、Prompt、staging 和 SessionOutput 投影明确排除。
- Runtime 串行物化确定性结果；Process 中的并行、资源和 fanout 契约被校验并逐项记录，但 demo 不声称实现了分布式 scheduler、原子 claim / lease / fencing、真实并发、进程崩溃恢复或 provider reconciliation。
- 页面展示的 `full/through_step/from_step/only_step` 是目标范围语义；Mock Runtime 本轮只物化完整 canonical Run，不执行局部命令。范围卡不能被解读成通用 scope compiler、hash-gated reuse、diagnostic 发布隔离或 Command CAS 已实现。
- `events.ndjson` 是数据库审计事件的便携投影；`logs/**/*.ndjson|stdout.log|stderr.log` 是确定性 mock 诊断。两者不能互相替代，Usage/Cost 也不能从日志文本反推。OpenCrew 当前通用 runner 有 Step `Report/` 目录但不等于已持久所有 stdout/stderr；页面对此按 `[implemented]`/`[proposed]` 明示。
- 视频创意 Agent 在一个 Step Attempt 内只建立一条 Agent Execution（`0..1`），其两条 Model Invocation 共用 `agent_execution_id` 并标记 turn 1/2；纯推理 Agent 的 Tool Call 可以为 0。这用于展示有界内循环及逐调用计费，仍不是 Process 回边。
- 金额均为 decimal string。`estimated` 不冒充 invoice-final；customer/internal charge 不覆盖 provider cost，也不映射成 OpenCrew 当前不存在的结算列。

## 5. 重建、契约测试与浏览器验证

从仓库根目录执行：

```bash
# 重建四套 Run 记录和 HTML
backend/.venv/bin/python docs/WorkflowSpec/demos/build_all.py

# Schema、图语义、跨文件血缘、哈希、预算、修订链、确定性与 HTML 回归
backend/.venv/bin/python -m pytest docs/WorkflowSpec/schema -q

# 真实 Chromium：1440×1000 + 390×844；检查综述、Demo 入口与四个 Demo，并生成截图和 summary.json
# 依赖 frontend/package.json 的 Playwright，页面本身仍无任何依赖
node docs/WorkflowSpec/demos/verify_browser.mjs /tmp/flowspec-visual-check
```

Chromium 对综述页、Demo 入口和四个 Demo 验证双受众内容、文档/Demo 入口、两次 Run 切换、六个标签、Step 详情、存储/日志投影、决策层 TL;DR、Agent 多轮归组、四种范围语义、Step 编号、fork/join 标记，以及**渲染箭头数必须逐条等于 Process 声明依赖数**；Artifact 视图还检查 canonical 范围说明、每行能解析 producer Attempt/Fanout Item 的输入摘要、Artifact 不重复 Run 级输入 hash，以及手机横向阅读提示。视觉层同时检查控制台错误、桌面/窄屏溢出、全站浅色文档主题、标题字号上限、正文/说明/术语字号下限、手机依赖清单、Stage 布局和 sticky 导航定位。具体测试数、截图数与 violation 以当次命令输出和 `summary.json` 为准，避免文档中的静态数字随用例增长而失真。

## 6. Demo 反推的 v0.4 修订

1. **区分 illustrative 与 executable**：概念骨架只需证明形状；可执行定义必须冻结 Tool Registry、Context/Artifact Schema、分支闭包、完成结果和 Run 预算。
2. **Step 不等于一次 Tool Call**：guard 可让 Step 不执行，Confirm 会创建 Human Task 并等待，fanout/Agent 的一次 Step Attempt 可含多个子项或 Model Invocation。
3. **分支闭包先于完成判断**：guard 为假或依赖已不可能满足的后代递归变为 `skipped`；未解释的 pending 是定义死锁，不是成功。
4. **完成采用 exactly-one outcome**：拒绝、补件、返修、发布都可成为正常业务结果；恰好一个 guard、终点 Step 和必需 Artifact 必须同时成立。
5. **Human Gate 是 dispatch 后的 Work Item**：先创建工作项，再进入 `waiting(user)`；决定必须绑定 actor、role、reason、输入哈希和 expected revision。
6. **fanout/fan-in 必须有稳定 key**：子项不能用数组位置或临时文件名做身份；并发上限、失败阈值、确定性 reduce 与输出 binding 都进入契约。
7. **定义和政策随 Run 冻结**：Process、Tool Registry、AI Profile 都记录版本与 digest，恢复/审计不能悄悄采用“最新配置”。
8. **计费操作与观察记录分开幂等**：operation key 防重复执行/收费，observation key 允许不可变纠错；price/tariff 估价绑定 snapshot，预算按 reserve/settle/release 记账。
9. **路径基准显式、Artifact 元数据最小充分**：session workspace、Run root、Step private 和 Artifact path 不再靠相对路径猜测；只有 canonical output 经 finalize 自动获得版本 ID/内容 hash，并引用 producer Attempt 的单份输入摘要。临时文件不强造业务 binding。
10. **用户范围是图查询，不是序号切片**：`through_step` 取目标前置闭包并在 canonical Run 上等待；`from_step` 取所选节点与受影响后继，复用由 hash/schema/binding/definition 决定；`only_step` 默认 diagnostic。
11. **流程图必须由定义投影**：页面连线从 `depends_on` 生成并与声明边数做浏览器回归；编号只服务阅读，fork/join 和 guard 必须显性，避免一张手画图与机器定义漂移。
12. **日志和存储必须声明权威与位置**：Event、Usage/Cost、诊断和服务日志分平面；每个 locator 带 base，Attempt 临时文件隔离，Artifact 只经 finalize 发布；兼容 OpenCrew 现有主目录而非另起炉灶。

## 7. 状态与后续接入 Gate

这批 demo 已把目标契约从“文档示意”提升为可重建、可校验、可展示的参考实现，但仍全部标为 `[proposed]`。团队在把某项能力标为 OpenCrew `[implemented]` 前，至少应满足：真实模型/存储接入；迁移与回滚策略；并发/崩溃/ambiguous response 测试；权限与数据政策 enforcement；生产监控；以及同一组 Schema/语义/浏览器回归。

建设顺序与退出条件见 [11·6](./11_FourScenarioValidation.md)。现行代码的精确映射与“列存在不等于值已写”边界见 [07](./07_ImplementationBinding.md) 和 [10·G](./10_AI_ModelAndAgent_Profile.md)。
