# 05_02 Video Wan Prompt Template / 视频 Wan 提示词模板

Scope: Wan / DashScope video modules.

中文用于维护；英文 `OPENCREW:*` 块由 `video_wan.py` 读取并进入模型调用。

## 中文维护版

Wan 图生视频任务：保持首帧稳定，生成真实竖屏 9:16 短视频延续。优先保护产品文字 / 包装稳定、人脸稳定、镜头连续和低运动。产品特写 / cutaway 禁止人类主体和嘴部运动。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要 identity drift、product text drift、package mutation、字幕、水印、jump cut、scene reset 或低质量。
- 2026-06-02 baseline：产品特写 / cutaway 不要人脸、主持人、talking head 或 mouth movement。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `VIDEO_WAN_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_WAN_POSITIVE_BASE_START -->
Wan image-to-video task: keep the first frame stable and generate a realistic vertical 9:16 short-video continuation.

Prioritize product text/package stability, face stability, camera continuity, and low motion.

Target duration: about {{duration_seconds}} seconds.
<!-- OPENCREW:VIDEO_WAN_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_WAN_DIALOGUE_STANDARD_START -->
Dialogue meaning: {{dialogue_text}}
<!-- OPENCREW:VIDEO_WAN_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_WAN_DIALOGUE_CUTAWAY_START -->
Product-only cutaway: no human subject or mouth movement.
<!-- OPENCREW:VIDEO_WAN_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_WAN_NEGATIVE_BASE_START -->
identity drift, product text drift, package mutation, subtitles, watermark, jump cut, scene reset, low quality
<!-- OPENCREW:VIDEO_WAN_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_WAN_NEGATIVE_CUTAWAY_START -->
human face, presenter, talking head, mouth movement
<!-- OPENCREW:VIDEO_WAN_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_WAN_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not cause identity drift, product text drift, package mutation, subtitles, watermark, jump cut, scene reset, or low quality.
- 2026-06-02 baseline: For product-only cutaways, do not create a human face, presenter, talking head, or mouth movement.
<!-- OPENCREW:VIDEO_WAN_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_WAN_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_WAN_PROMPT_END -->
