# 05_02 Image Grok Prompt Template / 图像 Grok 提示词模板

Scope: xAI / Grok image generation for 05_02.

This file is the Grok image prompt source. Keep model-specific wording here, not in
`image_grok.py`. The module may extract variables and choose conditional blocks,
but the positive and negative prompt language should live in this file so Grok can
be optimized by editing one Reference template.

本文件同时保留中文维护版和英文调用版。中文维护版用于人工阅读、讨论和追加踩坑；英文调用版由
`image_grok.py` 通过 `OPENCREW:*` 标记块读取，并进入真实 Grok 请求。

## Maintenance Contract

- Do not remove the `OPENCREW:*` marker comments. The module reads those blocks.
- 中文维护版不要使用 `OPENCREW:*` 标记，避免被程序误读。
- Add new positive guidance inside `GROK_POSITIVE_BASE` only when it is a stable
  behavior we want every Grok image request to follow.
- Add new failures inside `GROK_PITFALLS_APPEND_ONLY`. Treat that block as an
  append-only pitfall log: keep old lines intact and add new dated lines.
- 每次新增踩坑时，先在中文维护版记录清楚现象，再在英文调用版
  `GROK_PITFALLS_APPEND_ONLY` 追加对应英文禁令。
- Add cutaway-only prohibitions inside `GROK_NEGATIVE_CUTAWAY`.
- Keep provider-specific wording here. Do not move prompt style back into the
  main 05_02 tool.

## Reference Roles

- TARGET_FRAME: composition, camera, crop, pose, hand position, lighting,
  exposure, background, and vertical mobile-video realism.
- HOST_REFERENCE: complete visible host replacement when present and when this is
  not a product-only cutaway. Use it for face, hair, clothing, accessories,
  microphone, skin tone, and visible body appearance.
- PRODUCT_REFERENCE: complete product/package identity when present.
- Grok image editing supports multiple reference images in this executor. Send
  TARGET_FRAME first, followed by HOST_REFERENCE and PRODUCT_REFERENCE when
  available. Keep TARGET_FRAME as the composition anchor, and use HOST_REFERENCE /
  PRODUCT_REFERENCE only for identity constraints.
- For product-only cutaways with PRODUCT_REFERENCE, Grok may omit TARGET_FRAME
  from the actual image payload because TARGET_FRAME often contains old product
  objects that xAI tends to preserve. In that case, follow the scene context for
  the neutral surface, lighting, angle, and phone-video realism.

## 中文维护版

### 正向目标

Grok 图像任务：基于 TARGET_FRAME 生成一张真实的竖屏 9:16 短视频首帧。

TARGET_FRAME 是唯一的构图锚点：保留镜头角度、镜头质感、背景布局、主体比例、姿势、手和产品的几何关系、光线、曝光、景深、裁切，以及手机视频的真实感。

不要把画面重新理解为海报、产品展示板、广告图、肖像棚拍、产品目录图、拼贴图、参考图表、分镜页、方图、横图或带边框的画布。

Grok 调用时按顺序发送多张参考图：TARGET_FRAME 是构图锚点，HOST_REFERENCE 约束完整可见人物替换，PRODUCT_REFERENCE 只约束产品 / 包装身份。即使多图同时发送，也不要让人物参考图覆盖目标帧构图，不要让产品参考图变成产品展示板。

产品替换必须覆盖完整可见包装，不能只换 logo、标签颜色或正面贴纸。品牌身份、包装形状、瓶盖或封口、材质、侧面、标签方向、可见文字块、图形和色彩层级都要作为一个完整对象被替换。

必须移除字幕、烧录文字、界面覆盖、水印、二维码、旧产品文字残留和旧品牌痕迹，并自然补全底图，让结果仍然像真实视频帧。

### 人物和产品条件

有 HOST_REFERENCE：必须把 TARGET_FRAME 中可见的人物完整替换为 HOST_REFERENCE 中的人物，包括脸部身份、五官比例、发型、发色、肤色、可见服装、领口、配饰、麦克风、耳饰和整体口播风格。TARGET_FRAME 只提供姿势、机位、背景、手和产品的几何关系；不要保留 TARGET_FRAME 原人物的脸、头发、白色衣服、印花、首饰、红色手链、麦克风或造型。

没有 HOST_REFERENCE：只保持 TARGET_FRAME 中可见的人物身份，不要发明新人，也不要把人物美化成另一个身份。

产品特写 / 不对嘴 cutaway：不要使用人物参考，不要生成任何人、脸、嘴、手、身体、主持人或说话主体。画面必须保持纯产品。TARGET_FRAME 只保留台面、背景、光线、机位、透视、阴影和真实手机视频质感；TARGET_FRAME 中所有原产品相关内容都必须移除，包括旧产品盒、旧包装、旧标签、旧品牌色、旧 logo、旧产品照片、旧药片、旧胶囊、旧泡罩板、旧散落包装和旧产品残影。

有 PRODUCT_REFERENCE：用于完整产品 / 包装身份替换，保持参考产品的品牌体系、标签逻辑、色彩层级、包装形态、材质、瓶盖或封口，以及可见图形结构。移除 TARGET_FRAME 中全部旧产品残留。必须保持参考包装的真实物理比例和每个可见面的相对宽高，不能为了适配竖屏画布把盒子、条包、瓶身或袋子拉窄、拉高、纵向拉长或做透视扭曲。竖屏画布需要更多空间时，用台面、背景、阴影、景深或留白补足，不要拉伸产品本体。

没有 PRODUCT_REFERENCE：只保持 TARGET_FRAME 中可见的产品身份。不要发明新产品、标签、logo、包装文字或品牌。

### 负向禁止

禁止身份漂移、换错人、只美化原人物、保留原人物脸、保留原人物头发、保留原人物白色衣服、保留原人物印花、保留原人物首饰、保留原人物红色手链、保留原人物麦克风、把目标帧人脸和参考人物衣服混合、把参考人物人脸和目标帧衣服混合、换错产品、发明产品、发明品牌、发明 logo、局部替换、只替换 logo、贴纸覆盖、新旧包装混合、旧包装残留、旧产品文字、错误标签方向、错误包装形状、海报、产品板、目录图、参考图表、分镜页、分屏、拼贴、字幕、口播字幕、生成文字、水印、二维码、界面覆盖、额外道具、脸部变形、服装改变、手部错误、多手指、低质量、模糊、皮肤过度磨平、塑料感渲染、棚拍渲染、方图、横图、2:3 竖图、带边框画布、拉伸画面、重构构图。

产品特写 / cutaway 额外禁止：人、脸、主持人、身体、手、手指、嘴、眼睛、嘴唇、牙齿、说话脸、会说话的产品包装、动画肖像、包装上的活体角色、吉祥物脸、拟人化包装、原产品盒、原包装、原标签、原品牌色、原 logo、原产品照片、原药片、原胶囊、原泡罩板、旧产品残留、旧产品阴影形状、旧产品反光、旧产品边缘。

### 中文踩坑记录（只追加）

- 2026-06-02 baseline：不要输出海报 / 参考图表 / 产品展示板；Grok 必须返回一张真实视频首帧。
- 2026-06-02 baseline：不要做局部产品替换；只换 logo、贴纸、色块或正面标签都算失败。
- 2026-06-02 baseline：替换后不要留下旧产品文字、旧包装边缘、旧色块或旧品牌残留。
- 2026-06-02 baseline：不要生成字幕、口播文字、界面元素、二维码、水印或装饰性文字。
- 2026-06-02 baseline：产品特写 / cutaway 不要生成任何人、脸、手、嘴、眼睛、嘴唇、牙齿、主持人或动画包装角色。
- 2026-06-02 baseline：不要改变 TARGET_FRAME 的构图、镜头角度、主体比例、手和产品的几何关系、背景或手机视频真实感。
- 2026-06-02 host replacement：有 HOST_REFERENCE 时，不要只替换产品或只美化原人物；必须替换完整可见人物，包括脸、头发、衣服、配饰、麦克风和整体造型。
- 2026-06-02 product-only cutaway：仅产品替换的空镜不能保留任何原产品相关内容，包括旧盒子、旧包装、旧标签、旧药片、旧胶囊、旧泡罩板、旧品牌色、旧产品照片、旧边缘、旧反光或旧残影。
- 2026-06-12 product geometry：产品特写 / cutaway 不要把参考包装拉成长窄形；盒子、条包、瓶身、袋子必须保持 PRODUCT_REFERENCE 中真实物理比例。竖屏画面靠环境和留白适配，不能拉伸产品本体。

## English Model-Call Version

Only the following `OPENCREW:*` blocks are read by `image_grok.py` and sent to
Grok. Keep this English version aligned with the Chinese maintenance version.

### Positive Prompt Blocks

<!-- OPENCREW:GROK_POSITIVE_BASE_START -->
Grok image task: create one realistic vertical 9:16 short-video first frame from TARGET_FRAME.

Use TARGET_FRAME as the single visual composition anchor. Preserve its camera angle, lens feel, background layout, subject placement, pose, hand and product geometry, lighting direction, exposure, depth, crop, and casual phone-video texture.

Reference images are attached in the exact order listed in Reference order. Treat these role names as binding instructions, not captions.

Do not reinterpret the frame as a poster, product sheet, advertising board, portrait-photo crop, studio render, catalog image, collage, reference sheet, storyboard page, square image, horizontal image, or padded canvas.

When HOST_REFERENCE and PRODUCT_REFERENCE are provided, apply strict role binding. TARGET_FRAME provides composition only. HOST_REFERENCE is authoritative for the complete visible human subject. PRODUCT_REFERENCE is authoritative for the complete visible product/package. If one of these references is missing, rely on the text rules in this prompt and do not invent missing people, products, brand marks, logos, packaging text, or props.

Product replacement must cover the complete visible package, not only the logo, label color, or front sticker. Replace brand identity, package shape, cap or seal, material, side panels, label direction, visible text blocks, graphics, and color hierarchy as one coherent object.

Remove subtitles, burned-in captions, UI overlays, watermarks, QR codes, old product text remnants, and previous-brand artifacts. Inpaint the underlying frame naturally so the result still looks like a real video frame.
<!-- OPENCREW:GROK_POSITIVE_BASE_END -->

<!-- OPENCREW:GROK_HOST_PRESENT_START -->
Replace the entire visible human subject from TARGET_FRAME with the HOST_REFERENCE person.

The final person must match HOST_REFERENCE in face identity, facial proportions, eye shape, nose, mouth, hair shape and color, hairstyle, skin tone, visible clothing, collar or neckline, accessories, microphone, earrings, and overall presenter style.

Do not keep the original TARGET_FRAME person's face, hair, white clothing, printed shirt graphic, jewelry, red bracelet, microphone, or styling. TARGET_FRAME may provide pose, body placement, hand/product geometry, camera angle, background, lighting direction, crop, and mobile-video realism only, not identity or wardrobe.

If TARGET_FRAME and HOST_REFERENCE conflict, keep TARGET_FRAME pose, camera, background, product-holding geometry, and framing, but use HOST_REFERENCE for all human identity, face, hair, clothing, accessories, and visible body appearance.
<!-- OPENCREW:GROK_HOST_PRESENT_END -->

<!-- OPENCREW:GROK_HOST_MISSING_START -->
HOST_REFERENCE is missing. Preserve only the host identity visible in TARGET_FRAME. Do not invent a new person and do not beautify the host into a different identity.
<!-- OPENCREW:GROK_HOST_MISSING_END -->

<!-- OPENCREW:GROK_HOST_CUTAWAY_START -->
Product-only cutaway: do not use a host reference and do not create a person, face, mouth, hands, body, presenter, or speaking subject. The frame must stay product-only. Use PRODUCT_REFERENCE as the only product source. Use TARGET_FRAME, when available, only for neutral tabletop/background composition, camera angle, perspective, lighting direction, shadow realism, and casual phone-video texture. Remove every original-product-related object from TARGET_FRAME before placing the PRODUCT_REFERENCE item. The final frame must contain only PRODUCT_REFERENCE product forms plus neutral scene surfaces. PRODUCT_REFERENCE physical geometry outranks TARGET_FRAME composition: preserve the real package aspect ratio and the relative width/height of every visible face.
<!-- OPENCREW:GROK_HOST_CUTAWAY_END -->

<!-- OPENCREW:GROK_PRODUCT_PRESENT_START -->
Use PRODUCT_REFERENCE for complete product/package identity replacement. Preserve the reference product's brand family, label logic, color hierarchy, package form, material, cap or seal, visible graphic structure, and real physical proportions. For product-only cutaways, compose only the silver-green cardboard box and green stick sachet product family visible in PRODUCT_REFERENCE, plus neutral scene surfaces. Do not create any product format, prop, label family, color system, accessory, or package geometry that is not visible in PRODUCT_REFERENCE.
<!-- OPENCREW:GROK_PRODUCT_PRESENT_END -->

<!-- OPENCREW:GROK_PRODUCT_MISSING_START -->
PRODUCT_REFERENCE is missing. Preserve only the product identity visible in TARGET_FRAME. Do not invent a new product, label, logo, package text, or brand.
<!-- OPENCREW:GROK_PRODUCT_MISSING_END -->

<!-- OPENCREW:GROK_POSITIVE_CUTAWAY_START -->
For product-only cutaways, replace the complete product set, not just the front package. Keep only neutral non-product scene elements from TARGET_FRAME, such as tabletop, background, lighting, perspective, and natural shadows. The final shot must contain only the PRODUCT_REFERENCE silver-green box and green stick sachets as product objects. Remove every non-reference product prop and every mixed old/new product artifact. Fit the vertical frame by adding neutral tabletop/background space, depth, shadow, or crop breathing room; never stretch, narrow, elongate, squeeze, bend, or perspective-warp the product package, box, sachet, bottle, pouch, label panel, cap, or seal.
<!-- OPENCREW:GROK_POSITIVE_CUTAWAY_END -->

<!-- OPENCREW:GROK_CONTEXT_START -->
Shot context: {{shot_summary}}
Scene context: {{scene_summary}}
Spoken-dialogue context: semantic guidance only. Do not render dialogue words, subtitles, speech captions, title cards, labels, UI text, or overlay text from the dialogue in the image.
Cutaway mode: {{cutaway_mode}}
Reference availability: {{reference_summary}}
Reference order: {{reference_order}}
<!-- OPENCREW:GROK_CONTEXT_END -->

## Negative Prompt Blocks

<!-- OPENCREW:GROK_NEGATIVE_BASE_START -->
identity drift, wrong person, merely beautifying the TARGET_FRAME person, preserving the original TARGET_FRAME face, preserving the original TARGET_FRAME hair, preserving the original white sweatshirt, preserving the original printed shirt graphic, preserving the original jewelry, preserving the original red bracelet, preserving the original microphone, mixing TARGET_FRAME face with HOST_REFERENCE clothing, mixing HOST_REFERENCE face with TARGET_FRAME clothing, wrong product, invented product, invented brand, invented logo, partial product replacement, logo-only replacement, sticker overlay, mixed old and new packaging, old package remnants, old product text, wrong label direction, wrong package shape, poster, product board, catalog sheet, reference sheet, storyboard page, split-screen, collage, subtitles, speech captions, generated text, watermark, QR code, UI overlay, extra props, deformed face, changed outfit, bad hands, extra fingers, low quality, blurry, over-smoothed skin, plastic render, studio render, square image, horizontal image, 2:3 portrait, padded canvas, stretched frame, reframed composition
<!-- OPENCREW:GROK_NEGATIVE_BASE_END -->

<!-- OPENCREW:GROK_NEGATIVE_CUTAWAY_START -->
person, face, host, presenter, body, hands, fingers, mouth, eyes, lips, teeth, speaking face, talking product package, animated portrait, living character on package, mascot face, humanized package, non-reference product prop, non-reference package, non-reference label family, non-reference color system, non-reference product format, mixed old and new product props, leftover old product accessory, vertically stretched package, narrow tall box, elongated sachet, squeezed product, warped product perspective, distorted package aspect ratio, stretched label panel, stretched bottle, stretched pouch
<!-- OPENCREW:GROK_NEGATIVE_CUTAWAY_END -->

## Append-Only Pitfall Log

Add new lines here after each Grok failure. Do not rewrite old lines unless the
failure was logged incorrectly. Keep the date and the symptom visible so future
optimization is traceable.

<!-- OPENCREW:GROK_PITFALLS_APPEND_ONLY_START -->
- 2026-06-02 baseline: Do not output poster/reference-sheet/product-board layouts; Grok must return a single realistic video first frame.
- 2026-06-02 baseline: Do not perform partial product swaps; replacing only a logo, sticker, color patch, or front label is a failure.
- 2026-06-02 baseline: Do not leave old product text, old package edges, old color blocks, or previous-brand remnants after replacement.
- 2026-06-02 baseline: Do not create generated subtitles, captions, UI, QR codes, watermarks, or decorative text in the image.
- 2026-06-02 baseline: For product-only cutaways, do not create any person, face, hand, mouth, eyes, lips, teeth, host, presenter, or animated package character.
- 2026-06-02 baseline: Do not change TARGET_FRAME composition, camera angle, subject scale, hand/product geometry, background, or mobile-video realism.
- 2026-06-02 host replacement: When HOST_REFERENCE is provided, do not only replace the product or merely beautify the original person; replace the complete visible human subject, including face, hair, clothing, accessories, microphone, and styling.
- 2026-06-02 product-only cutaway: Product-only replacement frames must not preserve any original-product-related content. In the model-call wording, avoid repeating concrete forbidden product formats that can accidentally be recalled; instead require only PRODUCT_REFERENCE product forms plus neutral scene surfaces.
- 2026-06-12 product geometry: For product-only cutaways, do not stretch the PRODUCT_REFERENCE package into a narrow/tall form. Preserve real package aspect ratio and visible-face proportions; use neutral background/tabletop space to fit the vertical frame.
<!-- OPENCREW:GROK_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:GROK_PROMPT_START -->
{{positive_prompt}}

Negative prompt:
{{negative_prompt}}
<!-- OPENCREW:GROK_PROMPT_END -->
