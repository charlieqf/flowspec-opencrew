# 05_02 Lipsync Kling Request Template / 可灵对口型请求模板

Scope: Kling AI advanced lip-sync module.

中文用于维护；英文 `OPENCREW:*` 块由 `lipsync_kling.py` 读取。可灵当前使用 `identify-face` 先从源视频识别人脸，再调用 `advanced-lip-sync` 创建对口型任务，最后轮询任务结果。不发送自然语言生成提示词，但 dry-run 仍必须由模板生成，便于独立测试和审计。

## 中文维护版

可灵对口型调用使用 `POST /v1/videos/identify-face`，源视频必须是公网 `video_url` 或可灵侧已有 `video_id`，二者必须且只能提供一个。识别出 `session_id` 和 `face_id` 后，使用 `POST /v1/videos/advanced-lip-sync` 创建任务，`sound_file` 使用音频 base64，最后轮询 `GET /v1/videos/advanced-lip-sync/{task_id}`。请求审计可以包含 provider、model、输入视频路径、源视频 URL 摘要、输入音频路径、输出路径和非敏感参数。禁止记录 AK/SK、JWT、Authorization header、cookie 或完整敏感签名。

### 中文踩坑记录（只追加）

- 2026-06-21 baseline：可灵 lip-sync 不接受本地视频文件直传；05_02 必须传入公网 `video_url` 或可灵 `video_id`。
- 2026-06-21 baseline：`identify-face` 的 `video_url` 和 `video_id` 互斥，不能同时发送。
- 2026-06-21 baseline：不要把 AK、SK、JWT、Authorization header、cookie 或完整敏感签名写入请求审计或结果文件。
- 2026-06-21 fix：可灵 lip-sync 按 R2V 处理，05_02 先将音频适配到源视频时长，再创建对口型任务。
- 2026-06-21 fix：`advanced-lip-sync` 的 `faceChoose[0].soundEndTime` 不能为空；请求体必须显式发送 `soundStartTime` 和 `soundEndTime`。
- 2026-06-21 correction：可灵 lip-sync 的音频适配只在上游视频模型是 R2V 时启用；非 R2V 空镜链路由视频生成阶段匹配口播音频时长，lip-sync 前不再二次适配音频。
- 2026-06-21 correction：官方 OpenAPI schema 使用 `session_id`、`face_choose`、`face_id`、`sound_file`、`sound_insert_time`、`sound_start_time`、`sound_end_time`，不是 camelCase；`sound_*_time` 单位是 ms。
- 2026-06-21 correction：`sound_file` 本地音频需满足 2-60 秒、最大 5MB，并且插入区间与识别到的人脸可对口型区间至少重合 2 秒。
- 2026-06-21 correction：`sound_end_time` 不能贴着本地 ffprobe 音频总时长或人脸 `end_time` 边界传；需对音频时长和人脸/视频结束时间都留毫秒级安全余量，避免 Kling 解码后判定越界。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `LIPSYNC_KLING_PITFALLS_APPEND_ONLY` 中追加对应英文审计禁令。

## English Model-Call Version

<!-- OPENCREW:LIPSYNC_KLING_PROMPT_START -->
Kling AI advanced lip-sync call uses a public source video URL or an existing Kling video_id plus an audio file. No natural-language generation prompt is sent in the current 05_02 contract. The module identifies a face first, then creates an advanced-lip-sync task for the selected face and polls the task result.
<!-- OPENCREW:LIPSYNC_KLING_PROMPT_END -->

<!-- OPENCREW:LIPSYNC_KLING_PITFALLS_APPEND_ONLY_START -->
- 2026-06-21 baseline: Kling lip-sync does not accept direct local source-video upload in this 05_02 module; pass a public video_url or existing Kling video_id.
- 2026-06-21 baseline: identify-face accepts exactly one source video reference, either video_url or video_id, not both.
- 2026-06-21 baseline: Do not write real AK, SK, JWTs, Authorization headers, cookies, or full sensitive signatures into request audits or result files.
- 2026-06-21 fix: Treat Kling lip-sync as R2V in 05_02; fit the audio to the source video duration before creating the lip-sync task.
- 2026-06-21 fix: advanced-lip-sync requires a non-empty faceChoose[0].soundEndTime; send explicit soundStartTime and soundEndTime in the create payload.
- 2026-06-21 correction: Only fit Kling lip-sync audio to video when the upstream video model is R2V; non-R2V atmospheric-video flows keep the video-generation stage aligned to narration audio and do not refit audio before lip-sync.
- 2026-06-21 correction: The official OpenAPI schema uses session_id, face_choose, face_id, sound_file, sound_insert_time, sound_start_time, and sound_end_time, not camelCase; sound_*_time values are milliseconds.
- 2026-06-21 correction: Local sound_file audio must be 2-60 seconds and 5MB or smaller, and the inserted sound interval must overlap the detected face lip-sync interval by at least 2 seconds.
- 2026-06-21 correction: Do not send sound_end_time exactly at the local ffprobe audio duration or detected face end_time boundary; keep a small millisecond guard for both audio and face/video end limits to avoid provider-side decode-boundary failures.
<!-- OPENCREW:LIPSYNC_KLING_PITFALLS_APPEND_ONLY_END -->
