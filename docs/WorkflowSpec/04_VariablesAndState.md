# 04 · 变量与状态（Variables & State）

> 两件事：步骤间**共享什么数据**（变量/上下文），以及每步/每次运行**处于什么状态**（状态机）。立场见 [00·P5](./00_Overview.md)：**强类型 + 作用域阶梯 + 白名单写入**；状态须**可观测到"卡在什么上"**。

---

## 第一部分：变量与上下文

### A. 概念层

#### A1. 变量即上下文载荷

一个 Run 有一份**强类型的共享状态文档**（Context / `Variables`）。它是键值集合，同时充当：
- **参数**：门控步骤（`reads` 校验）、参数化步骤行为（模型选型、路径、模式）。
- **结果通道**：步骤把关键结论写回，供下游读。

**大文件不进变量**（[00·P4]）——变量只放小的、结构化的、需要跨步判断的值（ID、路径引用、开关、计数、选型）。文件走 Workspace/Artifact。

#### A2. 强类型 + 禁止额外字段

变量文档必须有 **Schema**，且**禁止未声明字段**（`extra=forbid`）。好处：拼写错的字段名当场报错而非静默；流程静态校验能验证每步 `reads/writes` 都在 Schema 内；跨团队对齐只需看 Schema。

#### A3. 作用域阶梯（借 Control-M）

变量按可见性分层，默认最小可见：

| 作用域 | 可见范围 | 用途 | 状态 |
|---|---|---|---|
| **Step-local** | 仅本步骤 | 步骤私有中间值（各步私有目录） | `[implemented]` |
| **Run / Context** | 本次运行的所有步骤 | 跨步共享（`Variables.json`，最常用） | `[implemented]` |
| **Named-Pool** | 引用同一命名池的相关运行 | 一组相关运行共享 | `[roadmap 🆕]`（借 Control-M，未实现） |
| **Global** | 整个环境 | 跨流程全局配置 | `[roadmap 🆕]`（未实现） |

规范默认把"共享变量"放在 **Run/Context** 层（现行唯一实现的共享层）。跨运行/全局作用域是路线图，尚未实现。

#### A4. 写入是白名单门控的（最小权限）`[implemented]`

步骤**不能随便改共享变量**。它只能通过返回 `context_patch` 提交变更，且**只能触碰自己在契约里声明 `writes` 拥有的字段**。编排器在合并前校验；越权字段拒绝——且**整个步骤判 failed**（`runner.py:507-523` 捕获 `merge_context_patch` 的 `ValueError` 后把 `ToolResult.status` 改为 `failed`）。

这缓解多步骤写同一状态时的"谁踩了谁"，但要分清哪部分**已实现**：
- **白名单写入 `[implemented]`**：runner 只校验"**当前工具**写的字段是否在它自己的 `writes_session_context` 白名单"（`registry_normalizer.py:305` + `runner.py:507`）。
- **跨工具唯一 owner 静态检查 `[proposed]`**：**当前并不检查**不同工具是否声明了同一字段——"每字段 owner 唯一"是规范目标，尚无静态校验兜底。
- **多写者的 `reducer`/`CAS` `[proposed]`**：确需多写者时的显式合并/比较交换，未实现。

#### A4.1 人工 override 也必须走受控 Patch `[proposed]`

变量查看默认只读；人、运维脚本和管理 UI 都不得直接编辑 `Variables.json`。人工修改必须提交独立 override 命令，至少包含：

```jsonc
{ "command_id":"cmd_...", "idempotency_key":"override:...",
  "session_id":"...", "task_id":"...", "run_id":"...",
  "expected_revision":17, "patch":{"business.review_required":true},
  "actor_id":"user_42", "reason":"风控补录" }
```

Runner/受信任后端须校验 override 字段白名单、权限与 `expected_revision`，以 CAS 方式写入；成功或拒绝都发 append-only 审计事件。成功后必须重新计算输入哈希，级联失效受影响的下游 Artifact/完成态，并重新执行 `when`、变量、产物、资源和人工门控检查。人工 override 不是绕过依赖或强行把步骤改成 completed 的后门。

#### A5. 读取门控——注意真实行为远弱于"全部校验" ⚠️

> **更正（团队审核）**：v0.1 称"所有 `reads` 都会做存在性校验、缺失进 `waiting(variable)`"——**与代码不符**。

- **实际 `[implemented]`**（`runner.py:526-581`）：`check_dependencies` 只对 **4 个特定 token** 特判存在性：`source_video` / `source_video_or_audio` / `task_id` / `opencode_image_model`；**其余 `reads` token 完全不校验**。产物依赖（`consumes_outputs`）也只检查"某上游 tool 有 completed 的 OutputManifest"或"某指定 path 存在"，**不按 artifact 名/哈希校验**。任一缺失 ⇒ 返回 `DependencyCheckResult(status="blocked")`——**没有 `waiting` 这个态**，原因放在 `missing_dependencies[]` 里。
- **目标 `[proposed]`**：把 `reads` 的存在性校验推广到全部声明字段、产物依赖按名+哈希校验、并引入带 `wait_reason` 的一等 `waiting` 状态（区别于 `blocked`）。这是规范方向，尚未实现。

---

### B. 可实现绑定（变量）

#### B1. 变量 Schema（Variables Contract）

> **以 [07·3.A.1](./07_ImplementationBinding.md) 与 [`schema/Variables.schema.json`](./schema/Variables.schema.json) 为准**。这里只对比"现行 vs 目标"。

**`[implemented]` 现行**（`schemas/models.py:17`，`extra=forbid`，28 字段固定白名单）：字段是 `tool_use_session_id / workflow_id / task_id:int / opencrew_session_id:int / workspace_dir / tool_session_root / ...`。**业务字段（`applicant_valid/risk_score/...`）当前塞不进**——会被 `extra=forbid` 拒绝（见反例测试 `schema/examples/Variables.invalid-extra-field.json`）。

**`[proposed]` 目标**：为承载业务字段，引入可扩展的 `business` 子文档或独立业务上下文；示例里"每字段 owner 唯一"的写回是**目标形态**，非现行 Variables 结构。Mutable Variables 还应带单调 `revision`、`updated_at` 与 `writer_id`；并发写以 `expected_revision` 做 CAS，`updated_at` 只用于观测、不能充当并发令牌。

```jsonc
// [proposed] 目标（仅示意，当前模型不接受业务字段）
{ "tool_use_session_id":"tus_...", "task_id":123, "workspace_dir":"...",
  "business": { "applicant_valid": true, "risk_score": 720, "decision": "approved" } }
```

#### B2. 变量补丁与白名单合并 `[implemented]`

> **注意**：工具返回的 `ToolResult.context_patch` 是**纯 patch 字典**（不含 tool_id/step_id）；runner 才把它包成 `SessionContextPatch(tool_id,step_id,patch=...)`（`runner.py:510`）。

```python
# 工具返回的 ToolResult.context_patch —— 纯字典：
context_patch = { "risk_score": 720 }

# runner 侧（真实：runner.py:507-523 + merge_context_patch:583-592）：
wrapped = SessionContextPatch(tool_id="S4_risk_score", step_id="S4", patch=context_patch)
denied = set(wrapped.patch) - set(tool.writes_session_context)   # 越权字段
if denied:
    raise ValueError(...)                                        # → 整个步骤判 failed
merged = {**current_variables, **wrapped.patch, "updated_at": now()}
Variables.model_validate(merged)                                # 重新过 Schema（extra=forbid）
write_json("Variables.json", merged)
```

#### B3. 读依赖校验 —— 真实行为 `[implemented]`

```python
# runner.py:526-544 —— 只有 4 个 token 被特判，其余 reads 不校验；缺失 → blocked（非 waiting）
SPECIAL = {"source_video", "source_video_or_audio", "task_id", "opencode_image_model"}
missing = []
for token in step.reads_session_context:
    if token in SPECIAL and not present(token, variables):
        missing.append(MissingDependency(kind="session_context", required_from=token, ...))
    # 其它 token：无校验
# consumes_outputs：查上游 completed manifest 或指定 path 是否存在（不按 name/hash）
return DependencyCheckResult(status="blocked" if missing else "ready",
                             missing_dependencies=missing)     # 只有 ready|blocked，没有 waiting
```
> `[proposed]` 目标：全字段校验 + 产物按名/哈希校验 + 引入 `waiting(reason)`。见 A5。

---

## 第二部分：状态

### C. 概念层

#### C1. 状态不只是"跑没跑完"，还应包括"卡在什么上"（目标立场）

立场（借 Control-M 的 Waiting Info 教训）：**`blocked` 不该是不透明黑箱**，应能查出到底在等什么。
- **现行 `[implemented]`**：缺依赖时 `check_dependencies` 返回 `blocked` + `missing_dependencies[]`（每项带 `kind/required_from/required_path/suggested_action`）——原因**可查**，但载体是 `DependencyCheckResult`，**不是** State 上的字段，也没有独立的 `waiting` 态。
- **目标 `[proposed]`**：把等待原因提升为 State 的一等 `wait_reason` 字段并区分 `waiting`。

#### C2. 步骤状态机

```
# —— [implemented] 现行 State.status 实际取值 ——
not_started ──▶ running ──┬──▶ completed
                          ├──▶ failed
                          └──▶ blocked          # 依赖未满足（原因在 DependencyCheckResult，非 State 字段）
              running ──(心跳超时)──▶ stale_running ──(恢复)──▶ 重跑 / failed
              (口播子系统：worker 死亡记为 failed + orphaned:true 标志位)

# —— [proposed] 目标增补 ——
not_started ──▶ waiting(step|condition|variable|artifact|resource|user|host|external_callback)
                                                                     # 带 wait_reason 的一等等待态
not_started ──(when=false)──▶ skipped                                # 未激活分支；不调 Tool
running ──(Confirm/Suspend 已创建 work item/callback token)──▶ waiting(user|external_callback)
waiting(user|external_callback) ──(CAS decision/callback)──▶ running ──▶ completed
running ──▶ orphaned                                                 # orphaned 升级为通用状态
```

- **现行**终态：`completed | failed | blocked`；异常存活态 `stale_running`。
- **`orphaned`** 目前仅口播执行子系统作为**标志位**（`failed` + `orphaned:true`），**不是**通用状态机取值——见 [09·L2.2](./09_ProductionLessons.md)。
- **`waiting` + `wait_reason`、`skipped`** 为 `[proposed]`。`skipped` 是成功关闭分支的终态，不等于 `completed`，也不产生 Tool 产物。除 guard false 外，当所有候选上游都已终态且依赖/变量/Artifact 已不可能满足时，`branch_closure=skip_unreachable` 必须递归关闭下游；“目前缺少但生产者仍可能完成”才是 waiting。

#### C3. 运行（Run）级状态

`running → {completed | failed | blocked | cancelled}`；`skipped` 步骤按终态参与闭包但不算执行成功。目标 Run 的 `completed` 不由“所有 Step completed”简单聚合，而由三件事共同决定：无 unresolved Step、`completion.outcomes` 恰好一个匹配、该 outcome 的终点 Step completed 且必需 Artifact valid。拒绝、补件或修订请求因此可以是 `Run.status=completed` 下的不同业务 `outcome`。通用 runner 还可能生成 `completed_with_sync_error`、`failed_with_sync_error`、`blocked_with_sync_error`、`cancelled_with_sync_error`（业务终态已知但投影同步失败）；这是 OpenCrew 现行投影状态，不等于目标 outcome 模型。

#### C4. 执行状态与 Artifact 有效性是两个轴

不能用“文件存在”覆盖执行状态，也不能用一个仍显示 completed 的旧 Run 证明当前 Artifact 有效。规范保留两个正交维度：

| 维度 | 目标枚举 | 回答的问题 |
|---|---|---|
| `execution_status` | `not_started/waiting/running/completed/failed/blocked/skipped/stale_running/orphaned` | 这次 Step/Run 执行到哪了？ |
| `artifact_validity` | `missing/valid/stale/unbound/corrupt` | 当前业务输入下，产物是否存在、完整、绑定正确且可消费？ |

面向用户的颜色/语气只是这两个轴的**产品投影**，不是权威状态机。OpenCrew 现有 `green/white/gray/yellow/red → done/pending/disabled/running/failed` 可继续作为 UI 映射，但规范不规定全域固定的“绿>黄>红>白>灰”优先级。尤其是“文件存在但绑定缺失”必须显式显示 `unbound/needs_repair`，不能标绿或让前端猜；旧输入哈希下的文件是 `stale`，不参与当前状态上色。

---

### D. 可实现绑定（状态）

#### D1. 步骤状态记录（State）

> **以 [07·3.A.3](./07_ImplementationBinding.md) 与 [`schema/State.schema.json`](./schema/State.schema.json) 为准。State.json 在 Step 根目录，不是 `Output/`。**

**`[implemented]` 现行**（`schemas/models.py:86`）——**没有** `waiting/wait_reason/result` 字段：
```jsonc
// <run>/S{index}_{tool}/State.json  （Step 根目录）
{ "schema_version":"1.0", "tool_use_session_id":"tus_...", "attempt_id":7,
  "step_index": 4, "step_id": "S4_risk_score", "tool_id": "risk_score", "tool_name": "…",
  "status": "running",                   // 普通 str；当前观察值见下，不是 Schema 枚举
  "started_at":"", "updated_at":"", "finished_at":"", "heartbeat_at":"…",
  "retry_count": 0, "idempotency_key": "tus_...:S4:0",
  "input_snapshot_hash":"", "output_manifest_path":"Output/OutputManifest.json",
  "error_summary": null }
```

**`[proposed]` 目标扩展**：`status:"waiting"` + `wait_reason:{kind,detail}`（把"卡在什么上"提升为 State 一等字段；现行原因在 `DependencyCheckResult.missing_dependencies`，见 B3），以及 guard false/dependency impossible 时的终态 `skipped`。Confirm/Suspend 允许同一 Attempt 在 `running↔waiting` 间转换，但必须持久化 work item/callback 身份，不能靠 worker 进程内存等待。Mutable State 还应带单调 `revision` 与 `writer_id`，写入用 CAS；`heartbeat_timeout_seconds` 现为 runner 侧配置、不在 State 文档。

#### D2. 状态值收敛目标（实现须统一）

```
# [implemented] 现行 State.status 是普通 str；以下只是当前代码路径的观察值：
StateStatusObserved = not_started | running | completed | failed | blocked | stale_running
# [proposed] 增补：waiting（带 wait_reason）、skipped（when=false）、orphaned（现仅口播作 failed+orphaned:true 标志位）
WaitKind     = step | variable | artifact | resource | condition | user | host | external_callback # [proposed]；step=depends_on 未满足
RunStatus    = running | completed | failed | blocked | cancelled | {completed|failed|blocked|cancelled}_with_sync_error
UITone       = done | pending | disabled | running | failed
ArtifactValidity = missing | valid | stale | unbound | corrupt
```

参考实现：OpenCrew 用字符串字面量（`models.py:State.status`/`OutputManifest.status`/`ToolResult.status` 都是 `str`；runner 出现 `running`/`completed`/`failed`/`blocked`/`stale_running`，`finalize_session` 在控制层校验 Run 终态集合并可追加 `_with_sync_error`）。只有 `DependencyCheckResult.status` 当前是 Pydantic `Literal`。**`orphaned` 通用状态、`waiting`、`skipped` 均为 `[proposed]`**，目标实现应再把集中值表固化为 Schema 枚举。

#### D3. 心跳与存活（防"假死"）

- 运行中的步骤每 `heartbeat_interval` 更新 `heartbeat_at`（OpenCrew：后台心跳线程）。
- 超过 `heartbeat_timeout_seconds` 未更新 → 判 `stale_running`（`recover_stale_running_steps`，默认 900s）。
- 无活跃 worker 且状态文件过期超过 orphan 宽限 → 判 `orphaned`（口播参考：`video_plan_execution_state_services.py` 的 orphan 检测）。

详见 [06 · 运行时](./06_Runtime_Observability.md)（恢复、事件、SLA）。

#### D4. 检查清单

- [ ] 变量文档有 Schema 且 `extra=forbid`？
- [ ] 每个业务字段是否默认只有一个写入者；多写入者是否显式声明 reducer/CAS 合并契约？
- [ ] 步骤写变量只走 `context_patch` + 白名单校验？
- [ ] 人工 override 是否带 actor/reason/expected_revision，写审计事件，并重新校验依赖与下游失效？
- [ ] `waiting` 状态一律带**可查询的 `wait_reason`**？
- [ ] 已不可能满足的分支是否转为 `skipped`，而不是永远 waiting？Run 是否由恰好一个 outcome 收口？
- [ ] 长任务有心跳，超时能被判 `stale_running` 并恢复？
- [ ] UI 是否同时读取 execution status 与 artifact validity，而不是靠固定颜色优先级猜业务真相？
