# 提示词撰写指南：口播人物产品一致性模型 GPT

更新时间：2026-05-16  
适用模型：GPT 图像生成/编辑模型，本文按 `gpt-image2` 工作流撰写  
适用任务：生成可复用的人物模板、产品模板，并用这些模板替换口播视频帧中的人物和产品，同时保持背景、构图、机位、手部关系、产品细节和真实手机视频质感。

## 一、文档定位

这是一份通用型指南，不绑定某一个具体产品、人物、房间或镜头。  
本文档中的具体案例经验来自 `/Users/duheng/Development/OpenCode/CrewAI/consistency` 下的三轮测试和复盘，但正文提示词均改写为可复用变量模板。

使用时只需要替换以下变量：

| 变量 | 含义 |
| --- | --- |
| `TARGET_FRAME` | 要编辑的目标视频帧 |
| `HOST_REFERENCE` | 新人物/主播的一致性参考图 |
| `PRODUCT_REFERENCE` | 新产品的一致性参考图 |
| `ORIGINAL_HOST` | 原视频里需要替换掉的人物 |
| `NEW_HOST` | 新人物/主播 |
| `OLD_PRODUCT` | 原视频里需要替换掉的产品 |
| `NEW_PRODUCT` | 新产品 |
| `SCENE_LOCKS` | 必须保留的背景、家具、灯光、机位、画幅和画面质感 |
| `HOST_LOCKS` | 必须锁定的人物脸型、发型、服装、配饰、麦克风等 |
| `PRODUCT_LOCKS` | 必须锁定的产品名称、包装颜色、文字、logo、形状、材质、规格、配件等 |
| `NEGATIVE_LOCKS` | 必须排除的旧人物、旧产品、旧包装、错误动作、字幕、水印等 |

## 二、扫描依据

本指南基于对 `/Users/duheng/Development/OpenCode/CrewAI/consistency` 的扫描和三轮测试总结生成。

扫描结果：

| 类型 | 数量 |
| --- | ---: |
| 总文件 | 571 |
| Markdown | 48 |
| JSON | 135 |
| TXT prompt | 132 |
| SRT | 33 |
| 图片 | 81 |
| 视频 | 99 |
| 音频 | 7 |

关键经验来源：

| 来源 | 用途 |
| --- | --- |
| 人物模板提示词 | 总结如何生成可复用的 `HOST_REFERENCE` |
| 产品模板提示词 | 总结如何生成可复用的 `PRODUCT_REFERENCE` |
| 两步替换提示词 | 总结人物替换、产品替换、字幕清理的稳定流程 |
| 替换结果 manifest | 总结目标帧编辑时需要保留和禁止的元素 |
| 最终视频参考帧 | 总结什么样的图像更适合作为后续视频模型参考 |
| 三轮视频测试报告 | 反推出图像阶段必须提前固定的人物、产品、手部和画面约束 |
| 模型优先级提示词指南 | 总结跨模型共通的避坑规则 |

## 三、GPT 图像模型在生产链路中的定位

GPT 图像模型不负责生成视频，不负责声音，不负责 SRT。它在口播人物产品替换流程中只负责三类图像工作：

1. 生成可复用的一致性参考模板：
   - `HOST_REFERENCE`
   - `PRODUCT_REFERENCE`
2. 编辑目标帧：
   - 替换人物
   - 替换产品
   - 移除字幕和覆盖文字
3. 为后续视频模型准备稳定参考图：
   - 首帧
   - 尾帧
   - 多帧参考序列

最重要的原则：

- 模板图可以是参考板。
- 替换图不能像参考板，必须回到真实手机口播视频帧。
- 人物模板负责身份一致性。
- 产品模板负责包装一致性。
- 目标帧负责背景、姿势、手部遮挡、机位和真实感。
- 图像阶段越稳定，后续视频阶段越不容易出现人物漂移、产品变形、手部穿帮和产品被放下。

## 四、通用工作流

推荐使用三类输入、三步替换。

### 4.1 三类输入

| 输入 | 作用 | 注意事项 |
| --- | --- | --- |
| `TARGET_FRAME` | 提供真实画面结构 | 背景、机位、构图、光线、手部动作都来自它 |
| `HOST_REFERENCE` | 提供新人物身份 | 只提取人物身份和造型，不复制参考板布局 |
| `PRODUCT_REFERENCE` | 提供新产品身份 | 只提取产品包装和形态，不复制参考板布局 |

### 4.2 三步替换

1. Pass 1：只替换人物。
   - 使用 `HOST_REFERENCE`
   - 保留产品、背景、手势、机位和字幕区域
2. Pass 2：只替换产品。
   - 使用 `PRODUCT_REFERENCE`
   - 保留人物、手部、背景、机位和光线
3. Pass 3：去字幕和清理。
   - 移除底部字幕、覆盖文字、水印、UI
   - 自然补齐衣服、手、产品、家具和背景

一次性同时替换人物和产品也可以，但稳定性通常不如三步法。只有在成本或流程必须压缩时才建议使用一次性替换。

## 五、通用变量填写规范

### 5.1 `SCENE_LOCKS`

`SCENE_LOCKS` 用来描述必须保留的目标画面元素。

建议填写：

- 画幅：竖屏 9:16、横屏 16:9 或原图比例
- 场景：厨房、客厅、办公室、直播间、柜台、货架、车内等
- 背景关键物：沙发、墙画、桌面、窗帘、灯具、货架、品牌背景板等
- 机位：固定机位、手机前置摄像头、半身近景、俯视/平视/微仰视
- 光线：自然光、暖光、顶灯、屏幕光、低照度等
- 画面质感：手机视频压缩感、轻微运动模糊、真实皮肤纹理、非棚拍
- 禁止改动：不要换背景、不要换房间、不要改家具、不要改曝光和色温

### 5.2 `HOST_LOCKS`

`HOST_LOCKS` 用来描述新人物必须一致的身份和造型。

建议填写：

- 性别、年龄段、族裔/地域气质
- 脸型、五官比例、肤色、妆容
- 发型、发色、发量、刘海或无刘海
- 服装颜色、版型、领口、图案、是否有 logo
- 配饰：耳环、项链、戒指、手链、眼镜等
- 麦克风：领夹麦、耳麦、手持麦，颜色和位置
- 身材比例和坐姿/站姿气质
- 必须避开原人物的特征

### 5.3 `PRODUCT_LOCKS`

`PRODUCT_LOCKS` 用来描述新产品必须一致的包装身份。

建议填写：

- 产品全称和正面主标题
- 品牌名、logo、关键标识
- 包装主色、辅助色、材质、反光方式
- 盒型、瓶型、袋型、罐型、条包、说明卡、配件
- 正面、侧面、顶部或底部可见元素
- 必须保留的数字、符号、图案、规格
- 产品是否必须闭合、是否允许露出内包装
- 在手中的展示方式：正面可见、角度、比例、遮挡关系

### 5.4 `NEGATIVE_LOCKS`

`NEGATIVE_LOCKS` 用来排除旧身份和常见错误。

建议填写：

- 不要原人物脸、原发型、原衣服、原配饰
- 不要旧产品包装、旧 logo、旧颜色、旧文字、旧配件
- 不要把产品改成普通同色盒子、普通瓶子或泛化商品
- 不要打开产品、拆包、撕袋、倒出内容物，除非任务明确要求
- 不要药片、医疗场景、治疗承诺，除非产品本身和合规要求允许
- 不要字幕、覆盖文字、水印、直播 UI、额外二维码
- 不要海报、广告排版、棚拍参考板、产品陈列图
- 不要改变背景、机位、手部动作和目标帧比例

## 六、之前踩过的坑和通用修正方式

### 6.1 人物模板相关

| 坑 | 通用说明 | 提示词处理 |
| --- | --- | --- |
| 新人物和原人物太像 | 只写“换一个主播”不够，模型会沿用原脸型和气质 | 明确写出新脸型、五官比例、发型、肤色、服装，并列出原人物负面特征 |
| 只有头像没有动作锚点 | 后续替换缺少目标姿势参考 | 人物模板必须包含三视图、表情和一个对位口播姿势小窗 |
| 服装没有拉开差异 | 模型容易沿用原衣服颜色、图案和领口 | 指定新服装颜色、版型、是否无 logo、是否无图案 |
| 发型沿用原人物 | 原发型轮廓最容易被复制 | 指定新发型，并把原发型写入禁止项 |
| 人物像棚拍硬照 | 替换结果会失去手机视频真实感 | 模板可干净清晰，替换帧必须保持 phone-video realism |

### 6.2 产品模板相关

| 坑 | 通用说明 | 提示词处理 |
| --- | --- | --- |
| 产品变成普通同色包装 | 包装细节不足时，模型会泛化 | 锁定产品名、品牌、主色、logo、数字、材质、图案和包装结构 |
| 旧产品残留 | 目标帧里的旧颜色、旧 logo 或旧形态会污染新产品 | 明确 `OLD_PRODUCT` 只作为替换对象，不能保留任何旧包装元素 |
| 产品被打开或拆开 | 后续视频模型也会被错误引导 | 如果任务是口播持物，默认要求产品完整闭合、正面可见、自然拿在手中 |
| 文字乱写 | 包装文字容易被编造 | 只要求保留确实需要的短文字和标识，不新增宣传语和功效承诺 |
| 参考板被当成视频帧 | 产品板是身份参考，不应进入真实画面 | 明确产品参考图只提供包装身份，不复制布局、白底、黑底或多角度排版 |

### 6.3 图像替换相关

| 坑 | 通用说明 | 提示词处理 |
| --- | --- | --- |
| 背景被改 | 模型会为了协调新人物重绘房间 | 用 `SCENE_LOCKS` 锁定背景、家具、光线、压缩感和机位 |
| 手部遮挡不自然 | 产品替换后容易漂浮、穿手或压住手指 | 保留手指遮挡、接触阴影、握持压力和手部姿态 |
| 产品位置变化 | 产品大小、角度、比例和手势不匹配 | 匹配原产品的位置、角度、比例、透视和手部接触关系 |
| 字幕残留 | 底部字幕会污染后续视频参考 | 最终图必须移除字幕并自然补齐底部区域 |
| 一步替换不稳 | 同时换人和换产品容易互相污染 | 优先两步法，必要时第三步单独去字幕 |
| 模板和目标混淆 | 参考板布局被复制进视频帧 | 明确只提取身份，不复制参考板布局 |

### 6.4 从视频测试反推到图像阶段的约束

| 视频阶段常见问题 | 图像阶段提前处理 |
| --- | --- |
| 后续视频会放下产品或换产品 | 替换图中就要让产品稳定在手中、完整闭合、正面可见 |
| 多图参考会拉长身体或改变比例 | 人物模板和替换帧都要锁定正常人体比例 |
| 链式生成逐段变暗或变色 | 每张替换图都要锁定原帧曝光、白平衡和色彩 |
| 产品文字不清 | 产品模板必须提供清晰包装多角度和关键标识近景 |
| 字幕或屏幕文字残留 | 所有最终视频参考图必须无字幕、无水印、无 UI |
| 口播台词被转成动作 | 图像阶段不处理台词，不让文案改变人物动作和产品状态 |

## 七、GPT 图像模型通用约束

### 7.1 输入角色定义

每次编辑都要明确三类图的角色：

```text
TARGET_FRAME = the image to edit. Preserve its background, camera angle, lighting, pose, hands, framing, and phone-video realism.
HOST_REFERENCE = identity and styling reference for the new host only. Do not copy its reference-board layout.
PRODUCT_REFERENCE = product identity reference only. Do not copy its product-board layout.
```

中文：

```text
TARGET_FRAME = 要编辑的目标画面。保留它的背景、机位、灯光、姿势、手部、构图和手机视频真实感。
HOST_REFERENCE = 新人物的人物身份和造型参考。不要复制整张人物参考板布局。
PRODUCT_REFERENCE = 新产品身份参考。不要复制整张产品参考板布局。
```

### 7.2 输出硬约束

```text
Output must remain a realistic phone-video frame in the same aspect ratio as the target frame, not a studio poster, not a product board, not a character sheet, not an ad layout.
```

中文：

```text
输出必须仍然是真实手机视频帧，并保持目标帧原始画幅比例，不是棚拍海报，不是产品参考板，不是人物设定板，不是广告排版图。
```

### 7.3 字幕和文字约束

```text
Remove all subtitles, captions, overlay text, watermarks, UI text, generated Chinese/English text, and old product text overlays. Fill the removed area naturally.
```

中文：

```text
移除所有字幕、标题、覆盖文字、水印、UI 文字、额外生成的中英文文字和旧产品字幕区域，并自然补齐被移除区域。
```

### 7.4 音频约束

图像模型本身不生成音频。  
如果这些图像后续进入视频模型，提示词必须继续限定：

```text
No background music, soundtrack, ambient music, or sound effects. Audio may contain speech/voice only.
```

中文：

```text
不能有背景音乐、配乐、环境音乐或音效；只能有人声口播。
```

## 八、标准人物模板生成指南

### 8.1 目标

生成一张可复用的人物一致性参考板，用于后续图像编辑和视频生成。

必须包含：

- 同一人物的正面、侧面、背面三视图
- 同一人物的表情变化
- 发型、服装、配饰、麦克风、手部首饰细节
- 一个和目标视频构图对位的口播姿势小窗

### 8.2 中文 Prompt：生成 `HOST_REFERENCE`

```text
根据参考帧生成一张新的任务级口播人物一致性参考板。参考帧只作为角色类型、坐姿/站姿构图、手部讲解姿势、拍摄真实感和场景气质参考，不作为脸部身份和精确服装参考。

新人物必须是一个真实自然的口播/讲解/带货人物，和原视频人物明显不同，但保持同类角色可信度：[填写 NEW_HOST 的性别、年龄段、族裔/地域气质、职业气质、妆容风格、亲和力要求]。

人物身份必须明确避开原人物：[填写 ORIGINAL_HOST 的脸型、眼睛、发型、服装、配饰等需要避开的特征]。新人物使用：[填写 NEW_HOST 的脸型、五官比例、肤色、妆容、自然不对称细节]。

发型：[填写发型和发色]。服装：[填写服装颜色、版型、领口、是否无 logo、是否无图案、是否无文字]。配饰：[填写耳环、项链、戒指、手链、眼镜等]。麦克风：[填写领夹麦/耳麦/手持麦的颜色、形状和位置，如不需要则写不要麦克风]。

画面需要包含同一人物的正面、侧面、背面三视图，保持脸、发型、体型、服装、配饰、麦克风位置完全一致；包含三个小头像表情：[填写需要的表情，例如自然专注、温和讲解微笑、强调表达]。

必须增加一个和目标视频参考图对位的口播姿势小窗：人物保持目标视频相同的坐姿/站姿、半身/近景裁切、头部位置、肩膀角度、手部高度和产品讲解姿势。背景可以简化，但构图要方便后续替换到真实视频帧中。可以模拟持有一个无品牌占位产品，但不要复制旧产品。

整体为真实摄影质感，参考板布局清晰，光线自然，皮肤真实不过度磨皮，人物可信。不要卡通、不要动漫、不要明星脸、不要夸张美妆、不要品牌 logo、不要水印、不要密集文字。不要生成原人物脸、原人物发型、原人物服装或原人物配饰。

比例：16:9 或适合参考板展示的比例。
```

### 8.3 English Prompt: Generate `HOST_REFERENCE`

```text
Create a photorealistic character consistency reference board for a new talking-head presenter. Use the provided reference frame only for role type, seated/standing composition, hand demonstration posture, camera realism, and scene mood. Do not use it as facial identity or exact wardrobe reference.

The new person must look natural and credible for a talking-head, product-explaining, or livestream-presenter role, visibly different from the original person while preserving the same role credibility: [insert NEW_HOST gender, age range, ethnicity/regional look, professional tone, makeup style, and approachability].

Avoid the original person's identity: [insert ORIGINAL_HOST face shape, eyes, hairstyle, clothing, accessories, and other features to avoid]. Use the new identity: [insert NEW_HOST face shape, facial proportions, skin tone, makeup, and natural asymmetry].

Hairstyle: [insert hairstyle and hair color]. Clothing: [insert clothing color, silhouette, neckline, no-logo/no-print/no-text rules]. Accessories: [insert earrings, necklace, rings, bracelet, glasses, etc.]. Microphone: [insert lavalier/headset/handheld mic color, shape, and placement, or no microphone].

The board must include front, side, and back views of the same person with consistent face, hairstyle, body proportions, clothing, accessories, and microphone placement. Include three small headshot expression variations: [insert target expressions].

Also include one reference-aligned talking-head pose panel matching the target video composition: same seated/standing posture, upper-body framing, head placement, shoulder angle, hand height, and product-explanation gesture. The background may be simplified, but the composition should be useful for later replacement into a real video frame. The person may hold a generic unbranded placeholder product, but do not copy the old product.

Use realistic photography, clean organized reference-board layout, natural lighting, believable skin texture, and credible human identity. No cartoon, anime, celebrity likeness, heavy glamour makeup, brand logos, watermarks, dense text, original face, original hairstyle, original clothing, or original accessories.

Aspect ratio: 16:9 or another ratio suitable for a reference board.
```

## 九、标准产品模板生成指南

### 9.1 目标

生成一张可复用的产品一致性参考板，用于后续图片替换和视频生成。

必须包含：

- 产品正面主视图
- 左右 3/4 角度
- 侧面厚度或边缘结构
- 顶部、底部或俯视角
- 关键配件、内袋、瓶盖、说明卡或套装组件
- 产品名、品牌、logo、数字、图案、材质和颜色细节

### 9.2 中文 Prompt：生成 `PRODUCT_REFERENCE`

```text
生成一页 [NEW_PRODUCT] 的纯产品多角度一致性参考图。

这不是视频画面替换图，也不是主播带货画面。不要出现人物、手、脸、目标场景背景、直播画面或原视频构图。画面只做产品一致性参考，用干净白底、黑底或中性背景把产品各个角度清楚展示出来，方便后续抠图、替换和视频重建时保持产品一致。

锁定产品身份：
- 产品全称：[填写产品全称]
- 品牌名：[填写品牌名]
- 正面主标题：[填写正面最重要的文字]
- 关键 logo/符号/数字：[填写必须保留的 logo、数字、图形]
- 包装主色：[填写主色]
- 包装辅助色：[填写辅助色]
- 材质：[纸盒、塑料瓶、玻璃瓶、金属罐、软袋、条包等]
- 形状比例：[盒型、瓶型、袋型、罐型、长宽高比例]
- 关键图案：[植物图形、人物图形、几何图案、色块、纹理等]
- 配件/组件：[条包、内袋、瓶盖、说明卡、勺子、外盒等，没有则写无]

需要展示：
- 产品正面主视图
- 产品左 3/4 角度
- 产品右 3/4 角度
- 侧面厚度或边缘结构
- 顶部、底部或轻微俯视角度
- 配件/组件整齐排列
- 单个关键组件近景
- 可选细节小图：logo、数字、图案、材质、封口、标签、瓶盖、条包等

风格为真实商业产品摄影，边缘清晰，比例准确，材质可信，反光自然，物体下方有轻微接触阴影，布局干净有序，方便检查和后续使用。

禁止项：不要人物、不要手、不要脸、不要主播、不要目标视频背景、不要直播 UI、不要字幕、不要 [OLD_PRODUCT] 包装、不要旧 logo、不要旧颜色、不要旧配件、不要无关品牌、不要水印、不要海报大标题、不要把产品改成普通同类包装、不要新增未经确认的卖点文字、功效承诺、二维码或监管敏感文案。

比例：16:9 或 1:1。
```

### 9.3 English Prompt: Generate `PRODUCT_REFERENCE`

```text
Create a clean photorealistic product-only multi-angle consistency reference board for [NEW_PRODUCT].

This is not a video-frame replacement image and not a livestream scene. No people, no hands, no face, no target-scene background, no livestream UI, and no original video composition. The output is only a product consistency board on a clean white, black, or neutral background for later cutout, replacement, and video rebuild work.

Lock the product identity:
- full product name: [insert full product name]
- brand name: [insert brand name]
- main front title: [insert most important front text]
- key logos/symbols/numbers: [insert must-preserve logos, numbers, marks, graphics]
- primary package color: [insert primary color]
- secondary package color: [insert secondary color]
- material: [paper box, plastic bottle, glass bottle, metal tin, soft pouch, sachet, etc.]
- shape and proportions: [box/bottle/pouch/tin shape and approximate proportions]
- key graphics: [botanical graphic, character graphic, geometric pattern, color blocks, texture, etc.]
- accessories/components: [sachets, inner pouch, cap, instruction card, spoon, outer box, etc.; write none if not applicable]

Show:
- front-facing product hero view
- left 3/4 angle
- right 3/4 angle
- side view showing thickness or edge structure
- top, bottom, or slightly elevated view
- accessories/components laid out neatly
- close-up of one key component
- optional detail panels for logo, numbers, graphics, material, seal, label, cap, sachet, etc.

Use realistic commercial product photography: crisp edges, accurate proportions, believable materials, natural reflections, light contact shadows, clean organized layout, high detail, and neutral studio lighting.

Negative constraints: no host, no hands, no human body parts, no face, no target video background, no livestream UI, no subtitles, no [OLD_PRODUCT] packaging, no old logo, no old colors, no old accessories, no unrelated brands, no watermark, no poster headline, no generic same-category packaging, no invented selling points, no unverified claims, no extra QR codes, and no regulatory-sensitive copy.

Aspect ratio: 16:9 or 1:1.
```

## 十、目标帧替换指南

### 10.1 Pass 1：只替换人物

用于先把 `ORIGINAL_HOST` 替换成 `NEW_HOST`，暂时不动产品。

#### 中文 Prompt

```text
编辑 TARGET_FRAME，只替换人物/主播身份，不替换产品。

TARGET_FRAME 是要编辑的真实手机视频帧。请严格保留 SCENE_LOCKS：[填写必须保留的背景、家具、灯光、阴影、手机拍摄角度、画幅、压缩质感、手部姿态、手指位置、手臂位置和当前产品位置]。

使用 HOST_REFERENCE 作为 NEW_HOST 的身份和造型参考：HOST_LOCKS = [填写新人物脸型、发型、服装、配饰、麦克风、妆容、气质]。只提取人物身份和造型，不复制 HOST_REFERENCE 的参考板布局。

NEW_HOST 必须自然执行 TARGET_FRAME 里 ORIGINAL_HOST 的同一个动作和姿势。保持原来的头部方向、视线方向、肩膀姿态、手部位置和产品拿法。结果必须像 NEW_HOST 原本就在同一场景、同一机位、同一时间被手机拍到。

本步骤暂时保留 TARGET_FRAME 中的 OLD_PRODUCT，不要改变产品，不要改变手部动作，不要改变背景，不要添加新道具，不要改变字幕区域。

避免：ORIGINAL_HOST 的脸、发型、服装、配饰、明星脸、过度磨皮、塑料皮肤、棚拍光、海报感、手指畸形、额外手指、衣服变色、背景变化、产品变化。
```

#### English Prompt

```text
Edit TARGET_FRAME by replacing only the person/host identity. Do not replace the product in this pass.

TARGET_FRAME is a realistic phone-video frame. Strictly preserve SCENE_LOCKS: [insert background, furniture, lighting, shadows, camera angle, aspect ratio, compression softness, hand pose, finger positions, arm positions, and current product placement].

Use HOST_REFERENCE as the NEW_HOST identity and styling reference: HOST_LOCKS = [insert new face, hairstyle, clothing, accessories, microphone, makeup, and presenter energy]. Extract only identity and styling; do not copy the HOST_REFERENCE board layout.

NEW_HOST must naturally perform the exact same action and pose as ORIGINAL_HOST in TARGET_FRAME. Preserve head direction, gaze direction, shoulder posture, hand positions, and product-holding action. The result should look as if NEW_HOST was captured in the same scene, same camera, same moment.

Temporarily keep OLD_PRODUCT unchanged in this pass. Do not change product, hand action, background, props, or subtitle area.

Avoid: ORIGINAL_HOST face, hairstyle, clothing, accessories, celebrity likeness, plastic skin, over-smoothed face, studio lighting, poster look, malformed hands, extra fingers, clothing color drift, background changes, and product changes.
```

### 10.2 Pass 2：只替换产品

用于把 `OLD_PRODUCT` 替换为 `PRODUCT_REFERENCE` 里的 `NEW_PRODUCT`。

#### 中文 Prompt

```text
编辑 TARGET_FRAME，只替换人物手中的产品，不改变人物、手部、背景和机位。

TARGET_FRAME 是 Pass 1 后的人物替换结果。请保留 NEW_HOST 的脸、服装、麦克风、发型、首饰、手部姿态、手指遮挡、背景、灯光、画幅构图和手机视频真实感。

使用 PRODUCT_REFERENCE 作为 NEW_PRODUCT 的产品身份参考：PRODUCT_LOCKS = [填写产品名、品牌、主色、辅助色、logo、数字、包装形状、材质、图案、配件/组件]。只提取产品包装身份，不复制 PRODUCT_REFERENCE 的参考板布局。

把 OLD_PRODUCT 的包装、颜色、logo、文字、配件或旧产品残留替换成 NEW_PRODUCT。匹配 OLD_PRODUCT 在 TARGET_FRAME 中的位置、角度、比例、透视和手部接触关系。

如果原帧中是盒子，就替换成 NEW_PRODUCT 的盒装形态；如果原帧中是瓶子，就替换成 NEW_PRODUCT 的瓶装形态；如果原帧中是袋、条包、配件或窄长物，就替换成 NEW_PRODUCT 对应组件。必须保留手指遮挡在产品前面的关系，保留手指与产品之间的接触阴影，产品不能漂浮，不能压在手指上方造成穿帮。

除非任务明确要求，NEW_PRODUCT 在替换图中必须完整闭合、正面或关键标识可见、自然拿在手中。不要打开盒子，不要撕开包装，不要露出不该出现的内包装，不要生成额外产品，不要生成和产品无关的物品，不要新增未经确认的文字、二维码、卖点或合规敏感文案。

不要改变人物脸、衣服、手、背景、字幕区域、机位和光线。
```

#### English Prompt

```text
Edit TARGET_FRAME by replacing only the product held by the person. Do not change the person, hands, background, or camera.

TARGET_FRAME is the Pass 1 host-replacement result. Preserve NEW_HOST face, clothing, microphone, hairstyle, jewelry, hand pose, finger occlusion, background, lighting, aspect ratio, framing, and realistic phone-video texture.

Use PRODUCT_REFERENCE as the NEW_PRODUCT identity reference: PRODUCT_LOCKS = [insert product name, brand, primary color, secondary color, logo, numbers, package shape, material, graphics, accessories/components]. Extract only product packaging identity; do not copy the PRODUCT_REFERENCE board layout.

Replace OLD_PRODUCT packaging, colors, logo, text, accessories, or old product residue with NEW_PRODUCT. Match OLD_PRODUCT's position, angle, scale, perspective, and hand-contact relationship in TARGET_FRAME.

If the original frame shows a box, replace it with the NEW_PRODUCT box form. If it shows a bottle, replace it with the NEW_PRODUCT bottle form. If it shows a pouch, sachet, accessory, or narrow object, replace it with the corresponding NEW_PRODUCT component. Preserve finger occlusion in front of the package and contact shadows between fingers and product. The product must not float and must not incorrectly cover the fingers.

Unless the task explicitly requires otherwise, NEW_PRODUCT must remain fully closed, with its front or key identifier visible, and naturally held in the hand. Do not open the box, tear packaging, expose unintended inner packaging, create extra products, create unrelated objects, or add unverified text, QR codes, selling points, or regulatory-sensitive copy.

Do not change the host face, clothing, hands, background, subtitle area, camera angle, or lighting.
```

### 10.3 Pass 3：移除字幕和清理画面

#### 中文 Prompt

```text
编辑 TARGET_FRAME，移除所有底部字幕、覆盖文字、水印、UI 文字和额外屏幕文字，并自然补齐被遮挡区域。

保持人物、产品、手部、背景、机位、光线、画幅构图完全不变。字幕移除后的区域应该自然延续衣服、手、产品、家具和背景纹理，看不出修补痕迹。

不要改变人物身份，不要改变产品包装，不要新增文字，不要改变手部位置，不要改变产品位置，不要裁切画面，不要改变颜色和曝光。
```

#### English Prompt

```text
Edit TARGET_FRAME by removing all bottom subtitles, overlay text, watermarks, UI text, and extra on-screen text. Fill the removed area naturally.

Keep the person, product, hands, background, camera angle, lighting, and aspect ratio unchanged. The repaired area should naturally continue clothing, hands, product, furniture, and background textures without visible retouching artifacts.

Do not change host identity, product packaging, hand position, product position, crop, color, or exposure. Do not add new text.
```

## 十一、一次性替换人物 + 产品 Prompt

如果成本或流程要求一次性完成，可以用下面模板。但稳定性通常不如两步法。

### 中文 Prompt

```text
编辑 TARGET_FRAME，同时替换人物和产品，但必须保留 TARGET_FRAME 的背景、机位、灯光、手部姿势、画幅构图和手机视频真实感。

TARGET_FRAME 是唯一的画面结构来源。请保留 SCENE_LOCKS：[填写背景、家具、道具、原始手机镜头角度、曝光、阴影、构图、手部动作、手指遮挡和产品位置]。

使用 HOST_REFERENCE 作为 NEW_HOST 的身份和造型参考，只提取 HOST_LOCKS：[填写新人物身份、发型、服装、麦克风、妆容和配饰]，不复制参考板布局。

使用 PRODUCT_REFERENCE 作为 NEW_PRODUCT 的产品身份参考，只提取 PRODUCT_LOCKS：[填写新产品包装、产品名、品牌、logo、主色、辅助色、数字、图案、材质和组件]，不复制产品参考板布局。

把 ORIGINAL_HOST 替换为 NEW_HOST，把 OLD_PRODUCT 替换为 NEW_PRODUCT。NEW_HOST 必须保持 TARGET_FRAME 相同姿势和手部动作，NEW_PRODUCT 必须匹配 OLD_PRODUCT 的位置、角度、比例、透视和手部接触关系。

移除所有字幕和覆盖文字，并自然补齐底部区域。

禁止：改变背景、改变机位、改变手部姿态、产品漂浮、手指穿帮、OLD_PRODUCT 残留、旧 logo 残留、产品打开、包装撕开、额外产品、字幕残留、海报风、棚拍风、参考板布局、额外文字、未经确认的卖点或合规敏感文案。
```

### English Prompt

```text
Edit TARGET_FRAME by replacing both the person and the product while preserving TARGET_FRAME's background, camera angle, lighting, hand pose, aspect ratio, framing, and realistic phone-video texture.

TARGET_FRAME is the only source for scene structure. Preserve SCENE_LOCKS: [insert background, furniture, props, phone-camera angle, exposure, shadows, composition, hand action, finger occlusion, and product placement].

Use HOST_REFERENCE as the NEW_HOST identity and styling reference only. Extract HOST_LOCKS: [insert new identity, hairstyle, clothing, microphone, makeup, and accessories]. Do not copy the reference-board layout.

Use PRODUCT_REFERENCE as the NEW_PRODUCT identity reference only. Extract PRODUCT_LOCKS: [insert package design, product name, brand, logo, primary color, secondary color, numbers, graphics, material, and components]. Do not copy the product-board layout.

Replace ORIGINAL_HOST with NEW_HOST, and replace OLD_PRODUCT with NEW_PRODUCT. NEW_HOST must keep the same pose and hand action from TARGET_FRAME. NEW_PRODUCT must match OLD_PRODUCT's position, angle, scale, perspective, and hand-contact relationship.

Remove all subtitles and overlay text, and naturally fill the lower area.

Forbidden: changing background, changing camera angle, changing hand pose, floating product, finger occlusion errors, OLD_PRODUCT residue, old logo residue, opened product, torn packaging, extra product, subtitle remnants, poster look, studio look, reference-board layout, extra text, unverified selling points, or regulatory-sensitive copy.
```

## 十二、批量帧一致性规则

用于一次替换多个连续帧。

```text
Batch consistency rules:
- Use the exact same HOST_REFERENCE identity across all frames.
- Use the exact same PRODUCT_REFERENCE packaging across all frames.
- Preserve each frame's original pose, hand position, product angle, background, lighting, and camera framing.
- Do not average multiple frames into one pose.
- Do not change the face from frame to frame.
- Do not change clothing color from frame to frame.
- Do not change product size, color, label direction, component count impression, or key logo placement from frame to frame.
- Remove subtitles consistently across all frames.
```

中文：

```text
批量一致性规则：
- 所有帧使用完全相同的 HOST_REFERENCE 人物身份。
- 所有帧使用完全相同的 PRODUCT_REFERENCE 产品包装身份。
- 每一帧都保留原本的姿势、手部位置、产品角度、背景、灯光和机位。
- 不要把多帧平均成同一个姿势。
- 不要让脸在不同帧之间变化。
- 不要让服装颜色在不同帧之间变化。
- 不要让产品大小、颜色、标签方向、组件数量印象或关键 logo 位置在不同帧之间变化。
- 所有帧都一致移除字幕。
```

## 十三、质检清单

### 13.0 StoryBoard / Task 范式继承踩坑

当当前任务来自 StoryBoard 聚合，且需要参考旧 Task 的提示词经验时，旧 Task 只能提供**提示词范式**，不能自动成为图片参考图。

正确做法：

- `TARGET_FRAME` 仍然使用当前 Task / 当前 Scene 的原有图片。
- `HOST_REFERENCE` 仍然使用当前 Task 的人物一致性参考图。
- `PRODUCT_REFERENCE` 仍然使用当前 Task 的产品一致性参考图。
- 旧 Task 只提供分段结构、优先级写法、替换方法写法、负面约束写法和质检标准。
- 最终提示词必须明确写出：`Use TARGET_FRAME as the base scene / editable scene frame`，只替换人物和产品，不重建房间、机位、手势和产品位置。
- 禁止把旧 Task 的成功图当成当前 Scene 的视觉参考，除非用户明确要求。

常见失败：

- 只把 `TARGET_FRAME`、`HOST_REFERENCE`、`PRODUCT_REFERENCE` 写成普通参考图，模型会重新创作一张近似照片，导致房间、脸、手势和产品位置整体漂移。
- `Product locks` 只写“绿色盒子、条包、15倍、UP”等泛化描述，模型会生成相似但错误的新包装。
- 使用 `can hold`、`where visible`、`or another visible face` 这类松口表达，会让模型自由重排产品和手部关系。

强制修正：

- 对当前 Task 的目标图使用“编辑底图”语言：`Use TARGET_FRAME as the base scene. Preserve camera angle, room geometry, head placement, shoulder crop, hand positions, product positions, scale, perspective, occlusion, and shadows.`
- 对产品使用“复制包装身份”语言：`Match PRODUCT_REFERENCE package identity exactly: brand/logo area, Chinese title hierarchy, 15倍 badge, UP graphic, silver-green box material, green sachet shape, 12g marking, label direction, and component relationship.`
- 对人物使用“复制身份造型”语言：`Match HOST_REFERENCE identity and styling exactly; do not inherit the original host identity from TARGET_FRAME.`
- 对输出使用硬淘汰语言：`If the room, framing, product position, product package identity, or hand occlusion is redesigned, the result is invalid.`

### 13.1 `HOST_REFERENCE` 检查

- 新人物和原人物明显不同。
- 有正面、侧面、背面三视图。
- 有和目标画面对位的口播姿势小窗。
- 脸型、发型、服装、配饰、麦克风在所有视图中一致。
- 没有原人物的脸、发型、服装、配饰和标志性特征。
- 不是明星脸、不是塑料皮肤、不是动漫/插画。

### 13.2 `PRODUCT_REFERENCE` 检查

- 产品是 `NEW_PRODUCT`，不是泛化同类产品。
- 产品名、品牌、logo、主色、辅助色、关键数字、图案和包装形状正确。
- 关键配件或组件完整。
- 没有 `OLD_PRODUCT` 的颜色、logo、文字、配件或包装形态残留。
- 没有未经确认的卖点文案、功效承诺、二维码、无关品牌。
- 多角度包装是同一产品，不是多个不同包装。

### 13.3 替换帧检查

- 仍然是原始手机口播画面，不是海报/参考板。
- `SCENE_LOCKS` 中的背景、家具、机位、光线、画幅比例保留。
- 人物是 `NEW_HOST` 身份，且多帧一致。
- 产品是 `NEW_PRODUCT` 身份，且多帧一致。
- 产品在手中，手指遮挡和接触阴影自然。
- 产品没有漂浮、没有穿手、没有被手指错误覆盖。
- 字幕和覆盖文字已移除。
- 没有额外文字、水印、UI。
- 没有产品打开、拆包、撕袋或旧产品残留，除非任务明确要求。
- 色彩、曝光、压缩感和目标帧匹配。

## 十四、硬淘汰规则

任何结果出现以下问题，直接重跑：

- 背景被替换。
- 人物不像 `HOST_REFERENCE`。
- 人物仍像 `ORIGINAL_HOST`。
- 产品不是 `PRODUCT_REFERENCE` / `NEW_PRODUCT`。
- 产品变成普通同类包装。
- `OLD_PRODUCT` 的颜色、logo、文字、包装或配件残留。
- 手指畸形、额外手指、产品漂浮。
- 产品打开、拆包、撕袋，除非任务明确要求。
- 字幕残留。
- 出现新增屏幕文字、水印、logo。
- 输出像参考板、海报或棚拍，不像真实视频帧。
- 多帧之间人物脸、衣服颜色或产品包装不一致。

## 十五、推荐文件命名

```text
HOST_REFERENCE.png                              # 标准人物模板
PRODUCT_REFERENCE.png                           # 标准产品模板
frame_001_host.png                              # Pass 1 人物替换中间图
frame_001_product_replaced.png                  # Pass 2 产品替换结果
frame_001_final_no_subtitle.png                 # Pass 3 去字幕最终图
frame_001.png / frame_002.png / frame_003.png   # 视频模型使用的最终序列图
```

## 十六、最终建议

1. 不要直接用视频模型替换人物和产品，先用 GPT 图像模型把关键帧做稳定。
2. 不要只生成单张人物头像，必须生成人物参考板和对位口播姿势小窗。
3. 不要只生成单个产品正面，必须生成产品多角度参考板。
4. 替换目标帧时优先三步法：先人物，后产品，再去字幕。
5. 图像阶段就要把产品“完整闭合、在手中、关键标识可见”固定住，减少后续视频模型放下产品、换产品或拆产品的概率。
6. 所有最终视频参考图必须无字幕、无覆盖文字、无水印。
7. 图像阶段不涉及音频；如果后续生成视频，音频只能有人声口播，不能有背景音乐、配乐、环境音乐或音效。
8. 所有具体产品和人物信息都应该写在 `HOST_LOCKS`、`PRODUCT_LOCKS`、`SCENE_LOCKS` 和 `NEGATIVE_LOCKS` 中，不要把单个案例的颜色、品牌、家具或服装写死在通用提示词里。
