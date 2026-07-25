# 05_02 Lipsync Chanjing Request Template / 蝉镜口型同步请求模板

Scope: Chanjing lipsync module.

中文用于维护；英文 `OPENCREW:*` 块由 `lipsync_chanjing.py` 读取。蝉镜当前使用文件管理上传视频和音频，再创建对口型任务，不发送自然语言生成提示词，但 dry-run 仍必须由模板生成，便于独立测试。

## 中文维护版

蝉镜口型同步调用使用 `POST /open/v1/access_token` 获取 access_token，通过 `GET /open/v1/common/create_upload_url` 获取视频和音频上传地址，使用 `PUT sign_url` 上传文件，然后用 `POST /open/v1/video_lip_sync/create` 创建任务，最后轮询 `GET /open/v1/video_lip_sync/detail`。`model` 映射到蝉镜 `model` 参数，允许 `basic/model=0` 和 `quality/model=1`。请求审计可以包含 provider、model、输入视频路径、输入音频路径、输出路径和非敏感参数。禁止记录 APP Key、API Key、Secret Key、access_token、Authorization header、cookie 或 signed upload URL query token。

### 中文踩坑记录（只追加）

- 2026-06-20 baseline：不要把真实 APP Key、API Key、Secret Key、access_token、Authorization header、cookie 或 signed upload URL query token 写入请求审计或结果文件。
- 2026-06-20 baseline：蝉镜 lipsync 的基础版/高质量版是 create payload 里的 `model=0/1`，不是单独 endpoint。
- 2026-06-20 baseline：上传文件后存在短暂同步延迟，创建对口型任务前需要轮询 `file_detail` 直到 status=1。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `LIPSYNC_CHANJING_PITFALLS_APPEND_ONLY` 中追加对应英文审计禁令。

## English Model-Call Version

<!-- OPENCREW:LIPSYNC_CHANJING_PROMPT_START -->
Chanjing lipsync call uses uploaded video and audio file IDs only. No natural-language generation prompt is sent in the current 05_02 contract. The selected model is sent as Chanjing video_lip_sync model value: 0 for basic, 1 for quality.
<!-- OPENCREW:LIPSYNC_CHANJING_PROMPT_END -->

<!-- OPENCREW:LIPSYNC_CHANJING_PITFALLS_APPEND_ONLY_START -->
- 2026-06-20 baseline: Do not write real APP keys, API keys, Secret keys, access tokens, Authorization headers, cookies, signed upload URLs, or signed URL query tokens into request audits or result files.
- 2026-06-20 baseline: Chanjing basic and quality are create-payload model values 0 and 1, not separate endpoints.
- 2026-06-20 baseline: Uploaded files can have a short sync delay, so poll file_detail until status=1 before creating the lipsync task.
<!-- OPENCREW:LIPSYNC_CHANJING_PITFALLS_APPEND_ONLY_END -->
