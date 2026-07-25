# 映射示例① · 贷款审批流程

> **状态 `[proposed]` · 概念压力测试**：本示例已通过目标 Process Schema + 语义 linter，但其中 `when/skipped`、`human_gate/confirm`、资源原语、SLA overlay 等构件仍为 `[proposed]/[roadmap 🆕]`，OpenCrew 通用 runner 尚不能直接执行。

> 配套可执行验证：[消费贷审批 demo](../demos/loan-approval/index.html)（独立 HTML + Process/Tool Registry + Mock Tools + 双 Run 记录）。本页保留专业边界与完整概念讨论，demo 展示物化契约和 UI。

> 目的：验证 FlowSpec 能表达一条**以可解释政策、并行外部核验、人工授权与不可重复放款为核心**的消费贷流程。示例覆盖申请至放款指令，但不替代具体司法辖区的信贷、隐私、反洗钱或消费者保护要求。

## 0. 业务定义与专业边界

**业务产品**：个人无担保消费贷款。一次业务实例以 application ID 为身份，从客户提交、身份与材料核验、征信/反欺诈/偿付能力评估，到授信决定、报价接受和放款。联合申请、担保品与贷后管理应建成相邻 Process，不硬塞进此例。

**主要角色**：申请人、信贷运营、欺诈调查员、授权信贷审批人、合规/模型风险、核心银行与征信机构。**职责分离**要求高额或政策例外不能由提交申请的人自批，人工 override 必须记录依据、原值/新值和审批人。

**完成标准**：身份与同意有效；输入快照、征信/收入/负债证据可追溯；版本化政策引擎给出批准/拒绝/转人工及 reason codes；必要的人审完成；客户接受有效报价；放款指令有稳定业务键并与核心账务对账。

**AI 边界**：生成式模型可做文件分类、字段抽取、材料摘要和审批意见草稿，但不得自行计算政策阈值、覆盖来源数字或作最终授信决定。结构化抽取必须带文档/页码证据与置信度；低置信度转人工。

## 1. 领域 → 要素映射

| FlowSpec 要素 | 本域对应 |
|---|---|
| **Process** | `loan_approval_v1`：个人消费贷审批流程定义 |
| **session + task** | 一笔贷款申请：**session**（`loan_2024_00123`）拥有 Workspace 存放申请材料/征信报告/审批意见与事件流；**task**（配对，1:1）存业务记录——申请人信息/额度/选型 |
| **Run** | 一次审批执行；被退回补件后重新提交 = 新 Run（`Step Attempt` 只表示单步技术重试） |
| **Step** | 申请/KYC 校验、材料抽取、征信与反欺诈并行核验、偿付能力与风险评分、政策决策、人工复核、报价接受、放款 |
| **Tool** | `form_validator`、`application_document_extract`(LLM)、`credit_bureau_pull`、`fraud_check`、版本化 scorecard/政策引擎、`human_confirm`、核心银行 API |
| **Variable/Context** | `applicant_valid`、`credit_score`、`fraud_flag`、`risk_score`、`decision`、`approved_amount`、`review_required` |
| **Artifact** | `ValidatedApplication.json`、`ApplicationEvidence.json`、`CreditReport.json`、`RiskAssessment.json`、`ApprovalPlan.json`、`AcceptedOffer.json`、放款/拒绝回执 |
| **依赖** | 流程内用 `depends_on`（如 S7 depends_on S6）+ `consumes`（数据）；本例无跨流程命名条件 |
| **Resource** | `mutex` `ledger:{session}`（放款写账本互斥）；`semaphore` `credit_bureau_api`（征信并发配额） |
| **Human Gate** | 风控官对授信方案的 **Confirm**（>阈值金额必过人审） |
| **State** | 各步 running/completed/failed；`waiting(user)` = 等风控官确认；`waiting(resource)` = 等征信配额 |
| **SLA** | "48 小时出审批结论"叠加在关键路径上 |

## 2. 流程定义（Process Registry 骨架）

> 依赖用 [00·P9](../00_Overview.md) 的三分法：`reads/consumes`=数据依赖、`depends_on`=流程内控制依赖、命名条件只留给跨流程/外部信号（本例无）。

```jsonc
{
  "process_id": "loan_approval_v1", "version": "1.0.0", "title": "个人消费贷审批",
  "resource_pools": { "credit_bureau_api": { "type":"semaphore", "limit":4 } },
  "defaults": { "retry": { "policy": "fail_fast" } },
  "stages": ["intake", "risk", "decision", "disburse"],
  "steps": [
    { "id":"S1_intake_validate", "tool":"form_validator", "stage":"intake",
      "reads":["applicant_id"], "side_effect_class":"pure",
      "produces":["ValidatedApplication.json"], "writes":["applicant_valid"] },

    // 生成式 AI 只把非结构化材料转成带 evidence 的候选字段，不做授信决定。
    { "id":"S1b_document_extract", "tool":"application_document_extract", "type":"model", "stage":"intake",
      "consumes":["ValidatedApplication.json"], "ai_profile_ref":"ai://loan-document-extract/v1",
      "uses_llm":true, "cost_level":"medium", "side_effect_class":"idempotent",
      "produces":["ApplicationEvidence.json"] },

    // S2/S3 并行；都在结构校验及证据抽取完成后启动。
    { "id":"S2_credit_pull", "tool":"credit_bureau_pull", "stage":"risk",
      "depends_on":[{"step_id":"S1b_document_extract","statuses":["completed"]}],
      "resources":[{"kind":"semaphore","pool":"credit_bureau_api","amount":1}],
      "cost_level":"medium", "external_quota":"credit_bureau_api",
      "side_effect_class":"idempotent",
      "retry":{"policy":"on_transient","max_attempts":3,"backoff_seconds":5},
      "produces":["CreditReport.json"], "writes":["credit_score"] },

    { "id":"S3_fraud_check", "tool":"fraud_check", "stage":"risk",
      "depends_on":[{"step_id":"S1b_document_extract","statuses":["completed"]}],
      "side_effect_class":"idempotent",
      "produces":["FraudSignals.json"], "writes":["fraud_flag"] },

    // S4 消费 S2/S3 的产物 → 数据依赖已隐含顺序，无需 depends_on
    { "id":"S4_risk_score", "tool":"versioned_credit_scorecard", "type":"service", "stage":"risk",
      "consumes":["ApplicationEvidence.json","CreditReport.json","FraudSignals.json"],
      "reads":["credit_score","fraud_flag"], "uses_llm":false, "cost_level":"medium",
      "side_effect_class":"pure",
      "produces":["RiskAssessment.json"], "writes":["risk_score"] },

    { "id":"S5_decision", "tool":"credit_policy_engine", "stage":"decision",
      "consumes":["RiskAssessment.json"], "reads":["risk_score"],
      "side_effect_class":"pure",
      "produces":["ApprovalPlan.json"], "writes":["decision","approved_amount","review_required"] },

    // 简单条件分支：只有 review_required=true 才激活人工卡点，否则 S6 直接 skipped
    { "id":"S6_human_review", "tool":"human_confirm", "type":"confirm", "stage":"decision",
      "depends_on":[{"step_id":"S5_decision","statuses":["completed"]}],
      "consumes":["ApprovalPlan.json"], "side_effect_class":"pure",
      "when":{"variable":"review_required","equals":true},
      "human_gate":{"type":"confirm","form":"approval_review","roles":["risk_officer"],
                    "sla_seconds":86400} },

    { "id":"S7_offer_accept", "tool":"offer_and_acceptance", "type":"suspend", "stage":"decision",
      "depends_on":[{"step_id":"S6_human_review","statuses":["completed","skipped"]}],
      "when":{"variable":"decision","equals":"approved"},
      "consumes":["ApprovalPlan.json"], "side_effect_class":"idempotent",
      "produces":["AcceptedOffer.json"], "writes":["offer_accepted_at"] },

    { "id":"S8_disburse", "tool":"disbursement", "type":"service", "stage":"disburse",
      "depends_on":[{"step_id":"S7_offer_accept","statuses":["completed"]}],
      "consumes":["AcceptedOffer.json"],
      "resources":[{"kind":"mutex","name":"ledger:{session}","mode":"exclusive"}],
      "side_effect_class":"reconcilable",
      "produces":["DisbursementReceipt.json"], "writes":["disbursed_at"] },

    { "id":"S9_reject_notice", "tool":"notify_rejection", "stage":"decision",
      "depends_on":[{"step_id":"S5_decision","statuses":["completed"]}],
      "when":{"variable":"decision","equals":"rejected"},
      "side_effect_class":"idempotent", "produces":["RejectionNotice.json"] }
  ],
  // [proposed] P10：critical_path 应从 DAG+历史耗时自动推导，此处手列仅示意
  "sla": [ { "service":"审批时效48h", "critical_path_override":["S1_intake_validate","S1b_document_extract","S2_credit_pull","S4_risk_score","S5_decision","S6_human_review","S7_offer_accept","S8_disburse"],
             "complete_in_seconds":172800, "priority":1,
             "on_breach":[{"do":"notify","target":"risk_manager"},{"do":"escalate"}] } ]
}
```

## 3. 这个域怎么用到了规范的每个关键设计

- **并行依赖**：S2（征信）与 S3（反欺诈）都在 S1b 完成后启动，可并行；S4 同时消费材料证据、征信与欺诈信号，数据依赖形成 AND-join（[02·A2]）。
- **AI 与决定分离**（[10](../10_AI_ModelAndAgent_Profile.md)）：S1b 只抽取带 evidence 的候选事实；S4 是冻结版本的 scorecard/预测模型，S5 是政策引擎。LLM 摘要不能成为放款条件的权威事实。
- **条件分支保持最小**（[02·A2.1] `[proposed]`）：S6 只看 `review_required == true`，S7 只看 `decision == approved`，S9 只看 `decision == rejected`；没有 Gateway 或任意表达式。S6 未激活时为 `skipped`，S7 显式接受 `completed|skipped`，但自身仍以批准 guard 防止拒绝件生成报价。
- **人工/外部等待是一等步骤**（[02·A4] `[proposed]`）：需要人审时 S6 是显式 `confirm`；S7 以 `suspend` 等客户接受/超时回调。未确认或未接受前 S8 不放款，可查、可催办。
- **资源原语各司其职**（[06·D]）：S2 用 **`semaphore`** 限征信 API 并发；S8 用 **`mutex`** 保证本系统不会并发提交放款。
- **重试取向分明**（[06·E1]）：征信查询用稳定 enquiry key，在供应商支持幂等/对账时才做瞬态重试；抽取失败可重算但需单独核算模型调用；放款是 reconcilable，响应未知时查核心账务，绝不盲发第二笔。
- **白名单写入防串改**（[04·A4]）：`decision/approved_amount` 只有 S5 能写，S6–S8 只读——授信金额不可能被下游步骤悄悄改动。
- **SLA overlay**（[06·G]）：48h 承诺叠在关键路径上，基于历史耗时预测超期并升级——与单步超时解耦。
- **可观测**：审批卡在哪一步、等谁确认、等哪个配额，都能从 `wait_reason` 查到（[06·F2]）——对合规审查是刚需。

## 4. 与来源域（视频生成）的同构

| 视频生成 | 贷款审批 | 同一规范要素 |
|---|---|---|
| 分镜方案生成→人确认→执行 | 授信方案生成→风控官确认→放款 | Plan & Confirm 人工卡点 |
| TTS/视频渲染抢 GPU | 征信拉取抢外部 API 配额 | 计数资源池 |
| `selected_scheme` 只由决策步写 | `approved_amount` 只由 S5 写 | 白名单变量所有权 |
| slot 状态驱动进度弹窗 | 审批步骤状态驱动审批看板 | State + Event 流 |

**结论**：两条业务毫不相干，但要素、契约、状态机、可观测机制在这套规范里可复用同一套词汇与结构。本例已通过目标 Schema + 语义 linter；它仍是 `[proposed]` 目标配置，不代表 OpenCrew 现行 runner 可执行。

> **循环边界**：风控退回后“补充材料 → 重新提交 → 重审”是 `session + task` 生命周期上的回环；重新提交创建新 Run，并用 `supersedes_run_id` 指向前次 Run。征信接口的瞬态失败则只增加 S2 的 Step Attempt，不创建依赖回边，也不等同于业务重审。

## 5. 生产级控制清单

- 所有外部报告保存 consent/purpose、查询时间、有效期、供应商 request ID 与原始不可变响应；过期报告不能被新 Run 默默复用。
- 决策同时保存 policy/model version、输入 feature snapshot、reason codes、override 和 adverse-action 文案来源；报告中的解释不能反向改写决定。
- 人工 Gate 要有授权额度、四眼原则、代理审批、撤回/过期与并发版本；批准必须绑定审核过的 `ApprovalPlan.json` 哈希。
- 放款前再次校验客户接受、账户验证、制裁/欺诈状态和批准有效期；`ledger:{session}` 锁只防本系统并发，核心账务的唯一业务键才是最终防重。
