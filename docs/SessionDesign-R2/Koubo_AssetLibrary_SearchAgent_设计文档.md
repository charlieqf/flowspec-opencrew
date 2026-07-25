# Koubo Asset Library 素材检索 Agent 设计文档

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

当前仓库已经具备可以复用的 Asset Library、Agent Chat、manifest 和文件落盘能力：

| 能力 | 当前落点 | 复用方式 |
| --- | --- | --- |
| Asset Library 左侧导航 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/LibrarySidebar.jsx` | 新增 `search-agent` 入口 |
| Asset Library 页面装配 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx` | 增加 view、候选状态、导入回调 |
| 图片/视频/音频资产归一化 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/uploadAssetLibraryModel.js` | 扩展外部候选素材的 kind/label/search text |
| 图像 Agent 面板 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/AgentPanel.jsx` | 交互风格参考，不直接塞入检索逻辑 |
| 视频 Agent 面板 | `OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx` | SSE、确认卡、进度状态可参考 |
| 素材 manifest 写入 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_pool_services.py` | 导入后调用或复用 `upsert_asset_manifest_item` 语义 |
| 素材目录常量 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/constants.py` | 使用 `ASSET_IMAGES_REL`、`ASSET_VIDEOS_REL`、`ASSET_AUDIOS_REL`、`ASSETS_REL` |
| 安全相对路径 | `OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/io_utils.py` | 导入、预览、删除都必须保持 workspace 内路径 |
| Session event | 当前 `add_event(...)` 模式 | 检索、候选、导入、失败都写事件 |
| Agent Chat 结构 | `agent_chat_routes.py`、`agent_chat_services.py` | 可复用上下文注入方式，但检索 Agent 建议单独服务文件 |

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
4. 点击“开始检索”后，后端通过 SSE 返回各来源进度。
5. 候选结果出现在中间区域。
6. 用户勾选候选并导入。

首版不要求 Agent 长对话生成复杂推理。它最重要的是稳定地产生结构化检索计划。

## 4. 能力边界

### 4.1 必须做

1. 支持自然语言输入素材需求。
2. 支持图片和视频素材检索。
3. P0 支持 Pexels、Pixabay、Wikimedia Commons。
4. 支持来源筛选、画幅筛选、关键词编辑。
5. 支持候选卡片预览。
6. 支持用户选择后导入到当前 Task Asset Library。
7. 导入后写入 `koubo_storyboard_assets.json`。
8. 每个导入素材保存来源、作者、license、source_url、provider_asset_id、download_url 或 file_page_url。
9. 检索和导入过程写 session events。
10. API key 只在后端读取，不进入前端。

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

## 5. 外部素材源策略

这里说的“开源好的素材库”在实现上拆成三类：

| 类型 | 示例 | 说明 |
| --- | --- | --- |
| 开放授权媒体库 | Wikimedia Commons | 授权更明确，但 license/attribution 复杂 |
| 免费商用素材 API | Pexels、Pixabay | 易用，适合短视频素材，但有平台使用规范 |
| 高质量图片 API | Unsplash | 图片质量高，但使用/下载规范更特殊，建议 P1 |

### 5.1 Pexels

官方 API 支持图片和视频检索。Pexels 文档要求 API 请求相关页面展示 Pexels 链接，建议尽量署名摄影师；API 需要 `Authorization` header；默认有请求限额。视频 endpoint 应使用 `https://api.pexels.com/v1/videos/` 新路径。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | 是 |
| 画幅过滤 | 是 |
| 尺寸过滤 | 是 |
| 作者/来源元数据 | 是 |
| 下载到本地 | 导入时下载 |

### 5.2 Pixabay

Pixabay API 支持 royalty-free 图片和视频搜索。官方文档要求在展示搜索结果时告诉用户素材来自 Pixabay；请求需要缓存 24 小时；不允许系统化大规模下载。返回的图片 URL 只适合临时展示搜索结果，实际使用时应下载到服务端。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | 是 |
| 分类/语言/方向 | 是 |
| 作者/来源元数据 | 是 |
| 下载到本地 | 导入时下载 |

### 5.3 Wikimedia Commons

Wikimedia Commons 是真正偏开放授权的媒体库，适合公共领域、历史、科学、地理、机构、真实场景类素材。它的关键不是搜索难度，而是 license 和 attribution 元数据必须保存。MediaWiki `imageinfo` 可返回文件 URL、尺寸、mime、mediatype、extmetadata 等信息。

首版接入：

| 能力 | 支持 |
| --- | --- |
| 图片搜索 | 是 |
| 视频搜索 | P0 可只做候选，P1 完整导入 |
| license 元数据 | 必须 |
| attribution 元数据 | 必须 |
| 文件页 URL | 必须 |
| 下载到本地 | 导入时下载 |

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

候选检索状态写入 SessionContext：

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
2. `Cache/` 只保存 API 响应缓存，不是资产库。
3. 导入成功的物理文件只进入 `SessionOutput/storyboard/assets/...`。

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
    "attribution_text": "Video by Creator Name on Pexels"
  },
  "width": 1920,
  "height": 1080,
  "duration_seconds": 8.2,
  "mime_type": "video/mp4",
  "orientation": "landscape",
  "tags": ["hospital", "doctor", "corridor"],
  "score": 0.86,
  "score_reasons": [
    "matches hospital corridor",
    "landscape ratio",
    "duration fits short B-roll"
  ],
  "raw": {}
}
```

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
    "pexels": {"requested": 2, "returned": 18, "kept": 8},
    "pixabay": {"requested": 2, "returned": 12, "kept": 5},
    "wikimedia": {"requested": 1, "returned": 9, "kept": 3}
  }
}
```

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
  "origin": {
    "tool": "asset_search_agent",
    "search_id": "search_1782144000000",
    "candidate_id": "pexels_video_123456",
    "provider": "pexels",
    "provider_asset_id": "123456",
    "source_url": "https://www.pexels.com/video/...",
    "creator": {"name": "Creator Name", "url": "https://..."},
    "license": {
      "name": "Pexels License",
      "url": "https://www.pexels.com/license/",
      "requires_attribution": false,
      "attribution_text": "Video by Creator Name on Pexels"
    },
    "query": "doctor tablet hospital corridor",
    "score": 0.86
  }
}
```

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
| `asset_search_routes.py` | 注册素材检索 Agent API、SSE、导入端点 |
| `asset_search_services.py` | 检索计划、候选合并、评分、导入、缓存、事件 |
| `asset_search_providers.py` | Pexels/Pixabay/Wikimedia provider adapter |

不要继续把主要逻辑追加到已经很大的 `asset_routes.py`。`asset_routes.py` 可以只保留 include/register 调用。

### 7.2 Provider Interface

```python
class AssetSearchProvider(Protocol):
    provider_id: str

    def search(
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

    def import_candidate(
        self,
        *,
        candidate: dict[str, Any],
        target_path: Path,
    ) -> dict[str, Any]:
        ...
```

Provider 不直接写 manifest，只负责：

1. API 调用。
2. raw -> candidate 归一化。
3. 下载素材文件。
4. 返回下载后的文件元信息。

manifest 写入由 `asset_search_services.py` 统一完成。

### 7.3 Settings

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
    "prefer_exact_aspect": true,
    "prefer_high_resolution": true,
    "prefer_open_license": true,
    "prefer_short_video_broll": true
  }
}
```

API key 不写 Settings，统一走 Connection 或 secret store：

```text
kind = "asset_search"
provider = "pexels" | "pixabay" | "unsplash"
```

如果当前 Connection 表没有 `asset_search` kind，P0 可以用环境变量兜底：

```text
PEXELS_API_KEY
PIXABAY_API_KEY
UNSPLASH_ACCESS_KEY
```

但正式产品路径应走 Connection。

### 7.4 Query Planner

Query Planner 使用 OpenCode Run Model 或现有 Agent Chat model，但只输出 JSON，不直接调用外网。

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

兜底：如果 Run Model 不可用，后端用规则抽取：

1. 中文需求保留原文。
2. 如果没有英文 query，调用轻量翻译 helper 或用用户文本原样搜索支持中文的源。
3. 根据关键词判断 `image` / `video`。
4. 根据 `横屏/竖屏/方图/16:9/9:16/1:1` 推断 aspect。

### 7.5 搜索编排

搜索流程：

```text
1. validate payload
2. load task/workspace
3. load settings
4. build query plan
5. for each source:
     for each query:
       provider.search(...)
       normalize candidates
       cache response
6. merge candidates
7. dedupe by provider_asset_id/source_url/download_url/hash
8. filter by media_type/aspect/min_quality
9. rank candidates
10. write SearchRuns/search_*.json
11. stream candidates to frontend
```

### 7.6 评分规则

基础分：

```text
score = text_match * 0.35
      + aspect_match * 0.15
      + media_quality * 0.20
      + source_trust * 0.15
      + license_clarity * 0.10
      + duration_fit * 0.05
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `text_match` | query、title、description、tags 与需求匹配程度 |
| `aspect_match` | 是否符合 16:9 / 9:16 / 1:1 |
| `media_quality` | 分辨率、码率、尺寸、缩略图质量 |
| `source_trust` | Wikimedia/Pexels/Pixabay 等来源基础权重 |
| `license_clarity` | license 是否明确、是否有 creator/source_url |
| `duration_fit` | 视频是否适合 B-roll，过短或过长降权 |

P0 可以不用 embedding。只用关键词命中 + 规则评分即可。

### 7.7 导入流程

导入必须是用户动作：

```text
用户选择候选
  -> POST import
  -> 后端根据 search_id 找候选
  -> provider 下载文件
  -> 校验 MIME / 扩展名 / 大小 / 真实文件头
  -> 写入 assets/images 或 assets/videos
  -> 写 sidecar JSON
  -> upsert manifest
  -> add_event
  -> 返回 imported assets
```

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
2. 不接受前端直接传任意 `download_url` 导入。
3. 下载超时必须有限制。
4. 单文件大小 P0 限制：
   - image: 30 MB
   - video: 500 MB
   - audio: 100 MB
5. response content-type 与文件扩展必须匹配。
6. 下载只写入 workspace 内的 `assets/` 目录。
7. 所有 API key 和授权 header 必须 redaction。
8. 不允许批量导入超过 12 个候选。
9. 不允许后台自动翻页抓取超过配置上限。
10. 导入失败不能写 manifest。

## 8. 后端接口设计

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
    "nice_to_have": []
  }
}
```

### 8.4 执行检索 SSE

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-library-search/search/events
```

事件：

```json
{"type":"started","search_id":"search_..."}
{"type":"plan","plan":{}}
{"type":"provider.started","provider":"pexels"}
{"type":"candidate.batch","provider":"pexels","items":[]}
{"type":"provider.completed","provider":"pexels","returned":18,"kept":8}
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

响应：

```json
{
  "ok": true,
  "imported": [
    {
      "id": "SessionOutput/storyboard/assets/videos/...",
      "path": "SessionOutput/storyboard/assets/videos/...",
      "kind": "video",
      "source": "asset_search"
    }
  ],
  "failed": []
}
```

## 9. 前端实现设计

### 9.1 新增 view

修改：

```text
OpenCrew/frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx
```

新增：

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

新增按钮：

```jsx
<button class={props.view() === "search-agent" ? "is-active" : ""} type="button" onClick={() => props.setView("search-agent")}>
  <span class="ual-nav-icon"><FlowIcon name="search" /></span>
  <span class="ual-nav-label">素材检索</span>
</button>
```

如果 `FlowIcon` 没有 `search`，先复用 `addNotes`，不要引入一套新图标系统。

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
| `SearchAgentWorkspace.jsx` | 页面装配、数据加载、SSE 生命周期 |
| `SearchAgentPanel.jsx` | 右侧对话/检索需求输入 |
| `SearchBriefCard.jsx` | 展示和编辑检索计划 |
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
const [settings, setSettings] = createSignal({});
```

### 9.5 候选卡片

候选卡片必须显示：

1. 缩略图或视频预览。
2. 来源 provider。
3. 作者/creator。
4. license 简要信息。
5. source_url 外链。
6. 分辨率、时长、比例。
7. 匹配分数和简短理由。
8. “选择导入”勾选框。

不要把授权说明做成大段正文。可以使用紧凑 badge：

```text
Pexels · 16:9 · 1920x1080 · 8.2s · score 86
```

详细 license 和 attribution 放在 hover / detail drawer。

### 9.6 导入交互

导入前必须出现确认卡：

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

确认卡样式复用现有生成确认卡的模式，避免 `window.confirm`。

## 10. Agent Prompt 设计

素材检索 Agent 的 system prompt 应强调：

1. 你只负责生成检索计划，不直接生成素材。
2. 不得编造素材 URL。
3. 查询词优先英文，但可以保留中文作为辅助。
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
4. 候选缩略图只用于临时预览。

### 11.2 导入期

导入时：

1. 下载文件到本地。
2. 保存 sidecar JSON。
3. manifest 中保存核心 attribution。
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

未来合成/导出模块可以读取所有 `origin.license` 字段生成素材来源清单。

## 12. 事件设计

新增 session events：

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
  "search_id": "search_1782144000000",
  "providers": ["pexels", "pixabay"],
  "candidate_count": 24,
  "imported_count": 3
}
```

## 13. 测试计划

### 13.1 后端单测

新增：

```text
OpenCrew/backend/tests/contracts/test_koubo_asset_search_agent_contract.py
```

覆盖：

1. `asset_search_routes.py` 存在并注册 endpoints。
2. provider adapter 不把 API key 返回给前端。
3. Pexels/Pixabay/Wikimedia raw item 可 normalize 成统一 candidate。
4. Search run 写入 `SessionContext/AssetSearchAgent/SearchRuns/`。
5. import 只接受 search run 中存在的 candidate_id。
6. import 写入正确资产目录。
7. import upsert `koubo_storyboard_assets.json`。
8. sidecar JSON 包含 license/source_url/creator。
9. 失败下载不写 manifest。
10. 超过导入数量上限返回 400。

### 13.2 前端 contract

覆盖：

1. `LIBRARY_VIEWS` 包含 `search-agent`。
2. Sidebar 出现 `素材检索`。
3. `SearchAgentWorkspace.jsx` 独立存在。
4. 不把搜索组件塞进 `AgentPanel.jsx`。
5. 候选卡显示 provider/source/license。
6. 导入确认不使用 `window.confirm`。

### 13.3 手工验收

用一个真实 Task 验证：

1. 打开 Asset Library。
2. 点击 `素材检索`。
3. 输入“医院走廊里医生查看平板，横屏，真实纪录片风格”。
4. 系统生成检索计划。
5. 执行检索。
6. 至少 Pexels/Pixabay 返回候选。
7. 勾选一个视频导入。
8. 文件出现在 `SessionOutput/storyboard/assets/videos/`。
9. `koubo_storyboard_assets.json` 增加 `source=asset_search` item。
10. 刷新 Asset Library 的 Videos 页面，能看到该素材。
11. StoryBoard Asset Panel 重新加载后也能看到。

## 14. 实施顺序

### P0.1 数据结构和 Provider 基础

1. 新建 `asset_search_providers.py`。
2. 实现 `normalize_candidate` 通用 helper。
3. 实现 Pexels 图片/视频 search。
4. 实现 Pixabay 图片/视频 search。
5. 实现 Wikimedia 图片 search + imageinfo metadata。
6. 写 provider normalize 单测。

### P0.2 后端检索与导入

1. 新建 `asset_search_services.py`。
2. 实现 settings 读写。
3. 实现 query planner 规则兜底。
4. 实现 search run 写文件。
5. 实现 search SSE。
6. 实现 import candidate。
7. 导入后 upsert manifest。
8. 写 session events。

### P0.3 前端素材检索页面

1. Sidebar 加 `素材检索`。
2. `UploadAssetLibraryOverlay.jsx` 加 `search-agent` view。
3. 新建 `searchAgent/` 组件目录。
4. 实现 Search Agent 输入和计划卡。
5. 实现候选网格。
6. 实现导入确认卡。
7. 导入成功后刷新 Asset Library payload。

### P0.4 验证

1. 跑后端 contract tests。
2. 跑 `OpenCrew/frontend` build。
3. 用真实 Task 手工验证导入文件和 manifest。
4. 检查 session events。

### P1

1. 接 Unsplash。
2. 接音频素材源。
3. 增加 StoryBoard Shot/Scene 批量检索。
4. 增加 CLIP/embedding 重排。
5. 增加最终视频素材来源清单导出。

## 15. 验收标准

### 文件

- 不产生新的 `SessionOutput/storyboard/asset_search/` 资产根目录。
- 检索过程写入 `SessionContext/AssetSearchAgent/`。
- 导入图片进入 `SessionOutput/storyboard/assets/images/`。
- 导入视频进入 `SessionOutput/storyboard/assets/videos/`。
- 导入音频进入 `SessionOutput/storyboard/assets/audios/`。
- `koubo_storyboard_assets.json` 有对应 manifest item。
- 每个导入素材有 sidecar JSON。

### UI

- 左侧有 `素材检索` 入口。
- 页面首屏是检索工作台，不是营销页。
- 候选结果显示 provider、creator、license、source_url。
- 导入必须二次确认。
- 导入成功后当前 Asset Library 可见。
- 不使用 `window.confirm`。

### 服务

- API key 不返回前端。
- LLM 不直接访问外部素材 URL。
- 后端不接受任意前端 download_url。
- provider 请求有超时、限额、缓存。
- 失败不会写 manifest。
- session events 可追踪 plan、search、import。

### 合规

- Pexels 候选显示 Pexels 来源链接。
- Pixabay 候选显示 Pixabay 来源。
- Wikimedia 候选保留 license 和 attribution。
- Unsplash 默认关闭，直到 download tracking 和 hotlink 规范实现。

## 16. 参考资料

- Pexels API Documentation: https://www.pexels.com/api/documentation/
- Pixabay API Documentation: https://pixabay.com/api/docs/
- Unsplash API Documentation: https://unsplash.com/documentation
- MediaWiki Imageinfo API: https://www.mediawiki.org/wiki/API:Imageinfo
- 当前参考文档：`OpenCrew/docs/SessionDesign-R2/Koubo_AssetLibrary_PromptAgent_设计文档.md`
