# 05_02 Lipsync SyncSo Request Template / Sync.so 口型同步请求模板

Scope: Sync.so lip-sync module.

中文用于维护；英文 `OPENCREW:*` 块由 `lipsync_syncso.py` 读取。Sync.so 当前通常只发送视频和音频文件，不发送自然语言生成提示词，但 dry-run 仍必须由模板生成，便于独立测试。

## 中文维护版

Sync.so 口型同步调用只使用视频和音频文件。请求审计可以包含 provider、model、输入视频路径、输入音频路径、输出路径和非敏感参数。禁止记录 API key、Authorization header、cookie 或 signed-token secret。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要把真实 API key、Authorization header、cookie 或 signed-token secret 写入请求审计或结果文件。
- 2026-06-02 baseline：Sync.so 当前不发送自然语言生成提示词；dry-run prompt 只用于说明请求合同。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `LIPSYNC_SYNCSO_PITFALLS_APPEND_ONLY` 中追加对应英文审计禁令。

## English Model-Call Version

<!-- OPENCREW:LIPSYNC_SYNCSO_PROMPT_START -->
Sync.so lipsync call uses video and audio files only. No natural-language generation prompt is sent in the current 05_02 contract.
<!-- OPENCREW:LIPSYNC_SYNCSO_PROMPT_END -->

<!-- OPENCREW:LIPSYNC_SYNCSO_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not write real API keys, Authorization headers, cookies, signed-token secrets, or signed URL query tokens into request audits or result files.
- 2026-06-02 baseline: The dry-run prompt is an audit contract only; Sync.so receives video and audio files, not a natural-language generation prompt.
<!-- OPENCREW:LIPSYNC_SYNCSO_PITFALLS_APPEND_ONLY_END -->
