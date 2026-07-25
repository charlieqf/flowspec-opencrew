# 05_02 Video GPT Prompt Template / 视频 GPT 提示词模板

Scope: OpenAI / GPT video modules.

中文用于维护；英文 `OPENCREW:*` 块由 `video_gpt.py` 读取并进入模型调用。

## 中文维护版

GPT 视频任务：从首帧生成真实的竖屏 9:16 延续视频。保留首帧人物身份、产品、背景、相机位置、光线、画面几何关系和手机视频质感。

运动要冷静、稳定、连续，不要切镜、激烈变焦、重新构图或引入新物体。产品特写 / cutaway 禁止人物、脸、嘴部动作、lip-sync 和会说话的包装肖像。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要身份漂移、产品漂移、场景重置、镜头跳变、字幕、水印或额外文字。
- 2026-06-02 baseline：产品特写 / cutaway 不要出现人脸、主持人、talking head、嘴部运动、speaking lips 或 blinking package portrait。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `VIDEO_GPT_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_GPT_POSITIVE_BASE_START -->
OpenAI video task: generate a realistic vertical 9:16 continuation from the provided first frame.

Preserve first-frame identity, product, background, camera position, lighting, frame geometry, and phone-video texture.

Target duration: about {{duration_seconds}} seconds.

Use calm, stable motion; do not cut, zoom aggressively, reframe, or introduce new objects.

Scene context: {{scene_summary}}
<!-- OPENCREW:VIDEO_GPT_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GPT_DIALOGUE_STANDARD_START -->
Dialogue meaning: {{dialogue_text}}
<!-- OPENCREW:VIDEO_GPT_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_GPT_DIALOGUE_CUTAWAY_START -->
Product-only cutaway: no person, no face, no mouth movement, no lip-sync, only subtle product-safe movement.
<!-- OPENCREW:VIDEO_GPT_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GPT_NEGATIVE_BASE_START -->
identity drift, product drift, scene reset, camera jump, subtitles, captions, watermark, extra text, low quality
<!-- OPENCREW:VIDEO_GPT_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GPT_NEGATIVE_CUTAWAY_START -->
human face, presenter, talking head, mouth movement, speaking lips, blinking package portrait
<!-- OPENCREW:VIDEO_GPT_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GPT_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not cause identity drift, product drift, scene reset, camera jump, subtitles, captions, watermark, extra text, or low quality.
- 2026-06-02 baseline: For product-only cutaways, do not create a human face, presenter, talking head, mouth movement, speaking lips, or blinking package portrait.
<!-- OPENCREW:VIDEO_GPT_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_GPT_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_GPT_PROMPT_END -->
