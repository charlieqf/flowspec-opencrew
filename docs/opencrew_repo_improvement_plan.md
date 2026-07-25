# OpenCrew Repo 改进计划

日期：2026-05-25

## 1. 目标

本计划用于把当前 OpenCrew repo 从“多个 workflow 功能已经能跑，但架构合同不统一”的状态，推进到“所有 workflow 共享稳定数据模型、安全边界、事件流、文件索引和执行治理”的状态。

本计划覆盖：

- OC-Analysis
- OC-Rebuild
- OC-StoryBoard
- WorkflowAssistant
- Session Detail
- Debug Console
- Share Page
- Tool Library
- workspace 文件访问与产物管理
- DB schema / migration / testing

本计划不覆盖：

- 新业务功能的产品设计。
- 模型效果优化。
- UI 视觉重设计。
- 生产部署流程重构。

## 2. 当前状态判断

repo 已经具备一部分基础设施：

- `sessions`、`session_events`、`session_files`、`session_shares` 已存在。
- OpenClip / OC-Analysis 基本具备 Task、Prompt Version、Skill Version、Attempt。
- OC-Rebuild 有自己的 Task、Prompt Version、Attempt 表。
- WorkflowAssistant 可以加载 Tool Registry、保存 plan、确认 plan。
- Analysis 工具链已经在 `ToolLibrary/Analysis` 下形成较完整的 01-17 工具集。
- 前端已经开始通过 `schemes/scheme_*/manifest.json` 绑定 Analysis 页面结果。

但 repo 还没有达到统一 workflow 架构标准：

- `session_events` 缺少 `visibility` / `event_scope` / `severity` 等字段。
- Session Detail、Debug Console、Share Page 没有可靠隔离 public/customer/debug/internal 事件。
- File API 的 raw / zip / share 下载安全边界不完整。
- 通用 session 删除和 workflow 专属删除路径不一致。
- OC-StoryBoard 复用 OC-Rebuild 的数据模型和 source，workflow 边界不清。
- OC-Rebuild 的 Version / Attempt lineage 不完整。
- WorkflowAssistant 的 OC-Rebuild 配置指向不存在的 `ToolLibrary/Rebuild`，实际目录是 `ToolLibrary/Rebuild_V1`；当前 `tool_library_root()` 找不到配置目录时会静默回退到 Analysis registry，导致 OC-Rebuild Assistant 可能用 Analysis 工具表校验 plan。
- `session_files` 没有 attempt 归属，文件索引可能陈旧。
- session event SSE 只有 `id` cursor 和固定窗口读取，缺少 per-session `seq_id` / 可验证重放合同，断线后高频事件可能丢失中间段。
- WorkflowAssistant execute runner 尚未实现。
- DB schema 演进主要依赖 ad hoc bootstrap，缺少正式 migration。

## 3. 改进原则

### 3.1 DB 是主状态，workspace 是产物区

业务状态必须以 DB 为准。workspace 只能保存输入文件、中间产物、导出文件和可恢复 artifacts。

不应通过扫描 workspace 来判断：

- task 是否存在；
- workflow 类型；
- 当前版本；
- 当前 attempt；
- 页面应显示多少业务卡片。

workspace 中的 manifest 可以作为 result index 的一部分，但它必须由 DB attempt/version 指针引用。

### 3.2 所有 workflow 共享同一套生命周期合同

每个 workflow 至少应有清晰的：

- Task
- Session
- OpenCode Session
- Prompt Version
- Runtime / Skill Version
- Attempt
- Result Index
- Session Events
- Session Files

可以允许每个 workflow 有自己的 detail 表，但通用字段和语义必须一致。

### 3.3 安全边界在后端，不依赖前端过滤

前端过滤只能改善展示，不能作为安全控制。

后端必须统一控制：

- share token 可见事件；
- share token 可见文件；
- raw 文件访问；
- zip 下载内容；
- hidden / sensitive 文件过滤；
- symlink escape；
- debug/internal payload 脱敏。

### 3.4 事件流是审计和恢复基础设施

所有长任务、工具调用、模型调用、plan 操作、版本切换、删除、失败和恢复都应写入 `session_events`。

事件写入不应散落在各个 router 中，应通过统一服务完成。

### 3.5 先收敛合同，再扩展功能

在事件、文件、删除、version、attempt、migration 合同稳定前，不建议继续大规模增加 workflow 功能。

### 3.6 信任边界先写清楚

当前 P0 不引入完整 auth，因此必须承认并约束临时信任边界：

- 不可信入口：匿名 share token 页面、share token API、任何公开转发链接。
- 有限可信入口：本地开发主 UI、Workflow 页面、Debug Console、内部 API。
- Debug Console 在无 auth 前不得通过公网、匿名 share 或未受控隧道暴露。
- `visibility` / `event_scope` 是展示边界，不是完整认证授权机制。
- 如果进入多人协作或公网部署，必须另行补正式 auth、角色、访问控制和审计。

P0 的安全目标是：即使没有完整 auth，匿名 share 入口也不能读取 debug/internal 事件、敏感文件、workspace 外文件或 secrets。

### 3.7 保留、归档和 GC 需要进入设计

长期运行后，`session_events`、workspace、history attempts、share token 都会增长。进入生产前必须定义：

- `session_events` 保留周期、归档策略和删除策略。
- workspace 产物、history attempts、临时上传的清理周期。
- share token 过期后的访问行为和数据保留行为。
- GC 执行方式：手动 cleanup、后台 job 或管理端操作。
- GC 必须尊重 attempt/result index，不得删除 latest attempt 的可下载交付物。

P0 不要求立即删除历史数据，但新增字段和服务设计必须给 GC 留出状态位，例如 `visibility`、`sensitivity`、`attempt_id`、`stale` 或等价字段。

### 3.8 多 worker 并发不能靠内存假设

后续如果后端以多进程或多 worker 运行，以下状态必须由 DB 保证一致性：

- `session_events` cursor / `seq_id` 必须来自 DB sequence、事务锁或全局 `id`，不能来自进程内计数器。
- `attempt_no` 创建必须避免并发重复，建议加唯一约束或 transaction-level lock。
- plan confirm / execute 必须有乐观锁、状态条件更新或版本号，避免重复确认和重复执行。
- runner step 幂等键必须落库，不能只存在前端或内存中。

### 3.9 外部架构建议采纳表

PRD 第 18 章是非规范附录。以下表用于防止建议悬空或被误当成 P0：

| 建议 | 状态 | 计划归属 / 取舍 |
| --- | --- | --- |
| 18.1 异步事件总线 | 推迟 | P0 先收敛 `SessionEventService`、visibility、replay 合同；事件量或 DB 阻塞成为真实瓶颈后再做异步总线。 |
| 18.2 计划三态机 / 两阶段提交 | 部分采纳 | P3.1 / P3.2 采纳持久 run/step 状态、suspend/resume、幂等键；严格两阶段提交和版本冻结作为 P3 设计输入。 |
| 18.3 Workspace 安全沙箱 | 部分采纳 | canonical path、symlink escape 防护进入 P0.3；受限 OS 用户沙箱推迟到生产安全加固。 |
| 18.4 `seq_id` / 断线重放 | 已采纳 | P1.5；可以新增 per-session `seq_id`，也可以证明全局 `id` + 正序分页满足 durable cursor。 |
| 18.5 Prompt Registry 热加载 | 推迟 | 当前不进入 P0/P1；待 Version/Attempt 合同稳定后作为可维护性优化。 |
| 18.6 OpenCode auto-healing | 推迟 | 当前先保证可诊断失败和主状态不丢失；自动重建 session 可能改变上下文，需单独设计。 |
| 18.7 PII 脱敏与 token/cost | 已采纳 | P0.2 采纳 redaction；P1.3 采纳 attempt metrics 字段。 |

## 4. Roadmap

## P0：安全边界和数据一致性

目标：先解决可能导致信息泄露、孤儿数据、错误分享和不可恢复状态的问题。

### P0.0 建立 migration baseline

问题：

- P0 / P1 会新增或调整 `session_events`、`session_files`、OC-Rebuild attempts、StoryBoard workflow 边界等 schema。
- 当前 repo 主要依赖 `metadata.create_all()` 和 ad hoc `ensure_*_columns()`，不适合承载连续 schema 演进。

方案：

- 在任何 P0 schema 改动前，引入 Alembic 或等价 migration baseline。
- baseline 必须能识别当前已有表，不重复创建已有对象。
- 后续 P0/P1 的所有 schema 改动必须通过 migration 进入，而不是继续堆叠 `ensure_*_columns()`。
- bootstrap 可以保留为启动兜底和 runtime seed，但不再作为主 schema 演进机制。

验收：

- 空库可以通过 migration + bootstrap 初始化。
- 旧库可以从当前 schema 无损升级。
- P0.1 / P0.3 / P1.3 / P1.4 所需字段或新表都有可重复执行的 migration。

### P0.1 建立 `session_events` 可见性模型

改动：

- 给 `session_events` 增加字段：
  - `visibility`
  - `event_scope`
  - `severity`
  - `family`
  - `workflow_id`
  - `task_id`
  - `attempt_id`
  - `tool_id`
  - `step_id`
- 定义默认值：
  - `visibility=internal`
  - `event_scope=debug`
  - `severity=info`
- 迁移历史事件时，默认设为 `internal/debug`，再按已知 event kind 白名单提升为 public/customer-visible。
- rollout 兼容策略：
  - 在所有 writer 迁移完成前，reader / presenter 必须支持“字段为空时按 kind 推断 visibility”。
  - `user.message`、`assistant.final`、必要的 `session.created` / `session.completed` / `session.failed` 必须在兼容期仍能进入 Session Detail。
  - API filter 上线不能早于兼容推断或 writer 显式标记，否则客户页会短暂或长期空白。

验收：

- Session Detail 只显示 public/customer-safe 事件。
- Debug Console 可以显示 debug/internal 事件，但 share/匿名出口不能返回 debug/internal 事件。本 repo 当前没有完整鉴权体系，P0 不引入新的 auth 前提。
- Share Page 不能看到 debug/internal 事件。
- 所有事件接口都支持 visibility filter。

### P0.2 统一 Session Event 写入服务

新增或重构：

- `SessionEventService`
- 统一 `add_event()` 入口。
- 统一 payload JSON encode。
- 统一 `sessions.updated_at` 更新。
- 统一 redaction / truncation。redaction 必须覆盖 API key、Bearer token、Authorization header、Cookie、session token、proxy credential、手机号、邮箱等 PII/secret；truncation 只处理大字段，不能替代脱敏。
- 统一 event kind 命名规范。
- OpenCode SSE -> `session_events` 桥接必须纳入该服务。OpenCode 原始 `properties` 不能原样落库，应默认标为 `visibility=internal,event_scope=debug`，并经过 redaction。

迁移：

- 替换 sessions route、OpenClip、OC-Rebuild、OC-StoryBoard、WorkflowAssistant 中分散的 `add_event`。

验收：

- repo 中业务模块不再直接 insert `session_events`。
- 事件 payload 包含统一 envelope。
- `user.message`、`assistant.final`、生命周期事件等客户页需要展示的事件必须显式标为 public/customer-safe，避免历史事件默认 internal 后客户页为空。
- OpenCode 原始事件迁移期间即使未完全替换，也必须经过 reader 侧兜底过滤，不能进入 share 匿名出口。
- 单元测试覆盖 public/debug/share 三类读取。

### P0.3 修复 File API 安全边界

新增或重构：

- `SessionFileService`
- `safe_workspace_path()`
- `read_raw_file()`
- `download_file_by_id()`
- `build_safe_zip()`
- `session_files` 最小可见性字段或等价策略：
  - `visibility`
  - `sensitivity`
  - `attempt_id`，可先 nullable

规则：

- 所有路径必须 workspace-relative。
- 使用 canonical resolve 校验 workspace 边界。
- raw 拒绝目录、hidden 文件、敏感文件。
- zip 跳过 hidden、sensitive、不可下载文件。
- 后端强制检查 `session_files.downloadable`。
- share 下载必须二次校验 share token 权限和文件 visibility。
- 明确处理 symlink：默认拒绝指向 workspace 外的 symlink。
- 发布依赖：在拒绝 workspace 外 symlink 前，必须先确认 OC-Analysis `01_video_metadata_extractor.py` 已经把 source video 复制为 workspace 内部 `source_video.mp4`，并为存量任务提供修复/重建路径。否则 P0 安全修复可能破坏 virtual playback。

验收：

- `../`、absolute path、workspace 外 symlink 均被拒绝。
- `.env`、token、secret、debug raw dump 默认不可 raw/zip/share。
- share token 只能下载 public/downloadable 文件。
- 如果字段迁移尚未完成，share/raw/zip 必须使用后端 denylist + path policy 兜底，不能等待 P1/P4 后再补安全过滤。
- 视频 raw endpoint 保持 Range request 可用。当前 FastAPI/Starlette `FileResponse` 已具备 Range 能力，此项以回归测试验证 `206 Partial Content` 为主，不作为新开发项。

### P0.4 修复删除一致性

问题：

- 通用 session delete 和 workflow 专属 delete 并存，容易留下业务 task、attempt、version 孤儿数据，或触发 FK 错误。

方案：

- 新增 `WorkflowDeletionService`。
- 删除 session 时根据 `sessions.source` 分派到对应 workflow 删除策略。
- 或者禁用通用 delete 对业务 workflow session 的直接删除，只允许走 workflow task delete。
- 删除顺序必须避免半删除：
  - 先在 DB transaction 中删除或标记业务行、versions、attempts、plans、session 子表。
  - DB 成功后再清理 workspace。
  - workspace 清理失败时写入 cleanup event / cleanup job，不回滚已完成的 DB 删除。
  - 禁止先删 workspace 再尝试 DB 删除。

每个 workflow 删除必须覆盖：

- task
- prompt versions
- runtime / skill versions
- attempts
- workflow plans
- session events
- session files
- session shares
- workspace
- OpenCode session 关联状态

验收：

- 删除 OpenClip task 后无 orphan session。
- 删除 session 后无 orphan OpenClip / OC-Rebuild task。
- 删除 StoryBoard task 后不会残留 Rebuild task 或 workspace meta。
- 删除失败时返回明确错误，不产生半删除状态。

### P0.4a 修复 StoryBoard 最小 workflow discriminator

问题：

- P0 删除分派和 share/debug/source 展示都依赖 workflow source 或等价 discriminator。
- 当前 StoryBoard 复用 OC-Rebuild source，如果等到 P1 再定完整模型，P0 删除服务会无法可靠区分 StoryBoard 与 Rebuild。

方案：

- 在完整 StoryBoard 重构前，先做最小 DB discriminator：
  - 方案 A 最小版：创建 StoryBoard session 时写 `sessions.source=oc-storyboard`。
  - 方案 B 最小版：保留 `source=oc-rebuild`，但在 DB 中增加明确 `workflow_mode=storyboard` 或等价字段。
- 禁止继续通过 `storyboard_meta.json` 作为 workflow 类型判断的唯一依据。
- 删除、分享、Debug Console、Session Detail 先使用该 discriminator 分流。

验收：

- 新建 StoryBoard task 不再只能靠 workspace JSON 被识别。
- P0 删除服务可以可靠选择 StoryBoard 删除策略。
- 后续 P1.4 仍可选择独立 workflow 或 Rebuild mode，不被 P0 最小修复锁死。

### P0.5 修复 OC-Rebuild Tool Registry 路径和静默回退

问题：

- `WORKFLOW_CONFIGS` 中 OC-Rebuild / Plan A workflow 的 `tool_library.root` 指向 `OpenCrew/ToolLibrary/Rebuild`，但当前实际目录是 `ToolLibrary/Rebuild_V1`。
- `tool_library_root()` 找不到配置目录时会静默回退到 `ToolLibrary/Analysis`。
- 结果是 OC-Rebuild Assistant 可能用 Analysis registry 进行 plan 校验，这是当前已经生效的正确性 bug。

方案：

- 将 OC-Rebuild 相关 workflow 配置改为 `ToolLibrary/Rebuild_V1`。
- `tool_library_root()` 找不到显式配置路径时必须报错，不能回退到 Analysis。
- registry / agent guide 路径启动时做 validation。
- 给 WorkflowAssistant bootstrap / plan validation 增加 registry source 断言。

验收：

- OC-Rebuild workflow 加载的是 `ToolLibrary/Rebuild_V1/tool_registry.json`。
- 配置路径缺失时接口返回明确错误，不静默使用 Analysis registry。
- registry schema 校验覆盖 OpenClip、OC-Rebuild、OC-Rebuild Plan A 变体。

## P1：Workflow 数据模型收敛

目标：让 OC-Analysis、OC-Rebuild、OC-StoryBoard 都符合统一 Task / Version / Attempt / Result Index 合同。

### P1.1 定义统一 Workflow Task 合同

所有 workflow task 至少应有：

- `id`
- `session_id`
- `status`
- `simple_prompt`
- `final_prompt`
- `current_prompt_version_id`
- `current_runtime_version_id`
- `latest_attempt_id`
- `prompt_model_provider`
- `prompt_model_id`
- `run_model_provider`
- `run_model_id`
- `created_at`
- `updated_at`

如果某 workflow 不需要 prompt 或 runtime，也应显式置空，而不是省略语义。

验收：

- 每个 workflow 的 task detail API 返回统一字段 envelope。
- 前端可以用统一字段展示顶部状态、当前版本和 latest attempt。

### P1.2 定义统一 Version 合同

Version 至少应记录：

- task id
- version name
- notes
- snapshot json
- simple prompt
- final prompt
- runtime content
- prompt model
- created at

删除 current version 必须有统一策略：

- 禁止删除 current version；或
- 删除后自动 fallback 到最新可用 version；或
- 删除后清空 current pointer，并写入 event。

验收：

- OpenClip、OC-Rebuild 都有明确 current version 删除行为。
- load version 能完整恢复 prompt model / run model / runtime content。
- attempt 能追踪实际使用的 version。

### P1.3 定义统一 Attempt 合同

Attempt 至少应记录：

- task id
- session id
- attempt number
- status
- prompt version id
- runtime / skill version id
- run model provider
- run model id
- started at
- finished at
- summary
- result index json
- metrics，包括 `input_tokens`、`output_tokens`、`estimated_cost`、`tool_runtime_ms`。没有真实 token 数据时可以为空，但字段语义应保留。

验收：

- OC-Rebuild attempts 补齐 prompt/runtime version lineage。
- OpenClip `result_manifest_json` 明确映射或迁移为 `result_index_json`。
- attempt 创建必须发生在输入校验通过后，避免 queued/failed 脏记录。

### P1.4 重构 OC-StoryBoard workflow 边界

决策点：

- 方案 A：StoryBoard 是独立 workflow。
- 方案 B：StoryBoard 是 OC-Rebuild 的一个 mode。

如果采用方案 A：

- 增加 `oc_storyboard_tasks`
- 增加 `oc_storyboard_prompt_versions`
- 增加 `oc_storyboard_attempts`
- `sessions.source=oc-storyboard`
- workspace JSON 只作为 artifacts。

如果采用方案 B：

- 移除独立 StoryBoard source 叙事。
- API、UI、文档统一表达为 OC-Rebuild mode。
- DB 中显式增加 `workflow_mode=storyboard`，不能通过 workspace 文件推断。

验收：

- 不再通过 `storyboard_meta.json` 判断 task 类型。
- Session Detail 能正确显示 StoryBoard / Rebuild 类型。
- 删除、分享、恢复、debug 不再混淆 StoryBoard 和 Rebuild。

### P1.5 建立 session event 重放和 cursor 合同

问题：

- 当前历史读取按 `id desc limit 500` 后反转，单次新增超过窗口时可能丢失中间事件。
- SSE 使用 `since=id`，但没有 per-session `seq_id`、`Last-Event-ID` 兼容策略和断线补齐测试。

方案：

- 为 `session_events` 增加 per-session 单调 `seq_id`，或明确以全局 `id` 作为 durable cursor 并提供分页补齐 API。
- 事件查询必须按 cursor 正序分页，不允许用 desc + limit 造成中间段丢失。
- SSE 支持 `last_seq_id` / `Last-Event-ID` 断线重连。
- Debug Console 和 Session Detail 统一使用可重放 cursor。
- 如果采用 per-session `seq_id`，必须由 DB 保证多 worker 下单调递增，不能依赖进程内计数器。
- 如果采用全局 `id`，必须明确它就是 durable cursor，并提供正序分页补齐和断线重放测试。

验收：

- 连续写入超过 500 条事件后，history + SSE reconnect 能完整补齐。
- Debug Console 刷新后不丢事件、不重复展示。
- Share Page 的事件重放同样遵守 visibility filter。

## P2：OC-Analysis 工具链和页面产物收敛

目标：让 OC-Analysis 从旧 runner 逐步迁移到 Plan Runner / Tool Library 驱动，同时保持当前页面稳定。

### P2.1 稳定 Analysis result index

`result_index_json` 或兼容字段必须包含：

- `schemes.detail_manifest`
- `schemes.balanced_manifest`
- `schemes.summary_manifest`
- `reports.analysis_summary`
- `reports.quality_check`
- `source_video`
- `clip_mode`
- `attempt_id`
- `tool_versions`

验收：

- 页面不需要扫描目录推断卡片数量。
- stale mp4 不会影响卡片展示。
- latest attempt 和 workspace manifest 能对应。

### P2.2 明确页面绑定规则

页面主数据源顺序：

1. DB task / latest attempt
2. attempt result index
3. `schemes/scheme_*/manifest.json`
4. segment JSON / SRT
5. legacy fallback

legacy fallback 包括：

- `storyboards/scheme_filename_manifest.json`
- 目录 mp4 listing

legacy fallback 必须显示 stale warning，并记录 debug event。

验收：

- virtual export、physical export 都能稳定播放。
- virtual card 用每段 JSON path 作为 metadata key，不用共享 `source_video.mp4` 作为 key。
- raw video Range request 保持当前能力，并有自动测试或 smoke test 防回归。

### P2.3 工具 `--print-json` 输出标准化

每个 `ToolLibrary/Analysis` 工具应输出统一 JSON：

```json
{
  "tool_id": "15",
  "tool_name": "15_scheme_export_validator",
  "status": "completed",
  "inputs": {},
  "outputs": {},
  "warnings": [],
  "errors": [],
  "result_paths": [],
  "metrics": {}
}
```

验收：

- Plan Runner 可以从工具 stdout 解析结果。
- 工具失败时能写入 structured event。
- Attempt result index 可以由工具输出逐步汇总。

### P2.4 保留旧 runner，但包一层兼容执行器

短期不要直接删除 `openclip_analysis_runner.py`。

建议：

- 新增 Analysis Runner Adapter。
- Adapter 可以调用旧 runner，也可以按 plan 调用 ToolLibrary 工具。
- 前端 Run Analysis 仍可保持原入口，但后端内部逐步迁移。

验收：

- 当前用户路径不破坏。
- 新 Plan Runner 可灰度启用。
- 失败时可回退旧 runner。

## P3：WorkflowAssistant 执行闭环

目标：让 WorkflowAssistant 不止能生成和确认 plan，还能执行、暂停、恢复、审计。

### P3.1 实现 Plan Runner

Plan Runner 负责：

- 读取 latest confirmed plan。
- 校验 task/session/workspace。
- 注入 `--workspace`、`--task-id`、model、attempt id。
- 按 step 执行 tool registry 中的脚本。
- 写入 step events。
- 更新 plan progress。
- 更新 attempt result index。

持久化模型：

- 在实现真实 runner 前，先定义 runner 状态存储，避免只把 progress 写入可变 `plan_json`。
- 建议新增：
  - `workflow_plan_runs`：一次 plan 执行记录，关联 workflow、task、session、plan、attempt、status、started/finished、summary。
  - `workflow_plan_steps`：每个 step 的执行状态，记录 step id、tool id、status、idempotency key、attempt count、started/finished、result paths、error summary。
- 如果短期不建新表，也必须在 `workflow_plans.plan_json` 中定义稳定的 `execution_state` schema，并在 P4 migration 中迁移到正式表。
- Debug Console 和恢复逻辑读取持久 step 状态，不依赖前端 memory 或 OpenCode messages。

验收：

- `/assistant/execute` 不再返回 501。
- 执行过程能被 Debug Console 实时看到。
- 刷新页面后能恢复 plan progress。
- suspend / resume / retry 能基于持久 step 状态判断是否重复执行高成本 step。

### P3.2 支持高成本步骤挂起和恢复

高成本步骤包括：

- LLM
- VLM
- 长视频处理
- 需要外部付费 API 的工具

规则：

- plan step 可标记 `requires_confirmation=true`。
- 未确认时 runner 进入 `suspended`。
- 用户确认后 resume。
- 所有确认行为写入 `session_events` 和 `workflow_plans`。
- step 必须有幂等键和执行状态记录。对 LLM/VLM/付费外部 API，resume 或 retry 不得重复扣费式执行已完成 step；确需重跑时必须显式 force，并写入 event。

验收：

- VLM-heavy 步骤不会绕过确认。
- resume 后不会重复执行已完成 step。
- plan 状态可审计。

### P3.3 Tool Library registry 正式化

registry 必须包含：

- id
- name
- script
- arguments schema
- input dependencies
- output paths
- cost class
- model requirements
- confirmation requirement
- timeout
- retry policy
- idempotency key / output contract

验收：

- WorkflowAssistant 不靠自然语言猜工具调用。
- Plan Runner 只执行 registry 中声明的工具。
- registry schema 有校验脚本。
- 所有 workflow registry 路径启动时校验，缺失即失败，不允许静默 fallback 到其他 workflow 的 registry。
- 中期将 `WORKFLOW_CONFIGS` 拆到集中配置或 `workflow_definitions` 表，减少硬编码路径导致的 registry 错配。

## P4：迁移、测试和质量门

目标：降低 schema 演进、workflow 重构和安全改造的回归风险。

### P4.1 完善正式 migration 治理

P0.0 已经建立 migration baseline。本阶段负责完善 migration 治理，而不是首次引入。

迁移范围：

- `session_events` visibility 字段。
- `session_files` attempt / visibility 字段。
- OC-Rebuild attempt lineage 字段。
- StoryBoard workflow 边界字段或新表。
- result index 字段统一。
- WorkflowAssistant runner 状态表。
- event retention / GC 所需状态字段。

治理要求：

- 每个 schema 变更必须有 migration、回填策略和回归测试。
- 停止新增 ad hoc `ensure_*_columns()`；历史 ensure 只保留兼容或逐步删除。
- migration 必须能在空库和旧库上重复验证。

验收：

- 新环境可以从空库初始化。
- 旧库可以无损迁移。
- migration 可重复验证。

### P4.2 建立架构合同测试

新增测试类别：

- session event visibility test
- session event replay / SSE reconnect test
- share page event filtering test
- file raw/zip security test
- workflow delete cascade test
- version delete fallback test
- attempt lineage test
- session_files stale cleanup test
- WorkflowAssistant plan validate/confirm/execute test
- Tool Registry path validation test
- secret / PII redaction test
- event retention / GC safety test
- multi-worker concurrency test for event cursor, attempt number, and plan confirm

验收：

- 每个 PRD 合同都有自动化测试覆盖。
- CI 可以在无真实模型调用环境下跑核心合同测试。

### P4.3 建立 workflow smoke tests

每个 workflow 至少有一条 smoke：

- create task
- generate prompt
- save version
- run fake/short attempt
- write event
- sync files
- open detail
- create share
- delete task

验收：

- OpenClip / OC-Rebuild / OC-StoryBoard 都能跑完整 smoke。
- smoke 使用 fake model / noop tool，避免真实成本。

### P4.4 文档重整

建议文档结构：

```text
docs/
  architecture/
    workflow_data_contract.md
    session_events_contract.md
    session_files_security.md
    deletion_contract.md
  workflows/
    oc_analysis.md
    oc_rebuild.md
    oc_storyboard.md
  runbooks/
    oc_analysis_pitfalls.md
    task_reset.md
  reviews/
    opencrew_workflow_data_storage_prd_review.md
    oc_analysis_tool_page_artifact_review.md
```

清理事项：

- 统一 repo-relative 路径。
- 去掉个人机器路径。
- 清理跨包深相对 import，例如宿主前端直接 import `../../OpenClip/frontend/src/...`；建立稳定 module boundary 或 workspace package alias。
- 清理 `parents[3].parent + OpenCrew/...` 这类依赖仓库目录名的路径拼接。
- 区分“当前实现”和“目标设计”。
- 将 review、PRD、runbook、implementation plan 分开。
- `ToolLibrary/Analysis/RUNBOOK_*` 迁移或索引到 `docs/runbooks/` 时必须明确单一真相来源，避免两份 runbook 漂移。

## 5. 建议执行顺序

### 第 1 阶段：止血

时间目标：2-4 周。若必须压缩到 1-2 周，应拆成更小的止血切片：先做 migration baseline、share/event 过滤、registry 路径、File API denylist；统一 event/file/delete service 可以分批收敛。

任务：

1. 建立 migration baseline，后续 P0 schema 改动都走 migration。
2. 增加 `session_events.visibility/event_scope/severity`，并实现字段为空时的 kind 推断兼容。
3. 修 Share Page / Session Detail / Debug Console 事件过滤。
4. 修 OC-Rebuild registry 路径和禁止静默 fallback。
5. 统一 OpenCode event 桥接脱敏。
6. 统一 File API 安全读取，并提供 `session_files` 可见性兜底策略。
7. 修通用 session delete，改为 DB-first、workspace cleanup second。
8. 增加 StoryBoard 最小 DB discriminator，支撑 P0 删除和分享分流。
9. 给上述内容补合同测试。

产出：

- 安全边界不再依赖前端。
- 删除不再产生孤儿数据。
- public/debug/internal 事件可隔离。

### 第 2 阶段：数据模型收敛

时间目标：2-3 周

任务：

1. 统一 Task / Version / Attempt 返回 envelope。
2. 补齐 OC-Rebuild attempt lineage。
3. 决策并重构 StoryBoard 边界。
4. 给 `session_files` 增加 attempt / visibility。
5. 统一 current version 删除策略。

产出：

- workflow 生命周期一致。
- 结果可追溯。
- workspace 不再承担主状态职责。

### 第 3 阶段：Analysis 工具链迁移

时间目标：2-4 周

任务：

1. 标准化 Analysis 工具 `--print-json`。
2. 建立 Analysis Runner Adapter。
3. 稳定 attempt result index。
4. 前端完全以 manifest / result index 为主绑定。
5. 旧 runner 逐步降级为兼容路径。

产出：

- OC-Analysis 不再依赖目录扫描。
- stale 文件不会污染页面。
- 工具输出可被 Plan Runner 消费。

### 第 4 阶段：WorkflowAssistant 执行闭环

时间目标：3-5 周起，属于最大不确定项

任务：

1. 定义 runner 持久状态模型。
2. 实现 Plan Runner。
3. 实现 step progress。
4. 实现 suspended / resume。
5. 打通 Tool Registry 参数 schema。
6. 将 execution events 进入统一 session event 流。
7. 先修 registry 路径 validation，再实现真实执行。
8. 为高成本 step 实现幂等键、resume 防重复执行和 force rerun 语义。

产出：

- Assistant plan 可以真正执行。
- 高成本步骤可确认、暂停、恢复。
- Debug Console 可以恢复执行过程。

### 第 5 阶段：工程化收尾

时间目标：持续进行

任务：

1. 完善 migration 治理和旧 `ensure_*_columns()` 收敛。
2. 补 CI 合同测试。
3. 整理 docs。
4. 清理 legacy fallback。
5. 建立 release checklist。

产出：

- schema 演进可控。
- 架构合同可自动验证。
- 文档可作为团队执行基线。

## 6. 风险和取舍

### 风险 1：一次性重构范围过大

缓解：

- 先做 P0 安全和一致性。
- 保留旧 runner 和 legacy fallback。
- 用 adapter 做渐进迁移。

### 风险 2：历史数据迁移复杂

缓解：

- 历史 `session_events` 默认 internal/debug。
- 历史 `session_files` 默认 downloadable=false，再对白名单放开。
- 对历史 attempts 保留 `result_manifest_json`，新增映射层，不立即硬改字段。

### 风险 3：StoryBoard 边界决策影响较大

缓解：

- 先增加显式 `workflow_mode` 或 source 修正。
- 再决定是否拆独立表。
- 避免继续新增依赖 `storyboard_meta.json` 的主状态逻辑。

### 风险 4：WorkflowAssistant execute 涉及真实成本

缓解：

- 先实现 noop/fake tool runner。
- 所有 LLM/VLM/high-cost step 默认 requires confirmation。
- 所有付费 step 必须有 step 级幂等键和已完成输出检查，避免 resume/retry 重复调用外部 API。
- 用短视频和 fake model 做 smoke。

### 风险 5：安全修复破坏历史 virtual playback

缓解：

- P0 File API symlink 收紧与 `source_video.mp4` workspace 内复制能力同批发布。
- 对存量 OC-Analysis task 提供检测和修复脚本，找出 manifest 指向 workspace 外部 source video 的任务。
- Range/206 只做回归测试，不把已由框架支持的能力重写成自研流式响应。

## 7. 完成标准

repo 改进计划完成后，应满足：

- 所有 workflow 都能从 DB 追踪 Task -> Session -> Version -> Attempt -> Result。
- Session Detail、Debug Console、Share Page 共享事件源但显示边界不同。
- session event 支持可靠 cursor / replay，Debug Console 断线重连不丢中间事件。
- Share Page 不暴露 debug/internal 事件和敏感文件。
- File API 无 path traversal、workspace escape、hidden/sensitive 泄露。
- 删除任意 workflow task/session 不产生孤儿数据。
- OC-StoryBoard 不再通过 workspace 文件判断 workflow 类型。
- OC-Rebuild attempt 能追踪实际使用的 prompt/runtime/model。
- OC-Rebuild Assistant 使用 Rebuild registry，不会静默回退到 Analysis registry。
- OC-Analysis 页面由 result index / scheme manifest 驱动，不受 stale 文件影响。
- WorkflowAssistant confirmed plan 可以执行、暂停、恢复和审计。
- DB schema 演进由 migration 管理。
- 核心架构合同有自动化测试。
- event / workspace / share token 的保留、归档和 GC 策略已定义。
- event cursor、attempt_no、plan confirm / execute 在多 worker 下有 DB 级一致性保障。

## 8. 优先落地清单

建议立即创建以下任务：

1. migration baseline。
2. `session_events` visibility migration + API filter + kind 推断兼容。
3. Share Page event/file visibility 修复。
4. File API safe path / hidden / sensitive / symlink / zip 统一服务。
5. OC-Rebuild Tool Registry 路径修复，禁止静默 fallback 到 Analysis registry。
6. OpenCode SSE -> `session_events` 桥接脱敏和默认 internal/debug 标记。
7. Session delete 分派到 workflow deletion service，并改为 DB-first 删除顺序。
8. StoryBoard 最小 DB discriminator。
9. session event `seq_id` / replay cursor 设计和断线重连测试。
10. OC-Rebuild attempt lineage 字段和 run 校验顺序修复。
11. StoryBoard workflow 边界决策文档和完整模型重构。
12. `session_files` attempt_id / visibility 字段设计。
13. WorkflowAssistant runner 持久状态模型。
14. Analysis result index schema 文档。
15. WorkflowAssistant Plan Runner MVP。
16. retention / GC 策略文档。
17. 多 worker 并发一致性测试设计。
18. 合同测试目录和首批 P0 测试。
