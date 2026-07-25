# 08 · 成熟体系对照与借鉴（Prior Art）

> 本规范不是凭空发明——它站在 Control-M（企业批调度四十年积累）与现代编排引擎（Airflow / Temporal / AWS Step Functions / Argo / BPMN-Camunda）的肩上。本章给出**术语对照**、**我们在每个分歧点取的立场**、以及**具体借了什么**。所有事实性论断附官方出处。

> **复核基线：2026-07-24。** 版本敏感项按当日 Airflow stable 3.3 文档校准；未来升级规范时应重新核对，而不是把本表当作永久不变的产品能力清单。

## 1. 术语对照总表

| FlowSpec | Control-M | Airflow | Temporal | Step Functions | Argo | BPMN/Camunda |
|---|---|---|---|---|---|---|
| Process | SMART Folder | DAG | Workflow Def | State Machine(ASL) | Workflow(Template) | Process |
| Run（一次执行） | Order | DAG Run | Workflow Execution | Execution | Workflow 实例 | Process Instance |
| Step Attempt（技术尝试，≠Run） | Job rerun 计数 | try_number | Activity Attempt | Retry | retry | Job retries |
| Step | Job | Task/Operator | Activity | Task state | Template/step | Task/ServiceTask |
| Tool | Job Type | Operator 类 | Activity 实现 | Resource | Container/Script tpl | Job Worker |
| Variable/Context | AutoEdit 变量 | XCom | 参数/返回 | State I/O + Assign | parameters | Process Variables |
| Workspace/Artifact | Agent 工作目录+OUTPUT | （无原生） | payload（无原生） | （无原生→S3） | **Artifacts+仓库** | （无原生） |
| Condition | In/Out Condition(Event) | DAG 边 | 代码控制流 | Next/Choice | dependencies | Sequence flow+Gateway |
| Resource | Control(锁)+Quantitative(计数) | Pool | — | — | **mutex + semaphore**¹ | — |
| State | Wait*/Executing/Ended | Task Instance states | Event History | Execution history | Pod/WF status | 事件日志 |
| 重试/恢复 | On-Do/Cyclic | retries | 持久重试+超时 | Retry/Catch | retryStrategy | Error boundary |
| 人工卡点 | Confirm/Hold-Free | **HITL Operator + `awaiting_input`**⁴ | Signal | .waitForTaskToken | Suspend | **User Task** |
| SLA | BIM/SLA Job | **Deadline Alerts**² | — | — | — | — |

> ¹ Argo 已原生支持 mutex 与 semaphore（含跨 workflow）：<https://argo-workflows.readthedocs.io/en/latest/synchronization/>
> ² 新版 Airflow 提供 Deadline Alerts（不再是空白，但官方文档**截至当前仍标 experimental**）：<https://airflow.apache.org/docs/apache-airflow/stable/howto/deadline-alerts.html>
> ³ 关于"声明式"：Airflow DAG 通常由**执行中的 Python 代码**构造，不能简单归为纯数据式声明——见 <https://airflow.apache.org/docs/apache-airflow/stable/best-practices.html>（影响 P1 的措辞，下表已注）。
> ⁴ Airflow 3.1 引入 HITL Operators；3.3 增加由 scheduler 管理的 `awaiting_input` 状态，等待时不占 worker/triggerer slot。它已不是 Sensor 的近似替代：<https://airflow.apache.org/docs/apache-airflow/stable/tutorial/hitl.html> · <https://airflow.apache.org/docs/apache-airflow-providers-standard/stable/operators/hitl.html>
> ⁵ 默认重试必须比较同一执行层：Temporal **Activity** 默认自动重试，而 Workflow Execution 默认不重试；Argo `retryPolicy` 通常默认 `OnFailure`，但 3.5+ 在提供 `expression` 且省略 policy 时默认 `Always`。来源：<https://docs.temporal.io/encyclopedia/retry-policies> · <https://argo-workflows.readthedocs.io/en/latest/retries/>

## 2. 分歧点与本规范立场

现代引擎在几处根本设计上真实分歧。本规范逐条选边（详见 [00·§3](./00_Overview.md)）：

> 下表立场已并入团队审核裁决（完整版见 [00·§3](./00_Overview.md)）。

| 分歧 | 各家做法 | 本规范立场（含裁决） |
|---|---|---|
| **拓扑：声明式 vs 命令式** | Airflow³/SFN/Argo/BPMN 偏声明式；**Temporal 命令式代码** | **每 Run 的执行计划是声明式 DAG**；完整 `session + task` 生命周期可跨 Run 形成返工循环。当前仅实现注册表归一化 + 单步执行 |
| **持久化：元数据 vs 事件溯源** | Airflow 元数据 DB、SFN 执行历史；**Temporal/Zeebe 事件溯源+重放** | **物化状态 + append-only 审计事件**（不要求重放）；transactional outbox 为 `[proposed]`。~~"可变文件天然可审计"~~ 撤回 |
| **确定性约束** | 仅 **Temporal 强制**；余者无 | **不约束确定性，要求幂等**（澄清：幂等=去重/重放/对账，非"重算得等价"）；每步声明 `pure/idempotent/reconcilable/non_idempotent` |
| **artifact 一等公民** | 在本章比较对象中，**Argo 对文件 Artifact/仓库的原生契约最直接**；其他体系更多依赖 payload、变量或外部存储集成 | **一等公民**（通过）+ 原子发布/输入哈希/provenance/GC |
| **重试默认姿态** | **Temporal Activity 默认自动重试**（Workflow Execution 默认不重试）；Argo 通常 `OnFailure`、但有 3.5+ `expression` 例外⁵；SFN/Airflow 显式配 | Step 默认 fail-fast；`pure`/`idempotent` 瞬态错误可显式 bounded retry。Run 失败传播另用 `stop_run/continue_independent`，不与 RetryPolicy 混写 |
| **资源治理** | Control-M 锁+计数；Argo mutex+semaphore | **三原语** `mutex/semaphore/rate_limit`（`llm_tokens/min` 是 rate limit 非 semaphore）+ lease/TTL/fencing/公平性 |
| **依赖表达** | Control-M 命名条件；余者 typed/图 | 流程内 **typed dependency 为主**；跨流程/外部事件才用命名 signal |
| **人工卡点** | **BPMN User Task、Airflow HITL 是一等交互任务**；Temporal Signal、SFN task token、Argo Suspend 提供耐久挂起原语 | **一等卡点**（roadmap）：除 assignee/权限/超时升级外，还要求输入基线哈希、稳定 Human Task 身份、revision/CAS、防重复提交和完整审计 |
| **业务实例身份** | 各家多**无**独立层 | **session+task 层**（保留为主词）；**语义上始终区分** session（文件/身份）与 task（业务记录）两种职责，**物理可分表可合并、但不得混淆职责**——OpenCrew 以 1:1 分表实现 |

## 3. 从 Control-M 借了什么（企业批调度的沉淀）

Control-M 在大规模生产批处理上积累最深，以下机制本规范直接借鉴：

1. **命名条件池作为解耦事件总线**（[06·C]）——生产者 add、消费者 wait，双方不直接引用；依赖是带日期的命名数据，改接线无需动两端。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Events.htm>
2. **独立资源原语**（[06·D]）——Control-M 分锁（互斥/正确性）与计数（限流/吞吐）两种；本规范据团队审核**进一步细分为三**：`mutex`/`semaphore`（并发数）/`rate_limit`（单位时间量），因为 `llm_tokens/min` 是速率而非并发。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Resource_Pools.htm>
3. **文件夹继承**（[02·A3]）——日程/条件/选型在容器上定义一次、子步骤继承，含 `USE_PARENT` 转义。来源：<https://docs.bmc.com/docs/automation-api/9191/folder-869560598.html>
4. **前置条件是一等、可组合的谓词集**（[06·B]）——事件/资源/日程/确认/时间窗统一求值，而非各写各的特例。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Job_prerequisites.htm>
5. **失败动作声明化（On-Do / If-Actions）**（[06·E2]）——`当<触发> 则<动作>`，把通知/阻断/清理从工具代码里剥出来；自动重试仍只有 RetryPolicy 一个入口。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Job_actions.htm>
6. **可观测的 wait-states + Waiting Info**（[04·C1] [06·F2]）——Wait Condition/Resource/Host/User 各是显式状态，并能查出"到底在等哪个"。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Monitoring.htm>
7. **SLA 叠加在关键路径、基于历史统计预测告警**（[06·G]）——业务截止与单步超时分离，预测性而非事后。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/SLA_Management_Job_parameters.htm>
8. **变量作用域阶梯**（[04·A3]）——Local→Folder→Named-Pool→Global，最小权限的跨步数据共享。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Variables.htm>
9. **人工确认与 Hold/Free**（[02·A4]）——Confirm 是设计好的审批检查点，Hold/Free 是运维暂停。来源：<https://documents.bmc.com/supportu/controlm-saas/en-US/Documentation/Job_management.htm>

> 术语注：Control-M SaaS 已改名——In/Out Conditions → **Events**；Control/Quantitative Resources → **Lock Resources / Resource Pools**。本规范沿用经典语义。

## 4. 从现代引擎借了什么（可标准化的共识原语）

五大引擎独立收敛到相同抽象的地方，就是可放心标准化的原语：

1. **定义 vs 实例两层**（本规范 Process vs Run）——全部五家都区分可复用定义与运行实例。
2. **命名的原子工作单元**（Step）——Task/Operator、Activity、Task state、Template、Service Task，都把叶子步骤当作重试/失败边界。
3. **步骤间显式传可序列化数据**（Context/Variables）——XCom / 参数返回 / State I/O / parameters / process variables 的共识。
4. **指数退避重试四参数**（[06·E1]）——SFN(`IntervalSeconds/BackoffRate/MaxAttempts`)、Temporal(Initial Interval/Backoff Coefficient/Max Attempts)、Argo(`limit/backoff`)、Airflow(`retries`) 高度一致，最可标准化；但各家默认是否重试并不一致，所以 FlowSpec 仍要求显式 bounded policy。来源：<https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html> · <https://docs.temporal.io/encyclopedia/retry-policies> · <https://argo-workflows.readthedocs.io/en/latest/retries/>
5. **catch-and-route 错误处理**（[06·E2]）——SFN `Catch`→fallback 与 BPMN error boundary→备用路径是同一抽象。来源：<https://docs.camunda.io/docs/components/modeler/bpmn/error-events/>
6. **挂起等外部/人工输入**（[03·A6] suspend/confirm）——SFN `.waitForTaskToken`、BPMN User Task、Temporal Signal、Argo Suspend、Airflow HITL 都能"durable 等外界"；Airflow 已明确把等待移出 worker/triggerer slot。FlowSpec 在此共识上进一步固定输入基线、角色授权、CAS 与审计字段。来源：<https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html> · <https://airflow.apache.org/docs/apache-airflow/stable/tutorial/hitl.html>
7. **超时+心跳护栏**（[04·D3] [06·E3]）——SFN 与 Temporal 独立收敛到 timeout+heartbeat。来源：<https://docs.temporal.io/encyclopedia/retry-policies>
8. **步骤幂等是普遍契约**（[00·P3] [03·A4]）——Temporal「recommend it be idempotent」、Zeebe at-least-once、SFN/Argo 会重跑：没有引擎保证 exactly-once 副作用，规范把幂等设为作者义务。来源：<https://docs.temporal.io/activities> · <https://docs.camunda.io/docs/components/zeebe/technical-concepts/architecture/>
9. **一等 artifact + 可插拔仓库**（[05]）——Argo 的 artifacts（打包 tar+gzip，存 S3/GCS/MinIO）是媒体/数据管线的正确模型。来源：<https://argo-workflows.readthedocs.io/en/latest/walk-through/artifacts/>

## 5. 一句话总结

> **FlowSpec ≈ Control-M 的"依赖/资源/恢复/SLA/可观测"运维成熟度 + Argo 的"一等 artifact" + Temporal/BPMN 的"幂等步骤与人工卡点"共识 + OpenCrew 实证的"强类型上下文 + 白名单写入 + 输入哈希防御"，并把通用 Checkpoint 恢复明确留在 `[proposed]`，形成去业务化后的最小可复用内核。**

各引擎官方文档入口（核心概念页）：
- Control-M Automation API: <https://docs.bmc.com/docs/automation-api/9192/>
- Airflow Core Concepts: <https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/>
- Temporal: <https://docs.temporal.io/workflows>
- AWS Step Functions: <https://docs.aws.amazon.com/step-functions/latest/dg/concepts-statemachines.html>
- Argo Workflows: <https://argo-workflows.readthedocs.io/en/latest/workflow-concepts/>
- Camunda/BPMN: <https://docs.camunda.io/docs/components/modeler/bpmn/bpmn-primer/>
