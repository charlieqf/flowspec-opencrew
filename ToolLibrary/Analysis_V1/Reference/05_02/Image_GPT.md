# 05_02 Image GPT Prompt Template / 图像 GPT 提示词模板

Scope: OpenAI / GPT image generation and image editing.

本文件是 GPT 图像模块的提示词来源。中文用于维护和记录优化意图；英文 `OPENCREW:*`
块由 `image_gpt.py` 读取并进入真实模型调用。Python 只负责抽取变量、选择条件块和拼接。

## 中文维护版

### 正向目标

基于 TARGET_FRAME 生成一张干净真实的竖屏 9:16 视频首帧。TARGET_FRAME 是唯一构图锚点：保留镜头角度、姿势、手与产品接触关系、产品位置、背景、光线、曝光、比例、透视和手机视频质感。

输入图像顺序为 TARGET_FRAME，随后是可选 HOST_REFERENCE，再随后是可选 PRODUCT_REFERENCE。HOST_REFERENCE 只用于人物身份，不复制其背景或姿势。PRODUCT_REFERENCE 用于完整产品包装替换，不能只换 logo 或颜色。

产品特写 / cutaway 时，不使用人物参考，不生成人、脸、嘴、手、身体、主持人或说话主体。必须移除字幕、水印、UI、二维码和旧产品文字残留。

### 负向禁止和踩坑记录（只追加）

- 2026-06-02 baseline：禁止 wrong person、wrong product、partial product replacement、only logo replaced、mixed old and new packaging。
- 2026-06-02 baseline：产品特写禁止 human face、person、host、presenter、body、hands、talking mouth、speaking lips、animated portrait。
- 2026-06-02 baseline：不要输出 poster、product board、portrait crop、padded canvas、square image 或 horizontal image。
- 新增踩坑时只追加，不要改写上面的历史记录；同时在英文 `IMAGE_GPT_PITFALLS_APPEND_ONLY` 中追加对应英文禁令。

## English Model-Call Version

<!-- OPENCREW:IMAGE_GPT_POSITIVE_BASE_START -->
OpenAI image edit task: transform TARGET_FRAME into a clean realistic vertical 9:16 video first frame.

Use TARGET_FRAME as the only composition anchor: keep camera angle, pose, hand contact, product position, background, lighting, exposure, scale, perspective, and mobile-video texture.

The input image order is TARGET_FRAME, then HOST_REFERENCE when present, then PRODUCT_REFERENCE when present.

Output one realistic vertical 9:16 image. Do not output a poster, product board, portrait-photo crop, padded canvas, square image, or horizontal image.

Remove subtitles, burned-in captions, watermarks, UI overlays, QR codes, and old product text remnants while naturally inpainting the underlying frame.
<!-- OPENCREW:IMAGE_GPT_POSITIVE_BASE_END -->

<!-- OPENCREW:IMAGE_GPT_HOST_PRESENT_START -->
Use HOST_REFERENCE only for host identity, hair, outfit, accessories, microphone, skin tone, and natural short-video presence. Do not copy its background or pose.
<!-- OPENCREW:IMAGE_GPT_HOST_PRESENT_END -->

<!-- OPENCREW:IMAGE_GPT_HOST_MISSING_START -->
HOST_REFERENCE is missing. Preserve only the host identity visible in TARGET_FRAME; do not invent a new person.
<!-- OPENCREW:IMAGE_GPT_HOST_MISSING_END -->

<!-- OPENCREW:IMAGE_GPT_HOST_CUTAWAY_START -->
Product-only cutaway: do not use a host reference and do not create a person, face, mouth, hands, body, presenter, or speaking subject. PRODUCT_REFERENCE physical geometry outranks TARGET_FRAME composition: preserve the real package aspect ratio and the relative width/height of every visible face.
<!-- OPENCREW:IMAGE_GPT_HOST_CUTAWAY_END -->

<!-- OPENCREW:IMAGE_GPT_PRODUCT_PRESENT_START -->
Use PRODUCT_REFERENCE to replace the complete visible product package: brand, colors, label direction, shape, material, side panels, cap/seal, text blocks, graphics, and real physical proportions. Remove every old-product remnant.
<!-- OPENCREW:IMAGE_GPT_PRODUCT_PRESENT_END -->

<!-- OPENCREW:IMAGE_GPT_PRODUCT_MISSING_START -->
PRODUCT_REFERENCE is missing. Preserve only the product identity visible in TARGET_FRAME; do not invent a new product.
<!-- OPENCREW:IMAGE_GPT_PRODUCT_MISSING_END -->

<!-- OPENCREW:IMAGE_GPT_POSITIVE_CUTAWAY_START -->
For product-only cutaways, only the product identity may change and the frame must contain no living person or speaking face. Fit the vertical frame with neutral tabletop/background space, depth, shadow, or crop breathing room; never stretch, narrow, elongate, squeeze, bend, or perspective-warp the product package, box, sachet, bottle, pouch, label panel, cap, or seal.
<!-- OPENCREW:IMAGE_GPT_POSITIVE_CUTAWAY_END -->

<!-- OPENCREW:IMAGE_GPT_CONTEXT_START -->
Shot context: {{shot_summary}}
Scene context: {{scene_summary}}
Spoken-dialogue context: semantic guidance only. Do not render dialogue words, subtitles, speech captions, title cards, labels, UI text, or overlay text from the dialogue in the image.
Cutaway mode: {{cutaway_mode}}
Reference availability: {{reference_summary}}
<!-- OPENCREW:IMAGE_GPT_CONTEXT_END -->

<!-- OPENCREW:IMAGE_GPT_NEGATIVE_BASE_START -->
wrong person, wrong product, partial product replacement, only logo replaced, sticker overlay, mixed old and new packaging, old product remnants, changed outfit, deformed face, extra fingers, subtitles, speech captions, text overlay, watermark, blurry, low resolution, 2:3 aspect ratio, square image, horizontal image, padded canvas, stretched frame, reframed composition, vertically stretched package, narrow tall box, elongated sachet, squeezed product, warped product perspective, distorted package aspect ratio
<!-- OPENCREW:IMAGE_GPT_NEGATIVE_BASE_END -->

<!-- OPENCREW:IMAGE_GPT_NEGATIVE_CUTAWAY_START -->
human face, person, host, presenter, body, hands, talking mouth, speaking lips, teeth, eyes, animated portrait, living character on package, stretched label panel, stretched bottle, stretched pouch
<!-- OPENCREW:IMAGE_GPT_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:IMAGE_GPT_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not output poster, product board, portrait crop, padded canvas, square image, or horizontal image.
- 2026-06-02 baseline: Do not perform partial product replacement; logo-only, sticker-only, or color-patch swaps are failures.
- 2026-06-02 baseline: Do not mix old and new packaging or leave old product remnants.
- 2026-06-02 baseline: For product-only cutaways, do not create a person, host, face, body, hands, mouth, speaking lips, or animated portrait.
- 2026-06-12 product geometry: For product-only cutaways, do not stretch the PRODUCT_REFERENCE package into a narrow/tall form. Preserve real package aspect ratio and visible-face proportions; use neutral background/tabletop space to fit the vertical frame.
<!-- OPENCREW:IMAGE_GPT_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:IMAGE_GPT_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:IMAGE_GPT_PROMPT_END -->
