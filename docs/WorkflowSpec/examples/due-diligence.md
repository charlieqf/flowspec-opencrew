# 映射示例③ · 大规模尽调材料自动处理

> **状态 `[proposed]` · 概念压力测试**：本例已通过目标 Process Schema + 语义 linter；但 `fanout`、glob consumes、逐项 checkpoint/scoped rerun、资源池、通用 `confirm` 等仍是 `[proposed]/[roadmap 🆕]`，OpenCrew 通用 runner 尚不能执行。

> 配套可执行验证：[交易尽调 demo](../demos/due-diligence/index.html)（独立 HTML + Process/Tool Registry + Mock Tools + 双 Run 记录）。本页保留专业边界与大规模设想，demo 以 4 份样例文档物化 stable key fanout/fan-in、逐项 Usage 和证据绑定。

> 目的：验证 FlowSpec 能表达一条**虚拟数据室摄入、权限/保密边界、海量抽取、跨来源核验、证据化风险发现、专业人员复核与报告版本化**的买方交易尽调流程。

## 0. 业务定义与专业边界

**业务产品**：对一个拟交易标的完成法律与财务文档的初步尽调工作包。范围由 engagement/scope matrix 决定，例如公司治理与股权、重大合同、融资与担保、诉讼/监管、知识产权、员工事项，以及历史财务、营运资金、债务/类债务和收入质量。税务、网络安全、环境等可作为独立子流程接入，不假装一个通用 LLM 能给出全部专业结论。

**主要角色**：交易负责人、法律/财务 workstream lead、分析员、数据室管理员、信息安全/隐私人员、外部顾问与 QA reviewer。文档访问受 matter、workstream、保密墙、legal privilege 与地域/保留政策约束。

**完成标准**：收到材料有不可变 inventory、来源与权限标签；每个抽取事实关联 doc ID、页码/段落与原文证据；跨文档冲突和缺件显式；重要性/严重性由规则+专业人员确认；报告区分事实、分析、假设与未解决请求；补件后形成可比较的新 Run。

**AI 边界**：OCR/LLM 做分类、字段/条款抽取、候选冲突和报告草稿；不能给出最终法律意见、审计鉴证或交易建议。Prompt injection 可能来自上传文档，文档文字始终作为不可信数据；Agent 无权读取其他 matter 或直接修改 finding disposition。

## 1. 领域 → 要素映射

| FlowSpec 要素 | 本域对应 |
|---|---|
| **Process** | `due_diligence_v1`：尽调材料自动处理流程 |
| **session + task** | 一个尽调标的：**session**（`dd_acme_2026`）拥有 Workspace 存放全部原始文档、抽取结果、复核意见、尽调报告；**task** 存标的元信息与尽调范围配置 |
| **Run** | 一次处理；补充材料后追加 = 新 Run（`Step Attempt` 只表示单步技术重试） |
| **Step（两级）** | 流程级：摄入→分类→（扇出抽取）→交叉校验→风险标记→人工复核→报告；文档级：每份文档一个抽取子步骤（fan-out） |
| **Tool** | `ingest_dedup`、`classify_route`、`extract_entities`(LLM/OCR)、`cross_validate`、`risk_flagger`、`human_review`、`report_compose`(LLM) |
| **Variable/Context** | `doc_count`、`classified_count`、`extracted_count`、`legal_conflicts_found`、`financial_conflicts_found`、`risk_flags`、`report_ref` |
| **Artifact** | `DocInventory.json`、每文档 `Extract_{docid}.json`、`ExtractIndex.json`、`Conflicts.json`、`RiskFindings.json`、`DDReport.pdf` |
| **依赖** | 主要靠 `consumes`（数据）；扇出 fan-in 与人工卡点用 `depends_on`（S4/S7/S8）；无跨流程命名条件 |
| **Resource** | `rate_limit` `llm_tokens`（tokens/分钟）；`semaphore` `ocr_workers`、`gpu`；`mutex` `report:{session}` |
| **Human Gate** | 冲突/高风险项的**人工复核**（`human_review`：只对被标记项要人裁决） |
| **State（扇出可观测）** | 抽取阶段 `extracted_count/doc_count` 与逐文档状态；单文档失败不丢失已完成子项，但默认阻断 fan-in，待修复或受审计的 exception disposition |
| **SLA** | "尽调 T+10 出初稿"叠加在关键路径 |

## 2. 流程定义（含扇出，骨架）

> 依赖用 [00·P9](../00_Overview.md) 三分法。扇出的 fan-in 用 `depends_on`；本例默认 S3 只有在全部子项完成后才 completed。未来若允许隔离，必须有显式 exception disposition 与阈值，不能把缺失抽取静默当成功。

```jsonc
{
  "process_id": "due_diligence_v1", "version": "1.0.0",
  "resource_pools": { "llm_tokens": { "type":"rate_limit", "per_minute":200000 },
                      "ocr_workers": { "type":"semaphore", "limit":16 },
                      "gpu": { "type":"semaphore", "limit":4 } },
  "defaults": { "retry": { "policy":"fail_fast" } },  // 全局 fail-fast；pure/idempotent 步遇瞬态错误可 opt-in
  "stages": ["ingest","extract","reconcile","review","report"],
  "steps": [
    { "id":"S1_ingest", "tool":"ingest_dedup", "stage":"ingest",
      "reads":["source_bundle_ref"], "side_effect_class":"idempotent",
      "produces":["DocInventory.json"], "writes":["doc_count"] },

    { "id":"S2_classify", "tool":"classify_route", "stage":"ingest",
      "consumes":["DocInventory.json"], "type":"model", "ai_profile_ref":"ai://dd-document-classifier/v1",
      "uses_llm":true, "side_effect_class":"idempotent",
      "produces":["ClassifiedInventory.json"], "writes":["classified_count"] },

    // —— 扇出：对清单里每份文档实例化一个抽取子步骤；consumes 覆盖对 S2 的顺序依赖 ——
    { "id":"S3_extract", "tool":"extract_entities", "stage":"extract",
      "consumes":["ClassifiedInventory.json"],
      "type":"model", "ai_profile_ref":"ai://dd-evidence-extractor/v1",
      "fanout":{ "over":"ClassifiedInventory.json#/documents", "as":"doc",
                 "concurrency_pool":"ocr_workers" },
      "resources":[{"kind":"rate_limit","pool":"llm_tokens","amount":2000},  // tokens/分钟 = 速率
                   {"kind":"semaphore","pool":"ocr_workers","amount":1}],    // 并发数
      "uses_llm":true, "cost_level":"medium", "side_effect_class":"idempotent",
      "produces":["Extract_{doc.id}.json"],
      "retry":{"policy":"on_transient","max_attempts":2},             // 自动重试只有这一处入口
      "on_error":[{"when":"retry_exhausted","do":"block","detail":"失败项转人工"}] },
      // S3 达 completed = 全部子项完成；失败子项保留 checkpoint，parent 默认 blocked。

    { "id":"S4_index", "tool":"extract_index", "stage":"extract",
      "depends_on":[{"step_id":"S3_extract","statuses":["completed"]}],  // fan-in
      "consumes":["Extract_*.json"], "side_effect_class":"pure",
      "produces":["ExtractIndex.json"], "writes":["extracted_count"] },

    // 两条校验链并行，S6 通过消费两份产物做多对一汇合
    { "id":"S5a_legal_validate", "tool":"legal_cross_validate", "stage":"reconcile",
      "consumes":["ExtractIndex.json"], "side_effect_class":"pure",
      "produces":["LegalConflicts.json"], "writes":["legal_conflicts_found"] },

    { "id":"S5b_financial_validate", "tool":"financial_cross_validate", "stage":"reconcile",
      "consumes":["ExtractIndex.json"], "side_effect_class":"pure",
      "produces":["FinancialConflicts.json"], "writes":["financial_conflicts_found"] },

    { "id":"S6_risk_flag", "tool":"risk_flagger", "stage":"reconcile",
      "consumes":["LegalConflicts.json","FinancialConflicts.json"],
      "type":"model", "ai_profile_ref":"ai://dd-finding-drafter/v1",
      "uses_llm":true, "side_effect_class":"idempotent",
      "produces":["RiskFindings.json"], "writes":["risk_flags"] },

    { "id":"S7_human_review", "tool":"human_review", "type":"confirm", "stage":"review",
      "depends_on":[{"step_id":"S6_risk_flag","statuses":["completed"]}],
      "consumes":["RiskFindings.json"], "side_effect_class":"pure",
      "human_gate":{"type":"confirm","form":"dd_findings_review","roles":["dd_lead"],
                    "scope":"only_flagged_items","sla_seconds":259200} },

    { "id":"S8_report", "tool":"report_compose", "stage":"report",
      "depends_on":[{"step_id":"S7_human_review","statuses":["completed"]}],
      "consumes":["ExtractIndex.json","RiskFindings.json"],
      "resources":[{"kind":"mutex","name":"report:{session}","mode":"exclusive"}],
      "type":"model", "ai_profile_ref":"ai://dd-report-composer/v1",
      "uses_llm":true, "cost_level":"high", "side_effect_class":"idempotent",
      "produces":["DDReport.pdf"], "writes":["report_ref"] }
  ],
  // [proposed] P10：critical_path 应从 DAG+历史耗时自动推导，此处手列仅示意
  "sla": [ { "service":"尽调初稿T+10", "critical_path_override":["S1_ingest","S2_classify","S3_extract","S4_index","S5a_legal_validate","S6_risk_flag","S7_human_review","S8_report"],
             "complete_in_seconds":864000, "priority":1,
             "on_breach":[{"do":"notify","target":"dd_lead"},{"do":"escalate"}] } ]
}
```

## 3. 这个域独有地压测了规范哪些能力

- **session 内部扇出（fan-out）**：一个尽调标的（session）内部要并行处理上万份文档。规范用 `fanout` 声明逐项实例化，子步骤共享资源限流；S3 完成后 S4 fan-in。该结构目前只完成目标 Schema 表达，子项状态/隔离阈值仍待实现，不把它冒充现行能力。
- **非单链条汇合**：S5a 法务校验与 S5b 财务校验并行；S6 同时消费两份产物形成数据 AND-join，不需要额外 Gateway。
- **资源限流是海量并行的命门**（[06·D]）：LLM tokens/分钟（`rate_limit`）、OCR worker/GPU（`semaphore`）都是硬约束。没有资源治理，扇出会瞬间打爆外部 API。这是本域相对前两个示例更极端的诉求。
- **人工复核只针对被标记项**（[02·A4]）：`human_review` 的 `scope=only_flagged_items`——不是让人看全部一万份文档，而是只裁决冲突/高风险项。人工卡点的**范围**也是契约的一部分。
- **进度可观测到"扇出进度"**（[06·F]）：`extracted_count/doc_count` 通过事件流/快照回传，UI 能显示"已抽取 8,432 / 10,000"，卡住的文档能查 `wait_reason`（等 token 配额还是转人工）。
- **幂等在重跑时救命**（[03·A4] [06·E4]）：补充材料后重跑，已抽取的文档凭 `Extract_{docid}.json` + 内容哈希跳过，只处理新增/失败项——对上万份文档的成本差异巨大。
- **白名单写入 = 可审计**（[04·A4]）：`risk_flags` 只由 S6 写、`report_ref` 只由 S8 写，尽调结论的来源可追溯，满足合规留痕。
- **证据先于结论**（[10](../10_AI_ModelAndAgent_Profile.md)）：S3 的每个事实必须有 source span 与置信度；S6 只生成 finding 草案，S7 专业人员确认 severity/disposition。报告引用 finding/evidence ID，不让 LLM 生成无法回指原文的“结论”。

## 4. 四个示例的横向对照（同一规范，四种形态）

| 维度 | 贷款审批 | 银行数据报告 | 尽调材料处理 | OpenCrew 视频创作 |
|---|---|---|---|---|
| 主导特征 | 规则决定 + 授权 | 数据控制 + 可重述 | 证据化文档扇出 | 多模态生成 + 资产绑定 |
| session 内并行度 | 中（多核验并行） | 中高（来源/附件并行） | **极高（文档/页级）** | 高（对白/镜头 slot） |
| 人工卡点 | 政策例外/高额审批 | DQ waiver + 发布签署 | finding 专业复核 | 方案/成片选择 |
| 资源瓶颈 | 征信/欺诈 API | 数仓/GPU/LLM | LLM token/OCR | 图像/视频 API/GPU |
| AI 的权限上限 | 抽取/解释，不决定 | 叙述，不改指标 | 草拟 finding，不给最终意见 | 创意/生成，不自行发布 |
| 恢复重点 | 放款对账 | cutoff/lineage/restate | checkpoint/局部重抽 | provider 对账/局部重做 |

**结论**：四个场景共同要求的不是更强的表达式语言，而是可校验 Artifact、稳定绑定、并行汇合、人工授权、AI 调用审计、资源/成本治理和跨 Run 修订。四例均作为 `[proposed]` Process 配置接受 Schema + linter 校验；OpenCrew 视频例另含现行实现映射。

> **循环边界**：发现缺件后“发补件请求 → 收到新材料 → 重审”是 `session + task` 生命周期上的回环。收到新材料后创建新 Run，并用 `supersedes_run_id` 关联旧 Run；单次 Run 的文档处理依赖图仍保持 DAG。

## 5. 生产级控制清单

- 摄入阶段做 hash 去重但保留每个来源/目录位置；压缩包、邮件附件、扫描件建立 parent-child lineage；加密/损坏/不支持格式进入 quarantine，不能计入“已完成抽取”。
- 检索、向量索引与 Agent 必须带 matter/workstream ACL；任何跨 matter 命中都按安全事件处理。删除/保留应能级联到派生 OCR、embedding、prompt 和报告引用。
- 文档级 checkpoint 记录完成边界、提取 Schema 与 prompt/model version；变更抽取规则时不得只凭源文件 hash 复用旧结果。
- finding 至少包含 category、事实摘要、evidence refs、severity、materiality、owner、review status、management response 与 resolution；AI 置信度不等于业务 materiality。
