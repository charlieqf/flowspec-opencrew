# 05_02 视频 SDR2V 提示词模板

适用范围：Analysis_V1 通过 OpenRouter Max SD 2 / `bytedance/seedance-2.0` 生成的非空镜人物口播分段，输入包含首帧身份图片和仅用于表情参考的视频。

中文维护内容不会送入模型；`OPENCREW:*` 块由 `video_openrouter.py` 读取并进入模型调用。代码标记、模型名和变量名保持原样。

## 中文维护版

Max SD 2 参考生视频任务：首帧图片是人物身份、脸部外观、发型、服装、身体、背景、灯光、构图和镜头的唯一视觉锚点。参考视频只允许提供参考人物的表情和微表情，包括情绪、强度、表情起落节奏，以及眼神、眼睑、眉部、面颊和嘴角的细微表情变化；除此之外不得参考或复制参考视频的任何内容。

人物使用自然、清晰的普通话中文说出给定台词，中文口型只由台词驱动，不得复制参考视频的嘴型、声音、音色、语言、口音或说话节奏。人物保持自然呼吸、非机械眨眼、细微视线变化，以及低幅度、连贯、克制的头部和肩颈微动。所有动作必须符合真实人体结构、关节活动范围、重力、平衡、惯性、重量、接触关系和动作连续性，不得出现任何违背物理常识或人体生物力学的动作。

参考视频人脸上的红色矩形线框、红线、网格或追踪标记仅是输入侧临时标记，不属于人物或场景。生成时必须完全忽略并去除，最终视频每一帧都必须是干净、完整、无遮挡的自然人脸，不得残留红线框、红色网格、追踪框、扫描线或红色伪影。画面全程保持纯净无文字；中文对白只作为声音存在，绝不生成字幕、标题、说明文字、气泡文字、Logo 或水印。

提示词长度预算：本模板在 OpenCrew 内按最多 1000 个字符执行硬限制。固定模板最多占用 700 个字符，至少为 `{{dialogue_text}}` 预留 300 个字符。最终提示词超限时必须报错，不得静默截断、改写或遗漏台词。

### 中文踩坑记录（只追加）

- 2026-07-14 route：仅用于 Analysis_V1 的 Max SD 2 非空镜口播；空镜仍使用通用 `Video_OpenRouter.md`。
- 2026-07-14 facial-expression-only contract：参考视频只提供人物表情和微表情；不得提供嘴型、声音、身份、外观、动作、姿态、手势、身体节奏、镜头、构图、剪辑、背景或场景。
- 2026-07-14 red-frame removal：参考视频人脸上的红色线框、网格和追踪标记仅是输入侧临时标记，输出每一帧都必须完全去除且不得残留红色伪影。
- 2026-07-14 physics contract：人物动作必须低幅、自然、连续，符合人体结构、关节范围、重力、平衡、惯性、重量和接触关系，禁止任何违背物理常识的动作。
- 2026-07-14 Mandarin and text-free contract：人物只使用自然普通话中文说出台词，口型由中文台词驱动；画面绝不出现字幕或任何可见文字。
- 2026-07-14 prompt budget：模型调用提示词全部使用中文，最终最多 500 个字符，固定模板最多 400 个字符，至少为台词预留 100 个字符；超限报错且不得截断台词。
- 2026-07-15 prompt budget override：覆盖 2026-07-14 的旧预算；Analysis_V1 与 TalkingHead_V1 的 SDR2V 统一为最终最多 1000 个字符、固定模板最多 700 个字符、至少为台词预留 300 个字符。
- 2026-07-14 fallback：分段没有显式参考视频时，使用同目录 `Video_SDR2V.mp4`。

## 中文模型调用版

<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_START -->
生成约{{duration_seconds}}秒9:16写实口播。首帧唯一确定人物、场景外观和镜头，全程一致。参考视频只提供表情、微表情的情绪、强度、节奏及眼眉、眼睑、面颊、嘴角变化，其他均不参考。呼吸、眨眼、视线和头肩微动自然，动作符合人体结构、重力、平衡、惯性及接触关系。脸上红线框、网格和追踪框仅为输入标记，输出每帧人脸干净完整、无标记残影。画面无文字。
<!-- OPENCREW:VIDEO_OPENROUTER_POSITIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_START -->
只用自然普通话说：{ {{dialogue_text}} }。台词驱动口型和停顿；不得增删改译、重复演唱或显示文字。
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_STANDARD_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_START -->
本模板不用于纯产品空镜；如误入此分支，仍须保留首帧人物和场景，并遵守仅参考表情、去除红框、动作符合物理规律、普通话声音和全程无文字的约束。
<!-- OPENCREW:VIDEO_OPENROUTER_DIALOGUE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_REFERENCE_ROLES_START -->
{{reference_role_contract}}。目标身份定人物，连续首帧定场景。
<!-- OPENCREW:VIDEO_OPENROUTER_REFERENCE_ROLES_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_POSITIVE_START -->
清除{{gridded_input_scope}}的红网格。
<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_POSITIVE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_START -->
禁止：参考视频身份外观、嘴型声音、动作姿态手势、镜头场景迁移；字幕文字、标志水印；红框网格追踪框、遮脸模糊、红残影；脸漂、僵硬夸张、机械眨眼、口型错位；肢体畸形反折、漂浮瞬移、脚滑失重、穿插、违反重力物理；抖闪、跳切重置、低画质。
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_BASE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_START -->
纯产品广告空镜
<!-- OPENCREW:VIDEO_OPENROUTER_NEGATIVE_CUTAWAY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_NEGATIVE_START -->
红网格红框残留
<!-- OPENCREW:VIDEO_OPENROUTER_PRIVACY_GRID_NEGATIVE_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_START -->
逐帧执行。
<!-- OPENCREW:VIDEO_OPENROUTER_PITFALLS_APPEND_ONLY_END -->

<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_START -->
{{positive_prompt}}

负向提示：
{{negative_prompt}}
<!-- OPENCREW:VIDEO_OPENROUTER_PROMPT_END -->
