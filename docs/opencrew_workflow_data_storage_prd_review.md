# OpenCrew Workflow Data Storage PRD 实现评审（综合版）

评审对象：`docs/opencrew_workflow_data_storage_implementation_prd.md`

评审日期：2026-05-25（第二轮综合更新）

评审性质：本文是**两轮评审的综合结果**。第一轮为基于 PRD 标准的架构评审；第二轮在此基础上做了逐文件、逐行的代码复核（标注 `file:line`），新增了若干发现，并修正了第一轮对 OC-StoryBoard 的判断。所有"复核"结论均来自实际代码核对。

---

## 结论

当前 repo **没有完整实现**该 PRD。

项目已经具备一部分基础设施：统一的 `sessions`、`session_events`、`session_files`、`session_shares` 表已经存在，OpenClip 的 Task / Session / OpenCode / Version / Attempt 链路也基本成型。

但从 PRD 的标准化 workflow 数据架构要求来看，当前实现仍存在明显缺口：

- OpenClip 基本接近 PRD，但仍有删除、文件索引、事件可见性等问题。
- OC-Rebuild 只是部分实现，Version / Attempt 可追溯性不完整（attempt 表缺版本引用）。
- OC-StoryBoard 明显偏离 PRD：复用 OC-Rebuild 表和 session source，并把 workspace JSON 当成任务类型识别与状态恢复的依据。
- Session Detail、Debug Console、Share Page 共用事件表，但缺少 `visibility` / `event_scope` 权限隔离。
- File API 的安全边界不完整，raw / zip / share 下载存在敏感文件或 symlink 风险。
- WorkflowAssistant 工具库路由配置存在静默回退 bug（OC-Rebuild 实际加载的是 Analysis 注册表）。

整体判断：这个 repo 更像是已经搭出了统一 session/event/storage 的雏形，但还不是 PRD 要求的标准化 workflow 数据架构。**最需要先补的不是 UI，而是五个架构合同：事件 visibility、task/version/attempt、文件访问安全、删除级联、workspace 非主状态。**

---

## PRD 关键要求对照

PRD 要求所有 workflow 都遵循统一链路：

```text
Task -> OpenCrew Session -> OpenCode Session -> Workspace
     -> Simple Prompt -> Final Prompt -> Version -> Tool/Skill
     -> Attempt -> session_events/session_files -> Session Detail / Debug Console
```

PRD 还明确要求：

- workspace 不能作为 task 的主状态来源。
- Task 表需要记录 `session_id`、`status`、`simple_prompt`、`final_prompt`、当前版本指针、latest attempt、prompt/run model、created/updated 等。
- Version 表需要记录 snapshot、simple/final prompt、runtime content、prompt model、created time。
- Attempt 表需要记录 prompt/runtime version、run model、status、started/finished、结果索引。
- 所有 tool call、long task、prompt generation、run 过程都应写入 `session_events`。
- Session Detail 和 Debug Console 可以共用 `session_events`，但必须通过 `visibility` / `event_scope` 区分用户可见信息和内部 debug 信息。
- Share Page 不能暴露 debug/internal 事件和敏感文件。
- File API 必须使用 workspace-relative path、安全 join，并拒绝目录、隐藏文件、敏感文件、非法路径和不安全 zip。

---

## 两轮评审发现对照表（含代码复核）

| # | 问题 | 第一轮 | 第二轮 | 代码复核结论 | 严重度 |
|---|---|---|---|---|---|
| 1 | visibility/event_scope 隔离缺失 → Session Detail/Share 泄露全量事件 | ✅ | ✅ | 证实：后端零写 visibility；`list_session_events`(routes/sessions.py:197) 无过滤；客户页 `isSystemLogEvent`(App.jsx:687) 为黑名单 + `JSON.stringify` 原样渲染(App.jsx:680) | 🔴 最高 |
| 2 | 通用 session 删除留业务表孤儿 | ✅ | ✅ | 证实：`delete_session_record` 只特判 openflow；`openclip_tasks.session_id` 无 `ON DELETE`(schema.py:341) | 🔴 |
| 3 | OC-StoryBoard 未作为独立 workflow | ✅ | ⚠️→采纳 | 证实：行落 `oc_rebuild_tasks`，但 `source=OC_REBUILD_SOURCE`(storyboard_router.py:139)，列表靠 `storyboard_meta.json` 识别(:1169-1172) | 🟠 |
| 4 | File API 安全边界不完整（symlink/隐藏/敏感） | ✅ | ✅ | 证实：raw 用弱的 `safe_read_join`(routes/sessions.py:86,595)，不解析 symlink 也不过滤 dotfile | 🟠 |
| 5 | OC-Rebuild Version/Attempt 血缘不完整 | ⊘ 漏 | ✅ | 证实：`oc_rebuild_attempts` 无 `prompt_version_id/runtime_version_id`(schema.py:472-487)；run 先建 attempt(:4880) 后校验 prompt(:4882) | 🟠 |
| 6 | session_files 索引陈旧、无 attempt 归属 | ⊘ 漏 | ✅ | 证实：`sync_session_files`(routes/sessions.py:169) 只 upsert 不删；表无 attempt_id | 🟡 |
| 7 | WorkflowAssistant 执行治理未闭环（execute=501） | ⚠️ | ✅ | 证实：`workflow_assistant_execute_guard` 返回 501(routes.py:661)；属有意桩，但闭环未成 | 🟡 |
| 8 | Debug Console 混前端本地事件、刷新不可恢复、无 secret/PII 脱敏 | ⚠️ | ✅ | 证实 | 🟡 |
| 9 | 事件写入分散、无统一合同/脱敏 | ✅ | ✅ | 证实：4 个模块各自 `add_event`（routes/sessions.py:152、router.py:286、workflow_assistant/routes.py:275、storyboard_router.py:117）| 🟠（根因）|
| 10 | 迁移体系 ad hoc（`ensure_*_columns`）| ⚠️ | ✅ | 一致 | 🟡 |
| 11 | **OC-Rebuild 工具注册表路径错 + 静默回退 Analysis** | ✅ | ⊘ | 第二轮新增。config 指 `ToolLibrary/Rebuild`(routes.py:88) 实际为 `Rebuild_V1`，`tool_library_root`(:229-237) 静默回退 Analysis registry | 🟠 |
| 12 | 路径耦合脆弱（`parents[3].parent`+`OpenCrew/` 前缀、跨包深相对 import）| ✅ | ⊘ | 第二轮新增 | 🟡 |
| 13 | SSE 无 `seq_id` 重放，`list_events` >500 丢中段 | ✅ | ⊘ | 第二轮新增（对应 Gemini §18.4）| 🟡 |

图例：✅ 明确指出 ⚠️ 部分提及 ⊘ 未覆盖。

---

## 已实现部分

### 统一 Session 基础表

已有基础表：

- `sessions`、`session_events`、`session_files`、`session_shares`、`workflow_plans`
- `openclip_tasks`、`openclip_prompt_versions`、`openclip_skill_versions`、`openclip_attempts`
- `oc_rebuild_tasks`、`oc_rebuild_prompt_versions`、`oc_rebuild_attempts`

相关代码：`backend/opcrew_backend/db/schema.py`、`repositories/sessions.py`、`routes/sessions.py`。

字段与 PRD §5.6 基本一一对应，说明项目已往统一 session/storage 方向收敛。

### OpenClip 链路基本成型（并有若干 PRD 坑点已规避）

OpenClip 是当前最接近 PRD 的 workflow：

- 创建 task 时会创建 OpenCrew session、workspace、OpenCode session 和 task。
- final prompt 生成复用 task 绑定的 OpenCode session（规避 §15.2/15.10）。
- prompt version、skill version、attempt 都有结构化记录；attempt 记录 `prompt_version_id`/`skill_version_id`/`run_model`（规避 §15.11）。
- run 时创建 attempt、更新 `latest_attempt_id` 与当前版本指针，并 `archive_current_outputs`(router.py:841) 归档上一轮输出（规避 §15.13 旧文件混淆）。

相关代码：`OpenClip/backend/openclip_backend/router.py`、`db/schema.py`。

### WorkflowAssistant 计划持久化部分已实现

- plan validation / save / confirm / high-cost confirmation 均已实现。
- confirm 写入 `confirmed_at` 与 `confirmed_by_message_id`；**OpenCode 写回失败不回滚 DB 确认**(routes.py:464-472，符合 §5.6.5)。
- bootstrap 返回 workflow/task/session/plan/messages/capabilities。

相关代码：`WorkflowAssistant/backend/workflow_assistant/routes.py`。

但 execute runner 尚未实现，见问题 7。

### 前端 Debug Store 已做 payload 截断

`frontend/src/debug/debugStore.js:49-67` 对 base64/bytes/blob 与超长字符串做 sanitize 与截断（规避 §15.6）——但仅前端，后端仍原样落库（见问题 1、9）。

---

## 主要问题

### 1. 高风险：Session Detail / Share Page 会暴露全量 session_events

PRD 要求 Session Detail 和 Debug Console 共用 `session_events`，但必须通过 `visibility` / `event_scope` 过滤。

当前问题：

- `session_events` 表没有 `visibility`、`event_scope`、`severity` 等字段。
- 普通 session events 接口直接返回全部事件。
- share token 事件接口也直接返回全部事件。
- 前端 Session Detail 没有用户可见事件过滤。

**代码复核**：

- 后端全仓 `.py` 中 `visibility`/`event_scope` 零出现（只在文档里）。
- `list_session_events`(routes/sessions.py:197) 对所有消费者返回全部事件。
- 分享页接口 `/api/session-share/{token}/events`(routes/sessions.py:742-745) 复用同一无过滤函数；分享页 HTML 对每条事件 `JSON.stringify(item.payload)`(routes/sessions.py:826-831)。share scope（viewer/collaborator）只用于上传只读控制，从不限制事件可见性。
- 客户页"工作日志"用黑名单 `isSystemLogEvent`(App.jsx:687)：除 `assistant.final` 与 3 个流式 kind 外全部显示，并用 `formatSessionEventPreview`(App.jsx:680) 原样 `JSON.stringify(payload)`。

影响：

- 模型请求、工具调用 payload、`session.error`（含 `str(exc)` 堆栈/路径）、直接落库的 OpenCode 原始 `properties`(routes/sessions.py:350) 等都会进入客户详情页与匿名分享页。
- 后续即使前端做过滤，也无法从数据模型层保证安全。

这是当前最核心的 PRD 不符合项，**且是唯一的隐私/安全级问题，应最高优先级处理**。

### 2. 高风险：通用 Session 删除会留下业务任务孤儿数据

当前 `DELETE /api/session-tasks/{session_id}` 只特殊处理了 `openflow-analysis`，然后调用 `SessionRepository.delete`。

`SessionRepository.delete`(repositories/sessions.py:31-36) 只删除 `session_shares` / `session_files` / `session_events` / `sessions`，不删除：

- `openclip_tasks` / `openclip_prompt_versions` / `openclip_skill_versions` / `openclip_attempts`
- `oc_rebuild_tasks` / `oc_rebuild_prompt_versions` / `oc_rebuild_attempts`

**代码复核**：`openclip_tasks.session_id = ForeignKey("sessions.id")` 无 `ON DELETE`(schema.py:341)。删除会话时若存在业务行，Postgres 会因外键约束**直接报错**或留下不一致。OpenClip 自身的 `delete_task`(repository.py:75-82) 会先删 attempts/versions/task，但**通用删除路径不会调用它**——两条删除路径并存且不一致。

影响：

- 从通用 Sessions 页面删除 OpenClip / OC-Rebuild / StoryBoard session 时，业务表会残留孤儿数据或删除失败。
- 违反 §13.8（不留无主记录、删除失败要明确报错）。

### 3. 高风险：OC-StoryBoard 没有作为独立 workflow 实现

PRD 把 OC-StoryBoard 作为一个应纳入统一机制的 workflow。

**代码复核**（第一轮曾误判"DB 主状态 OK"，第二轮修正）：

- StoryBoard 定义了 `OC_STORYBOARD_SOURCE = "oc-storyboard"`(storyboard_router.py:23)，但创建 session 时实际写入 `source=OC_REBUILD_SOURCE`(:139)。
- StoryBoard 复用 `oc_rebuild_tasks`（通过 `OCRebuildRepository.create_task`）。
- `list_storyboard_tasks`(:1169) 通过读取 workspace 中 `storyboard_meta.json` 是否存在来判断/筛选是否为 storyboard 任务(:1172)。
- 关键编辑状态依赖 workspace 中的 `storyboard_meta.json`、`storyboard_dialogue_plan.json`、`rebuild_shot_plan.json` 等文件。

准确表述：task 行确实落 DB，但**任务类型识别与列表/恢复依赖 workspace JSON**，且借用了另一 workflow 的 source 与表 → DB 不是 StoryBoard 真正的 authoritative state。

影响：

- 违反"workspace 不是主状态来源"。
- Session source 与 workflow 类型不准确。
- Session Detail、Debug Console、Share、删除、恢复、审计都会被 Rebuild 与 StoryBoard 混用污染。
- 这是典型的架构边界问题：用另一个 workflow 的数据模型承载新 workflow，短期省事，长期持续放大复杂度。

### 4. 高风险：File API 安全边界不完整

PRD 要求 raw 拒绝目录/隐藏/敏感文件，zip 限范围并过滤不安全文件，share 下载不绕过权限。

**代码复核**：

- 存在两套安全 join：`safe_join`(routes/sessions.py:77) 用 `.resolve()`，`safe_read_join`(:86) 用词法 `.absolute()`**不解析 symlink**。
- raw 下载端点用的是弱的 `safe_read_join`(:595)，且不过滤 `.` 开头/敏感文件（与 zip/list 跳过 dotfile 不一致）。
- zip 直接遍历 workspace 打包，无 realpath 二次比对处理 symlink escape。
- share 文件列表/下载缺少后端强制的 `downloadable` 校验。

影响：

- workspace 内一个指向外部的 symlink 即可逃逸。
- `.env`、token、内部 trace、debug artifacts 可能被 raw 或 zip 暴露。
- share token 安全性依赖前端过滤，后端边界不足。违反 §10.4 / §13.9。

### 5. 中风险：OC-Rebuild Version / Attempt 可追溯性不完整

PRD §5.4 要求 attempt 记录所用 prompt version、runtime/tool/skill version、run model、status、起止、结果索引。

**代码复核**：

- `oc_rebuild_attempts`(schema.py:472-487) **没有** `prompt_version_id` / `runtime_version_id` 字段（对比 openclip_attempts 是有的）。
- run 时**先创建 attempt 再校验 prompt**：rebuild_router.py:4880 `create_attempt` → :4882 才 `if not prompt: raise 400`，会产生无效 queued attempt。
- prompt version 删除只是直接删除，缺 current pointer fallback 的统一策略。
- 版本 schema 缺部分 PRD 显式字段（simple prompt / prompt model / runtime content）；OpenClip 侧 `openclip_prompt_versions` 也缺 `snapshot_json`（与 `oc_rebuild_prompt_versions` 不一致）。
- load version 未完整恢复 prompt model / run model 状态。

影响：无法可靠回答"这次结果由哪个 prompt/version/model 产生"；删除当前版本可能导致指针悬挂；审计/回放缺关键 lineage。

### 6. 中风险：session_files 索引会陈旧，且没有 attempt 归属

**代码复核**：

- `sync_session_files`(routes/sessions.py:169-195) 只 upsert 当前扫描到的文件，**不删除**已消失或已归档的旧记录。
- `session_files` 表结构无 `attempt_id`，无法区分哪个 attempt 产生了哪个文件。

影响：Session Detail 可能显示旧文件；share/download 可能暴露历史 attempt 残留结果；PRD 的"latest result vs history result"边界无法可靠表达。

### 7. 中风险：WorkflowAssistant 执行治理闭环未完成

plan save / confirm 已实现，但 execute 端点仍返回 501（`workflow_assistant_execute_guard`，routes.py:661）。

定位说明：返回 501 本身是合理的"有意停顿"（没有在未实现 runner 时乱执行高成本工具反而是对的），但 PRD §10.6 的"计划 → 确认 → 执行 → 审计 → 恢复"全链路尚未闭合，不应计入"已完成"。

当前缺口：

- Workflow Plan Runner 未实现，Tool Library 执行治理无闭环。
- assistant message 过程未完整写入统一 `session_events`。
- events stream 主要只处理 `workflow.*` 事件(routes.py:752-763)，不能作为统一事件总线。

### 8. 中风险：Debug Console 仍混合使用前端本地事件

Debug Console 既读 DB session events，也混入前端本地 debug events。

问题：本地事件刷新后不可恢复、不进 DB（不满足恢复/审计目标）；sanitizer 只做大字段截断，没有可靠 secret/PII redaction；DB 事件与 local 事件无统一 schema；family 只能靠 kind 字符串猜测(debugStore.js:110)。

相关代码：`frontend/src/debug/debugAdapter.js`、`debugStore.js`、`DebugConsole.jsx`、`App.jsx`。

### 9. 中风险：核心事件写入逻辑分散，没有统一事件合同（根因）

**代码复核**：4 个模块各自定义事件写入——`routes/sessions.py:152`、`workflow_assistant/routes.py:275`、`OpenClip/.../router.py:286`、`storyboard_router.py:117`，全仓约 198 处裸 `add_event` 调用。

问题：event kind 命名不统一、payload 结构不统一、是否更新 `sessions.updated_at` 不统一、无统一 visibility/event_scope/severity、无统一脱敏策略。

这是典型架构质量问题，也是问题 1/2/4/8 的共同根因：核心横切能力散落在业务模块里，导致后续所有安全、审计、展示、恢复需求都要多处修补。

### 10. 中风险：迁移体系偏 ad hoc

DB 初始化用 `metadata.create_all` 加若干 `ensure_*_columns` 补列(bootstrap.py)；只覆盖 openflow/openclip/oc_rebuild 三个表的部分列。

问题：能补列，但不适合管理约束、索引、字段语义变更、删除、数据迁移；后续补 `visibility`、`event_scope`、attempt lineage、file visibility 等字段时风险较高，易导致环境间 schema 漂移。

### 11. 中风险：OC-Rebuild 工具注册表路径错误 + 静默回退（第二轮新增）

**代码复核**：`WORKFLOW_CONFIGS` 中 `oc_rebuild*` 的 tool_library root 指向 `OpenCrew/ToolLibrary/Rebuild`(routes.py:88,106,124)，但实际目录是 `ToolLibrary/Rebuild_V1`。`tool_library_root`(:229-237) 找不到配置目录时**静默回退到 Analysis 注册表**。

影响：OC-Rebuild 的 Assistant 会用 **Analysis 的工具表**校验 Rebuild 计划，既违反"不得使用 registry 外工具"，又正中 §15.8"不用吞异常掩盖配置漂移"的反模式。建议改为正确路径并把"找不到 registry"从静默回退改为显式报错。

### 12. 低-中风险：路径耦合脆弱（第二轮新增）

- `tool_library_root` 依赖 `parents[3].parent / "OpenCrew/..."`(routes.py:230-233)——仓库目录一旦不叫 `OpenCrew` 即失效；config 字符串硬编码 `OpenCrew/` 前缀。
- 前端 `App.jsx:4-7` 用 `../../OpenClip/frontend/src/...` 深跨包导入，宿主构建直接耦合兄弟包内部（§15.18 build 混淆风险）。

### 13. 低-中风险：SSE 无序列号重放（第二轮新增）

`list_events`(routes/sessions.py:75-85) 按 id desc 取 500 再反转 → 单次轮询若新增 >500 条会丢中段事件；断线重连靠它补齐，可能漏事件。缺少 PRD/Gemini §18.4 建议的 `seq_id` + `last_seq_id` 重放机制。关键事件已落库可部分缓解，但与 §13.4"刷新后关键 debug 历史仍可加载"存在张力。

---

## 架构质量问题总结

### 1. Workflow 边界不清

OC-StoryBoard 复用 OC-Rebuild 的 session source、task 表和 repository，两个 workflow 生命周期混在一起。建议先做产品定性：

- 若是独立 workflow，应建立独立 task/version/attempt 表或明确的 shared base model。
- 若只是 Rebuild 的一个 mode，API、source、UI 命名都应反映这一点，不应在 PRD 中当作独立 workflow。
- 在定性之前不要继续往 `oc_rebuild_tasks` 上叠 StoryBoard 字段。

### 2. DB 主状态和 workspace 状态边界不清

PRD 明确 workspace 不是 task 主状态来源，但 StoryBoard 依赖 workspace JSON 判断任务类型和恢复状态。建议 DB 记录 authoritative state，workspace 只存 artifacts；workspace JSON 可作缓存/产物，但不能作为 list/detail/recovery 的唯一依据。

### 3. 横切能力没有抽象

事件写入、文件索引、安全过滤、删除级联、share presentation 都是横切能力，却散落在多个 router/repository。建议抽象：

- `SessionEventService`（统一 schema、visibility、脱敏、updated_at、kind 命名）
- `SessionFileService`（统一 path resolve/symlink/hidden/sensitive/downloadable/share visibility）
- `WorkflowDeletionService`（按 source 分派业务级联删除）
- `SharePresentationService`（统一客户可见性过滤）
- `WorkflowRunRecorder`（统一 attempt + 事件 lineage）

### 4. 安全边界依赖调用方自觉

raw/zip/share 的安全策略分散在各接口。建议所有 workspace 文件读取必须经同一安全文件服务，统一处理 path resolve、symlink、hidden、sensitive、downloadable、share visibility；前端过滤只能作为展示优化，不能作为安全边界。

### 5. Version / Attempt 合同不统一

OpenClip、OC-Rebuild、StoryBoard 对版本/运行/attempt 的表达不一致（如 oc_rebuild_attempts 缺版本引用、openclip_prompt_versions 缺 snapshot_json）。建议定义统一 `WorkflowAttempt` 合同：每个 workflow 可有自己的 detail 表，但必须满足统一字段（prompt version、runtime version、run model、status、started/finished、result index）；删除 current version 必须有统一策略（禁止删除 / 自动 fallback / 清空并记录 event）。

### 6. 配置与路径耦合（第二轮补充）

Workflow 定义硬编码在 `WORKFLOW_CONFIGS` 且路径依赖目录命名与跨包相对路径，已导致问题 11 的静默回退 bug。建议：配置路径集中校验、缺失即报错；长期按 PRD §5.1 迁移到 `workflow_definitions` 或配置文件。

---

## 历史建议优先级（以 improvement plan 为准）

本节保留第一轮/第二轮评审时形成的优先级摘要，用于理解问题背景。后续执行顺序、阶段边界和任务拆分以 `docs/opencrew_repo_improvement_plan.md` 的 Roadmap 和“优先落地清单”为单一真相来源；不要在本 review 中并行维护新的 P0-P4 计划。

### P0：先修安全和一致性边界

1. 给 `session_events` 增加 `visibility`、`event_scope`、`severity` 等字段，并落地统一 emit helper（自动补 `workflow_id/task_id/attempt_id` + 脱敏）。
2. 所有 Session Detail / Share / Debug Console 接口按 visibility **白名单**过滤；Share Page 默认只返回 public/customer-visible events。
3. 统一 File API 安全服务（`.resolve()`+realpath 二次比对、拒绝 dotfile/敏感文件、强制 `downloadable`），修 raw、zip、share download。
4. 修通用 session delete，按 `source`→adapter 分派业务级联删除（或加 `ON DELETE CASCADE`），删除失败显式报错。
5. **修 OC-Rebuild 工具注册表路径（问题 11）**，并把"找不到 registry"改为显式报错。

### P1：修 workflow 数据模型

1. 决定 StoryBoard 是独立 workflow 还是 OC-Rebuild mode。
2. 如果独立，实现 StoryBoard 自己的 task/version/attempt 模型。
3. 补齐 `oc_rebuild_attempts` 对 prompt/runtime version 的引用；run 改为"先校验后建 attempt"。
4. 为 current version 删除定义统一 fallback 策略；统一 Version 快照字段（补 openclip `snapshot_json`）。
5. `session_files` 增加 attempt 归属、visibility/downloadable，并实现 stale index 清理。

### P2：收敛横切服务

1. 建立 `SessionEventService` / `SessionFileService`。
2. 建立 workflow 删除协调层与 share presentation 层。
3. 统一 event kind、payload schema、脱敏策略。

### P3：完成 WorkflowAssistant 执行闭环

1. 实现 Workflow Plan Runner。
2. Tool Library 执行必须写入统一 `session_events`。
3. assistant message、plan、execute、tool call 统一进事件流。
4. Debug Console 和 Session Detail 从同一事件源恢复完整状态。

### P4：引入正式 migration

1. 使用 Alembic 或等价迁移机制。
2. 停止依赖零散 `ensure_*_columns` 管理 schema 演进。
3. 为历史库提供数据迁移脚本。

---

## 最终评估

当前实现大约完成了 PRD 的基础设施部分，但没有达到 PRD 对"所有 workflow 统一数据链路和安全展示边界"的要求。粗略估计（含第二轮修正）：

- OpenClip：约 70% 符合。
- OC-Rebuild：约 45% 符合（attempt 版本血缘缺失下调）。
- OC-StoryBoard：约 25% 符合（workspace 主状态 + 借表借 source）。
- Session Detail / Debug Console / Share：约 40% 符合。
- File API 安全：约 35% 符合。
- WorkflowAssistant 执行治理：约 40% 符合。

（以上为定性估计，非精确度量。）

最需要优先处理的不是补 UI，而是补齐架构合同：

1. event visibility 合同；
2. workflow task/version/attempt 合同；
3. file access 安全合同；
4. deletion cascade 合同；
5. workspace 不是主状态来源的边界。

在这些合同稳定之前，继续叠加 workflow 功能会增加后续迁移和安全修复成本。
