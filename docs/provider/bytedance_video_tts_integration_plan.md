# ByteDance 视频与 TTS 模型接入方案

日期：2026-06-08
状态：调研后方案；Phase 0 安全下载器已实施；Phase 1 Seedance 后端 mock MVP 已实施，含 `extra_json` 默认参数读取和 provider task id 续查；OpenRouter Seedance smoke 已通过；ByteDance TTS 配置页/05_02 生成 MVP 已实施，真实火山 TTS smoke 待 appid/access_token
范围：OpenCrew provider 配置、Analysis_V1 视频执行器、OpenRouter 视频路径、TTS 试听/匹配与 TTS Builder
实施选择：国内火山路径保留，视频默认 `https://ark.cn-beijing.volces.com/api/v3`，TTS 默认 `https://openspeech.bytedance.com`；另新增 OpenRouter 视频路径用于使用 OpenRouter key 调用 `bytedance/seedance-*`

## 1. 结论

ByteDance 生态的视频和 TTS 都可以接入 OpenCrew，但接入成本不同。

视频建议优先接入。Seedance 视频生成是标准异步任务 API，和当前 Wan 视频执行器的形态接近：提交任务、轮询状态、下载结果 URL。先做文生视频和首帧图生视频 MVP，风险较低。

TTS 可以接入，但需要拆成两个阶段。配置页的 TTS preview/match 可以较快支持；Analysis_V1 主流程里的 `03_02_TTSBuilderQuick` 当前硬限制 Google/Gemini voice catalog，不能只加 provider 配置就直接使用 ByteDance TTS。要进入完整成片流程，需要新增 ByteDance voice catalog 和 TTS 调用适配器。

推荐顺序：

0. 安全下载器前置：统一 provider artifact download，并修掉现有 `audio_url_bytes` SSRF 风险。
1. Seedance 视频 MVP：text-to-video、first-frame image-to-video、silent output。
2. OpenRouter Seedance 视频路径：用于没有火山 key、但已有 OpenRouter key 的测试和兜底。
3. TTS 配置页 MVP：固定音色试听、voice match 候选音频生成。
4. Analysis_V1 TTS Builder：ByteDance voice catalog、top candidates 生成、计费审计。
5. Seedance 高级输入：首尾帧、多参考图/视频/音频、有声视频。

## 2. 外部能力概览

### 2.1 视频：Seedance / 火山方舟 / BytePlus ModelArk

国内火山方舟：

- Base URL: `https://ark.cn-beijing.volces.com/api/v3`
- 创建任务：`POST /contents/generations/tasks`
- 查询任务：`GET /contents/generations/tasks/{id}`
- 鉴权：`Authorization: Bearer <ARK_API_KEY>`

国际 BytePlus ModelArk：

- Base URL: `https://ark.ap-southeast.bytepluses.com/api/v3`
- API 形态与火山方舟基本一致。

核心请求形态：

```json
{
  "model": "doubao-seedance-2-0-fast-260128",
  "content": [
    {"type": "text", "text": "A cinematic product video..."}
  ],
  "ratio": "9:16",
  "resolution": "720p",
  "duration": 5,
  "generate_audio": false
}
```

可支持输入：

- `text`
- `image_url`，支持 URL、base64 data URL、部分 asset URI
- `video_url`，Seedance 2.0 系列支持
- `audio_url`，Seedance 2.0 系列支持，但不能单独作为唯一输入，需要至少一个 image/video reference

任务状态：

- `queued`
- `running`
- `succeeded`
- `failed`
- `expired`
- `cancelled`

重要限制：

- Seedance 2.0 系列不支持直接上传含真人脸的参考图/视频。需要使用平台授权资产、预置虚拟人像或可信模型产物等合规路径。
- 结果 URL 通常是临时签名 URL，必须尽快下载到本地 workspace 或长期存储。
- 有声视频 `generate_audio=true` 会改变成本、输出稳定性和后续 lipsync/TTS 链路设计。MVP 建议先关闭。

### 2.2 TTS：豆包语音 / 火山引擎

可选接口：

- V3 HTTP Chunked 单向流式：`https://openspeech.bytedance.com/api/v3/tts/unidirectional`
- V3 WebSocket 单向/双向流式：低延迟场景
- 异步长文本：`POST /api/v3/tts/submit` + `POST /api/v3/tts/query`
- V1 HTTP 非流式：存在，但官方资料标注为旧接口或不推荐用于大模型音色

异步长文本接口特征：

- submit 路径：`https://openspeech.bytedance.com/api/v3/tts/submit`
- query 路径：`https://openspeech.bytedance.com/api/v3/tts/query`
- query 成功后返回 `audio_url`
- 服务端音频可保存 7 天，返回的下载 URL 有时效
- 支持 mp3、ogg_opus、pcm、wav 等输出，支持多语种、音色、语速、情绪等参数

典型 Header：

```text
X-Api-App-Id: <app_id>
X-Api-Access-Key: <access_token>
X-Api-Resource-Id: volc.service_type.10029 或 seed-tts-2.0
X-Api-Request-Id: <uuid>
```

注意：ByteDance TTS 的鉴权材料不是简单的单一 `api_key`。OpenCrew 现有 `tool_media_provider_configs` 只有一条主 secret 引用路径（`api_key_ref` 指向加密 secret，`api_key_ciphertext` 仅作为 legacy/迁移兼容），`app_id`、`resource_id`、采样率等附属参数需要放入 `extra_json`。

### 2.3 OpenRouter 视频路径

OpenRouter 可通过一个 OpenRouter API key 调用 ByteDance Seedance 视频模型，但它不是火山方舟协议：

- Base URL: `https://openrouter.ai/api/v1`
- 创建视频任务：`POST /videos`
- 轮询：提交响应中的 `polling_url`，或 `GET /videos/{id}`
- 鉴权：`Authorization: Bearer <OPENROUTER_API_KEY>`
- 模型示例：
  - `bytedance/seedance-2.0-fast`
  - `bytedance/seedance-2.0`
  - `bytedance/seedance-1-5-pro`

典型请求：

```json
{
  "model": "bytedance/seedance-2.0-fast",
  "prompt": "A handheld vertical product shot...",
  "duration": 5,
  "resolution": "720p",
  "aspect_ratio": "9:16"
}
```

OpenRouter 支持 `frame_images`，但官方 cookbook 要求 first-frame image 是稳定可下载的公开 HTTPS 图片 URL。OpenCrew 当前只有本地 workspace 图片，因此 OpenRouter 路径默认 text-to-video，不默认发送本地首帧图。后续如果实现临时对象存储/公开 signed URL，再启用 `frame_images`。

## 3. OpenCrew 现状对齐

### 3.1 Provider 配置表

当前媒体 provider 使用：

- 表：`tool_media_provider_configs`
- 唯一键：`(kind, provider)`
- 字段：`kind`, `provider`, `enabled`, `active`, `model`, `api_key_ciphertext`, `api_key_ref`, `extra_json`

密钥处理必须按现有 secret 路径走：

- `api_key_ref` 是 secret handle，不是密钥明文。
- 新保存路径应把 secret 写入加密 secret store，并把 `api_key_ciphertext` 置空。
- `api_key_ciphertext` 是兼容/迁移列；读取时可以作为 legacy fallback，但新接入不要把 ByteDance key 明文或半明文塞进去。
- ByteDance TTS 的 `X-Api-Access-Key` 是 secret，必须通过 `api_key_ref` 解密/读取；`app_id`、`resource_id`、`base_url`、`sample_rate` 等附属参数放 `extra_json`。

接入前需要验证现有 secret 解析路径支持 “一个 secret + 多个 `extra_json` 附属参数” 的组合。adapter 的输入应是：

- secret：Access Key / API Key，从 `api_key_ref` 对应 secret store 读取，legacy 时兼容 `api_key_ciphertext`
- config：`extra_json` 中的 `app_id`, `resource_id`, `base_url`, `audio_format`, `sample_rate`

建议 provider id：

- 视频：`bytedance`
- OpenRouter 视频：`openrouter`
- TTS：`bytedance`

因为唯一键包含 `kind`，视频和 TTS 可以共用 provider id，但分别保存：

- `kind=video, provider=bytedance`
- `kind=video, provider=openrouter`
- `kind=tts, provider=bytedance`

推荐 `extra_json`：

视频：

```json
{
  "base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "region": "cn-beijing",
  "default_ratio": "9:16",
  "default_resolution": "720p",
  "generate_audio": false
}
```

OpenRouter 视频：

```json
{
  "base_url": "https://openrouter.ai/api/v1",
  "default_aspect_ratio": "9:16",
  "default_resolution": "720p",
  "send_frame_images": false
}
```

TTS：

```json
{
  "base_url": "https://openspeech.bytedance.com",
  "app_id": "<volc_app_id>",
  "resource_id": "volc.service_type.10029",
  "audio_format": "mp3",
  "sample_rate": 24000,
  "auth_mode": "x_api_headers"
}
```

不要把 `app_id` 或 `resource_id` 当 secret；它们可以存在 `extra_json`，但日志仍应避免完整打印 provider 配置。不要把 `api_key_ref` 当作可打印的密钥值，它只能作为 secret 引用标识。

### 3.2 配置 UI 与连接测试

需要改动：

- `ModelConfig/backend/opcrew_model_config/media_model_config.py`
  - `media_options("video")` 增加 ByteDance / Seedance 模型。
  - `media_options("video")` 增加 OpenRouter / Seedance 模型。
  - `media_options("tts")` 增加 ByteDance / Doubao TTS 模型和音色。
  - `test_media_connection()` 增加 video/tts 分支。
  - TTS preview/match 增加 ByteDance adapter。

连接测试建议：

视频：

- 不真实提交高成本视频任务。
- 调用模型列表或做轻量校验。如果没有低成本校验接口，则只验证 API Key 格式和 base URL reachable，并明确返回 “key saved, generation test required”。
- OpenRouter 视频连接测试同样不提交视频任务，避免消耗 OpenRouter credits。

TTS：

- 用短文本、低成本音色真实生成一次更有价值。
- 但要限制字符数、timeout 和本地缓存。

## 4. 视频接入设计

### 4.1 执行器模块

新增：

- `ToolLibrary/Analysis_V1/video_plan_executor_modules/video_seedance.py`
- `ToolLibrary/Analysis_V1/Reference/05_02/Video_Seedance.md`

修改：

- `ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py`
  - `MODULE_REFERENCE_TEMPLATE_RELS` 增加 `Video_Seedance`
  - `video_module_for()` 增加 `bytedance`, `seedance`, `doubao-seedance`

分发建议：

```python
if provider_value in {"bytedance", "seedance", "volcengine", "ark"} or "seedance" in model_value:
    return import_executor_module("video_seedance")
```

### 4.2 `video_seedance.py` 行为

MVP 输入：

- prompt JSON 中的 `prompt`
- 可选 1 张 reference image，作为 `image_url` + `role=first_frame`
- `duration` 夹在 4 到 15 秒
- `ratio` 默认 `9:16`
- `resolution` 默认 `720p`
- `generate_audio=false`

MVP 不做：

- 回调 callback
- 多图/多视频/音频 reference
- 真人资产授权流
- 有声视频

任务流程：

1. 读取 provider config。
2. 构造 `content`。
3. `POST {base_url}/contents/generations/tasks`。
4. 解析 `id`。
5. 每 5 秒 `GET {base_url}/contents/generations/tasks/{id}`。
6. 终态：
   - `succeeded`: 提取 `content.video_url` 或递归提取首个 video URL。
   - `failed`/`expired`/`cancelled`: 报错并写审计。
7. 使用 Phase 0 的安全下载器下载视频到 output path。
8. 返回 `provider`, `model`, `task_id`, `video_url`, `duration`, `elapsed_seconds`。

### 4.3 URL 与素材策略

MVP 推荐把本地图片转成 base64 data URL。这样不用先实现对象存储上传。

后续如果要支持视频/音频 reference，需要二选一：

- 实现安全的临时对象存储上传，返回 public/signed URL。
- 使用火山/BytePlus asset API，返回 `asset://...`。

不要让 provider 直接读取内网 URL、本机 URL 或用户任意 URL。

### 4.4 计费与审计

应记录：

- provider: `bytedance`
- model_id: Seedance model id
- modality: `video`
- request: redacted request，不含 bearer key，不含完整签名 URL
- response: task id、状态、输出 URL host、duration、resolution
- units:
  - `video_seconds`
  - `resolution`
  - `generate_audio`
  - `reference_image_count`
  - `reference_video_count`
  - `reference_audio_count`

## 5. TTS 接入设计

### 5.1 配置页 TTS preview/match

新增函数：

- `bytedance_tts_preview_url(...)`
- `bytedance_tts_match_preview_url(...)`，可先复用 preview

MVP 推荐使用异步长文本 submit/query，而不是 V3 WebSocket。理由：

- 实现简单，与当前 preview/match 的 “拿到 audio URL 后缓存” 模型匹配。
- 便于记录 task id 和失败原因。
- 对短文本试听延迟可接受。

流程：

1. 从 `tool_media_provider_configs` 读取 `api_key_ref`、解析 secret，并读取 `extra_json`。
2. Header 组装 `X-Api-App-Id`, `X-Api-Access-Key`, `X-Api-Resource-Id`, `X-Api-Request-Id`。
3. `POST /api/v3/tts/submit`。
4. 解析 `data.task_id`。
5. 轮询 `POST /api/v3/tts/query`。
6. 成功后取 `data.audio_url`。
7. 使用 Phase 0 的安全下载器写入 preview cache。

请求体需要包含：

- `user.uid`
- `req_params.text`
- `req_params.speaker`
- `req_params.audio_params.format`
- `req_params.audio_params.sample_rate`
- 可选 `req_params.audio_params.speech_rate`
- 可选 `req_params.audio_params.emotion`

### 5.2 Analysis_V1 TTS Builder

当前 `03_02_TTSBuilderQuick.py` 的限制：

- 默认模型是 Gemini TTS。
- `load_catalog()` 双重限制 provider：catalog provider 只能是 `google` / `gemini`，`args.provider` 也只能是 `google` / `gemini`。
- `load_tts_api_key()` 只接受空 provider / `google` / `gemini` 的环境密钥，并复用 `03_01_TTSBuilderG` 的 Google key loader。
- `call_gemini_tts()` 写死 Gemini API、payload、retry prompt 和响应解析。
- `record_tts_audit()` 写死 `provider="google"`，request 里也写死 `provider=google`。
- 默认值和 fallback 多处偏向 Google/Gemini：`DEFAULT_TTS_MODEL`、CLI `--provider google`、候选行 fallback provider、错误码/错误文案、payload 字段 `gemini_meta`。

更重要的是，catalog 本身有硬契约：

- `voice_catalog_index.json.provider` 必须被 `load_catalog()` 接受。
- `voice_catalog_index.json.model` 必须和 `args.model` 精确匹配，否则触发 `voice_catalog_model_mismatch`。
- `sample_text_id` 必须等于 `CATALOG_SAMPLE_TEXT_ID`。
- `voices` 数量必须不小于 `final_count`。
- 每个 voice 必须能解析出 voice id。
- 每个 voice 必须有已提交的本地音频样本路径，且文件必须存在；当前错误文案要求部署前生成并提交 catalog wav assets。

要让 ByteDance TTS 进入主流程，需要：

1. 生成 ByteDance voice catalog。
   - 新增脚本或扩展现有 voice catalog 生成逻辑。
   - 目录示例：`ToolLibrary/Analysis_V1/VoiceCatalog/seed-tts-2.0-standard`
   - `voice_catalog_index.json` 中 `provider=bytedance`。
   - catalog 必须使用同一个固定 `CATALOG_SAMPLE_TEXT_ID`。
   - catalog model 必须与 CLI/model config 传入值精确一致。
   - 每个 voice 的金样本 wav 必须随 catalog 一起生成并提交。
2. 放开 `load_catalog()` 的 catalog provider 和 `args.provider` 限制。
3. 新增 `load_bytedance_tts_secret_and_config()`，同时解析 encrypted secret 和 `extra_json` 附属参数。
4. 新增 `call_bytedance_tts()`。
5. 抽象 `record_tts_audit()` 的 provider/model/request，不再写死 `google`。
6. 抽象 `generate_model_candidate()` 的 TTS dispatch，不能固定调用 `call_gemini_tts()`。
7. CLI 参数 `--provider bytedance --model seed-tts-2.0-standard` 能跑通。

建议先不要同时支持所有音色。先选 6 到 10 个中文商业短视频常用音色，生成 catalog 和金样本。

## 6. 安全要求

### 6.1 Phase 0 阻塞项：安全下载器

安全下载器不是可选复用项，而是 Phase 1/2 的阻塞前置。当前代码没有可直接复用的 provider artifact 安全下载能力：

- `backend/opcrew_backend/services/provider_resolver.py` 的 `urlopen()` 只负责代理选择；`proxy_policy != "mihomo"` 时直接调用 `urllib.request.urlopen()`，没有 SSRF 防护、重定向校验、大小限制或 Content-Type 校验。
- `ModelConfig/backend/opcrew_model_config/media_model_config.py` 的 `audio_url_bytes()` 对 provider 响应里的任意 URL 使用裸 `urllib.request.urlopen()`，这是现有 M5 SSRF 风险点。
- `ToolLibrary/Analysis_V1/video_plan_executor_modules/video_wan.py` 的 `download_binary()` 也直接下载 provider 返回 URL，后续 Seedance 不能复制这个模式。

Phase 0 必须新增统一下载能力，并把现有 `audio_url_bytes()` 一起迁移，顺手关闭 M5。

建议新增：

- `backend/opcrew_backend/services/safe_download.py`
- `safe_download_bytes(url, allowed_content_types, max_bytes, timeout, proxy_policy="direct")`
- `safe_download_to_path(url, output_path, allowed_content_types, max_bytes, timeout, proxy_policy="direct")`

最低要求：

1. Provider URL 下载安全
   - 只允许 `https`
   - 禁止 localhost、private IP、link-local、metadata IP
   - DNS 解析后校验 IP，重定向后对新 host 重新解析和校验
   - 限制 Content-Type
   - 限制最大下载大小
   - 限制重定向次数，并对重定向目标重复校验
   - 下载时按 chunk 读取，超过 `max_bytes` 立即中止
   - 错误和日志里只记录 URL scheme/host/path 摘要，不记录签名 query

2. Secret redaction
   - 不记录 `Authorization`
   - 不记录 `X-Api-Access-Key`
   - 不记录完整签名 URL query
   - error detail 进入日志前必须 redact

3. 肖像/真人素材合规
   - Seedance 2.0 真人参考素材不能直接透传。
   - UI 或执行器需要在 host/person reference 场景给出明确失败原因。
   - 后续若支持授权资产，必须记录 asset source 和 authorization id。

4. 幂等与重试
   - Provider timeout 不应盲目重复提交昂贵任务。
   - 当前 Wan 执行器把 provider task id 保存在函数局部变量里，只有成功返回时才进入结果；如果整步超时或进程中断，重跑会重新提交任务。
   - Phase 1 如果要宣称支持幂等，必须把 provider task id 持久化到 workspace 或 job state，retry 先 query 已创建 task。
   - 如果 Phase 1 不实现持久化续查，文档和 UI 必须明确标注 retry 有重复扣费风险。

5. 计费保护
   - MVP 默认 `duration=5`, `resolution=720p`, `generate_audio=false`。
   - 需要按用户/任务加单次最大时长和并发限制。

## 7. 分期计划

### Phase 0: 安全下载器与 M5 修复

目标：先提供统一 provider artifact 安全下载能力，阻断现有 TTS preview/match 的 SSRF 风险，并为 Seedance 视频/TTS 下载结果铺底。

改动：

- 新增 `safe_download` service，支持 bytes 和 file 两种调用形态。
- `media_model_config.py:audio_url_bytes()` 改用安全下载器。
- `video_wan.py:download_binary()` 后续迁移到安全下载器，至少不要让 Seedance 复制裸下载逻辑。
- contract tests 覆盖私网 IP、localhost、metadata IP、http scheme、重定向到私网、Content-Type 不匹配、超大小下载。

验收：

- `audio_url_bytes("http://127.0.0.1/...")` 被拒绝。
- `audio_url_bytes("https://example.invalid/private-redirect")` 模拟重定向到私网时被拒绝。
- provider 返回 `audio/mpeg` / `audio/wav` 在大小限制内可下载。
- 错误信息不泄漏签名 URL query。

### Phase 1: Seedance 视频 MVP

目标：在 Analysis_V1 `05_02_VideoPlanExecutor` 中可选择 ByteDance 视频 provider，生成无声短视频。

前置：Phase 0 必须完成。Seedance 的结果 URL 下载不得使用裸 `urllib.request.urlopen()`。

当前实现状态：后端 mock MVP 已完成，包含 provider 选项、`video_seedance.py` 执行器、`Video_Seedance.md` 模板、合同测试、`extra_json` 默认参数读取和 provider task id 持久化续查；尚未用真实火山 API Key 做付费 smoke。

改动：

- 配置页新增 video provider。
- 新增 `video_seedance.py`。
- 新增 Seedance prompt template。
- dispatch、审计、测试补齐。
- 如果支持 retry/timeout 后续查，新增 provider task id 持久化；否则明确标注本阶段重试可能重复扣费。

验收：

- 保存 `kind=video/provider=bytedance` 配置。
- 连接测试可验证 key 或保存状态。
- 使用 text-only prompt 生成 mp4。
- 使用 first-frame image 生成 mp4。
- result JSON 包含 provider task id。
- provider 返回失败时能展示可读错误，不泄漏 key。

### Phase 2: OpenRouter Seedance 视频路径

目标：在没有火山 key 时，可以用 OpenRouter key 跑 `bytedance/seedance-*` 的 text-to-video smoke。

当前实现状态：后端 mock MVP 已完成，包含 `provider=openrouter` 选项、`video_openrouter.py` 执行器、`Video_OpenRouter.md` 模板、合同测试、安全下载、provider task id 持久化续查。真实 OpenRouter smoke 尚未执行。

改动：

- 配置页新增 `provider=openrouter`。
- 新增 `video_openrouter.py`。
- 新增 OpenRouter prompt template。
- dispatch、审计、测试补齐。
- 默认 `send_frame_images=false`，避免本地图片无法被 OpenRouter provider 拉取导致付费失败。

验收：

- 保存 `kind=video/provider=openrouter` 配置。
- 连接测试只验证 key 已保存，不提交付费任务。
- 使用 text-only prompt 可生成 mp4。
- result JSON 包含 OpenRouter task id / polling URL 摘要。
- provider 返回失败时能展示可读错误，不泄漏 key。

### Phase 3: TTS 配置页 MVP

目标：配置页可用 ByteDance TTS 试听，voice match 可以生成候选音频。

前置：Phase 0 必须完成。ByteDance `audio_url` 下载必须走安全下载器。

当前实现状态：后端与页面 MVP 已完成，包含 `provider=bytedance` TTS 选项、`seed-tts-1.1` 模型、固定/自定义 `voice_type`、非付费连接测试、preview/match adapter、Usage 记录和 `05_02_VideoPlanExecutor.py` 的 ByteDance TTS 生成路径。真实火山 TTS smoke 待 appid/access_token。

改动：

- 配置页新增 tts provider。
- `extra_json` 支持 `endpoint`, `cluster`, `encoding`, `sample_rate`, `speed_ratio`, `uid`；`app_id` 可在 `extra_json` 中提供，也可随 secret 用 `appid:access_token` / JSON 形式保存。
- secret 通过 `api_key_ref` 对应 secret store 读取；legacy 才兼容 `api_key_ciphertext`。
- 新增 ByteDance TTS `/api/v1/tts` `operation=query` adapter。
- preview cache 安全下载。
- Analysis_V1 `05_02` 音频生成可调用 ByteDance TTS；完整 `03_02_TTSBuilderQuick` catalog 流程仍在 Phase 4。

验收：

- 输入短中文文本可生成 wav preview。
- match 流程能缓存候选音频。
- 本地 usage 记录 character 数、provider/model/status。
- query 超时或失败有明确错误。

### Phase 4: Analysis_V1 TTS Builder

目标：`03_02_TTSBuilderQuick` 可使用 ByteDance voice catalog 生成最终候选旁白。

改动：

- ByteDance voice catalog 生成脚本。
- 放开 provider 限制和 Google/Gemini 默认/错误文案。
- 新增 `call_bytedance_tts()`。
- 抽象 TTS audit provider。
- 抽象 TTS dispatch，`generate_model_candidate()` 根据 provider 调用对应 adapter。
- catalog 生成必须提交固定 sample text 的 wav 金样本，并保证 model 精确匹配。

验收：

- `--provider bytedance` 跑通 quick builder。
- 输出 `SessionOutput/tts/tts_builder_candidates.json`。
- 候选音频时长测量、tempo fit、voice match 均可用。
- 失败时恢复旧 TTS 输出。

### Phase 5: 高级视频能力

目标：支持 Seedance 2.0 多模态 reference 和有声视频。

改动：

- 支持 multi-reference image/video/audio。
- 支持 `generate_audio=true`。
- 支持 strict first/last frame。
- 支持 callback 或 task resume。
- 支持 asset API 或对象存储上传。

验收：

- 首尾帧生成可用。
- reference image 模式可用。
- provider audio 生成可用，并能与后续 lipsync/TTS 策略互斥。

## 8. 测试计划

Contract tests：

- `safe_download_bytes()` 拒绝 `http`、localhost、private IP、link-local、metadata IP。
- `safe_download_bytes()` 拒绝重定向到 unsafe host/IP。
- `safe_download_bytes()` 拒绝 Content-Type 不匹配和超过 `max_bytes` 的响应。
- `audio_url_bytes()` 改用安全下载器，保留 data URL 支持。
- `media_options("video")` 包含 ByteDance/Seedance provider 和模型。
- `media_options("video")` 包含 OpenRouter/Seedance provider 和模型。
- `media_options("tts")` 包含 ByteDance/Doubao TTS provider 和音色。
- `video_module_for("bytedance", "doubao-seedance...")` 返回 `video_seedance`。
- `video_module_for("openrouter", "bytedance/seedance...")` 返回 `video_openrouter`。
- `video_seedance.generate()` 使用 mocked POST/GET 完成轮询并下载结果。
- `video_openrouter.generate()` 使用 mocked POST/GET 完成轮询并下载结果。
- `video_seedance.generate()` 对 `failed`/`expired` 返回 ToolError。
- `video_openrouter.generate()` 拒绝跨 host polling URL。
- TTS submit/query mocked 成功时返回 audio URL 并写 cache。
- TTS submit/query mocked 失败时不泄漏 key。
- unsafe URL 下载被拒绝。

Manual smoke：

- 配置保存和读取。
- 短文生视频 5 秒。
- 首帧图生视频 5 秒。
- TTS 短文本试听。
- 本地 usage/audit 查看。

## 9. 主要风险

| 风险 | 影响 | 缓解 |
| --- | --- | --- |
| 国内火山和国际 BytePlus base URL/model id 不同 | 配置混乱、请求失败 | `extra_json.base_url` 显式配置，模型列表按区域标注 |
| TTS 鉴权不是单 key | 配置页需要额外字段 | 使用 `extra_json` 保存 app_id/resource_id/auth_mode |
| Seedance 真人脸素材限制 | Host/person reference 可能失败 | MVP 禁用真人 reference，后续接授权资产 |
| 结果 URL 有时效 | 后续无法下载 | 成功后立即安全下载到 workspace |
| 有声视频和现有 TTS/lipsync 冲突 | 成片链路重复配音 | MVP `generate_audio=false`，后续建立互斥策略 |
| OpenRouter first-frame 需要公开 HTTPS 图片 URL | 本地 workspace 图片不能可靠作为 `frame_images` | OpenRouter MVP 默认 text-to-video，后续接对象存储/signed URL |
| Provider URL 下载 SSRF | Phase 1/2 阻塞风险，也覆盖现有 M5 | Phase 0 新增安全下载器，并先迁移 `audio_url_bytes()` |
| 任务重试重复扣费 | 成本风险；当前 Wan 模式不能断点续查 | Phase 1 持久化 provider task id；否则明确标注 retry 可能重复扣费 |

## 10. 参考资料

- 火山方舟视频生成 API: https://www.volcengine.com/docs/82379/1520757
- 火山方舟查询视频生成任务 API: https://www.volcengine.com/docs/82379/1521309
- BytePlus ModelArk Seedance 2.0 API: https://docs.byteplus.com/en/docs/modelark/1520757
- BytePlus ModelArk 查询视频任务 API: https://docs.byteplus.com/en/docs/ModelArk/1521309
- 豆包语音产品简介: https://www.volcengine.com/docs/6561/1257543
- 豆包语音 API 列表: https://www.volcengine.com/docs/6561/2228192
- 豆包语音异步长文本接口: https://www.volcengine.com/docs/6561/1829010
- OpenRouter Seedance 2.0: https://openrouter.ai/bytedance/seedance-2.0
- OpenRouter 视频模型选择: https://openrouter.ai/docs/cookbook/video-generation/choose-video-model
- OpenRouter image-to-video: https://openrouter.ai/docs/cookbook/video-generation/image-to-video
- OpenRouter TTS: https://openrouter.ai/docs/guides/overview/multimodal/tts
