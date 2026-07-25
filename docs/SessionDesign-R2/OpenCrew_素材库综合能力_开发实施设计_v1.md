# OpenCrew 素材库综合能力开发实施设计

版本：v1.1.6

日期：2026-07-22

状态：Implementation-ready；M0–M4 与专项 v0.3.1 R0A–R4 已于 2026-07-22 完成本地实现和验收

文档导航：[OpenCrew 素材库文档索引](./OpenCrew_素材库文档索引.md)

需求基线：[OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_需求评审.md](./OpenCrew_素材库综合分析_视频剪辑与跨页面语义检索_需求评审.md) v0.9.5

本文只细化产品首版的开发实现。若本文与需求评审在业务范围上冲突，以需求评审 v0.9.3 为准；若需求评审只描述目标而未确定代码、表、接口或事务边界，以本文为开发合同。

变更记录：

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v1.1 | 2026-07-20 | M0–M4 首版实施合同。 |
| v1.1.1 | 2026-07-21 | 记录 `visual_semantic_prompt_v2` 中文约束及 composite prompt 从 v2 经 v4（引用审计）、v5（固定字段/全量修复）到当前 v6（过度合并防护）的演进；同步长固定机位分析窗口、无音轨状态边界与既有视觉调用配额；不扩大首版检索范围。 |
| v1.1.2 | 2026-07-21 | 补充业务适用性门禁：M0–M4 对对白优先场景完成，但对无声素材为主的目标客户不构成上线充分条件；无声原视频画面召回必须按专项 v0.2.2 R1 独立优先交付，派生片段复用不反向阻塞 R1。 |
| v1.1.3 | 2026-07-22 | 文档治理更新：将迁移 0018、58 项基线测试和第 2.3 节缺口明确标为实施前历史快照；新增统一文档索引，不改变实现合同。 |
| v1.1.4 | 2026-07-22 | 固定 R1 四帧视觉语义前向合同：每片段四个稳定采样槽、单次多图 VLM、逐帧 hash/证据、聚合负载与计量门禁；`scene_midpoint_v1` 保持 M0–M4 只读兼容，不允许直接进入 R1 视觉检索回填。 |
| v1.1.5 | 2026-07-22 | 同步专项 v0.3 实施验收：0023/0024、四帧 visual index、原视频/派生片段双 Candidate、三入口 import_clip、PostgreSQL 500/2,000 性能和浏览器发布门禁已完成。 |
| v1.1.6 | 2026-07-22 | 同步专项 v0.3.1：R1/R2 现有两个 feature flag 缺失时默认启用，仍支持显式关闭和无效值 fail closed，不新增环境变量；clip eligibility 继续逐片段 opt-in。 |

业务适用性说明：M0–M4 继续作为技术与回归基线；专项 v0.3.1 已完成 `scene_uniform_4_v1` 四帧视觉索引、三入口无声原视频召回，以及派生片段 metadata/eligibility、双 Candidate 和跨 Task 直接复用。R1/R2 功能缺省启用，但每个 clip 仍须显式 opt-in。本文以下“单中点/只召回对白/clip 永不进入”的表述只描述明确标注的实施前或 M0–M4 历史阶段，不再是当前合同。

## 1. 实施结论

本需求不需要再补另一份通用“概要设计”。本文完成后，可以按第 18 节任务顺序直接进入编码。

首版由四条可独立验证、最终一起发布的链路组成：

1. 稳定素材身份、分析运行历史和对白中心索引。
2. `03_03` 最小视觉语义与综合分析。
3. StoryBoard、Agent 和剪辑页共享的全局素材检索与导入。
4. 单原始视频时间轴、进程内 FFmpeg 剪切和派生片段导入。

以下决定已经锁定，不在编码阶段重新讨论：

- 原始视频不可原地替换；新内容必须新上传并生成新 `asset_id`。
- `source_version` 等于源文件规范化小写 SHA-256。
- 普通用户只使用自动激活的最新成功分析结果，不提供历史列表和旧结果激活。
- `stale` 当前结果只读可见，不参加检索，也不能初始化默认剪切选区。
- 业务分析状态不使用 `completed`；`completed` 只属于 Tool Session 和 clip job。
- 首版检索只承诺不超过 500 条原始视频，正常搜索 PostgreSQL 代表数据集端到端 P95 不超过 3 秒。
- 外部 provider 视频首版只能整条导入 StoryBoard，不能直接打开剪辑器。
- 只有全局素材库原始视频能进入本需求的剪辑器。
- 剪切任务只存在于当前后端进程；服务重启后返回 `clip_job_lost`。
- 成功派生片段持久化，不写入原视频集合；默认不可检索，只有显式 opt-in 后才作为 `derived_clip` 召回。
- M0–M4 历史 `03_03` 使用单中点；专项 v0.3 当前结构/语义 v2 固定每片段四帧且一次请求四图，不能证明的连续动作仍必须为 `null`。
- 首版按业务常见的 10 分钟以内视频设计和发布验收；30 分钟 synthetic 视频只作为非阻塞压力测试，不作为发布门禁。

## 2. 已核实的代码与运行基线

### 2.1 后端

当前可复用入口：

| 能力 | 当前文件 | 实施处理 |
| --- | --- | --- |
| 素材列表、详情、归档、删除 | `backend/opcrew_backend/routes/media_library.py` | 保留；扩充详情 DTO 和分析运行接口 |
| 分片上传和最终合并 | `backend/opcrew_backend/media_library_upload/` | 在最终合并阶段增加 SHA-256 |
| 对白分析 | `backend/opcrew_backend/media_library_analysis/dialogue.py` | 接入业务 run、发布器和正确的 blocked 映射 |
| Scene Detect 与中点 Keyframe | `backend/opcrew_backend/media_library_analysis/visual.py` | 定义为 `visual_structure`，再编排 `visual_semantic` |
| Tool Session 收尾与文件登记 | `backend/opcrew_backend/media_library_analysis/lifecycle.py` | 保留并统一调用 |
| 素材、素材任务仓储 | `backend/opcrew_backend/repositories/media_library*.py` | 保留兼容投影；新增专用仓储 |
| StoryBoard 外部搜索与导入 | `backend/opcrew_backend/koubo/koubo_storyboard/asset_search_*` | 复用 provider、运行文件、授权和 manifest；不复用远程下载分支处理全局素材 |
| StoryBoard Task 列表 | `GET /api/koubo-storyboard/tasks` | 目标选择服务在后端二次过滤 |
| StoryBoard 视频目录 | `SessionOutput/storyboard/assets/videos` | 原始素材和 clip 导入目标 |

实施开始前（2026-07-20）的迁移最后一个 ID 是：

```text
0018_media_library_upload_finalization
```

OpenCrew 仓库内置运行基线已经核实：

```text
PostgreSQL 16.14
ToolLibrary/.bin/ffmpeg 7.0
ToolLibrary/.bin/ffprobe 7.0
libx264 available
AAC encoder available
```

实施基线核验时，开发机的 Homebrew/PATH 同时存在 FFmpeg/ffprobe 8.1.1。两套版本不是矛盾的运行基线：Tool Session 默认显式注入仓库内置 7.0；开发者交互式 shell 才会直接找到 PATH 中的 8.1.1。

新增 clip service 必须复用统一解析顺序，不能裸调用不确定来源的 `ffmpeg` 或 `ffprobe`：

```text
1. OPENCREW_FFMPEG_PATH / OPENCREW_FFPROBE_PATH 指向的可执行文件
2. ToolLibrary/.bin/ffmpeg / ToolLibrary/.bin/ffprobe
3. shutil.which("ffmpeg") / shutil.which("ffprobe")
```

服务启动时记录最终绝对路径、版本和 `libx264/AAC` capability，但不得记录其他环境变量。发布合同以仓库内置 7.0 为必测基线；若部署机同时有 8.x，执行相同剪切 fixture 的兼容 smoke。已核验 7.0 与 8.1.1 在本文编码参数下得到相同的解码音视频帧序列。

实施前相关合同测试共 58 项通过，前端生产构建通过。该历史结果只用于说明当时的对白、画面、上传和 StoryBoard 搜索回归起点；M0–M4 的完成证据以验收记录为准。

### 2.2 前端

当前素材库使用 SolidJS，路由由 hash 手工解析：

```text
frontend/src/modules/mediaLibrary/mediaLibraryModel.js
frontend/src/modules/mediaLibrary/MediaLibraryModule.jsx
```

现有正则会把 `#/media-library/{asset_id}/editor` 误当成详情页。因此编辑器路由必须先于详情路由匹配。

所有新增通用 API 必须加入：

```text
frontend/src/lib/api.ts
```

不得在新组件中另建模块级 fetch helper。StoryBoard 既有 API 可以继续使用当前 `kouboStoryboardApi.js`；共享类型和新素材库接口仍以 `api.ts` 为主。

### 2.3 实施前缺口（M0–M4 后已按本文补齐）

实施开始前的代码没有：

- `content_sha256/source_version`。
- 独立 `analysis_run_id` 和分析历史表。
- `visual_structure_status/visual_semantic_status`。
- `03_03` VLM 视觉语义步骤。
- 综合分析服务。
- 中心 fragment 索引和共享全局搜索服务。
- 搜索遥测数据库记录。
- 全局原始视频跨 Session 复制分支。
- 素材编辑器路由和时间轴。
- clip job manager 和派生片段表。

## 3. 目标架构

```text
原始视频上传
  -> content_sha256/source_version
  -> Dialogue Tool Session
       -> dialogue analysis run
       -> dialogue fragments 原子发布
  -> Visual Structure Tool Session (01, 03_01, 03_02)
       -> visual_structure run
  -> Visual Semantic Tool Session (03_03)
       -> visual_semantic run
  -> Composite Tool Session (04_01)
       -> composite run
       -> composite fragments 原子发布

中心 fragment index
  -> MediaLibrarySearchService
       -> StoryBoard Dialogue 检索
       -> Agent - Asset Library 的 media_library source
       -> 视频剪辑页全局素材来源

原始视频 + 当前分析 fragments
  -> 单素材编辑器
       -> 进程内 FFmpeg clip job
       -> media_library_clip_derivatives
       -> 导入 StoryBoard Asset Pool
```

分层原则：

1. Tool Session 负责工具步骤、Attempt、产物和执行审计。
2. `media_library_analysis_runs` 负责业务运行身份、业务状态、上游版本和当前结果。
3. `media_library_fragment_index` 负责跨素材召回，不在请求时扫描 Session JSON。
4. `media_library_search_runs/actions` 负责检索质量和用户行为遥测。
5. `media_library_clip_derivatives` 只记录成功的物理派生片段。
6. clip job 的排队、运行、取消和失败只保存在当前进程内。
7. StoryBoard manifest 保存复制到目标 Task 后的资产身份和 provenance。

## 4. 新增与修改的代码目录

### 4.1 后端

新增：

```text
backend/opcrew_backend/media_library_analysis/
  __init__.py
  contracts.py
  run_repository.py
  lifecycle.py
  visual_semantic_contracts.py
  visual_semantic.py
  composite_contracts.py
  composite.py

backend/opcrew_backend/media_library_search/
  __init__.py
  schemas.py
  repository.py
  normalization.py
  planner.py
  model_planner.py
  service.py
  telemetry.py
  router.py

backend/opcrew_backend/media_library_clips/
  __init__.py
  models.py
  errors.py
  repository.py
  storage.py
  ffmpeg.py
  processor.py
  manager.py
  router.py

backend/opcrew_backend/media_library_imports/
  __init__.py
  schemas.py
  repository.py
  service.py
  router.py

backend/scripts/backfill_media_library_source_hashes.py
backend/scripts/rebuild_media_library_fragment_index.py
backend/scripts/benchmark_media_library_search_postgres.py

ToolLibrary/OpenCut_V1/03_03_KeyframeVisualSemantic.py
ToolLibrary/OpenCut_V1/04_01_CompositeSemanticIndex.py
```

实现落位说明：

- dialogue/composite fragment 的原子发布器由
  `media_library_search/repository.py::MediaLibraryFragmentPublisher`
  统一提供，不另设 analysis publisher。
- 视觉语义和综合分析的模型适配分别收敛在对应 service 模块；
  search planner 的生产模型适配位于
  `media_library_search/model_planner.py`。
- StoryBoard/Agent 的共享搜索适配接入现有
  `koubo_storyboard/asset_search_services.py`，不另复制一套
  `koubo_adapter.py`。

修改：

```text
backend/opcrew_backend/db/schema.py
backend/opcrew_backend/db/migrations.py
backend/opcrew_backend/context.py
backend/opcrew_backend/app.py
backend/opcrew_backend/model_policy.py
backend/opcrew_backend/routes/media_library.py
backend/opcrew_backend/media_library_upload/storage.py
backend/opcrew_backend/media_library_upload/repository.py
backend/opcrew_backend/media_library_upload/service.py
backend/opcrew_backend/media_library_analysis/dialogue.py
backend/opcrew_backend/media_library_analysis/visual.py
backend/opcrew_backend/repositories/media_library.py
backend/opcrew_backend/repositories/media_library_tasks.py
backend/opcrew_backend/koubo/koubo_storyboard/asset_search_routes.py
backend/opcrew_backend/koubo/koubo_storyboard/asset_search_services.py
backend/opcrew_backend/koubo/koubo_storyboard/asset_search_providers.py
ToolLibrary/OpenCut_V1/tool_registry.json
ToolLibrary/OpenCut_V1/README.md
```

`03_03` 和 `04_01` 的 ToolLibrary Python 文件只保存纯合同、规范化和结果校验逻辑。实际模型调用由后端自定义 `ToolAdapter` 完成，密钥和数据库 URL不得传给工具子进程。

### 4.2 前端

新增：

```text
frontend/src/modules/mediaLibrary/editor/
  editorModel.js
  timelineModel.js
  EditorTimeline.jsx
  EditorSearchPanel.jsx
  EditorClipPanel.jsx
  mediaLibraryEditor.css

frontend/src/modules/mediaLibrary/pages/
  MediaLibraryEditorPage.jsx

frontend/src/modules/koubo/KouboStoryBoard/components/
  MediaLibrarySearchDialog.jsx

frontend/src/modules/koubo/
  mediaLibrarySearchModel.js
```

编辑器页面保留在现有 `pages/` 路由层；播放器、时间轴、
fragment inspector、搜索和 clip job/StoryBoard 目标交互按状态边界聚合到
上述三个 editor panel，而不是拆成无独立状态合同的细粒度占位组件。

修改：

```text
frontend/src/lib/api.ts
frontend/src/modules/mediaLibrary/MediaLibraryModule.jsx
frontend/src/modules/mediaLibrary/mediaLibraryModel.js
frontend/src/modules/mediaLibrary/pages/MediaLibraryDetailPage.jsx
frontend/src/modules/mediaLibrary/detail/mediaLibraryDetailModel.js
frontend/src/modules/mediaLibrary/detail/MediaLibraryDetailHeader.jsx
frontend/src/modules/mediaLibrary/detail/MediaLibraryToolDrawer.jsx
frontend/src/modules/koubo/KouboStoryBoard/components/AssetPanel.jsx
frontend/src/modules/koubo/UploadAssetLibrary/
```

## 5. 数据库迁移设计

所有表同时写入 SQLAlchemy `schema.py`，保证新数据库 bootstrap 和迁移后的数据库一致。migration 仍使用当前 `migrations.py` 函数注册方式。

JSON 字段在 SQLAlchemy 层使用 `JSON`，生产 PostgreSQL 使用原生 JSON；首版不依赖 JSONB 专用操作符。时间一律为 Unix epoch 整数毫秒。

### 5.1 `0019_media_library_source_identity_and_analysis_runs`

给 `media_library_assets` 增加：

```text
content_sha256       TEXT NULL
content_hashed_at    BIGINT NULL
```

约束由服务和回填验证器共同执行：

```text
content_sha256 is null
or content_sha256 matches ^[0-9a-f]{64}$
```

不对 `content_sha256` 建唯一约束。重复上传相同内容仍可形成不同 `asset_id`；索引仅用于版本确认和重复提示。

新增 `media_library_analysis_runs`：

| 列 | 类型 | 约束/含义 |
| --- | --- | --- |
| `analysis_run_id` | TEXT | 主键，`mlar_{scheme}_{time}_{random}` |
| `asset_id` | TEXT | FK `media_library_assets.asset_id ON DELETE CASCADE` |
| `scheme` | TEXT | `dialogue/visual_structure/visual_semantic/composite` |
| `source_version` | TEXT | 64 位小写 SHA-256 |
| `status` | TEXT | 业务状态，见第 7 节 |
| `tool_use_session_id` | TEXT | 可空；存在时唯一 |
| `attempt_id` | BIGINT | Tool Session 当前/最终 Attempt |
| `prompt_version` | TEXT | 无模型步骤可空 |
| `model_config_id` | TEXT | 只存内部配置 ID，不向普通用户暴露 provider 密钥 |
| `model_session_id` | TEXT | VLM/LLM run 的独立 OpenCode session，可空 |
| `schema_version` | TEXT | 发布结果 schema |
| `result_hash` | TEXT | 成功或 stale 结果的规范化内容哈希 |
| `result_index_path` | TEXT | 相对来源 workspace 路径 |
| `upstream_refs_json` | JSON | 上游 run/hash/采样版本冻结快照 |
| `progress_json` | JSON | 业务进度 |
| `error_code` | TEXT | 结构化错误码 |
| `error_json` | JSON | 脱敏错误详情 |
| `is_current` | BOOLEAN | 当前普通用户可见结果 |
| `started_at` | BIGINT | 可空 |
| `finished_at` | BIGINT | 可空 |
| `created_at` | BIGINT | 非空 |
| `updated_at` | BIGINT | 非空 |

字段约束：

```text
scheme in (dialogue, visual_structure, visual_semantic, composite)
status in (queued, running, blocked, ready, stale, failed)
is_current boolean not null default false
source_version 只允许 64 位小写十六进制
result_hash 在 ready/stale 时为 64 位小写十六进制，其他状态可空
```

索引：

```sql
CREATE UNIQUE INDEX ux_media_library_analysis_runs_current
ON media_library_analysis_runs(asset_id, scheme)
WHERE is_current = TRUE;

CREATE UNIQUE INDEX ux_media_library_analysis_runs_tool_session
ON media_library_analysis_runs(tool_use_session_id)
WHERE tool_use_session_id IS NOT NULL;

CREATE INDEX ix_media_library_analysis_runs_asset_scheme_created
ON media_library_analysis_runs(asset_id, scheme, created_at DESC);

CREATE UNIQUE INDEX ux_media_library_analysis_runs_one_active
ON media_library_analysis_runs(asset_id, scheme)
WHERE status IN ('queued', 'running');
```

SQLite 合同路径也支持上述 partial index；如果测试环境版本不支持表达式 introspection，迁移合同测试直接查询 `sqlite_master`。

给 `media_library_tasks` 增加兼容投影列：

```text
dialogue_current_run_id
visual_structure_status
visual_structure_current_run_id
visual_semantic_status
visual_semantic_current_run_id
visual_semantic_tool_use_session_id
visual_semantic_error
visual_semantic_progress_json
composite_current_run_id
composite_tool_use_session_id
composite_error
composite_progress_json
```

现有 `visual_status` 保留为派生总状态。现有 `dialogue_*` 和 `visual_*` 字段保留，避免一次性破坏详情页，但所有更新必须由同一 `AnalysisRunRepository` 事务同步，不允许服务各自直接写出互相矛盾的状态。

历史状态迁移：

```text
old visual_status=ready
  -> visual_structure_status=ready
  -> visual_semantic_status=not_analyzed
  -> derived visual_status=partial

old visual_status in queued/running/processing
  -> visual_structure_status=对应 queued/running
  -> visual_semantic_status=not_analyzed

old visual_status=failed
  -> visual_structure_status=failed
  -> visual_semantic_status=not_analyzed
```

迁移不创建历史 run，不调用模型，也不把 `ready` 误认为视觉语义完成。

### 5.2 `0020_media_library_fragment_search`

新增 `media_library_fragment_index`：

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT identity | 主键 |
| `asset_id` | TEXT | 原始视频 |
| `source_session_id` | BIGINT | 来源文件 Session，非权限边界 |
| `source_version` | TEXT | 源 SHA-256 |
| `analysis_scheme` | TEXT | `dialogue/composite`；首版 visual 不作为独立召回资格 |
| `analysis_run_id` | TEXT | FK analysis run |
| `result_hash` | TEXT | 发布结果 hash |
| `fragment_id` | TEXT | run 内稳定 ID |
| `start_ms` | BIGINT | 左闭 |
| `end_ms` | BIGINT | 右开 |
| `dialogue_text` | TEXT | 可空 |
| `title` | TEXT | 可空 |
| `summary` | TEXT | 可空 |
| `keywords_json` | JSON | 数组 |
| `visual_labels_json` | JSON | 数组 |
| `keyframe_ref_json` | JSON | 受控引用，不保存绝对路径 |
| `search_text` | TEXT | Unicode 规范化后用于首版召回 |
| `search_lexemes_text` | TEXT | 首版为空，FTS 增强使用 |
| `tokenizer_name` | TEXT | 首版 `none` |
| `tokenizer_version` | TEXT | 首版 `none` |
| `dictionary_hash` | TEXT | 首版为空 |
| `normalization_version` | TEXT | 首版固定 `nfkc_casefold_ws_v1` |
| `quality_status` | TEXT | `ready/review` |
| `confidence` | REAL | 0..1，可空 |
| `is_active` | BOOLEAN | 只有 current ready 才为 true |
| `created_at` | BIGINT | 非空 |
| `updated_at` | BIGINT | 非空 |

约束和索引：

```text
unique (analysis_run_id, fragment_id)
check 0 <= start_ms < end_ms
index (is_active, analysis_scheme, asset_id)
index (asset_id, analysis_scheme, is_active)
index (analysis_run_id)
```

发布器必须在同一数据库事务中：

1. 校验全部新 fragments。
2. 插入/幂等确认新 run 的全部 fragments，暂设 `is_active=false`。
3. 将同素材、同 scheme 的旧 fragments 设为 false。
4. 将新 run fragments 设为 true。
5. 切换 run `is_current` 和 task current pointer。
6. 更新素材分析摘要。

任一步失败整笔回滚，旧 active fragments 保持完整。

新增 `media_library_search_runs`：

```text
search_id                  TEXT primary key
entry_point                TEXT not null
target_task_id             BIGINT null
dialogue_asset_key         TEXT null
source_asset_id            TEXT null
query_source               TEXT not null
query_hash                 TEXT not null
query_plan_json            JSON not null
planner_version            TEXT not null
retrieval_version          TEXT not null
planner_degraded           BOOLEAN not null
requested_sources_json     JSON not null
source_runs_json           JSON not null
status                     TEXT not null
result_count               INTEGER not null default 0
zero_result                BOOLEAN not null default true
planner_latency_ms         BIGINT null
retrieval_latency_ms       BIGINT null
total_latency_ms           BIGINT null
top_candidates_json        JSON not null
error_code                 TEXT null
created_at                 BIGINT not null
updated_at                 BIGINT not null
```

约束：

```text
entry_point in (storyboard, agent, editor)
status in (queued, running, completed, failed)
```

新增 `media_library_search_actions`：

```text
id                         BIGINT identity primary key
search_id                  TEXT not null references media_library_search_runs
action_kind                TEXT not null  # preview/open_editor/import
source                     TEXT not null
candidate_id               TEXT not null
source_asset_id            TEXT null
candidate_rank             INTEGER null
target_task_id             BIGINT null
metadata_json              JSON not null
created_at                 BIGINT not null
```

索引：

```text
(search_id, created_at)
(action_kind, created_at)
(target_task_id, created_at)
```

### 5.3 `0021_media_library_clip_derivatives`

新增 `media_library_clip_derivatives`：

```text
clip_id                    TEXT primary key
idempotency_key            TEXT not null unique
source_asset_id            TEXT not null references media_library_assets
source_session_id          BIGINT not null references sessions
source_version             TEXT not null
source_start_ms            BIGINT not null
source_end_ms              BIGINT not null
source_scheme              TEXT null
source_fragment_id         TEXT null
source_analysis_run_id     TEXT null
source_search_id           TEXT null
source_dialogue_asset_key  TEXT null
output_path                TEXT not null
display_name               TEXT not null
duration_ms                BIGINT not null
content_sha256             TEXT not null
size_bytes                 BIGINT not null
operation                  TEXT not null default 'precise_reencode_v1'
search_eligible            BOOLEAN not null default false
created_at                 BIGINT not null
```

约束：

```text
check source_start_ms >= 0
check source_end_ms > source_start_ms
check duration_ms > 0
check search_eligible = false
unique (source_session_id, output_path)
```

不创建 clip job 表。

### 5.4 `0022_media_library_storyboard_imports`

新增 `media_library_storyboard_imports`，用于跨 Session 复制的幂等与审计：

```text
import_id                   TEXT primary key
idempotency_key             TEXT not null unique
source_kind                 TEXT not null  # media_library_original/media_library_clip
source_asset_id             TEXT not null
source_clip_id              TEXT null
source_version              TEXT not null
source_search_id            TEXT null
source_dialogue_asset_key   TEXT null
target_task_id              BIGINT not null
target_session_id           BIGINT not null
target_path                 TEXT not null
target_manifest_asset_id    TEXT not null
content_sha256              TEXT not null
size_bytes                  BIGINT not null
requested_name              TEXT null
status                      TEXT not null
error_code                  TEXT null
created_at                  BIGINT not null
updated_at                  BIGINT not null
```

唯一约束：

```text
unique (target_session_id, target_path)
check status in (preparing, completed, failed)
```

外键：

```text
source_asset_id -> media_library_assets.asset_id
source_clip_id -> media_library_clip_derivatives.clip_id
source_search_id -> media_library_search_runs.search_id
target_task_id -> openclip_tasks.id
target_session_id -> sessions.id
```

外部 provider 导入继续使用现有 Search Agent import record，不写入该表。

### 5.5 哈希回填不是 schema migration

`0019` 只增加 nullable 列，不在应用启动 migration 中读取大文件。

回填脚本：

```text
backend/scripts/backfill_media_library_source_hashes.py
```

命令合同：

```bash
backend/.venv/bin/python backend/scripts/backfill_media_library_source_hashes.py
backend/.venv/bin/python backend/scripts/backfill_media_library_source_hashes.py --write
backend/.venv/bin/python backend/scripts/backfill_media_library_source_hashes.py --write --limit 50
backend/.venv/bin/python backend/scripts/backfill_media_library_source_hashes.py --write --asset-id mla_...
```

规则：

- 默认 dry-run。
- 只处理 `upload_status=ready AND content_sha256 IS NULL`。
- 每个文件按 1 MiB 流式读取。
- 写入前重新校验文件大小和素材记录。
- 重复执行幂等。
- 文件缺失或运行中变化时记录失败，不写 hash。
- 不自动启动任何分析或模型任务。

启用新索引发布 feature flag 前，验证：

```sql
SELECT count(*)
FROM media_library_assets
WHERE upload_status = 'ready' AND content_sha256 IS NULL;
```

结果必须为 0。

### 5.6 新上传文件的 SHA-256

不在 merge 完成后再次完整读取最大 50 GB 文件。修改 `merge_chunks()`，在现有分片写入最终 `.part` 的同一循环中更新 `hashlib.sha256()`，返回：

```text
source_rel
final_path
content_sha256
```

最终合并大小不等于声明大小时，不返回 hash、不发布 ready。`mark_ready()` 在持有 finalization token 的同一数据库事务写：

```text
upload_status=ready
source_video_path
content_sha256
content_hashed_at
```

崩溃恢复复用一个已经原子 rename 的完整 final file 时，该旧路径没有同流 hash；恢复 worker 允许对完整文件流式读取一次计算 hash，再发布 ready。除这条 stale-finalization 恢复分支外，不做第二次完整读取。

## 6. 身份、时间与结果 hash 合同

### 6.1 ID

```text
analysis_run_id  mlar_{scheme}_{13位毫秒}_{12位随机十六进制}
search_id        mls_{13位毫秒}_{12位随机十六进制}
clip_id          mlc_{13位毫秒}_{12位随机十六进制}
import_id        mli_{13位毫秒}_{12位随机十六进制}
clip_job_id      clipjob.{128位随机boot_id}.{uuid4hex}
```

ID 由后端生成。客户端只生成不透明 `idempotency_key`，长度 16–128，允许 `[A-Za-z0-9._:-]`。

### 6.2 时间

新增业务合同只允许：

```text
start_ms
end_ms
duration_ms
keyframe_time_ms
```

区间语义固定为 `[start_ms, end_ms)`。发布时断言：

```text
0 <= start_ms < end_ms <= source_duration_ms
duration_ms == end_ms - start_ms
```

旧工具输出进入发布合同前先做一次确定性边界规范化：当尾部
`end_ms` 因媒体探测与字幕/Scene 四舍五入差异超过
`source_duration_ms` 时，钳制到素材时长；钳制后
`end_ms <= start_ms` 的退化条目跳过，不让单条零时长结果使整个 run
失败。规范化后的已发布条目仍必须满足上述严格断言，负数起点仍拒绝。

旧 OpenCut 秒字段只在发布适配器中转换：

```python
milliseconds = round(float(seconds) * 1000)
```

转换结果写入新结果文件和中心索引，不修改旧工具原始产物。

### 6.3 规范化结果 hash

`result_hash` 计算流程：

1. 转换所有时间为整数毫秒。
2. 删除 URL、绝对路径、运行时间、`created_at/updated_at` 等非内容字段。
3. 数组按业务顺序保存；集合型标签去重后按 Unicode code point 排序。
4. 禁止 NaN/Infinity。
5. 使用：

```python
json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
).encode("utf-8")
```

6. 对字节计算 SHA-256 小写十六进制。

相同 schema 和内容必须得到相同 hash。

`media_library_assets` 不再额外保存一列可能漂移的 `source_version`。API、run、fragment、clip 和 import 中的 `source_version` 均由当前权威 `content_sha256` 复制；素材 DTO 序列化为：

```text
source_version = content_sha256
```

## 7. 状态机与一致性

### 7.1 业务状态

单个分析 run 允许：

```text
queued
running
blocked
ready
stale
failed
```

`not_analyzed` 表示没有 run，是 task/页面投影状态。`partial` 只表示画面或素材的聚合状态，不作为成功 run 的终态。业务表禁止写 `completed`。

Tool Session 映射：

| 业务结果 | Tool Session 终态 |
| --- | --- |
| `ready` | `completed` |
| `blocked` | `blocked` |
| `failed` | `failed` |
| `stale` | 不改历史 Tool Session 终态 |

clip job 独立使用：

```text
queued/running/completed/failed/cancelled
```

### 7.2 current 与 active

- `is_current=true`：普通用户当前可见的这一 scheme 结果。
- fragment `is_active=true`：当前可参加召回的结果。
- current ready 通常对应 active fragments。
- current stale 仍可只读查看，但全部 fragments 必须 inactive。
- 新 run 处于 queued/running 时，不自动撤销旧 ready current。
- 新 run 成功并完成发布后，在一个事务中替换 current。
- 新 run blocked/failed 时，旧 current 保持不变。
- 普通用户接口不提供 current 历史列表和 activate。

### 7.3 stale 级联

触发规则：

```text
visual_structure 新 current
  -> 旧 visual_semantic current = stale
  -> 依赖它的 composite current = stale

dialogue 新 current
  -> 依赖旧 dialogue hash 的 composite current = stale

visual_semantic 新 current
  -> 依赖旧 semantic hash 的 composite current = stale
```

stale 事务必须同时：

1. 将 run 状态改为 `stale`，保留 `is_current=true`。
2. 将对应 fragments `is_active=false`。
3. 更新 task 投影。
4. 写 Session event。

新的成功结果激活时，把旧 stale current 改为 `is_current=false`，但不删除审计记录和产物。

### 7.4 画面聚合状态

| structure | semantic | visual |
| --- | --- | --- |
| not_analyzed | not_analyzed | not_analyzed |
| queued/running | 任意 | running |
| ready | blocked | blocked |
| ready | not_analyzed/failed | partial |
| ready | queued/running | running |
| ready | ready | ready |
| ready | stale | stale |
| failed 且无 current 结构结果 | 任意 | failed |

素材总 `analysis_status` 按以下优先级派生：

```text
running > blocked > failed(全部不可用) > stale > partial > ready > not_analyzed
```

不得再由“最后完成的某个 scheme”直接覆盖素材总状态。

### 7.5 并发锁

依赖数据库 partial unique index阻止同素材同 scheme 的两个 active run。服务捕获唯一约束冲突并返回：

```text
409 analysis_run_active
```

发布和 stale 事务按固定顺序锁定：

```text
media_library_assets
  -> media_library_tasks
  -> current analysis runs
  -> fragment rows
```

PostgreSQL 使用 `SELECT ... FOR UPDATE`；SQLite 合同测试在单连接事务中验证同等结果。

后端当前按单应用进程部署。`AppContext` 启动时执行 analysis run reconciliation：把数据库中遗留的 `queued/running` run 标记为 `failed + analysis_worker_lost`，尽力把关联 Tool Session finalize 为 failed，并保留旧 current 结果。首版不自动恢复中断的模型或分析线程，避免页面永久显示“运行中”。

已有 current ready 结果时，失败或 blocked 的重跑不得替换该 current，也不得停用旧 active fragments。Task 投影继续保持可用结果的 ready 状态和 current run ID，但对应 `*_error` 必须显示“本次重新运行未成功，仍在使用上一次成功结果”及安全的失败原因，避免 UI 把本次失败误呈现为成功；下一次运行启动或成功发布时再清除此提示。

## 8. 分析产物与 Tool Session 实现

输入媒体采用以下锁定合同：

1. `prepare.py` 从 `inbox` 到每个 run 的 `0_SessionContext` 创建一份独立输入快照。当前实现是 `shutil.copy2`，不同 run 具有不同 inode。
2. `framework_bridge.py` 的旧式 `SessionContext/Video_Source.*` 只是同一 run 内的兼容别名：同卷必须优先硬链接 `0_SessionContext`，不可用时才复制；可变 JSON 始终独立复制。
3. 以一个原始文件同时执行 dialogue 和 visual structure 为例，正确基线是 5 个逻辑路径、3 个物理 inode，而不是 5 份物理视频。
4. 不允许把 `inbox` 与 `0_SessionContext` 直接硬链接来伪装 run 隔离；工具若原地写入输入会同时破坏原始素材。CoW clone/reflink 只有在部署卷实测能产生独立 inode、写时隔离且失败安全回退到 `copy2` 后才能启用，首版不以此为发布门禁。
5. 存量修复使用 `backend/scripts/repair_media_library_tool_sessions.py`：默认 dry-run，只在内容一致时把 legacy 媒体重链接到同 run 的 `0_SessionContext`。

### 8.1 存量结果采纳

迁移不会凭数据库旧状态伪造 run，但已完成的真实旧产物允许经过“采纳”进入新合同，避免强制重跑 ASR/Scene Detect。

`backend/scripts/rebuild_media_library_fragment_index.py` 默认 dry-run，逐素材执行：

1. 要求 ready asset 已有 `content_sha256`。
2. 从 task 的 current `dialogue_tool_use_session_id/visual_tool_use_session_id` 找到旧 Tool Session。
3. 要求 Tool Session 已由 repair 流程完成可信 finalize/result-sync。
4. 重新读取旧最终产物并执行与新 run 完全相同的秒到毫秒、路径、范围、引用和 schema 校验。
5. 创建 `adopted_legacy=true` 的 business run，关联原 Tool Session，生成新 manifest/result hash。
6. 原子发布并成为 current；不执行任何工具和模型调用。

对白旧产物合法时可直接发布 dialogue fragments。历史 visual structure 旧产物合法时创建 `visual_structure` current；随后用户显式运行画面分析时只需执行 visual semantic。

如果 tool session、源版本、产物或 keyframe 不可信：

- 不采纳。
- 将兼容投影改为 `not_analyzed` 或 `failed` 并返回具体原因。
- 后续由用户重跑对应分析。

同一 `asset_id + scheme + source_version + result_hash` 的采纳重复执行幂等。采纳脚本不运行 ASR、VLM、LLM 或 FFmpeg。

命令合同：

```bash
backend/.venv/bin/python backend/scripts/rebuild_media_library_fragment_index.py
backend/.venv/bin/python backend/scripts/rebuild_media_library_fragment_index.py --write
backend/.venv/bin/python backend/scripts/rebuild_media_library_fragment_index.py --write --asset-id mla_...
backend/.venv/bin/python backend/scripts/rebuild_media_library_fragment_index.py --write --scheme dialogue
backend/.venv/bin/python backend/scripts/rebuild_media_library_fragment_index.py --write --scheme visual_structure
```

开启搜索 flag 前，所有可检索素材必须已有 current ready dialogue run 和至少一条 active fragment。经完整对白工具链确认无对白的素材允许以 current ready、`fragment_count=0` 发布，用于表达“分析成功但没有对白”；它不进入首版对白检索资格。工具失败或产物无效仍标为 failed/not_analyzed，不能伪装成合法空结果。

无音轨是可识别的输入能力限制，不是云 ASR 授权缺失。投影必须区分三个维度，不能再由一个 `reason` 覆盖全部语义：

1. Dialogue 能力事实：`video_has_no_audio` 继续作为审计 error code 保留在业务 run 中；对白面板和辅助标签使用中文“无音轨 / 对白分析不可用”，并指引用户继续画面分析和剪辑。
2. 素材聚合业务状态：Dialogue 无音轨按“不适用”参与聚合，不能无条件覆盖 visual structure、visual semantic 和 composite 的真实状态；这些分析完成后，素材主状态必须如实显示“部分完成”或“已完成”，而不是停留在“无音轨”死胡同。
3. 跨页面检索能力：M0–M4 历史阶段只交付 dialogue/关键词召回；专项 v0.3.1 当前实现已补齐合格四帧 visual semantic 召回。历史单帧、stale、blocked 或 failed 视觉结果仍不获得资格；名称、文件名和人工标签筛选不能冒充画面语义召回。

`frontend/src/modules/mediaLibrary/mediaLibraryModel.js::analysisStatusMeta` 在 M0–M4 实施后曾让 `reason === video_has_no_audio` 无条件覆盖聚合状态；该偏差已随[无声素材视觉检索与派生片段全局复用 v0.3.1](./OpenCrew_无声素材视觉检索与派生片段全局复用_需求变更与开发实施设计_v0.2.md) 的 R1/ML-R105 修正。当前只有后端权威 `visual_search_ready=true` 且视觉语义满足四帧资格时，独立能力标签才显示“可按画面检索”。

### 8.2 对白发布适配器

输入仍为：

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/subtitle/calibrated_srt_items.json
```

新增发布结果：

```text
SessionOutput/json/dialogue_fragment_index.json
SessionOutput/manifests/dialogue_analysis_manifest.json
SessionReport/dialogue_quality_check.json
```

`dialogue_fragment_index.json`：

```json
{
  "schema_version": "media_library_dialogue_fragments_v1",
  "asset_id": "mla_...",
  "source_version": "0123...",
  "analysis_run_id": "mlar_dialogue_...",
  "items": [
    {
      "fragment_id": "srt_0001",
      "start_ms": 0,
      "end_ms": 1200,
      "duration_ms": 1200,
      "dialogue_text": "第一句话",
      "keyframe_refs": ["srt_0001-keyframe"],
      "confidence": null,
      "needs_review": false
    }
  ]
}
```

对白服务顺序：

1. 创建 business run=`queued`。
2. 创建 Tool Session，写回 `tool_use_session_id`。
3. 执行现有 `01/02_01/02_02`。
4. 适配秒字段、schema 校验、生成结果 hash。
5. finalize/result-sync。
6. 原子发布 fragments 并激活 business run。
7. 如果依赖缺失或授权缺失，business run=`blocked`，不能写 `failed`。

### 8.3 Visual Structure

现有 `01/03_01/03_02` 定义为 `visual_structure` run。以下 `scene_midpoint_v1` 是已经验收的 M0–M4 基线；R1 必须按第 8.4 节升级为 `scene_uniform_4_v1`，不能把本段单帧输出直接改名。

`03_01` 先保留 PySceneDetect 的真实切点；单个检测 Scene 超过 15 秒时，再按均衡时长拆成连续分析窗口，并记录 `segment_kind=long_scene_window`、来源 Scene 序号和窗口序号。该内部元数据用于审计和页面文案，发布后的 fragment 仍沿用现有 `scene_XXXX` 身份及 `scene_midpoint_v1` 合同，不新增第二套片段表。74 秒无切点固定机位视频应得到 5 个不超过 15 秒的画面分析片段，而不是一个覆盖全片的片段。

15 秒覆盖窗口不是无上限的 VLM 成本承诺。当前视觉语义服务在模型调用前执行以下既有硬门禁：

1. `OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_SCENES_PER_ASSET` 默认 `300`；单素材结构片段超过该值时，visual semantic run 以结构化 `quota_exceeded` 阻断，不发起 VLM 调用。
2. `OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_CALLS_PER_RUN` 默认 `600`，覆盖每片段一次基础调用和至多一次结构化修复。
3. `OPENCREW_MEDIA_LIBRARY_VISUAL_MAX_EST_COST_MICROS` 默认 `1_000_000`，并结合 `OPENCREW_MEDIA_LIBRARY_VISUAL_EST_COST_PER_CALL_MICROS` 默认 `1_000` 在每次调用前检查；调用次数和估算成本任一先到上限即阻断。

在完全无切点的情况下，M0–M4 单帧基线中，10 分钟产生 40 个窗口，即最多 40 次基础调用、最坏 80 次（每窗均修复）；30 分钟 non-blocking synthetic 产生 120 个窗口，即最多 120 次基础调用、最坏 240 次。R1 四帧仍保持每片段一次基础调用，但每次携带四图，必须另外按第 8.4 节记录并限制 `image_count` 与四图请求成本。

新增发布文件：

```text
SessionOutput/visual/visual_structure_segments.json
SessionOutput/visual/visual_structure_manifest.json
SessionReport/visual_structure_quality_check.json
```

M0–M4 每个 Scene 的历史合同：

```json
{
  "fragment_id": "scene_0001",
  "start_ms": 0,
  "end_ms": 3000,
  "duration_ms": 3000,
  "keyframes": [
    {
      "keyframe_id": "scene_0001-midpoint",
      "keyframe_time_ms": 1500,
      "image_path": "SessionOutput/visual/scene_frames/scene_0001.jpg",
      "image_sha256": "..."
    }
  ],
  "sampling_strategy": "scene_midpoint_v1"
}
```

图片 hash 在 `03_02` 输出完成后计算。任何 keyframe 缺失、越界或 hash 不符都阻止 structure 发布。

### 8.4 `03_03 Keyframe Visual Semantic` 与 R1 四帧升级

`03_03` 使用单独 Tool Session，scheme=`visual_semantic`。它不读取源视频。M0–M4 当前实现仍使用一个中点图像；R1 在保持“不读取源视频”的前提下，消费 `03_02` 已冻结的四张采样图。

prepare 阶段只把当前 structure manifest、Scene JSON 和 Keyframe 文件放入：

```text
0_SessionContext/visual_inputs/
```

同卷优先硬链接 Keyframe，不支持时复制。输入 manifest 冻结：

```text
source_version
visual_structure_run_id
visual_structure_result_hash
sampling_strategy
keyframe_id/image_sha256
visual_prompt_version
model_config_id
schema_version
allow_cloud_visual_data_transfer
```

新增模型策略 surface：

```text
media_library.visual_semantic
```

普通用户只能看到别名和只读版本。后端解析真实模型，并先确认模型支持 image input。云模型且未明确授权时返回：

```text
business status: blocked
code: cloud_visual_data_transfer_not_authorized
```

`VisualSemanticToolAdapter`：

1. 从 `AppContext` 获取 OpenCode client、模型策略和 usage recorder，为本 run 创建独立 OpenCode model session 并写入 `model_session_id`。
2. M0–M4 每次只发送当前 Scene 的一个受控 Keyframe；R1 每次发送当前 fragment 的四个有序受控 Keyframe。
3. R1 一个基础请求必须包含一个 text part 和四个 image parts，不得拆成四次请求。
4. 模型只返回描述字段；Scene ID、边界和 keyframe refs 由后端覆盖。
5. 校验失败允许同一 Attempt 内一次结构化修复；仍失败则 run failed。
6. 写 ToolResult、标准 OutputManifest 和以下文件：

```text
SessionOutput/visual/visual_semantic_segments.json
SessionOutput/visual/visual_semantic_manifest.json
SessionReport/visual_semantic_quality_check.json
```

M0–M4 单帧片段 schema：

```json
{
  "fragment_id": "scene_0001",
  "start_ms": 0,
  "end_ms": 3000,
  "keyframe_refs": ["scene_0001-midpoint"],
  "visual_summary": "一名讲解者在室内手持桌面产品。",
  "people": ["一名讲解者"],
  "objects": ["桌面产品"],
  "scene": "室内演示区",
  "action": null,
  "keywords": ["室内", "手持产品"],
  "claim_evidence": {
    "people": ["scene_0001-midpoint"],
    "objects": ["scene_0001-midpoint"],
    "scene": ["scene_0001-midpoint"],
    "action": []
  },
  "confidence": 0.82,
  "needs_review": false
}
```

M0–M4 单帧硬性验证：

- `visual_semantic_prompt_v2` 要求 `visual_summary/people/objects/scene/keywords`
  等面向用户的自然语言字段使用简体中文；稳定 ID、时间和证据引用保持原值，
  不做翻译或改写。结构化修复提示必须重复同一语言约束。
- `sampling_strategy=scene_midpoint_v1` 时发布适配器强制 `action=null`。单帧可见姿态或活动只写入 `visual_summary/keywords`，不占用连续动作字段。
- R1 虽升级为四帧 sampling，仍不定义非空 `action`；只有未来连续视频动作合同通过后才能改变。
- 任一非空 people/objects/scene/action 都必须有已知 keyframe evidence。
- 不允许真实身份和敏感属性推断。
- 模型不得修改 fragment ID 和时间。

R1 前向合同固定为：

```text
structure schema     = media_library_visual_structure_v2
semantic schema      = media_library_visual_semantic_v2
sampling_strategy    = scene_uniform_4_v1
sampling_ratios      = 0.125,0.375,0.625,0.875
visual_prompt_version= visual_semantic_prompt_v3
```

`03_02` 对每个不超过 15 秒的 fragment 生成四个稳定槽：`scene_XXXX-sample-01..04`。每槽记录实际整数毫秒时间、workspace 相对路径和 SHA-256；全部四帧必须位于片段范围并登记到 Session 文件清单。目标点失败只允许在所属四分之一区间内确定性重试；缺少任一槽时 structure run 失败。短片段因帧率得到相同图片 hash 时保留四个槽，不静默去重。

`03_03` prepare 将有序四帧硬链接或复制到该 run 的独立 `0_SessionContext/visual_inputs/`，并冻结四个 ID/hash。模型策略预检必须支持一次请求至少四图，否则 business run=`blocked`、code=`visual_model_multi_image_unsupported`。云授权文案明确发送四张采样截图；不上传视频。单图继续使用既有大小限制，四图原始字节总和默认不得超过 `32 MiB`，超限 code=`visual_semantic_keyframe_payload_too_large`。

四图只进行一次基础 `prompt_async`：parts 顺序固定为 text、sample-01、sample-02、sample-03、sample-04。模型输出的顶层 `keyframe_refs` 必须包含全部四帧；`claim_evidence.people/objects/scene` 只能引用真实支持字段的帧。后端覆盖 fragment ID、边界和完整四帧 refs，并继续强制 `action=null`、`claim_evidence.action=[]`，不允许把稀疏状态差异写成连续动作或因果。

缓存键使用有序四帧 SHA-256 + sampling + prompt + schema + provider/model/version；任一变化都 cache miss。一次基础调用记录 `model_call_count=1,image_count=4`；唯一结构化修复发送同样四帧，再各增加 `1/4`。新增四图总负载和每次请求估算成本门禁，`OPENCREW_MEDIA_LIBRARY_VISUAL_EST_COST_PER_CALL_MICROS` 必须重新按四图请求校准，不能沿用单图估算值。

`scene_midpoint_v1` 结果继续只读显示和审计，但 R1 publisher/reconcile 必须以 `sampling_strategy_ineligible` 跳过并计入 `reanalysis_required_count`。只有重新运行 structure v2 + semantic v2 后才可设置 `visual_search_ready=true`。

### 8.5 Composite

新增模型策略 surface：

```text
media_library.composite
```

综合分析使用独立 Tool Session，scheme=`composite`。prepare 只复制当前发布的结构化 JSON：

```text
0_SessionContext/composite_inputs/dialogue_fragment_index.json
0_SessionContext/composite_inputs/visual_structure_segments.json
0_SessionContext/composite_inputs/visual_semantic_segments.json
0_SessionContext/composite_inputs/InputManifest.json
```

不复制或链接源视频、音频、Keyframe 图像。

Composite 同样为每个 business run 创建独立 OpenCode model session，避免与素材 Session 的其他分析或人工消息共享历史上下文。model session 保留用于审计，但普通用户 API 不返回真实 ID。

`CompositeAnalysisToolAdapter` 向文本模型发送：

- 稳定候选边界。
- 对白片段。
- 视觉语义字段。
- 上游引用。
- 源视频方向和总时长。
- 系统维护 prompt 和 output schema。

后端覆盖并验证：

- `asset_id/source_version`。
- 时间边界必须来自上游边界集合或这些边界的合法组合。
- 所有 dialogue/visual/keyframe refs 必须存在。
- 所有视觉事实必须由 `visual_claim_refs` 支撑。
- 未在视觉语义出现的视觉事实必须删除；不能根据对白补写。
- M0–M4 的 `scene_midpoint_v1` 和 R1 的 `scene_uniform_4_v1` 都要求 composite `action=null`，不能由综合 LLM 根据稀疏帧或对白补出连续动作。

系统维护的 `composite_prompt_v6` 明确要求所有新生成的标题、摘要、关键词和
边界说明使用简体中文，同时保持上游事实值、稳定 ID 和证据引用原值不变；每个候选片段以全部
`dialogue_refs + visual_refs` 的最小 `start_ms` 和最大 `end_ms` 形成
引用闭包，`visual_claim_refs.<field>` 必须是同一片段
`visual_refs` 的子集且直接包含该字段的精确上游值。v4 起向模型提供
`reference_ranges` 与 `visual_evidence_catalog`，要求返回前逐项核对
引用边界和每个画面事实的精确支持来源；v5 进一步固定所有字段类型，并在
唯一一次结构化修复中附带完整候选审计，列出全部引用闭包、重叠、场景类型和
无证据事实问题，避免模型只修首个错误；片段按时间排序、
不得重叠，无有效证据的片段必须移除。确定性校验首次失败时，同一
Attempt 内唯一一次结构化修复会收到完整 `code:detail` 和上述全量规则，
而不只收到首个错误代码。后端仍严格拒绝未知引用、越界引用和无上游
证据的视觉事实，不用自动替换引用或删除事实的方式掩盖无效模型输出。

实现中的当前版本为 `composite_prompt_v6`：当源视频至少 30 秒且上游已有至少 3 个画面分析窗口时，单个覆盖全片的 composite 候选以 `composite_segments_overmerged` 拒绝，并使用既有唯一一次结构化修复要求输出至少 2 个互不重叠的可用片段。跨窗口对白可以继续保留在独立对白索引中，不得为了把它强塞进 composite 而制造重叠范围。

输出：

```text
SessionOutput/json/composite_semantic_segments.json
SessionOutput/json/composite_fragment_index.jsonl
SessionOutput/manifests/composite_virtual_clips.json
SessionOutput/manifests/search_index_manifest.json
SessionReport/composite_quality_check.json
```

运行启动时冻结上游 run/hash；模型完成后、发布前再读取 current 上游。任一不一致则本 run：

```text
status=stale
error_code=analysis_upstream_changed
is_current=false
```

不发布 fragments，也不覆盖旧 current。

### 8.6 模型缓存键

```text
visual semantic:
  image_sha256 + visual_prompt_version + model_config_id + schema_version

composite:
  dialogue_result_hash
  + visual_structure_result_hash
  + visual_semantic_result_hash
  + composite_prompt_version
  + model_config_id
  + schema_version

search planner:
  normalized_query + filters + planner_prompt_version + model_config_id
```

缓存命中仍要重新验证当前素材、上游 hash 和授权范围。

Search planner 新增模型策略 surface：

```text
media_library.search_planner
```

它与 `media_library.visual_semantic`、`media_library.composite` 一样执行普通用户 alias/mask，不接受前端直接提交真实 provider、model 或 key ref。

## 9. 分析 API 合同

现有 run 路径保留。新增响应统一返回业务 run ID。

### 9.1 对白

```text
POST /api/media-library/{asset_id}/analyses/dialogue/run
GET  /api/media-library/{asset_id}/analyses/dialogue/current
GET  /api/media-library/{asset_id}/analyses/dialogue/runs/{run_id}
```

请求：

```json
{
  "force": false,
  "allow_cloud_asr_data_transfer": false
}
```

`force=true` 允许创建新 run，但不提前删除旧 current。

### 9.2 画面

```text
POST /api/media-library/{asset_id}/analyses/visual/run
GET  /api/media-library/{asset_id}/analyses/visual/current
GET  /api/media-library/{asset_id}/analyses/visual/runs/{run_id}
```

请求：

```json
{
  "force_structure": false,
  "force_semantic": false,
  "allow_cloud_visual_data_transfer": false,
  "visual_prompt_version": "default",
  "model_config_id": "server-default"
}
```

编排规则：

- 无 current structure：先 structure，成功后继续 semantic。
- current structure ready、semantic 未开始/blocked/failed：只运行 semantic。
- `force_structure=true`：重跑 structure；新 structure 激活后旧 semantic/composite stale，再运行 semantic。
- `force_semantic=true`：复用 current structure，重跑 semantic。
- 两个 force 都为 false 且 visual ready：返回 `409 visual_analysis_exists`。

POST 返回：

```json
{
  "status": "queued",
  "operation_id": "mlvo_...",
  "structure_run_id": "mlar_visual_structure_...",
  "semantic_run_id": null
}
```

semantic run 创建后，visual current/operation 响应补充其 ID。

### 9.3 综合

```text
POST /api/media-library/{asset_id}/analyses/composite/run
GET  /api/media-library/{asset_id}/analyses/composite/current
GET  /api/media-library/{asset_id}/analyses/composite/runs/{run_id}
```

请求：

```json
{
  "force": false,
  "prompt_version": "default",
  "model_config_id": "server-default"
}
```

不提供：

```text
GET  /analyses/{scheme}/runs
POST /analyses/{scheme}/runs/{run_id}/activate
```

### 9.4 current 响应

```json
{
  "run": {
    "analysis_run_id": "mlar_...",
    "scheme": "composite",
    "source_version": "0123...",
    "status": "ready",
    "schema_version": "media_library_composite_v1",
    "prompt_version": "composite_default_v1",
    "model_config_label": "Max",
    "result_hash": "abcd...",
    "progress": {},
    "error": null,
    "started_at": 0,
    "finished_at": 0,
    "elapsed_ms": 0
  },
  "items": []
}
```

普通用户响应不出现真实 provider、真实 model ID、API key ref、绝对路径或完整系统 prompt。

## 10. 首版共享检索

### 10.1 查询计划

内部 `MediaLibraryQueryPlanV1`：

```json
{
  "schema_version": "media_library_query_plan_v1",
  "original_query": "想找一段介绍防水能力的视频",
  "exact_phrases": ["防水能力"],
  "optional_terms": ["防水", "防护", "进水"],
  "negative_terms": [],
  "orientation": "any",
  "min_duration_ms": null,
  "max_duration_ms": null,
  "sources": ["media_library"],
  "planner_version": "ml_query_planner_v1"
}
```

规范化 `nfkc_casefold_ws_v1`：

1. Unicode NFKC。
2. `casefold()`。
3. 所有空白折叠为一个空格。
4. 去除首尾空白。
5. 不做隐藏中文分词。

查询少于 2 个规范化字符返回 `422 search_query_too_short`。

规划器超时、关闭、配额不足或非法 JSON 时：

```text
exact_phrases=[original_query]
optional_terms=[]
planner_degraded=true
```

搜索继续执行。

### 10.2 资格 SQL

必须同时满足：

```text
media_library_assets.upload_status = ready
media_library_assets.archived = false
media_library_assets.content_sha256 is not null
media_library_tasks.dialogue_status = ready
fragment.analysis_scheme = dialogue
fragment.is_active = true
analysis_run.status = ready
analysis_run.is_current = true
fragment.source_version = asset.content_sha256
```

`media_library_clip_derivatives` 不参与查询。

### 10.3 首版召回与确定性排序

召回条件：规范化全文、任一 exact phrase 或任一 optional term 在 active dialogue fragment 的 `search_text` 中出现。negative term 命中则排除。

PostgreSQL 使用参数化 `strpos(search_text, :value) > 0`，不得把用户输入拼接到 SQL。

fragment 原始分数：

| 信号 | 分值 |
| --- | ---: |
| 完整原始查询命中 `dialogue_text` | +100 |
| 完整原始查询命中素材标题/人工标签 | +40 |
| 每个 exact phrase 命中对白 | +30，最多 90 |
| 每个 optional term 命中对白 | +8，最多 40 |
| optional term 覆盖率 | +0..20 |
| 标题/人工标签 term 覆盖率 | +0..10 |
| 请求方向完全匹配 | +5 |
| fragment confidence | +0..5 |

排序使用 raw score；API `score = min(1, raw_score / 200)`。`score_reasons` 由实际命中信号生成，不能调用 LLM虚构。

执行上限：

```text
最多召回 300 个 fragments
每个 asset 最多保留 3 个 fragments
单次 API limit 默认 12，最大 50
稳定排序：raw_score DESC, asset.updated_at DESC, asset_id ASC
```

按 asset 聚合分数：

```text
top fragment raw score
+ second fragment raw score * 0.15
+ third fragment raw score * 0.05
```

综合分析已 ready 时可以在候选解释中返回相关 composite 证据，但首版不允许 composite 代替 dialogue 资格或扩大召回集合。

### 10.4 搜索运行

run 状态：

```text
queued/running/completed/failed
```

这里的 `completed` 是 search run 状态，不是业务分析状态。

每次执行：

1. 创建 `media_library_search_runs`。
2. 保存脱敏计划和版本。
3. 执行资格过滤和召回。
4. 聚合并在返回前重新检查资格。
5. 保存 top candidate 快照。
6. 更新各阶段耗时。
7. 遥测写入失败只告警，不让搜索主请求失败。

`POST .../runs` 在当前 HTTP 请求内异步等待 planner 和各 source 完成，返回完整结果；网络 I/O 使用 async client，不阻塞事件循环。`GET .../runs/{search_id}` 用于页面重载、结果回放和已知 run 查询。Agent 原有 SSE 入口继续保留，由 adapter 把共享服务阶段映射为现有事件，不再新建第二套全局 SSE 合同。

遥测默认不持久化完整对白或人工查询：

```text
OPENCREW_MEDIA_SEARCH_RAW_QUERY_RETENTION_DAYS=0
```

默认 `query_plan_json` 只保存过滤条件、term/phrase 数量、各项 hash、版本和降级标记；`top_candidates_json` 只保存 source、candidate ID、asset ID、rank、score 和 matched fragment IDs，不保存对白正文、URL 或路径。若部署显式设置大于 0 的保留期，完整 plan 进入受限审计字段，并由每日清理任务按期限删除。

### 10.5 三个入口

StoryBoard：

```text
POST /api/koubo-storyboard/tasks/{task_id}/dialogues/{dialogue_asset_key}/media-library-search/plan
POST /api/koubo-storyboard/tasks/{task_id}/dialogues/{dialogue_asset_key}/media-library-search/runs
GET  /api/koubo-storyboard/tasks/{task_id}/media-library-search/runs/{search_id}
POST /api/koubo-storyboard/tasks/{task_id}/media-library-search/import
```

服务端按 `dialogue_asset_key` 重新读取当前 Dialogue，不信任前端传来的对白文本。

Agent - Asset Library：

- 保留 `local` 的“当前 Task manifest”语义。
- 新增 provider/source ID `media_library`。
- `MediaLibraryProviderAdapter` 调用共享 service。
- candidate 的 `provider_asset_id=asset_id`。
- import 时走 `media_library_original`，不能调用 `provider_for()`、refresh URL 或网络下载。

剪辑页：

```text
POST /api/media-library/{asset_id}/search/plan
POST /api/media-library/{asset_id}/search/runs
GET  /api/media-library/{asset_id}/search/runs/{search_id}
POST /api/media-library/{asset_id}/search/runs/{search_id}/import-to-storyboard
```

请求：

```json
{
  "target_task_id": 27,
  "sources": ["external", "media_library"],
  "fragment_refs": [
    {"scheme": "dialogue", "run_id": "mlar_...", "fragment_id": "srt_0001"}
  ],
  "user_text": "",
  "orientation": "any",
  "limit": 12
}
```

规则：

- `media_library` 可在未选择目标 Task 时搜索。
- 启用 `external` 时必须先选择有效 `target_task_id`，否则返回 `422 search_target_task_required`。
- 当前编辑的 `asset_id` 默认从 media_library 候选排除。
- 外部 adapter 复用目标 Task 的既有 Search Agent run、provider、SSE/缓存和授权链路。
- 统一 search run 保存外部子 run ID，后续外部导入仍由既有 import 服务完成。
- 外部候选 `source_version=null`，且只提供“整条导入”。

### 10.6 统一候选 DTO

```json
{
  "source": "media_library",
  "candidate_id": "mla_...",
  "asset_id": "mla_...",
  "source_version": "0123...",
  "display_name": "原始采访视频",
  "preview_url": "/api/...",
  "thumbnail_url": "/api/...",
  "duration_ms": 180000,
  "orientation": "portrait",
  "score": 0.88,
  "score_reasons": ["对白原句命中", "竖屏匹配"],
  "matched_fragments": [
    {
      "scheme": "dialogue",
      "run_id": "mlar_dialogue_...",
      "fragment_id": "srt_0012",
      "start_ms": 42100,
      "end_ms": 49800,
      "dialogue_text": "……",
      "summary": "",
      "keyframe_url": "/api/..."
    }
  ],
  "license": null,
  "allowed_actions": ["preview", "open_editor", "import_original"]
}
```

外部候选：

```text
source=external
asset_id=null
source_version=null
allowed_actions=[preview, import_whole]
```

前端严格按 `allowed_actions` 渲染按钮。

## 11. StoryBoard 导入

### 11.1 目标列表

```text
GET /api/media-library/import-targets/storyboards
```

响应只包含：

```json
{
  "items": [
    {
      "task_id": 27,
      "session_id": 31,
      "title": "产品口播",
      "workflow_mode": "script",
      "updated_at": 0
    }
  ]
}
```

筛选：

- `openclip_tasks` 与 `sessions` 一一对应。
- Session workspace 存在。
- StoryBoard source/manifest 可以安全解析。
- Task 未归档，未处于删除中。
- 不返回 workspace 绝对路径。

每次导入仍重新验证目标，不信任列表缓存。

### 11.2 原始视频导入

内部请求：

```json
{
  "source_kind": "media_library_original",
  "source_id": "mla_...",
  "target_task_id": 27,
  "requested_name": "防水能力原片",
  "search_id": "mls_...",
  "dialogue_asset_key": "dialogue_0005",
  "idempotency_key": "..."
}
```

执行：

1. 按 `asset_id` 重读权威素材、Session 和源相对路径。
2. 校验 ready、未归档、源版本一致、源文件在来源 workspace 内。
3. 按 `target_task_id` 重读目标 Task/Session/workspace，并取得该 workspace 的进程内导入锁。
4. 以 idempotency key 插入/重读 `status=preparing` 的 import record。
5. 安全生成目标 basename 和唯一相对路径。
6. 复制到同目录 `.part`，流式计算 SHA-256。
7. 校验 hash 必须等于源 `content_sha256`。
8. 基于旧 manifest 生成新 manifest 临时文件，并保留旧 manifest 备份。
9. 原子 rename 视频和 manifest。
10. 一个数据库事务写目标 `session_files`、`search_action=import` 并把 import 标记为 completed。
11. 任一步失败时恢复旧 manifest、删除本次目标/part、把 import 标记为 failed。
12. 写来源和目标 Session event。

启动 reconciliation 扫描 `status=preparing`：

- 最终文件和 manifest 项同时存在且 hash 正确：补齐 `session_files` 并标记 completed。
- 其他情况：移除本次 manifest 项和孤儿文件，标记 `failed + import_interrupted`。
- 不触碰其他导入或用户上传的文件。

目标：

```text
SessionOutput/storyboard/assets/videos/{safe_filename}
```

manifest provenance：

```json
{
  "source": "media_library_original",
  "source_asset_id": "mla_...",
  "source_session_id": 12,
  "source_version": "0123...",
  "source_search_id": "mls_...",
  "source_dialogue_asset_key": "dialogue_0005",
  "content_sha256": "0123...",
  "imported_at": 0
}
```

不自动绑定 Dialogue 槽位。

### 11.3 派生 clip 导入

流程与原始视频相同，但权威源来自 `media_library_clip_derivatives`，并校验 clip 的 source asset/version。

provenance 增加：

```text
source=media_library_clip
source_clip_id
source_start_ms
source_end_ms
source_scheme
source_fragment_id
```

### 11.4 外部候选

继续使用当前：

```text
import_asset_search_candidates()
```

以及既有 host allowlist、MIME、文件头、文件大小、license、候选刷新和下载流程。不得先把外部文件写成 `media_library_assets`，不得给外部候选生成 editor URL。

## 12. 视频剪切后端

### 12.1 API

```text
GET  /api/media-library/{asset_id}/editor
POST /api/media-library/{asset_id}/clip-jobs
GET  /api/media-library/{asset_id}/clip-jobs/{clip_job_id}
POST /api/media-library/{asset_id}/clip-jobs/{clip_job_id}/cancel
GET  /api/media-library/{asset_id}/clips
GET  /api/media-library/{asset_id}/clips/{clip_id}
DELETE /api/media-library/{asset_id}/clips/{clip_id}
POST /api/media-library/{asset_id}/clips/{clip_id}/import-to-storyboard
```

`GET clips` 只返回成功 derivative，按 `created_at DESC, clip_id ASC` 排序。导入请求：

```json
{
  "target_task_id": 27,
  "requested_name": "产品核心卖点",
  "search_id": "mls_...",
  "dialogue_asset_key": "dialogue_0005",
  "idempotency_key": "client-generated-opaque-id"
}
```

`GET editor` 一次返回：

- 原始素材 DTO 和 `source_version`。
- 当前 dialogue/visual/composite fragments。
- current run 摘要。
- stale 只读结果。
- 已持久化 clips。
- 服务端校验后的导航上下文。

首版面向 10 分钟以内单视频，一次返回全部 fragments，不分页、不静默截断。响应必须记录 fragment 总数和序列化字节数；超过 `OPENCREW_MEDIA_EDITOR_PAYLOAD_WARN_BYTES` 时写结构化容量告警，但仍返回完整响应。默认告警阈值为 2 MiB：

```text
OPENCREW_MEDIA_EDITOR_PAYLOAD_WARN_BYTES=2097152
```

分页或按可见窗口增量获取属于实测达到告警阈值后的接口增强，不能在首版用隐式截断替代。

hash 参数不会自动发送到后端。前端先按 allowlist 解析 hash，再把同名参数作为 `GET editor` query 发送；后端返回钳制后的：

```json
{
  "navigation_context": {
    "start_ms": 42100,
    "end_ms": 49800,
    "target_task_id": 27,
    "dialogue_asset_key": "dialogue_0005",
    "search_id": "mls_...",
    "matched_fragment_id": "srt_0012",
    "return_to": "storyboard_dialogue",
    "target_valid": true,
    "dialogue_valid": true
  }
}
```

### 12.2 创建任务

```json
{
  "source_version": "0123...",
  "start_ms": 12300,
  "end_ms": 18800,
  "display_name": "产品核心卖点",
  "source_scheme": "composite",
  "source_fragment_id": "composite_0001",
  "source_analysis_run_id": "mlar_composite_...",
  "source_search_id": "mls_...",
  "source_dialogue_asset_key": "dialogue_0005",
  "manual_override": false,
  "idempotency_key": "client-generated-opaque-id"
}
```

校验：

- asset 必须 ready、未归档、有源文件和 SHA-256。
- 请求 `source_version` 必须等于当前 asset hash。
- `250ms <= duration <= 1,800,000ms`，默认值可由环境变量调整。
- 以 fragment 建议创建且 `manual_override=false` 时，fragment 必须是 current ready。
- stale fragment 不能创建默认选区任务。
- 手动选区可以不携带 fragment；如果从 stale 转换而来，必须 `manual_override=true` 并清空 fragment/run ID。
- display name 清洗后非空，输出扩展名固定 `.mp4`。

成功创建返回 HTTP 202：

```json
{
  "clip_job_id": "clipjob.boot.uuid",
  "status": "queued",
  "progress": 0,
  "clip_id": null,
  "error": null
}
```

GET job 使用相同外壳；成功后：

```json
{
  "clip_job_id": "clipjob.boot.uuid",
  "status": "completed",
  "progress": 100,
  "clip_id": "mlc_...",
  "error": null,
  "clip": {
    "clip_id": "mlc_...",
    "display_name": "产品核心卖点",
    "start_ms": 12300,
    "end_ms": 18800,
    "duration_ms": 6500,
    "preview_url": "/api/...",
    "download_url": "/api/...",
    "content_sha256": "abcd...",
    "size_bytes": 123456
  }
}
```

默认配置：

```text
OPENCREW_MEDIA_CLIP_MIN_MS=250
OPENCREW_MEDIA_CLIP_MAX_MS=1800000
OPENCREW_MEDIA_CLIP_MAX_CONCURRENCY=2
OPENCREW_MEDIA_CLIP_PART_TTL_MS=86400000
```

### 12.3 `ClipJobManager`

在 `AppContext` 中创建一个进程级实例：

```text
ctx.media_clip_job_manager
```

内部：

- 128-bit random `boot_id`。
- 有界 `ThreadPoolExecutor`。
- `jobs_by_id`。
- `job_id_by_idempotency_key`。
- 每 asset 当前运行 job 集合。
- 锁保护状态变更。

shutdown：

1. 标记不再接受任务。
2. terminate 当前 FFmpeg。
3. 最多等待 5 秒。
4. kill 未退出进程。
5. 清理 `.part`。
6. 关闭 executor。
7. 最后 dispose DB engine。

旧 `clip_job_id`：

- 格式合法但 boot ID 不同：HTTP 410，`clip_job_lost`。
- 当前 boot 但不存在或格式非法：HTTP 404，`clip_job_not_found`。

### 12.4 FFmpeg

只使用参数数组：

```text
{resolved_ffmpeg_absolute_path}
-hide_banner
-nostdin
-loglevel error
-ss {start_seconds_3_decimal}
-accurate_seek
-i {source_absolute_path}
-t {duration_seconds_3_decimal}
-map 0:v:0
-map 0:a?
-c:v libx264
-preset medium
-crf 20
-pix_fmt yuv420p
-c:a aac
-b:a 192k
-movflags +faststart
-progress pipe:1
-nostats
-y
{controlled_part_path}
```

`-ss` 是输入选项，必须放在 `-i` 前。转码时显式启用 `-accurate_seek`：FFmpeg 先跳到目标时间之前最近的可寻址点，再只解码并丢弃该点到目标时间之间的残差，从而避免从视频开头解码。首版不使用双 `-ss`，也不为此建立编码关键帧索引。毫秒只在命令适配边界格式化为三位小数。

10 分钟、从 `543.217s` 开始剪切的本机核验中，仓库 FFmpeg 7.0 的前置 `-ss` 为 0.08 秒，后置 `-ss` 为 0.65 秒；前置、后置和双 `-ss` 三种输出的解码音视频帧序列一致。该绝对耗时只证明本机低码率 fixture 的命令路径，不作为跨机器 SLA；发布测试必须使用接近真实分辨率/码率的 10 分钟视频，并覆盖靠近尾部的非关键帧起点。

进度读取 `out_time_us`，换算为 0–99；只有全部持久化成功才返回 100。

取消：

```text
terminate
等待最多 5 秒
kill
删除 part
status=cancelled
```

### 12.5 文件与持久化顺序

目标：

```text
SessionOutput/clips/{clip_id}/{safe_filename}.mp4
```

执行顺序：

1. FFmpeg 输出同目录隐藏 `.part`。
2. ffprobe 验证有视频流、时长正数、大小正数。
3. 读取 format、视频流和可选音频流时长，以其中最大正数作为用户实际播放时长 `actual_duration_ms`。
4. 按下式计算容差，并要求 `abs(actual_duration_ms - requested_duration_ms) <= duration_tolerance_ms`：

```text
video_frame_budget_ms =
  ceil(1000 / output_avg_frame_rate)，帧率缺失或非法时回退 50

audio_frame_budget_ms =
  有 AAC 音频时 ceil(1024 * 1000 / output_sample_rate)，否则 0

duration_tolerance_ms =
  min(
    250,
    max(
      video_frame_budget_ms,
      audio_frame_budget_ms,
      ceil(requested_duration_ms * 0.05)
    )
  )
```

5. 250 ms、30 fps、48 kHz AAC 请求的容差约为 34 ms；250 ms、24 fps 请求约为 42 ms。禁止再用固定 ±250 ms 验收最短片段。
6. 流式计算输出 SHA-256。
7. 原子 rename 为最终文件。
8. 一个 DB 事务插入 derivative 和 `session_files`。
9. DB 失败则删除最终文件。
10. 写成功事件。

数据库里只存在成功片段。

服务启动扫描：

```text
*/SessionOutput/clips/{clip_id}/
```

如果数据库存在 derivative，只验证登记文件存在，绝不自动删除；缺失时发 critical 告警。如果数据库不存在 derivative，只清理超过 TTL 的受控 `.part`、孤儿最终文件和空 clip 目录。这样可以恢复“文件已 rename、DB 尚未提交”时的进程崩溃，不会删除已登记成功文件。

### 12.6 幂等

- 同一进程内相同 idempotency key：返回已有 job。
- 数据库已有成功 derivative：直接返回 completed job view，不启动 FFmpeg。
- 同 key 但输入参数不同：`409 idempotency_key_conflict`。
- 数据库 unique 冲突后重读成功记录并返回，不能生成第二份文件。

### 12.7 删除

删除 clip 时：

1. 校验 `asset_id/clip_id`。
2. 若存在 StoryBoard import 审计引用，返回 `409 media_clip_in_use`。
3. 删除受控文件和空目录。
4. 删除 `session_files` 与 derivative。
5. 写事件。

删除原始 asset 时，现有删除服务增加保护：

- active analysis run。
- 当前进程 active clip job。
- 任一 derivative。
- `referenced_by_count > 0`。

返回具体阻塞原因，不允许数据库 cascade 留下物理孤儿文件。

## 13. 视频剪辑前端

### 13.1 路由

解析顺序：

```javascript
^#/media-library/([^/?#]+)/editor(?:\?|$)  -> editor
^#/media-library/([^/?#]+)(?:\?|$)         -> detail
^#/media-library(?:\?|$)                   -> list
```

解码失败或 asset ID 为空时回列表并显示错误。

导航上下文只接受：

```text
start_ms
end_ms
target_task_id
dialogue_asset_key
search_id
matched_fragment_id
return_to=storyboard_dialogue|media_library_detail
```

`return_to` 不接受 URL。前端只用于体验，后端仍重校验。

### 13.2 编辑器状态

```javascript
{
  assetId,
  sourceVersion,
  durationMs,
  playheadMs,
  focusedFragmentRef,
  searchFragmentRefs,
  selection: {
    startMs,
    endMs,
    sourceScheme,
    sourceFragmentId,
    sourceRunId,
    manualOverride
  },
  visibleTracks: { composite: true, dialogue: true, visual: true },
  pixelsPerMs,
  scrollLeft,
  viewportWidth,
  targetTaskId,
  navigationContext
}
```

状态内部禁止保存秒浮点值。

### 13.3 时间轴窗口化

```text
visible_start_ms = max(0, scrollLeft / pixelsPerMs)
visible_end_ms   = min(duration, (scrollLeft + viewportWidth) / pixelsPerMs)
buffer_ms        = max(5000, (visible_end_ms - visible_start_ms) * 0.5)
render range     = visible range ± buffer
```

只渲染与 render range 相交的片段、刻度和 Keyframe 标记。时间轴外层滚动宽度可以表达总时长，但不得为每毫秒或每帧创建 DOM。

默认：

```text
fit view: 视频时长正好适应可用宽度
min zoom: fit view
max zoom: 200 px/second
```

刻度从以下集合选择，使主刻度屏幕间距位于 80–160 px：

```text
100ms, 200ms, 500ms, 1s, 2s, 5s, 10s, 30s, 60s, 300s
```

### 13.4 交互

- 点击 fragment：设置焦点、播放器跳到起点、用其范围初始化 selection。
- 拖 playhead：只改预览时间。
- 拖入点/出点或手动输入：`manualOverride=true`。
- 双击 fragment：范围预览。
- 范围预览到 `endMs` 时暂停并钳制到出点。
- 隐藏轨道不删除 selection 或 search refs。
- stale fragment 显示斜纹与警告，不能“加入搜索条件”，不能初始化 selection。
- 用户可显式把 stale 范围转成手动范围；转换后清空 fragment/run identity。
- 同轨重叠首版按开始时间渲染，焦点片段 z-index 最高；旁边索引列表可以选择被遮挡片段。

### 13.5 详情页

详情页修改：

- `blocked/partial/ready/stale/failed` 分别显示。
- 删除对业务 `completed` 的兼容判断。
- 画面按钮显示 structure 与 semantic 子状态。
- 综合按钮只在 dialogue、visual structure、visual semantic 全 ready 时可运行。
- 综合按钮右侧增加“视频剪辑”。
- “视频剪辑”在 asset ready、有 source version 时可用；分析未完成仍可手动剪切，但不加载缺失轨道。
- stale 结果继续在详情 Tab 只读展示。
- 将现有 `formatFragmentTime(seconds)` 替换为 `formatFragmentTimeMs(milliseconds)`；旧工具秒字段只能由后端发布适配器处理，前端不再猜单位。

### 13.6 StoryBoard

Asset Pool：

- 有有效当前 Dialogue 时显示“检索素材”。
- 未选择 Dialogue 时按钮可显示但 disabled，并说明原因。
- 切换 Dialogue 立即废弃旧搜索 UI 状态。
- 结果卡支持预览、整条导入、打开剪辑。
- 打开剪辑构造受控 hash 参数。
- 导入成功后保留当前 Dialogue 和 Asset Pool tab。
- “返回原 Dialogue”只按 `task_id + dialogue_asset_key` 定位；如果编辑器 DTO 返回 `dialogue_valid=false`，跳转 `#/koubo-storyboard/tasks/{task_id}`，不按旧数组下标猜 Dialogue。

Agent - Asset Library：

- 设置增加“全局素材库”来源。
- `local` 标签改为“当前 Task”，`media_library` 标签为“全局素材库”。
- 外部、local、media_library 的 allowed actions 分开。

## 14. 错误合同

API 错误统一：

```json
{
  "detail": {
    "code": "analysis_upstream_missing",
    "user_message": "综合分析需要先完成对白、画面结构和画面语义分析。",
    "suggested_action": "先完成缺失的分析后重试。",
    "run_id": "mlar_...",
    "failed_step": "preflight",
    "metadata": {}
  }
}
```

首版至少支持：

| HTTP | code | 场景 |
| ---: | --- | --- |
| 404 | `media_asset_not_found` | asset 不存在 |
| 409 | `media_source_missing` | 源文件缺失 |
| 409 | `media_source_version_mismatch` | hash/version 不一致 |
| 409 | `analysis_run_active` | 同 scheme 已运行 |
| 409 | `analysis_result_exists` | 无 force 的重复运行 |
| 409 | `analysis_upstream_missing` | composite 前置不满足 |
| 409 | `analysis_upstream_changed` | 运行中上游变化 |
| 409 | `analysis_result_stale` | stale 结果尝试搜索/默认剪切 |
| 403/409 | `cloud_asr_data_transfer_not_authorized` | 音频外发未授权 |
| 403/409 | `cloud_visual_data_transfer_not_authorized` | 图片外发未授权 |
| 429 | `quota_exceeded` | 模型预算/并发超限 |
| 422 | `search_query_too_short` | 查询过短 |
| 422 | `search_target_task_required` | external 搜索缺少目标 Task |
| 404 | `search_run_not_found` | run 不存在 |
| 422 | `clip_range_invalid` | 剪切范围非法 |
| 409 | `clip_job_active` | 同 key/input 冲突 |
| 410 | `clip_job_lost` | 后端重启导致任务丢失 |
| 404 | `clip_job_not_found` | 非本进程任务 |
| 409 | `idempotency_key_conflict` | 同 key 不同输入 |
| 409 | `media_clip_in_use` | clip 已被导入引用 |
| 409 | `storyboard_target_invalid` | 目标 Task 不可写 |
| 409 | `storyboard_dialogue_stale` | 来源 Dialogue 已失效 |

内部异常堆栈只进日志，不进入普通用户响应。

## 15. 可观测性、安全和容量

### 15.1 事件

至少写：

```text
media_library.source_hash.completed
media_library.analysis.run.created
media_library.analysis.run.blocked
media_library.analysis.run.failed
media_library.analysis.run.ready
media_library.analysis.run.stale
media_library.fragment_index.published
media_library.search.completed
media_library.search.failed
media_library.search.action
media_library.clip.requested
media_library.clip.completed
media_library.clip.failed
media_library.clip.cancelled
media_library.clip.lost
media_library.storyboard_import.completed
media_library.storyboard_import.failed
```

payload 不记录密钥、绝对路径、完整系统 prompt 或无必要的完整对白。

### 15.2 指标

```text
media_library_ready_assets
media_library_active_dialogue_fragments
media_library_search_total
media_library_search_zero_result_total
media_library_search_planner_degraded_total
media_library_search_latency_ms
media_library_search_retrieval_latency_ms
media_library_clip_active
media_library_clip_duration_ms
media_library_clip_failure_total{code}
media_library_analysis_total{scheme,status}
media_library_editor_fragment_count
media_library_editor_payload_bytes
```

告警：

- ready 原始视频达到 450 条：容量预警。
- 达到 500 条：禁止继续宣称简单召回在承诺范围内。
- 任意规模正常搜索滚动 P95 > 3 秒：进入 FTS 增强。
- fragment 发布失败、stale 失效失败或 orphan part 清理失败：结构化告警。
- editor 响应超过 `OPENCREW_MEDIA_EDITOR_PAYLOAD_WARN_BYTES`：记录 asset、fragment 总数和字节数，不记录 fragment 正文。

### 15.3 路径与数据外发

- 所有路径先按 workspace root `resolve` 并验证 `is_relative_to(root)`。
- manifest 永远保存相对路径。
- FFmpeg 不经 shell。
- provider URL 继续走既有 allowlist。
- 云 VLM 每次只发送当前 Keyframe。
- Composite 不读取或发送图片/视频字节；测试通过打开文件审计 fake 强制验证。
- 模型配置走 `model_policy.py` 新 surface，普通用户响应使用 alias/mask。

## 16. Feature flags 与发布

新增配置：

```text
OPENCREW_MEDIA_ANALYSIS_RUNS_V1
OPENCREW_MEDIA_LIBRARY_SEARCH_V1
OPENCREW_MEDIA_VISUAL_SEMANTIC_V1
OPENCREW_MEDIA_COMPOSITE_V1
OPENCREW_MEDIA_EDITOR_V1
```

兼容与安全语义：未设置时按已启用处理，以保持现有单机部署行为；
显式 `0/false/off/no` 关闭，`1/true/on/yes` 启用（忽略大小写与首尾
空白），其他值按配置无效并 fail closed。后端通过
`GET /api/media-library/capabilities` 只公开 enabled/配置有效性，前端
据此隐藏新入口；该接口不公开环境变量原值。关闭开关只阻止相应的新
运行、发布或变更，历史分析、搜索审计和已成功登记的 clip 保持只读。

默认策略：

1. migration 可以提前上线。
2. hash 回填完成前，只开启 analysis run 双写，不开启中心索引发布。
3. 存量对白/画面结构采纳完成，且不存在“dialogue ready 但无 current active fragments”的记录后，才开启中心索引发布。
4. 搜索服务完成性能和资格测试后开启 StoryBoard/Agent 内部入口。
5. VLM 和 composite 先按单 asset 内部入口验证。
6. editor 完整闭环验收前，生产不显示可点击按钮。
7. 五项全部通过后才标记产品首版。

回滚：

- 关闭 flag，不删除新表或新产物。
- 新旧详情 DTO 保持向后兼容一个发布周期。
- 旧 current active fragment 在新发布失败时保持可用。
- 不执行 down migration 删除审计数据。
- clip 文件已经成功登记时不因关闭 editor flag 被删除。

## 17. 测试实施

### 17.1 后端合同测试文件

新增：

```text
backend/tests/contracts/test_media_library_source_identity_contract.py
backend/tests/contracts/test_media_library_analysis_runs_contract.py
backend/tests/contracts/test_open_cut_v1_visual_semantic_contract.py
backend/tests/contracts/test_media_library_composite_contract.py
backend/tests/contracts/test_media_library_fragment_publisher_contract.py
backend/tests/contracts/test_media_library_search_contract.py
backend/tests/contracts/test_media_library_search_telemetry_contract.py
backend/tests/contracts/test_media_library_storyboard_import_contract.py
backend/tests/contracts/test_media_library_clip_jobs_contract.py
backend/tests/contracts/test_media_library_clip_storage_contract.py
backend/tests/contracts/test_media_library_editor_surface_contract.py
```

重点断言：

- 秒到毫秒只有一次转换。
- 同 source/schema/content 结果 hash 稳定。
- blocked 不写 failed。
- 业务状态从不写 completed。
- 旧 visual ready 迁移为 structure ready + semantic not_analyzed + visual partial。
- 03_03 无源视频读取；M0–M4 单帧与 R1 稀疏四帧的连续动作输出均被拒绝。
- R1 每 fragment 固定四个采样槽和有序 hash；缺帧、越界、路径逃逸、hash 改变或伪造 v2 均被拒绝。
- R1 每 fragment 一个基础请求包含四个 image parts，计量为 `model_call_count=1/image_count=4`；不支持多图和四图总负载超限产生结构化 blocked，不降级单帧。
- R1 四帧缓存键、字段证据、结构化修复、配额和四图估算成本合同通过。
- 单帧 current/ready 结果保持只读，但不能发布为 R1 visual index，并进入 `reanalysis_required_count`。
- composite 无图片/视频读取，未知引用被拒绝。
- fragment 原子切换、幂等和失败回滚。
- current stale 可读但 inactive。
- 搜索只返回 eligible 原始视频并按 asset 聚合。
- planner 降级不阻断搜索。
- `local` 与 `media_library` 不混淆。
- media_library import 不经过 provider/network。
- external import 不创建 asset/editor action。
- clip job current process、取消、lost 和幂等。
- 成功 clip 的文件、DB、session_files 一致。
- 路径穿越全部拒绝。

### 17.2 PostgreSQL 集成与性能

新增 marker/脚本生成 500 条代表视频的索引数据，不需要复制 500 个真实大视频文件，但 fragment 数量和文本长度必须来自真实样本分布：

```text
每视频 dialogue fragments: P50/P95 接近真实数据
中文、数字单位、中英文混排
短句、长句、重复对白、无命中
横竖屏和不同时长
```

脚本：

```bash
backend/.venv/bin/python backend/scripts/benchmark_media_library_search_postgres.py \
  --database-url postgresql://... \
  --assets 500 \
  --queries 200 \
  --warmup 20
```

报告：

```text
dataset_seed
asset_count
active_fragment_count
query_count
planner_mode
P50/P95/P99 database retrieval
P50/P95/P99 total without external provider
zero_result_rate
top query plans
```

发布门禁以关闭外部 provider、planner 已缓存的“正常全局素材搜索”测量，P95 <= 3 秒；另行记录 planner 冷调用耗时，不能用外部模型不稳定性掩盖数据库退化。

### 17.3 FFmpeg 测试

fixtures：

- 有音频 MP4。
- 无音频 MP4。
- 竖屏。
- 低于 1 秒的合法短片。
- 10 分钟、接近真实分辨率/码率的代表视频，并允许在靠近尾部的非关键帧时间起切。
- 30 分钟 synthetic sparse/低码率视频只作非阻塞压力测试。
- 含非 ASCII 文件名。

测试：

- 起止边界和 250 ms 最短值。
- 24/30/60 fps、有无 AAC 音频的动态输出时长容差；帧率缺失时使用 50 ms 回退。
- 命令合同断言 `-ss/-accurate_seek` 位于 `-i` 前；与后置 `-ss` 慢速参考比较首尾音视频帧。
- 仓库内置 FFmpeg 7.0 必测；部署存在 FFmpeg 8.x 时执行相同 fixture 的兼容 smoke。
- 无音频 `-map 0:a?`。
- 取消后进程退出且 part 清理。
- DB 失败后最终文件清理。
- 服务重启模拟后旧 job 410，成功 clip 可读。

### 17.4 前端

新增纯 model tests 或 Node contract：

```text
frontend/scripts/media_library_editor_route_contract.mjs
frontend/scripts/media_library_editor_timeline_contract.mjs
frontend/scripts/media_library_search_action_contract.mjs
```

浏览器 E2E：

```text
frontend/e2e/media-library-analysis-runs.mjs
frontend/e2e/media-library-storyboard-search.mjs
frontend/e2e/media-library-editor.mjs
frontend/e2e/media-library-clip-restart.mjs
```

必须覆盖 10 分钟代表视频的窗口化和尾部选区、StoryBoard 上下文往返、stale 禁用、双来源按钮差异和 clip job lost。30 分钟视频只运行非阻塞压力场景。

路由合同至少覆盖：

```text
#/media-library/{id}/editor                     -> editor
#/media-library/{id}/editor?start_ms=1000       -> editor
#/media-library/{id}                            -> detail
#/media-library/{id}?tab=dialogue               -> detail
#/media-library/{id}/unknown                    -> 不得命中 detail
```

### 17.5 回归命令

```bash
backend/.venv/bin/python -m unittest \
  backend.tests.contracts.test_media_library_surface_contract \
  backend.tests.contracts.test_media_library_upload_contract \
  backend.tests.contracts.test_media_library_tool_session_repair_contract \
  backend.tests.contracts.test_open_cut_v1_dialogue_contract \
  backend.tests.contracts.test_open_cut_v1_visual_contract \
  backend.tests.contracts.test_koubo_asset_search_agent_contract \
  backend.tests.contracts.test_koubo_storyboard_dialogue_asset_key_contract

cd frontend && npm run build
```

新增测试在各任务完成时加入同一回归集。

## 18. 开发任务拆分与依赖

### 18.1 M0：身份、运行和迁移

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-001 | 实现 `0019` 和 schema | 无 | source identity、analysis runs、split status |
| ML-002 | 上传合并流式 SHA-256 | ML-001 | 新 ready asset 必有 hash |
| ML-003 | 哈希回填与验证脚本 | ML-001 | dry-run/write/幂等 |
| ML-004 | `AnalysisRunRepository` 与状态机 | ML-001 | run 并发、current、stale 事务 |
| ML-005 | 对白服务接 run 和发布适配器 | ML-002,004 | dialogue run/hash/ms 结果 |
| ML-006 | 画面结构服务接 run | ML-002,004 | structure run/hash/image hash |
| ML-007 | 存量 Tool Session 结果采纳脚本 | ML-003,005,006 | 无模型重跑的 legacy run/index |
| ML-008 | 输入快照、legacy 硬链接和存量 repair 验收 | 无 | 每 run 一份快照、兼容媒体同 inode |

M0 门禁：

- 新旧素材 hash 完整。
- 业务状态无 completed。
- 每个 run 只有 `0_SessionContext` 一份物理输入快照；同卷 legacy 媒体与其 `samefile`，跨卷安全复制。
- 现有分析回归通过。

非阻塞存储 spike：在真实部署卷验证 CoW clone/reflink 的独立 inode、写时隔离、异常清理和 `copy2` 回退。未同时通过这些条件时保持当前 run 快照复制策略；禁止用 inbox 硬链接绕过验证。

### 18.2 M1：中心索引、检索和原始导入

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-101 | 实现 `0020` | ML-001 | fragment/search/action tables |
| ML-102 | dialogue fragment 原子发布器 | ML-005,101 | current active index |
| ML-103 | normalization、planner 与降级 | ML-101 | query plan v1 |
| ML-104 | 确定性召回、评分和聚合 | ML-102,103 | shared search service |
| ML-105 | 搜索运行与行为遥测 | ML-104 | queryable search/action |
| ML-106 | StoryBoard 原始素材导入和 `0022` | ML-002,105 | safe copy/provenance |
| ML-107 | StoryBoard Dialogue 搜索 UI/API | ML-104,106 | 选 Dialogue 搜索/导入 |
| ML-108 | Agent `media_library` adapter | ML-104,106 | source 可开关 |
| ML-109 | PostgreSQL 500 条性能脚本与门禁 | ML-104 | P95 报告 |

M1 是内部基础里程碑，不是产品首版发布点。

### 18.3 M2：视觉语义

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-201 | `03_03` schema/registry/validator | ML-006 | Tool contract |
| ML-202 | visual model policy surface/adapter | ML-201 | VLM 调用不泄密 |
| ML-203 | 图像外发授权、缓存和配额 | ML-202 | blocked/ready 行为 |
| ML-204 | visual semantic 编排、结果和 UI | ML-203 | visual ready/partial/stale |

### 18.4 M3：综合分析

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-301 | composite schema、prompt、validator | ML-005,204 | 04_01 contract |
| ML-302 | composite model adapter/service | ML-301 | run/current/known-run API |
| ML-303 | 上游冻结、stale 级联和发布 | ML-102,302 | composite active index |
| ML-304 | 详情页综合结果和只读版本 | ML-302,303 | 真实 composite UI |

### 18.5 M4：剪辑和跨页面闭环

| ID | 任务 | 依赖 | 完成输出 |
| --- | --- | --- | --- |
| ML-401 | `0021`、clip repository/storage | ML-001 | derivative persistence |
| ML-402 | `ClipJobManager`、FFmpeg、取消/lost | ML-401 | process-local async job |
| ML-403 | clip list/get/delete/import | ML-106,402 | 持久化 clip 闭环 |
| ML-404 | editor DTO、路由和状态 model | ML-303,403 | editor page data |
| ML-405 | 播放器、时间轴、窗口化和选区 | ML-404 | 10 分钟代表视频可用 |
| ML-406 | editor 双来源搜索 | ML-104,405 | external + global |
| ML-407 | StoryBoard 目标选择和上下文往返 | ML-107,403,405 | 默认/更换/返回 |
| ML-408 | 全链路 E2E、重启和发布门禁 | 全部 | 产品首版可发布证据 |

### 18.6 合并策略

建议按 M0–M4 合并，每个 M 内可拆小 PR。禁止：

- 一个 PR 同时重写现有 StoryBoard Search Agent 和时间轴。
- 在 migration 未合入前提交依赖新列的生产代码。
- 在 publisher 原子性测试前打开 search flag。
- 用占位按钮提前宣称 editor 完成。

## 19. Definition of Ready 与完成判定

开发开始前只需要确认以下环境项，它们不改变产品设计：

1. 为 `media_library.visual_semantic` 指定一个支持 image input 的已批准模型别名。
2. 为 `media_library.composite` 和 search planner 指定文本模型别名。
3. 确认测试环境允许明确勾选云图像外发授权；若选择本地 VLM，提供对应 model config。
4. 指定性能测试使用的隔离 PostgreSQL database/schema。

这些是部署配置，不是开放产品问题。没有这些配置时仍可完成 schema、状态机、publisher、搜索降级、clip 和前端开发；VLM/LLM 真实端到端会显示结构化 blocked。

产品首版完成必须同时满足：

- `0019–0022` 在空库和现有 `0018` 数据库上均可迁移。
- hash 回填为 0 遗漏。
- dialogue、visual structure、visual semantic、composite 均有业务 run 和可信 Tool Session 终态。
- current/stale/active 行为与本文一致，普通用户没有 activate 接口。
- StoryBoard、Agent、editor 复用同一全局搜索服务。
- 500 条代表数据 PostgreSQL 正常搜索 P95 <= 3 秒。
- 外部候选没有 editor action。
- 10 分钟代表视频可缩放、滚动、窗口化选择和剪切；30 分钟 synthetic 压力测试失败不单独阻塞发布，但必须记录结果。
- clip job 重启后 lost，成功 derivative 不丢失。
- 原始和 clip 均能安全复制到有效 StoryBoard Task 并保留 provenance。
- 所有新增合同、前端生产构建和真实浏览器 E2E 通过。

达到以上条件后，M0–M4 不需要再写一份开发设计文档。中文 FTS、top-N LLM 重排、向量召回及高级时间轴交互仍是后续增强；每片段四帧 VLM 已由专项 v0.3 提升为 R1/P0 前置门禁，不再属于可跳过的发布后增强。自动全库存量模型重跑仍不在 R1 内，必须由明确授权和成本可见的重新分析触发。

客户适用性附加门禁：上述 M0–M4 只代表对白优先基线；专项 v0.3 第 18.2、18.3 节现已通过真实 DSCF0157 四帧模型/媒体、三入口范围召回与剪切导入、派生片段跨 Task 复用、500/2,000 PostgreSQL 性能、生产构建、浏览器 E2E 和离线手册证据。当前可以按专项限定表述已交付能力，但仍不得宣称向量召回、连续动作理解或 clip 独立 VLM。
