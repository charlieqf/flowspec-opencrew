# 05_02 Video Gemini Prompt Template / 视频 Gemini 提示词模板

Scope: Gemini / Veo video modules.

中文用于维护；英文 `OPENCREW:*` 块由 `video_gemini.py` 读取并进入模型调用。

## 中文维护版

Gemini / Veo 图生视频任务：根据首帧生成竖屏 9:16 短视频片段。帧连续性比创意新颖更重要；必须保留身份、产品、房间、镜头角度、光线、产品位置和手机视频真实感。

避免硬切、场景重置、发明字幕和产品重设计。产品特写 / cutaway 要保持产品和背景稳定，不要出现人、脸、嘴或 lip-sync。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要 scene reset、identity drift、product drift、字幕、水印、生成文字、jump cut 或 montage。
- 2026-06-02 baseline：产品特写 / cutaway 不要人脸、人物、host、talking head、mouth movement、lip-sync 或 blinking package face。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `VIDEO_GEMINI_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:VIDEO_GEMINI_POSITIVE_BASE_START -->
Gemini/Veo image-to-video task: create a vertical 9:16 short-video segment from the provided first frame.

Frame continuity is more important than creative novelty. Preserve identity, product, room, camera angle, lighting, product position, and phone-video realism.

Target duration: about {{duration_seconds}} seconds.

Avoid hard cuts, scene resets, invented captions, and product redesign.

Scene context: {{scene_summary}}
<!-- OPENCREW:VIDEO_GEMINI_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GEMINI_DIALOGUE_STANDARD_START -->
Dialogue meaning: {{dialogue_text}}
<!-- OPENCREW:VIDEO_GEMINI_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_GEMINI_DIALOGUE_CUTAWAY_START -->
Product-only cutaway: keep product and background stable; no people, faces, mouths, or lip-sync.
<!-- OPENCREW:VIDEO_GEMINI_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GEMINI_NEGATIVE_BASE_START -->
scene reset, identity drift, product drift, subtitles, captions, watermark, generated text, jump cut, montage, low quality
<!-- OPENCREW:VIDEO_GEMINI_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_GEMINI_NEGATIVE_CUTAWAY_START -->
human face, person, host, talking head, mouth movement, lip-sync, blinking package face
<!-- OPENCREW:VIDEO_GEMINI_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_GEMINI_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not cause scene reset, identity drift, product drift, subtitles, captions, watermark, generated text, jump cut, montage, or low quality.
- 2026-06-02 baseline: For product-only cutaways, do not create a human face, person, host, talking head, mouth movement, lip-sync, or blinking package face.
<!-- OPENCREW:VIDEO_GEMINI_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_GEMINI_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:VIDEO_GEMINI_PROMPT_END -->
