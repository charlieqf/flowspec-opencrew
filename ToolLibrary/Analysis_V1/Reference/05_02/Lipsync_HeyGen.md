# 05_02 Lipsync HeyGen Request Template / HeyGen 口型同步请求模板

Scope: HeyGen lipsync module.

中文用于维护；英文 `OPENCREW:*` 块由 `lipsync_heygen.py` 读取。HeyGen 当前使用视频和音频 asset 创建 lipsync job，不发送自然语言生成提示词，但 dry-run 仍必须由模板生成，便于独立测试。

## 中文维护版

HeyGen 口型同步调用使用 `POST /v3/assets` 上传视频和音频，再用 `POST /v3/lipsyncs` 创建任务。`model` 映射到 HeyGen `mode`，允许 `speed` 和 `precision`。请求审计可以包含 provider、model/mode、输入视频路径、输入音频路径、输出路径和非敏感参数。禁止记录 API key、Authorization header、cookie 或 signed-token secret。

### 中文踩坑记录（只追加）

- 2026-06-18 baseline：不要把真实 API key、Authorization header、cookie 或 signed-token secret 写入请求审计或结果文件。
- 2026-06-18 baseline：HeyGen lipsync 的 `speed` / `precision` 是 `mode` 参数，不是单独 endpoint。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `LIPSYNC_HEYGEN_PITFALLS_APPEND_ONLY` 中追加对应英文审计禁令。

## English Model-Call Version

<!-- OPENCREW:LIPSYNC_HEYGEN_PROMPT_START -->
HeyGen lipsync call uses video and audio assets only. No natural-language generation prompt is sent in the current 05_02 contract. The selected model is sent as HeyGen lipsync mode.
<!-- OPENCREW:LIPSYNC_HEYGEN_PROMPT_END -->

<!-- OPENCREW:LIPSYNC_HEYGEN_PITFALLS_APPEND_ONLY_START -->
- 2026-06-18 baseline: Do not write real API keys, Authorization headers, cookies, signed-token secrets, or signed URL query tokens into request audits or result files.
- 2026-06-18 baseline: HeyGen speed and precision are lipsync mode values, not separate endpoints.
<!-- OPENCREW:LIPSYNC_HEYGEN_PITFALLS_APPEND_ONLY_END -->
