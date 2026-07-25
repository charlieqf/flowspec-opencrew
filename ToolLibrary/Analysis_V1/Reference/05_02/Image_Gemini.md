# 05_02 Image Gemini Prompt Template / 图像 Gemini 提示词模板

Scope: Gemini / Google image modules, including Nano Banana aliases.

中文用于维护；英文 `OPENCREW:*` 块由 `image_gemini.py` 读取。Python 不保存 Gemini
提示词正文，只保存读取和拼接逻辑。

## 中文维护版

Gemini 图像任务：输出一张真实的竖屏 9:16 视频首帧。第一张 inline 图 TARGET_FRAME 是构图锁定，不要重设计房间、镜头、姿势、手部接触、光线、产品位置或手机视频质感。后续 inline 图只作为身份参考，不作为布局参考。

有 HOST_REFERENCE 时只迁移人物身份和风格；没有时保持 TARGET_FRAME 人物，不发明新脸。有 PRODUCT_REFERENCE 时完整替换可见产品包装，包括侧面、封口、瓶盖、颜色、logo、文字块和图形。

产品特写 / cutaway 时，不使用 HOST_REFERENCE，不生成任何人、脸、手、眼睛、嘴、嘴唇、牙齿或说话包装角色。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要把帧变成产品渲染、商业海报、肖像照、参考图表或干净棚拍。
- 2026-06-02 baseline：不要做局部包装替换、只换 logo、贴纸覆盖或新旧包装混合。
- 2026-06-02 baseline：产品特写 / cutaway 不要生成任何人、脸、手、眼睛、嘴、嘴唇、牙齿或说话包装角色。
- 新增踩坑时只追加，不要改写历史记录；同时在英文 `IMAGE_GEMINI_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:IMAGE_GEMINI_POSITIVE_BASE_START -->
Gemini image generation/editing task. Produce one realistic vertical 9:16 first frame for image-to-video.

Use the first inline image TARGET_FRAME as the composition lock. Do not redesign the room, camera, pose, hand contact, lighting, product position, or mobile-phone texture.

Treat later inline images only as identity references, not layout references.

Gemini must avoid turning the frame into a product render, commercial poster, portrait photo, reference sheet, or clean studio still.

Remove subtitles, caption overlays, watermarks, interface elements, and old-product text remnants.
<!-- OPENCREW:IMAGE_GEMINI_POSITIVE_BASE_END -->

<!-- OPENCREW:IMAGE_GEMINI_HOST_STANDARD_START -->
If HOST_REFERENCE exists, transfer host identity and styling only. If it is missing, preserve the target-frame host and avoid inventing a new face.
<!-- OPENCREW:IMAGE_GEMINI_HOST_STANDARD_END -->

<!-- OPENCREW:IMAGE_GEMINI_HOST_CUTAWAY_START -->
Product-only cutaway mode: do not use HOST_REFERENCE; do not create humans, faces, hands, eyes, mouth, lips, teeth, or a speaking package character. PRODUCT_REFERENCE physical geometry outranks TARGET_FRAME composition: preserve the real package aspect ratio and the relative width/height of every visible face. Fit the vertical frame with neutral tabletop/background space, depth, shadow, or crop breathing room; never stretch, narrow, elongate, squeeze, bend, or perspective-warp the product package, box, sachet, bottle, pouch, label panel, cap, or seal.
<!-- OPENCREW:IMAGE_GEMINI_HOST_CUTAWAY_END -->

<!-- OPENCREW:IMAGE_GEMINI_PRODUCT_START -->
If PRODUCT_REFERENCE exists, fully replace the entire visible product package identity, including side panels, seals, caps, colors, logo, text blocks, graphics, and real physical proportions.
<!-- OPENCREW:IMAGE_GEMINI_PRODUCT_END -->

<!-- OPENCREW:IMAGE_GEMINI_CONTEXT_START -->
Shot context: {{shot_summary}}
Scene context: {{scene_summary}}
Spoken-dialogue context: semantic guidance only. Do not render dialogue words, subtitles, speech captions, title cards, labels, UI text, or overlay text from the dialogue in the image.
Cutaway mode: {{cutaway_mode}}
Reference availability: {{reference_summary}}
<!-- OPENCREW:IMAGE_GEMINI_CONTEXT_END -->

<!-- OPENCREW:IMAGE_GEMINI_NEGATIVE_BASE_START -->
poster, product board, reference sheet, studio render, wrong identity, wrong product, partial package swap, logo-only replacement, sticker overlay, mixed old/new package, subtitle remnants, generated text, watermark, extra UI, deformed hands, blurry, 2:3 portrait, square, horizontal, padded canvas, reframed scene, vertically stretched package, narrow tall box, elongated sachet, squeezed product, warped product perspective, distorted package aspect ratio
<!-- OPENCREW:IMAGE_GEMINI_NEGATIVE_BASE_END -->

<!-- OPENCREW:IMAGE_GEMINI_NEGATIVE_CUTAWAY_START -->
person, human face, presenter, host, hand, body, eyes, mouth, lips, teeth, speaking package, animated portrait, stretched label panel, stretched bottle, stretched pouch
<!-- OPENCREW:IMAGE_GEMINI_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:IMAGE_GEMINI_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not turn the frame into a product render, commercial poster, portrait photo, reference sheet, or clean studio still.
- 2026-06-02 baseline: Do not perform partial package swaps, logo-only replacement, sticker overlays, or mixed old/new packaging.
- 2026-06-02 baseline: For product-only cutaways, do not create humans, faces, hands, eyes, mouth, lips, teeth, speaking packages, or animated portraits.
- 2026-06-12 product geometry: For product-only cutaways, do not stretch the PRODUCT_REFERENCE package into a narrow/tall form. Preserve real package aspect ratio and visible-face proportions; use neutral background/tabletop space to fit the vertical frame.
<!-- OPENCREW:IMAGE_GEMINI_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:IMAGE_GEMINI_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:IMAGE_GEMINI_PROMPT_END -->
