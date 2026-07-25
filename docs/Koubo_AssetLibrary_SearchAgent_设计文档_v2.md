# Koubo Asset Library 素材检索 Agent 设计文档 v2.0

用户操作说明见：[Koubo_AssetLibrary_SearchAgent_用户操作手册.md](./Koubo_AssetLibrary_SearchAgent_用户操作手册.md)。

## 1. 目标

在 Asset Library 中新增一个独立的“素材检索 Agent”，用于根据用户的创作需求，到开放素材库、免费商用素材库和可授权媒体库中检索图片、视频、音频候选，并把用户确认后的素材导入当前 StoryBoard Task 的 Asset Library。

这个 Agent 不直接替代“图像生成”“视频生成”“图像智能体”“视频智能体”“数字人智能体”“提示词 Agent”。它的职责是补齐当前生产链路中缺失的“外部真实素材发现与导入”能力：

1. 用户用自然语言描述需要的素材，例如“医院走廊里医生查看平板，横屏，真实纪录片风格”。
2. Agent 将需求拆成素材类型、画幅、关键词、排除项、授权要求和来源优先级。
3. 后端到 Pexels、Pixabay、Wikimedia Commons 等素材源检索候选。
4. 系统统一候选格式，做去重、比例过滤、质量评分、授权提示和来源记录。
5. 前端展示候选卡片，用户选择一个或多个素材。
6. 用户点击导入后，后端下载素材并写入当前 Task 的 `SessionOutput/storyboard/assets/` 与 `koubo_storyboard_assets.json`。
7. StoryBoard Asset Panel 与 Upload Asset Library 重新加载后都能看到同一批导入素材。

一句话：

> 素材检索 Agent 是“外部素材发现 + 合规元数据保存 + 用户确认导入”的工作台，不是自动批量下载器，也不是生成模型代理。

## 2. 当前系统基础

当前仓库已经具备可以复用的 Asset Library、Agent Chat、manifest 和文件落盘能力。下表中的落点均已核对存在：

| 能力 | 当前落点 | 复用方式 |
| --- | --- | --- |
| Asset Library 左侧导航 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx` | 新增 `search-agent` 入口 |
| Asset Library 页面装配 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx` | 扩展 `LIBRARY_VIEWS`、候选状态、导入回调 |
| 图片/视频/音频资产归一化 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/uploadAssetLibraryModel.js` | `assetKind()` 已按 kind/asset_type/扩展名推断，导入项沿用同一约定 |
| 图像 Agent 面板 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/AgentPanel.jsx` | 交互风格参考，不直接塞入检索逻辑 |
| 视频 Agent 面板 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx` | 流式事件、确认卡（`role="alertdialog"`）、进度状态可参考 |
| 素材 manifest 写入 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_pool_services.py` | 导入后调用 `upsert_asset_manifest_item(workspace, asset)` |
| 素材目录常量 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/constants.py` | 使用 `ASSET_IMAGES_REL`、`ASSET_VIDEOS_REL`、`ASSET_AUDIOS_REL`、`ASSETS_REL` |
| 安全相对路径 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/io_utils.py` | 导入、预览、删除都必须经 `safe_workspace_rel(...)` 保持 workspace 内路径 |
| Session event | `runtime.py` 的 `StoryboardRuntime.add_event(session_id, kind, payload)` | 检索、候选、导入、失败都写事件 |
| Agent Chat 结构 | `agent_chat_routes.py`、`agent_chat_services.py` | 复用 LLM 注入与流式范式，但检索 Agent 单独建服务文件 |
| LLM 调用入口 | `opencode_client_for(session_row).prompt_async()` + `resolve_model(...)`（见 `asset_routes.py`） | Query Planner 复用此入口，只取 JSON 结果，不让 LLM 触网 |

关键事实，后续设计据此约束：

1. `LIBRARY_VIEWS` 现有 7 项：`images`、`images-agent`、`videos`、`videos-agent`、`digital-human-agent`、`prompt-agent`、`history`。
2. `asset_routes.py` 当前约 2483 行，已经过大，新功能必须独立文件，不得继续追加。
3. `add_event` 以 `session_id` 为键，内部 `json.dumps(payload, ensure_ascii=True)`。检索 Agent 的 API 以 `task_id` 寻址，写事件前必须先做 task→session 解析（沿用 asset_routes 现有映射）。
4. `upsert_asset_manifest_item` 的去重键是 manifest item 的 `path` 或 `id`，**不按来源去重**。这一点直接决定了导入幂等必须在调用前自行查重（见 7.7）。
5. `tool_media_provider_configs` 表以 `kind`/`provider`/`api_key_ciphertext` 存储凭据，现有 `kind` 为 `image`/`video`/`audio`，尚无 `asset_search`，也没有录入 Pexels/Pixabay key 的 UI（凭据策略见 7.3）。
6. `FlowIcon` 已内置 `search` 图标，导航图标直接使用，无需替代方案。

说明：历史文档中出现过 `OpenCrew/OpenClip/...` 路径。当前真实代码已经迁移到 `OpenCrew/frontend/...` 和 `OpenCrew/backend/...`，本设计以当前代码为准。

## 3. 产品形态

### 3.1 左侧入口

在 Asset Library 左侧导航新增：

```text
素材检索
```

建议排序：

```text
图像生成
图像智能体
视频生成
视频智能体
数字人智能体
提示词 Agent
素材检索
History
```

如果当前 UI 暂时没有 `提示词 Agent`，则先放在 `数字人智能体` 下方。

### 3.2 页面布局

素材检索 Agent 打开后，页面仍保持 Asset Library 三栏心智：

| 区域 | 内容 |
| --- | --- |
| 左侧 | Asset Library 导航 |
| 中间 | 检索候选结果、来源筛选、导入队列、已导入记录 |
| 右侧 | 素材检索 Agent 对话与需求拆解 |

中间区域不是当前 `Images` 网格，也不是 `Videos` 网格。它是“候选素材工作台”：

```text
Search Brief
Source Filters
Candidate Grid
Selected to Import
Imported Results
```

候选素材还没有进入 Asset Library，只有用户点击“导入”后才写入 `assets/images`、`assets/videos` 或 `assets/audios`。

### 3.3 Agent 对话

右侧 Agent 的语气和使用方式：

1. 用户描述素材需求。
2. Agent 输出一张“检索计划卡片”。
3. 用户可以修改关键词、来源、比例、素材类型。
4. 点击“开始检索”后，后端通过流式事件返回各来源进度。
5. 候选结果出现在中间区域。
6. 用户勾选候选并导入。

首版不要求 Agent 长对话生成复杂推理。它最重要的是稳定地产生结构化检索计划。

## 4. 能力边界

### 4.1 必须做

1. 支持自然语言输入素材需求。
2. 支持图片和视频素材检索。例外：Wikimedia 视频 P0 仅作候选展示、不支持导入（候选 `import_supported=false`），完整导入留到 P1（见 5.3）。
3. P0 支持 Pexels、Pixabay、Wikimedia Commons。
4. 支持来源筛选、画幅筛选、关键词编辑。
5. 支持候选卡片预览。
6. 支持用户选择后导入到当前 Task Asset Library。
7. 导入后写入 `koubo_storyboard_assets.json`。
8. 每个导入素材保存来源、作者、license、source_url、provider_asset_id、download_url 或 file_page_url。
9. 检索和导入过程写 session events。
10. API key 只在后端读取，不进入前端。
11. 导入幂等：同一候选重复导入不产生重复文件或重复 manifest 条目。
12. attribution 强制：对 `requires_attribution=true` 的素材，缺失 attribution 元数据时由服务端拒绝导入。

### 4.2 首版不做

1. 不做全自动批量下载。
2. 不做无限滚动式大规模采集。
3. 不做向量检索或 CLIP 视觉相似搜索。
4. 不做用户上传到第三方素材库。
5. 不做素材二次编辑。
6. 不做版权法律判断，只做来源元数据保留和风险提示。
7. 不让 LLM 直接编造素材 URL。
8. 不把候选素材写入 `koubo_storyboard_assets.json`。

### 4.3 P1 再做

1. Unsplash 图片候选。
2. 音频素材检索。
3. Internet Archive 或 Freesound 等音频/历史媒体源。
4. 按 StoryBoard Shot/Scene 自动生成一组检索任务。
5. CLIP embedding 重排。
6. 本地素材库相似检索。
7. 多候选对比评分。
8. Connection 凭据录入 UI（P0 走环境变量，见 7.3）。

## 5. 外部素材源策略

这里说的“开源好的素材库”在实现上拆成三类：

| 类型 | 示例 | 说明 |
| --- | --- | --- |
| 开放授权媒体库 | Wikimedia Commons | 授权更明确，但 license/attribution 复杂 |
| 免费商用素材 API | Pexels、Pixabay | 易用，适合短视频素材，但有平台使用规范 |
| 高质量图片 API | Unsplash | 图片质量高，但使用/下载规范更特殊，建议 P1 |

### 5.1 Pexels

官方 API 支持图片和视频检索。Pexels 文档要求 API 请求相关页面展示 Pexels 链接，建议尽量署名摄影师；API 需要 `Authorization` header；默认有请求限额。

端点路径（以官方文档为准，实现前必须按此，且写 provider contract test 锁定）：

- 图片搜索：`https://api.pexels.com/v1/search`
- 视频搜索：`https://api.pexels.com/v1/videos/search`

注意：官方明确旧路径 `https://api.pexels.com/videos/` 将被弃用，视频端点必须使用 `v1/videos/` 新路径，不得退回旧路径。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | 是 |
| 画幅过滤 | 是 |
| 尺寸过滤 | 是 |
| 作者/来源元数据 | 是 |
| 下载到本地 | 导入时下载 |

下载域名白名单：`*.pexels.com`、`images.pexels.com`、`videos.pexels.com`、`player.vimeo.com`（视频文件 CDN，以实际响应为准并补入白名单）。

### 5.2 Pixabay

Pixabay API 支持 royalty-free 图片和视频搜索。官方文档要求在展示搜索结果时告诉用户素材来自 Pixabay；请求需要缓存 24 小时（缓存 key/TTL/命中/过期重查规则见 6.5）；不允许系统化大规模下载。返回的图片/视频 URL 只适合临时展示搜索结果，实际使用时应下载到服务端，不得长期热链。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | 是 |
| 分类/语言/方向 | 是 |
| 作者/来源元数据 | 是 |
| 下载到本地 | 导入时下载 |

下载域名白名单：`pixabay.com`、`*.pixabay.com`、`cdn.pixabay.com`。

### 5.3 Wikimedia Commons

Wikimedia Commons 是真正偏开放授权的媒体库，适合公共领域、历史、科学、地理、机构、真实场景类素材。它的关键不是搜索难度，而是 license 和 attribution 元数据必须保存。MediaWiki `imageinfo` 可返回文件 URL、尺寸、mime、mediatype、extmetadata 等信息。

Wikimedia 是三家中归一化最复杂的来源：`extmetadata` 的 license 模板形态多样（PD / CC-BY-SA / CC-BY / GFDL / 多重许可）。处理规则：

1. license 字段优先取 `LicenseShortName`、`License`、`UsageTerms`、`Artist`、`Credit`、`AttributionRequired`。
2. **license/attribution 解析失败时不得静默丢弃**：将该候选标记 `license_status="unconfirmed"`，在评分中降权，并在卡片上提示“授权未确认”。
3. 解析失败的候选默认不允许直接导入，除非用户显式确认。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | P0 可只做候选，P1 完整导入 |
| license 元数据 | 必须 |
| attribution 元数据 | 必须 |
| 文件页 URL | 必须 |
| 下载到本地 | 导入时下载 |

下载域名白名单：`upload.wikimedia.org`、`commons.wikimedia.org`。

工时提示：Wikimedia 归一化的工作量明显高于 Pexels/Pixabay，排期需单独预留，不要按等量平铺到 P0.1。

### 5.4 Unsplash

Unsplash P1 再接。原因是 Unsplash 官方要求直接使用 API 返回的图片 URL 进行展示，并且下载时要触发 download tracking。它适合高质量图片候选，但导入实现需要单独处理合规流程。

首版可以在设置中保留 `unsplash_enabled=false`，但不默认打开。

## 6. 文件与数据契约

### 6.1 不新增资产根目录

导入后的素材必须进入现有目录：

```text
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/assets/audios/
SessionOutput/storyboard/koubo_storyboard_assets.json
```

不得新增：

```text
SessionOutput/storyboard/asset_search/
SessionOutput/storyboard/asset_library_search/
```

候选检索状态写入 SessionContext（与现有 `SessionContext/PromptAgent/`、`SessionContext/AgentChats/` 等子目录先例一致）：

```text
SessionContext/AssetSearchAgent/
  Settings.json
  SearchRuns/
    search_1782144000000.json
  Imports/
    import_1782144000000.json
  Cache/
    pexels/
    pixabay/
    wikimedia/
```

说明：

1. `SessionContext/AssetSearchAgent/SearchRuns/` 保存候选结果和检索过程。
2. `Cache/` 只保存 API 响应缓存，不是资产库。缓存策略见 6.5。
3. 导入成功的物理文件只进入 `SessionOutput/storyboard/assets/...`。

### 6.5 API 响应缓存策略

Pixabay 官方要求 API 请求结果缓存 24 小时，且其返回的图片/视频 URL 仅临时有效。统一缓存规则（Pexels/Pixabay/Wikimedia 共用，Pixabay 为强制项）：

1. **cache key**：`sha1(provider + "|" + normalized_query + "|" + media_type + "|" + aspect + "|" + page + "|" + language + "|" + safe_search)`，文件落 `Cache/<provider>/<key>.json`。
2. **缓存条目结构**：`{provider, request, response_raw, cached_at, expires_at}`；Pixabay `expires_at = cached_at + 24h`，Pexels/Wikimedia 默认 6h（可在 Settings 配）。
3. **命中规则**：`now < expires_at` 命中并直接复用，**24 小时内不得对 Pixabay 发重复请求**；过期或未命中才发起真实请求并覆盖写。
4. **候选 preview/download URL 时效**：`SearchRun` 候选里的 `preview_url`/`download_url` 视为临时 URL。导入旧 `SearchRun` 时若该 run 的 `created_at` 距今超过对应 provider 的 TTL，**不得直接用旧 download_url 下载**，必须以 `(provider, provider_asset_id)` 重新查该候选、刷新 download_url 后再下载；刷新失败则该候选导入失败、reason=`stale_candidate_url`。
5. 缓存目录可安全清空，不影响已导入资产。

### 6.2 Candidate 结构

统一候选格式：

```json
{
  "schema_version": "koubo_asset_search_candidate_0.1",
  "candidate_id": "pexels_video_123456",
  "provider": "pexels",
  "provider_asset_id": "123456",
  "media_type": "video",
  "title": "Doctors walking through hospital corridor",
  "description": "Hospital staff walking in a bright corridor",
  "preview_url": "https://...",
  "thumbnail_url": "https://...",
  "download_url": "https://...",
  "source_url": "https://www.pexels.com/video/...",
  "creator": {
    "name": "Creator Name",
    "url": "https://..."
  },
  "license": {
    "name": "Pexels License",
    "url": "https://www.pexels.com/license/",
    "requires_attribution": false,
    "attribution_text": "Video by Creator Name on Pexels",
    "license_status": "confirmed"
  },
  "width": 1920,
  "height": 1080,
  "duration_seconds": 8.2,
  "mime_type": "video/mp4",
  "orientation": "landscape",
  "tags": ["hospital", "doctor", "corridor"],
  "provider_rank": 1,
  "import_supported": true,
  "import_unsupported_reason": "",
  "score": 0.86,
  "score_reasons": [
    "matches hospital corridor",
    "landscape ratio",
    "duration fits short B-roll"
  ],
  "raw": {}
}
```

字段约定：

1. `preview_url` / `thumbnail_url` 仅用于检索结果临时展示，前端不缓存、不长期热链。
2. `download_url` 仅在导入阶段由后端使用，且必须通过 provider 域名白名单校验。
3. `license.license_status` 取值 `confirmed` | `unconfirmed`，由归一化阶段写入。
4. `provider_rank` 保存 provider 返回的原始排序位次，作为 P0 评分的主信号。
5. `import_supported=false` 的候选可展示、可勾选预览，但导入接口必须拒绝（返回 `import_unsupported_reason`）。P0 用于 Wikimedia 视频：可作为候选呈现，但 P0 不支持导入（见 4.1 / 5.3）。前端对此类候选应禁用“选择导入”勾选或在导入时给出明确提示。

### 6.3 Search Run 结构

```json
{
  "schema_version": "koubo_asset_search_run_0.1",
  "search_id": "search_1782144000000",
  "task_id": 5,
  "session_id": 58,
  "created_at": 1782144000000,
  "request": {
    "user_text": "找一个医院走廊里医生查看平板的横屏视频",
    "media_types": ["video"],
    "aspect": "16:9",
    "sources": ["pexels", "pixabay", "wikimedia"],
    "limit_per_source": 12
  },
  "plan": {
    "queries": [
      {
        "query": "doctor tablet hospital corridor",
        "language": "en",
        "media_type": "video",
        "priority": 1
      }
    ],
    "negative_terms": ["cartoon", "illustration"],
    "must_have": ["doctor", "hospital corridor"],
    "nice_to_have": ["tablet", "documentary style"]
  },
  "candidates": [],
  "provider_stats": {
    "pexels": {"requested": 2, "returned": 18, "kept": 8, "status": "ok"},
    "pixabay": {"requested": 2, "returned": 12, "kept": 5, "status": "ok"},
    "wikimedia": {"requested": 1, "returned": 9, "kept": 3, "status": "ok"}
  }
}
```

`provider_stats[*].status` 取值 `ok` | `rate_limited` | `error`，用于在前端区分“无结果”和“被限流/出错”。

### 6.4 导入后的 manifest item

导入成功后写入 `koubo_storyboard_assets.json`：

```json
{
  "id": "SessionOutput/storyboard/assets/videos/1782144000000_001_pexels_123456.mp4",
  "path": "SessionOutput/storyboard/assets/videos/1782144000000_001_pexels_123456.mp4",
  "label": "doctor tablet hospital corridor",
  "filename": "1782144000000_001_pexels_123456.mp4",
  "asset_type": "Video",
  "kind": "video",
  "source": "asset_search",
  "created_at": 1782144000000,
  "content_sha256": "…",
  "origin": {
    "tool": "asset_search_agent",
    "search_id": "search_1782144000000",
    "candidate_id": "pexels_video_123456",
    "provider": "pexels",
    "media_type": "video",
    "provider_asset_id": "123456",
    "source_url": "https://www.pexels.com/video/...",
    "creator": {"name": "Creator Name", "url": "https://..."},
    "license": {
      "name": "Pexels License",
      "url": "https://www.pexels.com/license/",
      "requires_attribution": false,
      "attribution_text": "Video by Creator Name on Pexels",
      "license_status": "confirmed"
    },
    "query": "doctor tablet hospital corridor",
    "score": 0.86
  }
}
```

幂等去重键：完整 `origin.candidate_id`（其编码已含 provider + media_type + provider_asset_id，例如 `pexels_video_123456`），等价于三元组 `(origin.provider, origin.media_type, origin.provider_asset_id)`；辅以 `content_sha256` 兜底。注意不要只用 `(provider, provider_asset_id)`——图片与视频的 ID 空间可能碰撞。导入前后均据此判重，详见 7.7。

同时建议写 sidecar：

```text
SessionOutput/storyboard/assets/videos/1782144000000_001_pexels_123456.json
```

sidecar 保存完整 candidate raw metadata，manifest 只保存核心字段。

## 7. 后端实现设计

### 7.1 新增文件

建议新增：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/
  asset_search_routes.py
  asset_search_services.py
  asset_search_providers.py
```

职责：

| 文件 | 职责 |
| --- | --- |
| `asset_search_routes.py` | 注册素材检索 Agent API、流式检索、导入端点；导出 `register_asset_search_routes(router, deps)` |
| `asset_search_services.py` | 检索计划、候选合并、评分、导入、缓存、事件、幂等判重；导出 `register_asset_search_services(ns)` |
| `asset_search_providers.py` | Pexels/Pixabay/Wikimedia provider adapter |

不要继续把主要逻辑追加到已经很大的 `asset_routes.py`（当前约 2483 行）。

接线必须落在现有两个入口，不能只改 `asset_routes.py`：

1. **路由注册**：在 `router.py` 的 `build_koubo_storyboard_router(ctx)` 内，与现有 `register_asset_routes(router, deps)` 并列，新增一行 `register_asset_search_routes(router, deps)`。
2. **service namespace 同步**：`asset_search_services.py` 采用与 `asset_pool_services` 相同的 `register_*(ns)` + `globals().update(vars(ns))` 模式，并把模块加入 `services.py` 的 `_SERVICE_MODULES` 元组（同时在文件顶部 import）。否则 `_sync_service_globals` 不会把 namespace 风格 helper（如 `upsert_asset_manifest_item`、`safe_workspace_rel`、`read_json/write_json`）注入本模块，运行期会 NameError。

### 7.2 Provider Interface

provider 在 async 检索路由中被调用，**必须避免阻塞事件循环**：HTTP 调用使用 `httpx.AsyncClient`（异步实现），或对同步实现统一通过 `run_in_executor` 包装。下方接口以 async 形式定义。

```python
class AssetSearchProvider(Protocol):
    provider_id: str
    download_host_allowlist: tuple[str, ...]

    async def search(
        self,
        *,
        query: str,
        media_type: str,
        aspect: str,
        limit: int,
        page: int = 1,
        language: str = "en",
    ) -> list[dict[str, Any]]:
        ...

    def normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        ...

    async def import_candidate(
        self,
        *,
        candidate: dict[str, Any],
        target_path: Path,
    ) -> dict[str, Any]:
        ...
```

Provider 不直接写 manifest，只负责：

1. API 调用（异步、带超时、带 429 处理）。
2. raw -> candidate 归一化（含 license_status 标注）。
3. 下载素材文件（下载前校验 `download_url` host 命中 `download_host_allowlist`）。
4. 返回下载后的文件元信息（含 `content_sha256`）。

manifest 写入、幂等判重由 `asset_search_services.py` 统一完成。

### 7.3 Settings 与凭据

Settings 存入：

```text
SessionContext/AssetSearchAgent/Settings.json
```

结构：

```json
{
  "schema_version": "koubo_asset_search_settings_0.1",
  "sources": {
    "pexels": {"enabled": true, "limit_per_query": 12},
    "pixabay": {"enabled": true, "limit_per_query": 12},
    "wikimedia": {"enabled": true, "limit_per_query": 12},
    "unsplash": {"enabled": false, "limit_per_query": 12}
  },
  "defaults": {
    "media_types": ["image", "video"],
    "aspect": "auto",
    "language": "auto",
    "safe_search": true,
    "max_candidates": 36,
    "auto_download": false,
    "require_user_import_confirmation": true
  },
  "ranking": {
    "trust_provider_order": true,
    "prefer_exact_aspect": true,
    "prefer_high_resolution": true,
    "prefer_open_license": true,
    "prefer_short_video_broll": true
  }
}
```

API key 不写 Settings。凭据策略分阶段：

- **P0：环境变量兜底**（现实路径，因为当前没有 `asset_search` 凭据录入 UI）：

  ```text
  PEXELS_API_KEY
  PIXABAY_API_KEY
  UNSPLASH_ACCESS_KEY
  ```

- **P1：接入 Connection / secret store**，复用 `tool_media_provider_configs` 的 `kind`/`provider`/`api_key_ciphertext` 机制与 `context.py` 的解密路径：

  ```text
  kind = "asset_search"
  provider = "pexels" | "pixabay" | "unsplash"
  ```

  注意 `asset_search` 是新增 `kind`，需要新增凭据录入 UI，故归入 P1，不阻塞 P0。

无论哪条路径，provider 凭据只在后端读取，`provider_status.configured` 仅返回布尔，绝不回传 key。

### 7.4 Query Planner

Query Planner 复用现有 OpenCode LLM 入口，但只输出 JSON，不直接调用外网，也不得编造素材 URL。

重要：`prompt_async(...)` 返回 `None`、只提交任务，**不能当成同步取结果用**（见 `adapters/opencode.py`）。必须照搬现有同步取结果模式（参考 `host_product_services.py` 的 builder prompt 流程）：

```text
1. client = opencode_client_for(session_row)
2. resolve_model(...) 得到 {providerID, modelID}
3. started_at = now_ms()
4. client.prompt_async(opencode_session_id, planner_prompt, model=model, system=PLANNER_SYSTEM_PROMPT)
5. 轮询直到 deadline：
     assistant_text = last_completed_assistant(client.messages(opencode_session_id, limit=120), started_at)
     命中则 break，否则 sleep(1)
6. 超时未取到 -> 写 plan.failed 事件 -> 走 7.4 兜底链路（翻译/规则）
7. 解析 assistant_text 中的严格 JSON（带 request_id 校验）-> 得到 plan
8. JSON 解析失败 -> 同样走兜底链路
```

planner 不复用业务 OpenCode session 的对话历史时，可创建/复用一个独立 session，避免污染图像/视频 builder 的消息流。

重要：对 Pexels / Pixabay 这类以英文关键词匹配为主的来源，**高质量英文 query 是检索效果的命门**。LLM Planner 在 P0 是检索质量的主路径，而非可有可无的修饰。

输入：

```json
{
  "user_text": "找一个医院走廊里医生查看平板的横屏视频",
  "task_context": {
    "storyboard_title": "",
    "selected_shot": "",
    "selected_scene": "",
    "selected_dialogue": ""
  },
  "settings": {},
  "available_sources": ["pexels", "pixabay", "wikimedia"]
}
```

输出：

```json
{
  "media_types": ["video"],
  "aspect": "16:9",
  "queries": [
    {
      "query": "doctor tablet hospital corridor",
      "language": "en",
      "media_type": "video",
      "priority": 1
    },
    {
      "query": "medical staff walking hospital hallway",
      "language": "en",
      "media_type": "video",
      "priority": 2
    }
  ],
  "negative_terms": ["cartoon", "animation"],
  "must_have": ["doctor", "hospital"],
  "nice_to_have": ["tablet", "corridor"],
  "style": "realistic documentary b-roll",
  "license_policy": "prefer_open_or_free_commercial"
}
```

兜底链路（Run Model 不可用时）：

1. 先尝试**轻量翻译 helper**，把中文需求译为英文关键词。对 Pexels/Pixabay 这是兜底路径的必需步骤，不可省略。
2. 翻译不可用时，再退化为“中文原文直搜支持中文的来源”，并在 SearchRun 与前端标注 `plan.degraded=true`，提示用户检索质量可能下降。
3. 根据关键词判断 `image` / `video`。
4. 根据 `横屏/竖屏/方图/16:9/9:16/1:1` 推断 aspect。
5. 规则抽取 must_have / negative_terms。

### 7.5 搜索编排

搜索流程：

```text
1. validate payload
2. load task/workspace，task_id -> session_id 解析
3. load settings + 解析可用凭据
4. build query plan（LLM 或兜底）
5. for each source（并发，受单 provider 限额约束）:
     for each query:
       await provider.search(...)        # 命中 429 -> 标记 status=rate_limited 并跳过该 source 余下查询
       normalize candidates              # 含 license_status 标注
       cache response（Cache/<provider>/）
6. merge candidates
7. dedupe by provider_asset_id / source_url / preview_url   # 检索期没有文件，不做 content hash
8. filter by media_type / aspect / min_quality / license_status
9. rank candidates（P0 以 provider_rank 为主，详见 7.6）
10. write SearchRuns/search_*.json
11. stream candidates to frontend（流式事件，见 8.4）
```

任一 provider 限流或出错只降级该来源（`provider_stats[*].status` 标注），不使整个检索失败。

### 7.6 评分规则

P0 默认信任 provider 自带的相关性排序（`trust_provider_order=true`）：以 `provider_rank` 为主信号，评分只用于**过滤与轻微微调**，不做激进重排，避免朴素关键词重叠把高质量结果挤下去。

```text
base   = provider_rank_score          # 由 provider 原始位次归一化
adjust = aspect_match   * 0.15
       + media_quality  * 0.10
       + license_clarity* 0.05
       - duration_penalty
score  = clamp(base + adjust, 0, 1)
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `provider_rank_score` | provider 返回的原始排序归一化为基础分（信任来源相关性） |
| `aspect_match` | 是否符合 16:9 / 9:16 / 1:1 |
| `media_quality` | 分辨率、码率、尺寸、缩略图质量 |
| `license_clarity` | license 是否明确（`license_status=confirmed`、有 creator/source_url） |
| `duration_penalty` | 视频过短或过长（不适合 B-roll）时的降权 |

`license_status=unconfirmed` 的候选一律降权并打标。P0 不用 embedding；P1 再引入 CLIP 重排。

### 7.7 导入流程

导入必须是用户动作，且必须幂等：

```text
用户选择候选
  -> POST import
  -> 后端按 search_id 找候选（仅接受 search run 中存在的 candidate_id）
  -> 幂等预检：按 candidate_id == (provider, media_type, provider_asset_id) 在当前 manifest 查重
       命中 -> 跳过下载，直接返回已存在的 manifest item（标记 skipped=true）
  -> license 校验：requires_attribution=true 但 attribution_text 缺失 -> 拒绝该项
  -> 校验 download_url host 命中 provider 域名白名单（含重定向后的最终 URL，见 7.8.2）
  -> provider 下载文件（带超时、大小上限）
  -> 计算 content_sha256
  -> 二次幂等：content_sha256 命中现有 manifest -> 删除临时文件并返回已存在项
  -> 校验 MIME / 扩展名 / 大小 / 真实文件头（与响应 content-type 一致）
  -> 计算目标 rel_path，经 safe_workspace_rel 校验在 workspace 内，
     并额外断言 rel_path.startswith(ASSET_IMAGES_REL/ASSET_VIDEOS_REL/ASSET_AUDIOS_REL + "/")
  -> 写入对应 assets 子目录
  -> 写 sidecar JSON
  -> upsert manifest（带 origin + content_sha256）
  -> add_event
  -> plan, meta = load_plan(task) 重载
  -> 返回 {ok, task, meta, plan, imported, failed}（对齐上传接口形状，见 8.6）
```

由于 `upsert_asset_manifest_item` 仅按 `path`/`id` 去重，而文件名含 timestamp 必然唯一，幂等性**不能依赖** upsert 自身，必须由上面的预检/二次校验保证。

导入目标：

| media_type | 目录 | manifest kind |
| --- | --- | --- |
| image | `ASSET_IMAGES_REL` | `image` |
| video | `ASSET_VIDEOS_REL` | `video` |
| audio | `ASSET_AUDIOS_REL` | `audio` |

文件名规则：

```text
{timestamp}_{index:03d}_{provider}_{provider_asset_id}_{safe_slug}.{ext}
```

例如：

```text
1782144000000_001_pexels_123456_doctor_tablet_corridor.mp4
```

### 7.8 安全限制

1. 只允许导入 provider 返回且在 search run 中存在的 candidate。
2. 不接受前端直接传任意 `download_url` 导入；同时 provider 返回的 `download_url` 也必须经 host 白名单校验（防 SSRF）。下载客户端禁用自动跟随跨域重定向，或对每一跳重定向后的最终 URL 重新校验 host 仍在白名单内，避免 provider 响应重定向到内网地址。
3. 下载超时必须有限制。
4. 单文件大小 P0 限制：
   - image: 30 MB
   - video: 500 MB
   - audio: 100 MB
5. response content-type 与文件扩展、真实文件头必须三者匹配。
6. 下载只写入 workspace 内的 `assets/` 目录。注意 `safe_workspace_rel` 仅保证路径在 workspace 内、不含 `..`，**不保证**落在 `assets/images|videos|audios` 下；因此还须额外断言目标 rel_path 以对应的 `ASSET_*_REL + "/"` 前缀开头，再写盘。
7. 所有 API key 和授权 header 必须 redaction，不入日志、不入事件、不入响应。
8. 不允许批量导入超过 12 个候选。
9. 不允许后台自动翻页抓取超过配置上限；命中 provider 429 即降级该来源。
10. 导入失败不能写 manifest，且不留半成品文件。
11. `requires_attribution=true` 而 attribution 缺失的素材，由服务端拒绝导入（不依赖前端 `confirm_license`）。

## 8. 后端接口设计

所有路径以 `task_id` 寻址；写事件前在服务层完成 task→session 解析。

### 8.1 获取设置

```text
GET /api/koubo-storyboard/tasks/{task_id}/asset-library-search/settings
```

响应：

```json
{
  "ok": true,
  "settings": {},
  "provider_status": {
    "pexels": {"enabled": true, "configured": true},
    "pixabay": {"enabled": true, "configured": true},
    "wikimedia": {"enabled": true, "configured": true},
    "unsplash": {"enabled": false, "configured": false}
  }
}
```

`configured` 仅为布尔，绝不回传 key。

### 8.2 保存设置

```text
PUT /api/koubo-storyboard/tasks/{task_id}/asset-library-search/settings
```

### 8.3 生成检索计划

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-library-search/plan
```

请求：

```json
{
  "text": "找一个医院走廊里医生查看平板的横屏视频",
  "media_types": ["video"],
  "aspect": "16:9",
  "sources": ["pexels", "pixabay", "wikimedia"],
  "selected_assets": [],
  "selected_storyboard_context": {}
}
```

响应：

```json
{
  "ok": true,
  "plan": {
    "media_types": ["video"],
    "aspect": "16:9",
    "queries": [],
    "negative_terms": [],
    "must_have": [],
    "nice_to_have": [],
    "degraded": false
  }
}
```

`degraded=true` 表示 LLM 与翻译 helper 均不可用、走了中文直搜兜底，前端应提示检索质量可能下降。

### 8.4 执行检索（流式）

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-library-search/search/events
```

实现复用现有 `kouboAgentChat` 的流式范式：后端 `StreamingResponse` + `text/event-stream`，前端用 `fetch + ReadableStream` 手动解析（**不使用浏览器原生 `EventSource`，因其仅支持 GET**）。

事件：

```json
{"type":"started","search_id":"search_..."}
{"type":"plan","plan":{}}
{"type":"provider.started","provider":"pexels"}
{"type":"candidate.batch","provider":"pexels","items":[]}
{"type":"provider.completed","provider":"pexels","returned":18,"kept":8,"status":"ok"}
{"type":"provider.completed","provider":"pixabay","returned":0,"kept":0,"status":"rate_limited"}
{"type":"completed","search_id":"search_...","candidate_count":24}
{"type":"failed","detail":"..."}
```

### 8.5 查询历史检索

```text
GET /api/koubo-storyboard/tasks/{task_id}/asset-library-search/runs
GET /api/koubo-storyboard/tasks/{task_id}/asset-library-search/runs/{search_id}
```

### 8.6 导入候选

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-library-search/import
```

请求：

```json
{
  "search_id": "search_1782144000000",
  "candidate_ids": [
    "pexels_video_123456"
  ],
  "label_prefix": "hospital corridor",
  "confirm_license": true
}
```

`confirm_license` 仅作为用户已知晓授权提示的 UI 信号；服务端仍独立强制 attribution 完整性（见 7.8.11）。

响应必须对齐现有上传接口的形状（`asset_routes.py` 的 `/assets/upload` 返回 `{ok, task, meta, plan, added}`），以便前端 `applyAssetLibraryResult` 直接刷新页面状态。注意有两个不同实现都叫 `applyAssetLibraryResult`，且要求不同：

- `UploadAssetLibraryPage.jsx` 版只要 `task`/`meta`。
- `KouboStoryBoardModule.jsx` 版要求 `task`/`meta`/`plan` **三者齐全，缺 `plan` 直接 return 不刷新**。

因此 `plan` 是必需字段，不能省。导入接口返回：

```json
{
  "ok": true,
  "task": {},
  "meta": {},
  "plan": {},
  "imported": [
    {
      "id": "SessionOutput/storyboard/assets/videos/...",
      "path": "SessionOutput/storyboard/assets/videos/...",
      "kind": "video",
      "source": "asset_search",
      "skipped": false
    }
  ],
  "failed": [
    {"candidate_id": "...", "reason": "attribution_required_but_missing"}
  ]
}
```

`task`/`meta`/`plan` 由 `plan, meta = load_plan(task)` 重新加载后回传，与上传接口一致。若需进一步兼容上传语义，可额外加 `"added": imported`（`added` 为 `imported` 的别名）。`skipped=true` 表示命中幂等判重、复用了已存在素材，未重复下载。

若选择不对齐该形状，则前端导入成功后必须显式调用 `kbApi.detail(task_id)` 重新拉取并 apply，二者取其一，不能只返回 `imported/failed`（当前页面不会据此刷新，且 StoryBoard 侧因缺 `plan` 会静默不更新）。

## 9. 前端实现设计

### 9.1 新增 view

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx
```

扩展 `LIBRARY_VIEWS`（在现有 7 项基础上加入 `search-agent`）：

```js
const LIBRARY_VIEWS = new Set([
  "images",
  "images-agent",
  "videos",
  "videos-agent",
  "digital-human-agent",
  "prompt-agent",
  "search-agent",
  "history",
]);
```

如果 `prompt-agent` 尚未实现，不强依赖它。

### 9.2 新增 Sidebar 项

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx
```

新增按钮（`FlowIcon` 已内置 `search` 图标，直接使用）：

```jsx
<button class={props.view() === "search-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("search-agent")}>
  <span class="ual-nav-icon"><FlowIcon name="search" /></span>
  <span class="ual-nav-label">素材检索</span>
</button>
```

### 9.3 新增组件目录

建议新增：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/searchAgent/
  SearchAgentWorkspace.jsx
  SearchAgentPanel.jsx
  SearchBriefCard.jsx
  SearchSourceFilters.jsx
  SearchCandidateGrid.jsx
  SearchCandidateCard.jsx
  SearchImportTray.jsx
  SearchAgentSettings.jsx
  searchAgent.css
```

职责：

| 组件 | 职责 |
| --- | --- |
| `SearchAgentWorkspace.jsx` | 页面装配、数据加载、流式检索生命周期 |
| `SearchAgentPanel.jsx` | 右侧对话/检索需求输入 |
| `SearchBriefCard.jsx` | 展示和编辑检索计划（含 degraded 提示） |
| `SearchSourceFilters.jsx` | 来源、素材类型、画幅、数量筛选 |
| `SearchCandidateGrid.jsx` | 候选结果网格 |
| `SearchCandidateCard.jsx` | 单个候选预览、评分、授权提示、选择 |
| `SearchImportTray.jsx` | 待导入清单、确认导入 |
| `SearchAgentSettings.jsx` | Provider 开关、默认数量、安全搜索 |

不要继续扩大 `AgentPanel.jsx`。图像 Agent 已经很重，素材检索应该独立维护。

### 9.4 前端状态

```js
const [searchText, setSearchText] = createSignal("");
const [searchPlan, setSearchPlan] = createSignal(null);
const [searchBusy, setSearchBusy] = createSignal(false);
const [searchId, setSearchId] = createSignal("");
const [candidates, setCandidates] = createSignal([]);
const [selectedCandidateIds, setSelectedCandidateIds] = createSignal(new Set());
const [importBusy, setImportBusy] = createSignal(false);
const [importedAssets, setImportedAssets] = createSignal([]);
const [providerStatus, setProviderStatus] = createSignal({});
const [settings, setSettings] = createSignal({});
```

检索通过 `fetch + ReadableStream` 消费 8.4 的事件流，逐批 `setCandidates`，并据 `provider.completed.status` 在筛选栏提示限流/出错来源。

### 9.5 候选卡片

候选卡片必须显示：

1. 缩略图或视频预览（使用 `preview_url`，仅临时展示）。
2. 来源 provider。
3. 作者/creator。
4. license 简要信息（`license_status=unconfirmed` 时显式标“授权未确认”）。
5. source_url 外链。
6. 分辨率、时长、比例。
7. 匹配分数和简短理由。
8. “选择导入”勾选框；`import_supported=false`（如 Wikimedia 视频 P0）时禁用勾选并显示 `import_unsupported_reason`。

不要把授权说明做成大段正文。可以使用紧凑 badge：

```text
Pexels · 16:9 · 1920x1080 · 8.2s · score 86
```

详细 license 和 attribution 放在 hover / detail drawer。

### 9.6 导入交互

导入前必须出现确认卡（复用现有生成确认卡 `role="alertdialog"` 模式，避免 `window.confirm`）：

```text
将 3 个外部素材导入当前 Asset Library

来源：
- Pexels 2
- Wikimedia Commons 1

系统会下载文件到当前 Task，并保存来源、作者和授权元数据。
```

按钮：

```text
取消
确认导入
```

导入接口返回后，调用 `props.onAssetLibraryResult(result)`（即 `UploadAssetLibraryPage.jsx` 的 `applyAssetLibraryResult`，要求 result 含 `task`/`meta`）刷新页面状态；若接口未返回 `task`/`meta`，则改为 `kbApi.detail(taskId)` 重新加载后再 apply。对 `skipped=true` 的项提示“已存在，复用”，对 `failed` 项（如 `attribution_required_but_missing`、`import_not_supported`）提示原因。

## 10. Agent Prompt 设计

素材检索 Agent 的 system prompt 应强调：

1. 你只负责生成检索计划，不直接生成素材。
2. 不得编造素材 URL。
3. 查询词优先英文（面向 Pexels/Pixabay 匹配），可保留中文作为辅助。
4. 必须明确素材类型、画幅、必须出现的对象、排除项。
5. 医疗、金融、品牌、人物肖像等场景要提示授权风险。
6. 输出严格 JSON。

示例输出：

```json
{
  "request_id": "asset_search_...",
  "summary": "用户需要一段真实医院走廊 B-roll。",
  "media_types": ["video"],
  "aspect": "16:9",
  "sources": ["pexels", "pixabay", "wikimedia"],
  "queries": [
    {
      "query": "doctor tablet hospital corridor",
      "language": "en",
      "media_type": "video",
      "priority": 1
    },
    {
      "query": "medical staff hospital hallway",
      "language": "en",
      "media_type": "video",
      "priority": 2
    }
  ],
  "negative_terms": ["cartoon", "animation", "surgery", "blood"],
  "must_have": ["doctor", "hospital corridor"],
  "nice_to_have": ["tablet", "walking"],
  "risk_notes": [
    "如果画面中有可识别人脸，后续商用需保留来源授权记录。"
  ]
}
```

## 11. 合规与来源记录

### 11.1 展示期

候选展示阶段：

1. 必须显示 provider。
2. 必须显示 source_url。
3. 如果 provider 要求显示来源链接，卡片中必须可见。
4. 候选缩略图只用于临时预览，不缓存、不长期热链。

### 11.2 导入期

导入时：

1. 下载文件到本地（host 白名单校验）。
2. 保存 sidecar JSON。
3. manifest 中保存核心 attribution，`requires_attribution=true` 时强制 attribution 非空。
4. 写入 session event。

### 11.3 导出/最终视频阶段

本设计不实现最终视频片尾署名，但必须为后续预留字段：

```json
{
  "attribution_required": true,
  "attribution_text": "Photo by ...",
  "attribution_url": "https://..."
}
```

未来合成/导出模块可以读取所有 `origin.license` 字段生成素材来源清单。服务端在导入期已强制 attribution 完整，可保证该清单不出现空缺。

## 12. 事件设计

新增 session events（经 `StoryboardRuntime.add_event(session_id, kind, payload)` 写入，task→session 已在服务层解析）：

```text
koubo_storyboard.asset_search.plan.created
koubo_storyboard.asset_search.started
koubo_storyboard.asset_search.provider.started
koubo_storyboard.asset_search.provider.completed
koubo_storyboard.asset_search.completed
koubo_storyboard.asset_search.failed
koubo_storyboard.asset_search.import.requested
koubo_storyboard.asset_search.import.completed
koubo_storyboard.asset_search.import.failed
```

事件 payload 示例：

```json
{
  "task_id": 5,
  "session_id": 58,
  "search_id": "search_1782144000000",
  "providers": ["pexels", "pixabay"],
  "candidate_count": 24,
  "imported_count": 3,
  "skipped_count": 1
}
```

payload 内不得包含任何 API key 或授权 header。

## 13. 测试计划

### 13.1 后端单测

新增：

```text
OpenCrew/backend/tests/contracts/test_koubo_asset_search_agent_contract.py
```

覆盖：

1. `asset_search_routes.py` 存在并注册 endpoints。
2. provider adapter 不把 API key 返回给前端；`provider_status.configured` 仅为布尔。
3. Pexels/Pixabay/Wikimedia raw item 可 normalize 成统一 candidate。
4. Wikimedia license 解析失败时标 `license_status=unconfirmed`，不丢 attribution。
5. Search run 写入 `SessionContext/AssetSearchAgent/SearchRuns/`。
6. import 只接受 search run 中存在的 candidate_id。
7. import 写入正确资产目录。
8. import upsert `koubo_storyboard_assets.json`。
9. sidecar JSON 包含 license/source_url/creator。
10. 失败下载不写 manifest、不留半成品文件。
11. 超过导入数量上限返回 400。
12. **幂等**：同一 candidate 重复导入只产生一份文件和一条 manifest，第二次返回 `skipped=true`。
13. **SSRF**：download_url host 不在 provider 白名单时拒绝下载。
14. **attribution 强制**：`requires_attribution=true` 且 attribution 缺失时导入失败并返回原因。
15. provider 429 时该来源 `status=rate_limited`，整体检索仍成功返回其它来源候选。
16. **Pexels 端点**：provider 使用 `https://api.pexels.com/v1/search`（图片）与 `https://api.pexels.com/v1/videos/search`（视频），不得使用已弃用的 `https://api.pexels.com/videos/`。
17. **import_supported 拒绝**：`import_supported=false` 的候选（如 Wikimedia 视频）导入返回失败、reason=`import_not_supported`，且不下载、不写 manifest。
18. **接线**：`register_asset_search_routes` 已在 `router.py` 注册；`asset_search_services` 已加入 `services.py` 的 `_SERVICE_MODULES`，namespace helper（如 `upsert_asset_manifest_item`）在本模块可用。
19. **目录前缀**：导入目标 rel_path 以 `ASSET_*_REL + "/"` 开头，构造越界 rel_path 时被拒绝。
20. **导入响应形状**：返回含 `task`、`meta`、`plan` 三者，可驱动两版 `applyAssetLibraryResult`（含要求 `plan` 的 StoryBoard 版）刷新。
21. **manifest origin**：`origin` 含 `media_type`，幂等判重不依赖从顶层 `kind` 反推。
22. **planner 取结果**：planner 用 prompt_async + 轮询 messages，超时/JSON 解析失败时降级到翻译/规则兜底，不抛未捕获异常。
23. **Pixabay 缓存**：24h 内相同 cache key 不重复请求；命中缓存返回与首次一致的候选。
24. **过期候选**：导入超过 TTL 的旧 SearchRun 候选时，先按 `(provider, provider_asset_id)` 刷新 download_url；刷新失败返回 reason=`stale_candidate_url`，不写 manifest。

### 13.2 前端 contract

覆盖：

1. `LIBRARY_VIEWS` 包含 `search-agent`。
2. Sidebar 出现 `素材检索`，使用 `FlowIcon name="search"`。
3. `SearchAgentWorkspace.jsx` 独立存在。
4. 不把搜索组件塞进 `AgentPanel.jsx`。
5. 候选卡显示 provider/source/license，`unconfirmed` 时显式标注。
6. 导入确认不使用 `window.confirm`。
7. 检索流式消费使用 `fetch + ReadableStream`，不使用 `EventSource`。

### 13.3 手工验收

用一个真实 Task 验证：

1. 打开 Asset Library。
2. 点击 `素材检索`。
3. 输入“医院走廊里医生查看平板，横屏，真实纪录片风格”。
4. 系统生成检索计划（确认 query 已转为有效英文关键词）。
5. 执行检索。
6. 至少 Pexels/Pixabay 返回候选。
7. 勾选一个视频导入。
8. 文件出现在 `SessionOutput/storyboard/assets/videos/`。
9. `koubo_storyboard_assets.json` 增加 `source=asset_search` item，含 `content_sha256` 与完整 `origin.license`。
10. 再次导入同一候选，返回 `skipped=true`，不新增文件。
11. 刷新 Asset Library 的 Videos 页面，能看到该素材。
12. StoryBoard Asset Panel 重新加载后也能看到。

## 14. 实施顺序

### P0.1 数据结构和 Provider 基础

1. 新建 `asset_search_providers.py`，定义 async provider 接口与 host 白名单。
2. 实现 `normalize_candidate` 通用 helper（含 license_status 标注）。
3. 实现 Pexels 图片/视频 search。
4. 实现 Pixabay 图片/视频 search。
5. 实现 Wikimedia 图片 search + imageinfo metadata（license 解析含失败降级，单独预留工时）。
6. 写 provider normalize 单测（含 Wikimedia license 失败用例）。

### P0.2 后端检索与导入

1. 新建 `asset_search_services.py`。
2. 实现 settings 读写 + 环境变量凭据解析。
3. 实现 query planner（prompt_async + 轮询 messages 取结果 + 翻译/规则兜底 + degraded 标记）。
4. 实现 API 响应缓存（cache key/TTL/命中，Pixabay 24h，见 6.5）。
5. 实现 search run 写文件。
6. 实现 search 流式编排（429 降级、provider 状态）。
7. 实现 import candidate（幂等预检 + 过期 URL 刷新 + SSRF/重定向校验 + assets 前缀校验 + attribution 强制 + import_supported 校验 + content_sha256），返回 `{ok, task, meta, plan, imported, failed}`。
8. 导入后 upsert manifest。
9. 写 session events（task→session 解析）。
10. 接线：`router.py` 加 `register_asset_search_routes(router, deps)`；`services.py` 的 `_SERVICE_MODULES` 加入 `asset_search_services` 并在顶部 import，确认 namespace helper 注入生效。

### P0.3 前端素材检索页面

1. Sidebar 加 `素材检索`（FlowIcon search）。
2. `UploadAssetLibraryOverlay.jsx` 扩展 `LIBRARY_VIEWS` 加 `search-agent` view。
3. 新建 `searchAgent/` 组件目录。
4. 实现 Search Agent 输入和计划卡（含 degraded 提示）。
5. 实现候选网格（fetch+ReadableStream 流式消费，provider 状态提示）。
6. 实现导入确认卡（alertdialog，skipped/failed 反馈）。
7. 导入成功后刷新 Asset Library payload。

### P0.4 验证

1. 跑后端 contract tests（含幂等、SSRF、attribution、限流用例）。
2. 跑 `OpenCrew/frontend` build。
3. 用真实 Task 手工验证导入文件、manifest、幂等复用。
4. 检查 session events。

### P1

1. 接入 Connection 凭据录入 UI（`kind="asset_search"`）。
2. 接 Unsplash（download tracking + hotlink 合规）。
3. 接音频素材源。
4. 增加 StoryBoard Shot/Scene 批量检索。
5. 增加 CLIP/embedding 重排。
6. 增加最终视频素材来源清单导出。

## 15. 验收标准

### 文件

- 不产生新的 `SessionOutput/storyboard/asset_search/` 资产根目录。
- 检索过程写入 `SessionContext/AssetSearchAgent/`。
- 导入图片进入 `SessionOutput/storyboard/assets/images/`。
- 导入视频进入 `SessionOutput/storyboard/assets/videos/`。
- 导入音频进入 `SessionOutput/storyboard/assets/audios/`。
- `koubo_storyboard_assets.json` 有对应 manifest item，含 `content_sha256` 与 `origin.license`。
- 每个导入素材有 sidecar JSON。
- 同一候选重复导入不产生重复文件或重复 manifest。

### UI

- 左侧有 `素材检索` 入口。
- 页面首屏是检索工作台，不是营销页。
- 候选结果显示 provider、creator、license、source_url；`unconfirmed` 显式标注。
- 导入必须二次确认。
- 导入成功后当前 Asset Library 可见。
- 不使用 `window.confirm`；流式消费不使用 `EventSource`。

### 服务

- API key 不返回前端。
- LLM 不直接访问外部素材 URL，也不编造 URL。
- 后端不接受任意前端 download_url；provider 返回的 download_url 也经 host 白名单校验。
- provider 请求有超时、限额、缓存、429 降级。
- Pexels 视频端点使用 `v1/videos/search`，未使用已弃用的旧路径。
- 失败不会写 manifest，且不留半成品文件。
- 同一候选导入幂等（去重键含 media_type）。
- 导入只写入 `assets/{images,videos,audios}/`（前缀校验通过），不落在其它 workspace 路径。
- 导入响应含 `task`/`meta`/`plan`，StoryBoard 与 Asset Library 两侧导入后均无需手动刷新即可看到新素材。
- Pixabay 请求 24h 缓存生效；导入过期旧 run 候选会刷新 URL 或明确失败，不静默下坏链。
- `import_supported=false` 的候选导入被拒绝（如 Wikimedia 视频 P0）。
- `register_asset_search_routes` 在 `router.py` 注册、service 模块在 `services.py` 同步。
- session events 可追踪 plan、search、import；payload 无密钥。

### 合规

- Pexels 候选显示 Pexels 来源链接。
- Pixabay 候选显示 Pixabay 来源。
- Wikimedia 候选保留 license 和 attribution；解析失败标 `unconfirmed` 而非丢弃。
- `requires_attribution=true` 的素材缺失 attribution 时服务端拒绝导入。
- Unsplash 默认关闭，直到 download tracking 和 hotlink 规范实现。

## 16. 参考资料

- Pexels API Documentation: https://www.pexels.com/api/documentation/
- Pixabay API Documentation: https://pixabay.com/api/docs/
- Unsplash API Documentation: https://unsplash.com/documentation
- MediaWiki Imageinfo API: https://www.mediawiki.org/wiki/API:Imageinfo
- 当前参考文档：`OpenCrew/docs/SessionDesign-R2/Koubo_AssetLibrary_PromptAgent_设计文档.md`
