# 映射示例② · 银行数据挖掘与报告生成

> **状态 `[proposed]` · 概念压力测试**：本例已通过目标 Process Schema + 语义 linter；资源池、条件性人工放行、SLA overlay、`when/skipped` 等仍未在 OpenCrew 通用 runner 实现。

> 配套可执行验证：[银行风险报告 demo](../demos/bank-risk-report/index.html)（独立 HTML + Process/Tool Registry + Mock Tools + 双 Run 记录）。本页保留专业边界与完整概念讨论，demo 展示物化契约和 UI。

> 目的：验证 FlowSpec 能表达一条**有数据截止点、总账/控制总额对账、质量例外、指标血缘、叙述审阅和受控发布**的月度零售信贷风险报告流程。它可生成管理报告及监管报送前置数据包；正式监管申报仍需叠加司法辖区专属规则和签署要求。

## 0. 业务定义与专业边界

**报告产品**：某法人实体、业务线和 as-of date 的月度零售信贷组合风险包，包含余额/逾期/违约/迁徙/损失等冻结指标、环比与同比差异、数据质量例外、方法与口径、叙述报告及发布清单。

**主要角色**：数据 owner/steward、数仓平台、风险分析师、模型 owner、财务/总账控制人、报告编制人、独立复核人与发布批准人。报告 Run 必须绑定 `legal_entity + portfolio + reporting_period + data_cut_id + metric_definition_version`，否则“同一个月报”无法重现。

**完成标准**：所有来源形成不可变快照；关键控制总额与记录数完成对账；DQ 例外被修复或有范围/期限明确的 waiver；指标带 source→transform→metric lineage；LLM 叙述只引用已批准指标；人工批准绑定报告哈希；分发有接收回执。

**AI 边界**：预测/评分模型与生成式 LLM 是两种 Tool。前者输出受模型治理的分数/分群；后者只根据 `metrics.json + variance evidence` 起草叙述，不得重新计算、四舍五入覆盖或创造数字。每个自然语言断言应能关联 metric ID 与期间。

## 1. 领域 → 要素映射

| FlowSpec 要素 | 本域对应 |
|---|---|
| **Process** | `monthly_risk_report_v1`：月度零售信贷风险报告与发布流程 |
| **session + task** | 某月某业务线的一次报告实例：**session**（`risk_report_2026_07_retail`）拥有 Workspace 存中间数据集与报告；**task** 存业务参数（周期/业务线/口径版本） |
| **Run** | 一次跑批；数据修正后重跑 = 新 Run（`Step Attempt` 只表示单步技术重试） |
| **Step** | 多来源冻结取数、总账/控制总额对账、DQ、清洗、特征、模型打分、指标聚合、叙述与附件并行、签署分发 |
| **Tool** | `sql_extract`、`gl_control_extract`、`reconcile_and_dq`、`clean_transform`、`model_score`、`metric_aggregate`、`report_render`(grounded LLM)、`distribute` |
| **Variable/Context** | `extract_row_count`、`dq_passed`、`model_version`、`report_period`、`report_ref` |
| **Artifact** | 冻结的 loan/payment 数据、`GLControlTotals.json`、`Reconciliation.json`、`DQReport.json`、指标/lineage、报告与发布回执 |
| **依赖** | 主要靠 `consumes`（Parquet 数据依赖）；DQ 检查总是产出报告，异常时激活 waiver Gate；无跨流程命名条件 |
| **Resource** | `mutex` `warehouse:{table}`（写目标表互斥）；`semaphore` `gpu`、`warehouse_connections` |
| **Human Gate** | 数据质量未过阈值时的**人工放行**（`dq_check` failed → confirm 才继续） |
| **SLA** | "每月 5 日出报告"叠加在关键路径上 |

## 2. 流程定义（骨架）

> 依赖用 [00·P9](../00_Overview.md) 三分法。DQ “检查执行失败”和“检查成功但业务指标不达标”要分开：前者 Step failed；后者 S2 completed 并产出报告，再以最小 `when` 激活 waiver Gate。

```jsonc
{
  "process_id": "monthly_risk_report_v1", "version": "1.0.0",
  "resource_pools": { "warehouse_connections": { "type":"semaphore", "limit":10 },
                      "gpu": { "type":"semaphore", "limit":4 } },
  // 全局默认 fail-fast（P7）；pure/idempotent 步遇瞬态错误可显式 opt-in bounded retry（成本越高次数越少）。
  "defaults": { "retry": { "policy":"fail_fast" } },
  "stages": ["extract","prepare","model","report"],
  "steps": [
    { "id":"S1_extract", "tool":"sql_extract", "stage":"extract",
      "reads":["report_period","business_line"],
      "resources":[{"kind":"semaphore","pool":"warehouse_connections","amount":2}],
      "side_effect_class":"idempotent", "cost_level":"low",
      "retry":{ "policy":"on_transient", "max_attempts":3, "backoff_seconds":10 }, // 低成本+幂等，显式 opt-in
      "produces":["raw_txns.parquet"], "writes":["extract_row_count"] },

    { "id":"S1b_payment_extract", "tool":"sql_extract_payments", "stage":"extract",
      "reads":["report_period","business_line"],
      "resources":[{"kind":"semaphore","pool":"warehouse_connections","amount":2}],
      "side_effect_class":"idempotent", "cost_level":"low",
      "retry":{ "policy":"on_transient", "max_attempts":3, "backoff_seconds":10 },
      "produces":["raw_payments.parquet"] },

    { "id":"S1c_gl_controls", "tool":"gl_control_extract", "stage":"extract",
      "reads":["report_period","business_line"],
      "resources":[{"kind":"semaphore","pool":"warehouse_connections","amount":1}],
      "side_effect_class":"idempotent", "cost_level":"low",
      "produces":["GLControlTotals.json"] },

    { "id":"S2_dq_check", "tool":"reconcile_and_dq", "stage":"prepare",
      "consumes":["raw_txns.parquet","raw_payments.parquet","GLControlTotals.json"],
      "reads":["extract_row_count"], "side_effect_class":"pure",
      "produces":["DQReport.json","Reconciliation.json"],
      "writes":["dq_passed","reconciliation_passed"] },

    // 技术检查已完成但业务阈值未过时，激活受审计 waiver；通过时该 Gate skipped。
    { "id":"S2b_dq_override", "tool":"human_confirm", "type":"confirm", "stage":"prepare",
      "depends_on":[{"step_id":"S2_dq_check","statuses":["completed"]}],
      "when":{"variable":"dq_passed","equals":false}, "side_effect_class":"pure",
      "human_gate":{"type":"confirm","form":"dq_waiver","roles":["data_steward","finance_controller"]} },

    // DQ 通过时 S2b=skipped；异常被批准时 S2b=completed；两者都显式可审计。
    { "id":"S3_clean", "tool":"clean_transform", "stage":"prepare",
      "depends_on":[{"step_id":"S2b_dq_override","statuses":["completed","skipped"]}],
      "consumes":["raw_txns.parquet","raw_payments.parquet","DQReport.json","Reconciliation.json"],
      "resources":[{"kind":"mutex","name":"warehouse:monthly_risk_clean","mode":"exclusive"}],
      "side_effect_class":"idempotent",
      "produces":["cleaned.parquet"] },

    // S4/S5/S6/S7 均 consumes 上游产物 → 数据依赖隐含顺序，无需 depends_on
    { "id":"S4_features", "tool":"feature_build", "stage":"prepare",
      "consumes":["cleaned.parquet"], "side_effect_class":"idempotent",
      "produces":["features.parquet"] },

    { "id":"S5_score", "tool":"model_score", "stage":"model",
      "consumes":["features.parquet"],
      "resources":[{"kind":"semaphore","pool":"gpu","amount":1}],
      "uses_gpu":true, "cost_level":"high", "side_effect_class":"idempotent",
      "retry":{ "policy":"fail_fast" },   // 昂贵 GPU 步骤显式 fail-fast（P7）
      "produces":["scores.parquet"], "writes":["model_version"] },

    { "id":"S6_aggregate", "tool":"aggregate", "stage":"report",
      "consumes":["scores.parquet","DQReport.json","Reconciliation.json"],
      "side_effect_class":"pure", "produces":["metrics.json"] },

    { "id":"S7_report", "tool":"report_render", "stage":"report",
      "consumes":["metrics.json","DQReport.json","Reconciliation.json"],
      "uses_llm":true, "cost_level":"medium",
      "type":"model", "ai_profile_ref":"ai://grounded-risk-narrative/v1",
      "side_effect_class":"idempotent",
      "produces":["RiskReport.pdf"], "writes":["report_ref"] },

    // 两份报告并行生成，分发必须等两者完成（多对一 AND-join）
    { "id":"S7b_compliance_appendix", "tool":"compliance_appendix", "stage":"report",
      "consumes":["metrics.json"], "side_effect_class":"pure",
      "produces":["ComplianceAppendix.pdf"] },

    { "id":"S8_publish_confirm", "tool":"human_confirm", "type":"confirm", "stage":"report",
      "depends_on":[{"step_id":"S7_report","statuses":["completed"]},
                    {"step_id":"S7b_compliance_appendix","statuses":["completed"]}],
      "consumes":["RiskReport.pdf","ComplianceAppendix.pdf"],
      "reads":["report_ref"], "side_effect_class":"pure",
      "human_gate":{"type":"confirm","form":"report_publish_review",
                    "roles":["report_owner","independent_reviewer"]} },

    { "id":"S9_distribute", "tool":"distribute", "stage":"report",
      "depends_on":[{"step_id":"S8_publish_confirm","statuses":["completed"]}],
      "consumes":["RiskReport.pdf","ComplianceAppendix.pdf"],
      "reads":["report_ref"], "side_effect_class":"reconcilable" }
  ],
  // [proposed] P10：critical_path 应从 DAG+历史耗时自动推导，此处手列仅示意
  "sla": [ { "service":"月报时效", "critical_path_override":["S1_extract","S2_dq_check","S3_clean","S4_features","S5_score","S6_aggregate","S7_report","S8_publish_confirm","S9_distribute"],
             "complete_in_seconds":432000, "priority":1,
             "on_breach":[{"do":"notify","target":"reporting_team"}] } ]
}
```

## 3. 这个域怎么用到了规范的每个关键设计

- **断点续跑是刚需**（[06·E4]）：跑批常在中途失败（数据源抖动、GPU 抢占）。因状态落 `State.json`、产物落 Parquet + OutputManifest，重跑时 S1–S4 若已完成直接跳过，从 S5 续跑——省下昂贵的重复取数与清洗。
- **DQ 卡点是“业务例外，不是技术失败”**（[02·A4]）：S2 若没跑成才 failed；若跑成但阈值不达标，仍 completed 并写 `dq_passed=false` 与报告。S2b 因 `when` 激活，批准后 completed；指标正常则 skipped。S3 只接受这两个显式终态，避免把 retry、waiver 和状态改绿混成一件事。
- **非单链条汇合**：S1/S1b/S1c 是三个并行数据根，S2 通过三份 Artifact 汇合；S7 风险报告与 S7b 合规附件并行，S8 以两个 `depends_on` 做 AND-join 并完成人工发布签署，S9 才分发。
- **大文件全走 Artifact 引用**（[05·A4]）：数据集是 Parquet 文件，变量里只存 `*_ref` 与行数/版本等元数据，绝不把数据塞进 Context——这正是 Airflow XCom/SFN State I/O 扛不住、需要一等 artifact 的场景。
- **数值与叙述分权**（[10](../10_AI_ModelAndAgent_Profile.md)）：S5 是版本化统计/ML 打分，S6 形成权威 metrics，S7 的 LLM 只能基于 metric ID 起草文字。输出校验须拒绝未引用数字，人工修文走受审计版本而非直接覆盖 `metrics.json`。
- **资源原语**（[06·D]）：仓库连接数、GPU 用 **`semaphore`**（限并发数，别把数仓/显卡打爆）；写目标表用 **`mutex`** 互斥。
- **重试取向按步区分**（[06·E1]）：取数在瞬态错误下显式 bounded retry；DQ 业务不通过走 `block`/人工放行，不伪装成技术重试；模型打分昂贵默认不静默重试。
- **SLA overlay**（[06·G]）："每月 5 日出报告"是服务承诺，基于历史耗时预测性告警，与单步超时分离。

## 4. 与 Control-M 的贴合度

这个域几乎是 Control-M 的经典用例，能一一对上：SMART Folder=Process、Job=Step、In/Out Condition=命名条件、Quantitative Resource=计数池、Control Resource=写表锁、BIM=SLA、Cyclic/On-Do=重试恢复。**FlowSpec 在这里等于把 Control-M 的批调度能力，用同一套要素与更现代的 artifact/上下文模型重述了一遍**——同时又和视频生成、贷款审批共享完全相同的要素词汇。

> **循环边界**：模型指标不达标后的调参/重训属于业务输入修订，v0.4 创建带新 `input_revision_hash` 的 Run，并用 `supersedes_run_id` 关联旧 Run；不在本 DAG 内画“评估 → 重训”的回边。

## 5. 生产级控制清单

- 报告期、时区、业务日历、late-arriving data、重述/restatement 与重开账期政策显式化；数据截止后到达的记录不能无痕混入已发布版本。
- 每个 extract 保存 query/template version、source snapshot/partition、水位、行数和 hash；清洗与指标代码也有版本与可回放参数。
- DQ waiver 必须指出受影响指标、materiality、补救 owner、到期日和批准角色；“人工放行”不等于把 DQ 状态改成通过。
- 发布包同时包含报告、机器可读指标、数据字典、异常/waiver、lineage 与签署记录；分发 Tool 以 report version + recipient 为去重键，并保留撤回/更正版链。
