# 05_02 Video OpenRouter Prompt Template / 视频 OpenRouter 提示词模板

Scope: OpenRouter normalized video generation modules, including ByteDance Seedance models.

中文用于维护；英文 `OPENCREW:*` 块由 `video_openrouter.py` 读取并进入模型调用。

## 中文维护版

OpenRouter 视频任务：通过 OpenRouter normalized video API 调用可用的视频模型。当前优先支持 `bytedance/seedance-2.0-fast`。OpenCrew 与 xAI/Grok 视频链路对齐：有本地 workspace 参考图时只发送第一张作为 `first_frame`，不发送尾帧。配置 R2 public asset publisher 后，首帧先上传到 R2，再以短时效 presigned HTTPS URL 传给 OpenRouter。

### 中文踩坑记录（只追加）

- 2026-06-08 baseline：不要字幕、水印、品牌文字漂移、产品包装变形、跳切、场景重置或低质量。
- 2026-06-21 sound default：默认允许模型生成有声视频；旁白和口型如需替换，仍可由后续 TTS/lipsync 链路覆盖。
- 2026-06-15 update：与 Grok 对齐，只传首帧；不要默认启用首尾帧约束。
- 2026-06-15 update：生产路径优先使用 R2 presigned HTTPS URL 发布首帧，避免 provider 无法拉取本地文件。

## English Model-Call Version

<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_START -->
OpenRouter video generation task: create a realistic vertical 9:16 commercial short-video shot.

Preserve product identity, product text/package structure, visual continuity, and camera continuity.

Target duration: about {{duration_seconds}} seconds.
<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_START -->
Dialogue meaning for the visual action: {{dialogue_text}}
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_START -->
Product-only cutaway: no human subject, no face, and no mouth movement.
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_START -->
subtitles, watermark, brand text drift, product package mutation, identity drift, jump cut, scene reset, low quality, background music, sound effects
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_START -->
human face, presenter, talking head, mouth movement
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_START -->
- 2026-06-08 baseline: Do not add subtitles, watermark, brand text drift, product package mutation, jump cuts, scene reset, or low quality.
- 2026-06-08 baseline: For product-only cutaways, do not create a presenter, human face, talking head, or mouth movement.
- 2026-06-21 sound default: Allow provider audio by default; downstream TTS/lipsync can still replace it when needed.
- 2026-06-15 update: Use only the first reference image as the first frame; do not request last-frame constraints by default.
- 2026-06-15 update: Prefer an R2 presigned HTTPS URL for first-frame images when the public asset publisher is configured.
<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_END -->
