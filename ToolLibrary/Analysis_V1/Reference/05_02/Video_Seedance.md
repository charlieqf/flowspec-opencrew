# 05_02 Video Seedance Prompt Template / 视频 Seedance 提示词模板

Scope: ByteDance Volcano Ark Seedance video modules.

中文用于维护；英文 `OPENCREW:*` 块由 `video_seedance.py` 读取并进入模型调用。

## 中文维护版

Seedance 视频任务：生成真实竖屏 9:16 商业短视频镜头。优先保持首帧主体、产品文字、包装结构和镜头连续性。默认请求模型生成有声视频；如后续 TTS/lipsync 需要替换声音，再由下游流程覆盖。

### 中文踩坑记录（只追加）

- 2026-06-08 baseline：不要字幕、水印、品牌文字漂移、产品包装变形、跳切、场景重置或低质量。
- 2026-06-08 baseline：产品特写 / cutaway 不要凭空生成主持人、人脸或口型运动。
- 2026-06-21 sound default：默认请求 provider 生成音频，`generate_audio=true`；只有任务明确要求静音时才覆盖为 false。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `VIDEO_SEEDANCE_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_SEEDANCE_POSITIVE_BASE_START -->
Seedance video task: create a realistic vertical 9:16 commercial short-video shot.

Preserve subject identity, product text/package structure, first-frame continuity, and camera continuity.

Target duration: about {{duration_seconds}} seconds.
<!-- OPENCREW:VIDEO_SEEDANCE_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_DIALOGUE_STANDARD_START -->
Dialogue meaning for the visual action: {{dialogue_text}}
<!-- OPENCREW:VIDEO_SEEDANCE_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_DIALOGUE_CUTAWAY_START -->
Product-only cutaway: no human subject, no face, and no mouth movement.
<!-- OPENCREW:VIDEO_SEEDANCE_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_NEGATIVE_BASE_START -->
subtitles, watermark, brand text drift, product package mutation, identity drift, jump cut, scene reset, low quality, background music, sound effects
<!-- OPENCREW:VIDEO_SEEDANCE_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_NEGATIVE_CUTAWAY_START -->
human face, presenter, talking head, mouth movement
<!-- OPENCREW:VIDEO_SEEDANCE_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_PITFALLS_APPEND_ONLY_START -->
- 2026-06-08 baseline: Do not add subtitles, watermark, brand text drift, product package mutation, jump cuts, scene reset, or low quality.
- 2026-06-08 baseline: For product-only cutaways, do not create a presenter, human face, talking head, or mouth movement.
- 2026-06-21 sound default: Request provider audio by default with generate_audio=true; only override it to false when a task explicitly needs silent output.
<!-- OPENCREW:VIDEO_SEEDANCE_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_SEEDANCE_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_SEEDANCE_PROMPT_END -->
