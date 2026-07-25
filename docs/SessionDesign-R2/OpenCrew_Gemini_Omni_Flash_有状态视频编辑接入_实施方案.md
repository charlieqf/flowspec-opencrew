# OpenCrew Gemini Omni Flash 有状态视频编辑接入实施方案

- 文档状态：已实施，并在 macmini-4 完成真实付费验收
- 编写日期：2026-07-22
- 复审修订：2026-07-22
- 目标模型：gemini-omni-flash-preview
- 接入页面：视频生成、视频智能体
- 核心能力：视频生成、上传视频编辑、基于上一轮结果的有状态连续编辑

## 1. 结论摘要

现有 Gemini Key 已在 2026-07-23 的直接供应商链和 OpenCrew 真实浏览器产品链中完成 Gemini Omni Flash Preview 付费验证；非付费模型目录检查仍只用于确认模型可见性，不能替代 Interactions 兼容性验证。

2026-07-22 至 2026-07-23 已在 macmini-4 本机测试环境完成四级验证：

1. 使用现有密钥读取 gemini-omni-flash-preview 模型信息，返回 HTTP 200。
2. 使用同一密钥发起受预算保护的两轮直接 Interactions 链，首轮和连续编辑均返回 completed、有效 MP4，并删除两轮云端状态。
3. 通过真实 Chromium、OpenCrew SSE、后端持久化、媒体清洗、实际时长计量和本地素材区完成两轮产品链；验证父子版本关系后，从界面触发云端清理并确认本地 MP4 继续保留。
4. 通过真实 Chromium 从 OpenCrew 素材区选择一段本地视频，执行“蓝色圆形改为红色圆形”的上传视频编辑；Files API、Interaction、URI 下载、清洗、实际时长计量、历史展示和云端清理均通过。

付费“新建、继续编辑、上传视频编辑”验收已经执行。直接探针和浏览器脚本均有必须显式提供的调用次数、总秒数与美元预算硬门禁，默认测试不会触发付费生成。最终浏览器产品验收共生成 9.024 秒视频，名义视频输出费用约 US$0.90；整个 API 适配与产品调试窗口共产生约 21 秒供应商视频输出，名义约 US$2.10，另有少量输入 token。

为避免 Preview API 的关键事实只存在于本文档，仓库新增以下分级证据：

- backend/tests/integration/gemini_omni_live_probe.py：默认只做模型发现；付费生成必须显式开启环境开关和预算上限。
- backend/tests/artifacts/gemini_omni_probe_2026-07-22.json：保存模型发现的在线结果，以及历史付费调用的脱敏重建投影、视频字节数和哈希，不保存 Key、interaction_id 或视频 base64。
- backend/tests/contracts/test_gemini_omni_probe_artifact_contract.py：验证证据结构、已观察接口约束和官方 Preview 能力快照。

阶段零必须先运行该契约测试和非付费模型发现，再开始适配器开发。要特别注意：模型发现只能证明 Key 能读取该模型的目录元数据，不能证明 Interactions 端点兼容；Interactions 兼容性只有受预算保护的付费探针才能在线回归。Preview 文档或 API 行为变化时，先更新探针证据和契约，不能等线上生成失败后再发现。

### 1.1 2026-07-23 实施后在线验收更新

用户明确授权在 macmini-4 使用现有 Key 完成真实测试后，受门禁保护的两轮状态链已在线通过。最终证据在 `docs/SessionDesign-R2/acceptance/2026-07-22/gemini-omni-paid-chain-20260723-v4.json`：首轮和连续编辑均返回 HTTP 200、`completed` 和一个有效 MP4，第二轮通过 `previous_interaction_id` 继承首轮上下文，两轮云端 Interaction 均已删除。

真实 OpenCrew 浏览器产品链也已通过，脱敏证据在 `docs/SessionDesign-R2/acceptance/2026-07-22/gemini-omni-paid-browser-20260723-v2.json`。它覆盖付费确认、SSE、两层幂等、turn/thread 父子关系、两个 3.008 秒本地 MP4、按实际时长计量、公开字段隔离和两轮云端状态删除；浏览器响应与证据不含原始 Interaction ID。

真实上传视频编辑也已通过，脱敏证据在 `docs/SessionDesign-R2/acceptance/2026-07-22/gemini-omni-paid-upload-browser-20260723-v9.json`。它覆盖素材选择、付费确认、Files API 上传、编辑 Interaction、3.008 秒本地 MP4、实际时长计量、历史展示和云端状态删除；供应商标识仍只保存在后端。

在线验收同时发现 2026-05-20 Interactions API 修订后的四类破坏性变化，已同步修正正式适配器、恢复路径、模型配置、探针和契约测试：

- 新建视频的画幅、交付方式和时长已从 `generation_config.video_config` 移到顶层 `response_format`；`video_config` 只保留新建任务类型。
- 当前模型输出范围为 3–10 秒，1 秒请求会在生成前被拒绝。OpenCrew 首期固定使用最短 3 秒，历史 1 秒 pending turn 恢复时自动规范为 3 秒。
- 连续编辑携带 `previous_interaction_id` 时不能同时发送 `video_config.task`；当前 API 根据父 Interaction 推断编辑任务，因此连续轮次只重发输出格式，不重发 task。
- 上传视频编辑使用 Files API 的 ACTIVE URI 作为 `document` 输入，并由输入视频推断编辑任务；它不能再显式发送 task、aspect ratio 或 duration，输出画幅和时长继承输入视频。

最终链的两段输出均经 ffprobe 验证为 1280×720、24fps、H.264 + AAC、3.008 秒。抽帧确认首轮蓝色圆形在第二轮准确变为绿色，背景与构图保持一致。

首次真实产品链还发现一个仅在供应商返回真实 MP4 后才会出现的落盘缺陷：临时文件以 `.provider.tmp` 结束，使共享 ffmpeg 清洗器无法推断输出格式。现已改为 `.provider.mp4`，并用真实输出回归清洗成功。清洗、下载或校验异常也已统一映射为稳定公开错误码，避免本机路径或原始 ffmpeg 输出进入 SSE 与工作区历史。

## 2. Key 实测记录

### 2.1 测试环境

- 主机：macmini-4 测试环境
- 后端健康检查：通过
- 已启用的视频供应商配置：gemini
- 当前模型配置：veo-3.1-lite-generate-preview
- 密钥来源：本地 Secret Store 中的 video_gemini_key
- 安全处理：测试过程和本文档均未输出密钥明文

测试环境的供应商密钥与生产配置共用来源，因此本次真实生成可能进入同一 Gemini 账单。未在 macmini-1 生产环境进行实验。

### 2.2 测试结果

| 检查项 | 请求 | 结果 |
| --- | --- | --- |
| 模型访问 | GET /v1beta/models/gemini-omni-flash-preview | HTTP 200 |
| 返回模型 | models/gemini-omni-flash-preview | 通过 |
| 展示名称 | Gemini Omni Flash Preview | 通过 |
| 首次参数保护测试 | URI 视频交付并设置 store=false | HTTP 400，明确要求 store=true |
| 早期历史最小生成（不可作为当前参数契约重放） | 请求 1 秒、16:9、简单蓝色圆形动画、store=false | 历史脱敏投影为 HTTP 200，completed；当前 API 最短时长已改为 3 秒 |
| 当前直接两轮状态链 | 2 × 3 秒、16:9、store=true，第二轮使用 previous_interaction_id | 两轮 HTTP 200、completed，云状态删除成功 |
| 当前 OpenCrew 浏览器产品链 | 新建蓝色圆形，再继续编辑为绿色 | 两个本地 MP4、父子 turn/计量/清理全部通过 |
| 当前 OpenCrew 上传视频编辑 | 选择本地蓝色圆形视频，编辑为红色 | Files API、编辑 Interaction、本地 MP4、计量和清理全部通过 |
| 视频结果 | 内联 MP4 | 1 个，有效载荷 767,294 字节 |
| 结果校验 | SHA-256 | cbcf2b498637aa20faa34ebb112cafa889642c68b34fc869be48fd80657cec87 |
| 状态链 ID | store=false | 按预期未返回可继续使用的 interaction_id |

首个 HTTP 400 是接口约束验证，不是密钥或权限失败：当 response_format 选择视频 URI 交付时，API 要求 store=true。正式适配器必须把这条约束固化为参数校验。

以上记录已整理到 backend/tests/artifacts/gemini_omni_probe_2026-07-22.json。需要如实说明：首次付费成功响应的原始 body 当时没有落盘，该 artifact 是根据同日脱敏终端输出重建的白名单投影，不冒充原始响应；视频主体只保留长度和 SHA-256。新增探针会让后续运行直接生成脱敏 artifact。由于当前 Key 与生产共享，在测试/生产 Key 拆分前不为补原始 body 再次发起付费生成。

### 2.3 能确认与不能确认的事项

已确认：

- 可在线重复执行的模型发现返回 HTTP 200，证明 Key 有权读取目标 Preview 模型元数据。
- 该模型目录响应的 supportedGenerationMethods 只有 generateContent 和 countTokens，没有通告 interactions；因此模型发现结果不证明 `/v1beta/interactions` 或 previous_interaction_id 可用。
- 同日一次历史付费调用完成了真实推理并返回视频，说明当时的 Key、网络和付费/配额条件可以调用 Interactions；但其原始成功响应未保留，现有 artifact 是脱敏终端输出的重建投影，证据强度低于可重放的在线探针。
- 官方 Preview 文档说明目标接口、任务和状态参数，但官方说明不能替代当前 Key 的在线兼容性验证。

已在 2026-07-23 实现后验收：

- store=true 能取得 interaction_id，并且该标识只保存在后端边界内。
- previous_interaction_id 的第二轮视频编辑在直接探针和真实浏览器产品链中均通过。
- 用户上传视频的 Files API 全流程及单视频编辑已在真实浏览器产品链中通过。
- 本地 MP4 落盘、实际时长计量、版本父子关系、云状态删除与公开字段隔离通过。

仍需在扩大上线范围前验证：客户真实长视频或大文件 URI、3 秒以上新建视频、不同输入画幅/时长的继承结果、配额/区域策略，以及生产并发容量。当前验收使用无客户数据的 3 秒合成视频，并未在 macmini-1 执行实验。

因此结论已从“Key 和历史调用证据足以支持开始接入”升级为“新建、连续编辑和上传视频编辑均已在 macmini-4 的 OpenCrew 产品路径中实现并通过真实付费验收”。非付费检查仍不能守住 Interactions 兼容性；后续应使用独立测试 Key，并仅在明确授权与预算门禁下重复付费探针。

## 3. 官方能力与边界

依据 Google 官方的 [Gemini Omni 文档](https://ai.google.dev/gemini-api/docs/omni?hl=zh-cn) 和 [Interactions API 概览](https://ai.google.dev/gemini-api/docs/interactions-overview)：

- 创建入口为 POST https://generativelanguage.googleapis.com/v1beta/interactions。
- 视频任务包括 text_to_video、image_to_video、reference_to_video 和 edit。
- 连续编辑通过 previous_interaction_id 引用上一轮 Interaction。
- 每一轮编辑都会生成一段新视频，不是在原文件上进行无损局部修改。
- 用户上传视频需要先经 Files API 上传，并等待文件状态为 ACTIVE。
- 当视频输出使用 URI 交付时必须启用 store。
- 新建和连续编辑都应在 `response_format` 明确发送输出参数；task 只用于新建，连续编辑不得重发 task。
- 当前模型卡声明输出视频为 3–10 秒、720p、24fps；OpenCrew 首期将产品时长固定为最短 3 秒。
- 上传视频编辑把 Files API URI 作为 `document` 输入，只发送视频 URI 交付方式；API 从输入视频继承画幅和时长，并拒绝 task、aspect ratio 或 duration。

这些外部事实不是永久常量。backend/tests/artifacts/gemini_omni_probe_2026-07-22.json 保存带 retrieval date 的 official_contract_snapshot，契约测试逐项断言任务枚举、有状态参数、存储关系、输入限制、区域限制和 SynthID 口径。每次 Preview 升级或上线前重新运行模型发现并人工对照官方文档；快照变化必须先评审，再修改适配器。

根据 [Gemini API 定价](https://ai.google.dev/gemini-api/docs/pricing)，该模型属于付费能力，视频输出按生成量计费。每次“继续编辑”都是一次新的完整生成，产品界面和预算统计不能把它表现成免费修改。

已知产品边界应在界面中明确：

- 不支持把音频作为参考输入。
- 不支持多段视频同时作为参考。
- 不支持视频延长、插帧或语音编辑。
- 上传视频编辑在部分区域存在可用性限制。
- Preview 模型、参数和价格可能调整，必须通过模型目录和受控冒烟测试持续确认。

## 4. 现有架构与差距

### 4.1 当前视频生成

当前 Gemini 视频生成分支在：

- backend/opcrew_backend/koubo/koubo_storyboard/asset_video_generation_services.py

它按 Veo 的 predictLongRunning 接口实现。模型目录在：

- ModelConfig/backend/opcrew_model_config/media_model_config.py

当前前端的视频生成和视频智能体最终都调用现有 generate_asset_library_video 流程，相关页面位于：

- frontend/src/modules/koubo/UploadAssetLibrary/components/VideoWorkspaceLibrary.jsx
- frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx
- frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx

### 4.2 关键差距

Omni 不能只作为另一个 Veo 模型名塞入现有分支，原因包括：

- API 从 predictLongRunning 变为 Interactions。
- 请求体采用 task 和视频 response_format。
- 输出可能是内联数据，也可能是 Files URI。
- 有状态编辑依赖 interaction_id 和 previous_interaction_id。
- 需要持久化版本关系、过期时间、远端删除状态和本地视频副本。
- 视频智能体的聊天状态不等同于供应商视频状态，二者必须显式关联。

因此需要一个独立的 Gemini Omni 供应商适配器，同时复用现有素材落盘、SSE 进度、权限校验和计费记录。

## 5. 产品设计

### 5.1 视频生成页面

当用户选择支持 Omni 的模型时，提供三种入口：

1. 新建视频：文本生成视频，或图片生成视频。
2. 编辑上传视频：选择一段已有/上传视频，输入编辑指令。
3. 继续编辑：从某个 Omni 生成结果继续修改。

每次成功结果都作为一个新版本保存，原视频不被覆盖。结果卡片显示：

- 版本编号与父版本。
- 本轮编辑指令。
- 创建时间、时长、画幅和状态。
- “继续编辑”“从此版本创建分支”“设为当前结果”操作。
- 状态上下文可用、即将过期、已过期或已清除的提示。

用户可以从历史版本分支。分支本质上是把该版本对应的 interaction_id 作为下一轮 previous_interaction_id，而不是永远从最新版本继续。

### 5.2 视频智能体页面

视频智能体保持自然语言交互，但增加明确的供应商状态规则：

- 同一聊天线程第一次调用 Omni 时创建一个视频交互链。
- 后续“把背景改成夜晚”等指令，默认引用该线程最近一次成功的视频 Interaction。
- 生成失败、被取消或本地保存失败时，不推进链头。
- 用户切换模型、选择“新建视频”或新建聊天时，启动新链。
- 用户从历史结果继续时，创建分支并更新当前链头。
- 智能体文本聊天记录与 Omni interaction_id 分开存储，通过 thread_id 和 turn_id 关联。

智能体不能仅根据“上一条聊天消息”猜测视频状态。服务端必须验证上一版本属于当前任务和当前用户，并由服务端解析 previous_interaction_id，前端不直接提交原始供应商 ID。

### 5.3 用户提示与隐私

启用有状态编辑意味着输入和输出会由 Google 保存一段时间以支持后续轮次。第一次启用时应显示简明提示：

- 素材会发送给 Gemini。
- 为支持连续编辑，供应商将临时保存 Interaction。
- 每次编辑会生成新视频并产生费用。
- 用户可以清除云端上下文；清除后仍保留本地已生成素材，但不能继续原状态链。

任务删除、线程删除和手动“清除云端上下文”应触发远端 Interaction 删除任务。删除失败要记录可重试状态，但不能阻止本地任务删除。

## 6. 模型配置

在媒体模型目录新增独立模型项：

~~~text
model_option(
  "gemini-omni-flash-preview",
  label="内部配置使用的展示名",
  input_modes=["text", "first_frame", "multi_reference", "video_reference"],
  tasks=["text_to_video", "image_to_video", "reference_to_video", "edit"],
  capabilities=["Conversational Edit", "Video Input", "Multi-image Reference"],
  stateful_edit=True,
  provider_state="interaction",
  supports_video_input=True,
  supports_audio_reference=False,
  output_delivery=["inline", "uri"],
  allowed_aspect_ratios=["16:9", "9:16"],
)
~~~

这里必须遵循 media_model_config.py 的 model_option(model, label, **extra) 实际结构：

- capabilities 已被现有目录用于面向用户的展示徽章字符串，不能拿来承载机器任务枚举。
- tasks 是新的机器语义字段，videoModelCapabilities.js 优先读取 tasks，再结合 input_modes、reference_images 和 reference_videos。
- stateful_edit、provider_state 等保持独立结构化字段，不靠模型名猜测。

模型别名仍通过现有连接配置对用户展示。公开 API、前端事件和错误信息不返回内部供应商密钥、原始 interaction_id，且遵循现有模型名称屏蔽规则。

视频智能体的别名解析还经过 media_model_config.py 中的 canonical_agent_model_alias_target 硬编码分派。实施时先确定新的公开别名，再显式增加该别名到 gemini/gemini-omni-flash-preview 的映射，并为直连视频生成和视频智能体各加一个解析契约测试；不能只假设默认 provider/model 回落始终正确。

Preview 模型应有单独服务端开关 OPENCREW_GEMINI_OMNI_ENABLED。模型配置启用与服务端开关同时满足时才在页面显示，便于快速止损。

## 7. 后端设计

### 7.1 独立适配器

建议新增：

- backend/opcrew_backend/koubo/koubo_storyboard/gemini_omni_video_services.py

职责：

- 构建 Interactions API 请求。
- 上传输入视频并轮询 Files API 到 ACTIVE。
- 创建并轮询后台 Interaction。
- 解析内联视频或 URI 视频结果。
- 下载到任务目录并验证 MP4。
- 返回统一的生成结果和内部状态元数据。
- 将 Google 错误映射为稳定的 OpenCrew 错误码。

当前 koubo_storyboard 采用扁平服务文件惯例，例如 asset_search_providers.py 和 media_tts_provider_services.py，本期遵循该结构，不单独创建只有一个实现的 providers 子包。asset_video_generation_services.py 只负责模型分派和复用现有素材入库流程；Veo 本期不迁移，避免出现半完成的目录重组。

### 7.2 调用模式

正式环境建议使用 store=true 和后台 Interaction：

1. 校验任务、用户、模型能力和输入文件。
2. 如有上传视频，调用 Files API，等待 ACTIVE。
3. 创建 Interaction 并尽早持久化供应商 interaction_id 和 pending 状态。
4. 轮询 Interaction 至 completed、failed 或超时。
5. 获取视频 URI 或解析内联视频。
6. 将视频保存到 OpenCrew 任务目录，执行 ffprobe 验证。
7. 写入素材库元数据。
8. 仅在以上步骤全部成功后推进线程 head_turn_id。

使用 URI 交付时强制 store=true。开发代码应在发请求前拒绝 store=false 加 URI 的组合，避免重复产生无效网络请求。

### 7.3 不同任务的请求

新建文本视频：

~~~json
{
  "model": "gemini-omni-flash-preview",
  "input": "Create ...",
  "response_format": {
    "type": "video",
    "delivery": "uri",
    "aspect_ratio": "16:9",
    "duration": "3s"
  },
  "generation_config": {
    "video_config": {
      "task": "text_to_video"
    }
  },
  "store": true,
  "background": true
}
~~~

继续编辑：

~~~json
{
  "model": "gemini-omni-flash-preview",
  "input": "Change the background to night ...",
  "previous_interaction_id": "由服务端从 parent_turn_id 解析",
  "response_format": {
    "type": "video",
    "delivery": "uri",
    "aspect_ratio": "16:9",
    "duration": "3s"
  },
  "store": true,
  "background": true
}
~~~

上传视频编辑：

~~~json
{
  "model": "gemini-omni-flash-preview",
  "input": [
    {
      "type": "document",
      "uri": "Files API 返回的 ACTIVE URI",
      "mime_type": "video/mp4"
    },
    {
      "type": "text",
      "text": "Change the blue circle to red ..."
    }
  ],
  "response_format": {
    "type": "video",
    "delivery": "uri"
  },
  "store": true,
  "background": true
}
~~~

示例只表达关键结构。正式请求按操作区分：新建显式发送 `video_config.task` 及画幅/时长；连续编辑发送 `previous_interaction_id` 并重发画幅/时长，但不能发送 task；上传视频编辑以 `document` 传 Files API URI，不能发送 task、画幅或时长，由输入视频决定这些属性。差异由契约测试固化，并以当前正式 API schema 为准。

### 7.4 OpenCrew 接口扩展

在现有视频生成请求上增加：

~~~text
client_action_id: 前端为一次用户付费动作生成的 UUID，必填
operation: generate | edit | continue
stateful: boolean
thread_id: OpenCrew 内部线程 ID，可选
parent_turn_id: OpenCrew 内部版本 ID，可选
source_video_asset_id: 上传视频对应的素材 ID，可选
~~~

不接受客户端直接传 previous_interaction_id。服务端按 task_id、用户权限和 parent_turn_id 查找并验证，防止跨任务引用供应商状态。

client_action_id 是供应商调用/轮次层的幂等键：

- 由前端在用户点击“生成/继续编辑”时用 crypto.randomUUID() 生成。
- SSE 断线重连、页面重试和网络重放必须复用同一个 client_action_id，只有用户明确发起新的付费动作才生成新值。
- 服务端按 task_id、调用者、operation、client_action_id 建唯一约束，并把它持久化到 turn。
- 相同键命中 pending 时返回同一 turn 的进度，命中 completed 时返回原结果，命中供应商结果不明的状态时只做查询/对账，禁止再创建 Interaction。
- 去重记录与任务同生命周期，不采用容易让旧请求再次付费的短时间窗口。

它不能命名为 request_id，也不能直接替代现有计费幂等键。计费层继续复用 usage_metering.py 的 stable_usage_request_id()：在 turn 事务落库后，服务端以稳定的 task_id、turn_id 和 operation 生成 usage_request_id，并将其传给 record_storyboard_usage。后者按现有规则组成 local_usage_log.idempotency_key，继续由 ux_local_usage_log_idempotency_key 唯一索引防止本地重复记账。

~~~text
usage_request_id = stable_usage_request_id(
  "koubo_gemini_omni_video", task_id, turn_id, operation
)
local_usage_log.idempotency_key =
  "koubo:{task_id}:{attempt_id}:{step_id}:{usage_request_id}"
~~~

两层键的映射关系必须一对一持久化在 turn：

- client_action_id：防止同一次用户动作重复调用收费供应商。
- usage_request_id：防止同一个已确定 turn 被重复写入本地用量账。
- local_usage_id：可空；指向实际用量记录，便于付费对账。

usage_request_id 必须从已落库且稳定的 turn_id 推导，不能依赖可能缺失或变化的 interaction_id、输出路径或前端随机值。相同 client_action_id 只能命中同一个 turn；该 turn 的 usage_request_id 也必须保持不变。

公开响应可增加：

~~~text
video_thread_id
video_turn_id
parent_turn_id
client_action_id
can_continue
provider_state_status
provider_state_expires_at
~~~

原始 interaction_id 只存在服务端内部元数据。

## 8. 状态持久化

### 8.1 权威存储选择

线程和轮次使用数据库作为唯一权威状态，不使用无锁 JSON 做读改写。新增：

- video_interaction_threads
- video_interaction_turns

SessionContext 的现有惯例是 SessionContext/VideosAgentSettings.json；如果需要方便排障，可生成只读投影 SessionContext/VideoInteractionThreads.json，但它不能参与并发判断或恢复决策。每个输出视频仍按现有 ASSET_VIDEOS_REL 在 SessionOutput/storyboard/assets/videos 下保存 MP4 和 sidecar。

线程表主要字段：

~~~text
thread_id
task_id
session_id
chat_session_id
model_alias
internal_provider
internal_model
head_turn_id
status
lease_token
lease_expires_at
row_version
created_at
updated_at
~~~

轮次表主要字段：

~~~text
turn_id
thread_id
task_id
actor_id
parent_turn_id
client_action_id
client_action_scope
usage_request_id
local_usage_id
interaction_id
operation
prompt
input_asset_id
output_asset_id
output_path
status
provider_request_status
provider_state_status
provider_state_expires_at
provider_expiry_source
delete_status
created_at
updated_at
~~~

数据库唯一约束至少包括：

- thread_id 主键。
- turn_id 主键。
- task_id、actor_id、operation、client_action_id 组合唯一，用于供应商调用去重。
- usage_request_id 唯一且可空；记账前写入，写入后不可修改。

interaction_id 不是 API Key，但仍属于内部供应商状态标识，不写入浏览器日志、公开 SSE、客户下载 sidecar 或普通素材元数据。

### 8.2 数据库迁移计划

本仓库的数据库变更不能只修改 SQLAlchemy schema。实施时必须同时完成：

1. 在 backend/opcrew_backend/db/schema.py 定义 video_interaction_threads、video_interaction_turns、外键、普通索引和上述唯一索引，使全新数据库直接具备完整结构。
2. 在 backend/opcrew_backend/db/migrations.py 新增 migration_0024_video_interaction_threads，并加入 MIGRATIONS 列表；如果合并前 0024 已被占用，则使用当时下一个连续编号，不能复用或插入旧编号。
3. 迁移一次性创建两张表及索引，并同时兼容当前支持的 PostgreSQL 与 SQLite；重复执行必须幂等，旧库升级不得影响现有视频和用量表。
4. 同步修改 backend/tests/contracts/test_migration_baseline_contract.py：断言 migration IDs 与 MIGRATIONS 顺序完全一致，断言新表字段、外键和唯一索引存在，并覆盖旧基线升级及第二次执行不重复变更。
5. 在数据库集成测试中验证 PostgreSQL 的行锁/租约竞争；SQLite 只用于结构、幂等和基础状态契约，不能用它代替生产并发语义验收。

功能回滚时不执行破坏性 down migration。关闭功能开关后保留两张表和历史轮次，便于审计、重试云端清理和后续恢复。

### 8.3 事务、租约与重启恢复

并发控制采用数据库行锁和可续租租约：

1. 创建轮次时在事务内锁定 thread 行，检查 head 和现有 lease。
2. 没有有效租约时写入 pending turn、lease_token 和 lease_expires_at，再提交事务。
3. worker 在轮询供应商时定期续租；租约时长由 OPENCREW_VIDEO_INTERACTION_LEASE_SECONDS 配置，默认 900 秒，续租间隔不超过 30 秒。
4. 同一线程存在未过期租约时拒绝第二个新 client_action_id，返回 video_stateful_edit_in_progress。
5. 供应商 interaction_id 一取得就写入 turn，不能等视频下载完成后才保存。
6. 本地 MP4 清洗、ffprobe、素材入库全部成功后，再在事务内比较 parent/head 和 row_version，推进 head_turn_id 并释放租约。
7. 失败或取消将 turn 标记为 failed/cancelled 并释放租约，不能推进 head。

服务重启后由恢复任务扫描 pending turn：

- 有 interaction_id：重新查询同一个 Interaction 并恢复下载，绝不新建收费请求。
- 没有 interaction_id 且供应商请求确定未发出：允许在原 client_action_id 下重新发起。
- 请求是否已被供应商接收无法确认：标记 provider_result_unknown，停止自动重试，等待人工对账。
- lease 已过期只是允许恢复 worker 接管，不等于允许创建新 Interaction。

这样既避免崩溃后永久锁死，也避免进程内 Lock 在重启后失效。

### 8.4 分支与幂等

每个新轮次包含 parent_turn_id 和 client_action_id。服务端创建时记录 expected head/version：

- 如果用户从当前 head 继续，成功后更新 head。
- 如果用户从历史版本继续，创建新分支，并在成功后把该分支设为当前 head。
- 同一个 client_action_id 重放时始终返回原 turn，不重复调用供应商；如果重放携带不同 operation 或不同输入摘要，返回幂等冲突，不能创建新 turn。
- 两个不同 client_action_id 同时争用一个线程时，只有取得数据库租约的请求可以继续。
- usage_request_id 只在 turn 已确定后由服务端稳定推导；同一 turn 重复执行记账时，由现有 local_usage_log 唯一索引返回原用量记录。

### 8.5 过期判断与回退

官方文档说明付费 Interaction 默认保留 55 天且可配置为 7、14、28 或 55 天，但没有保证每个响应都返回可直接持久化的 expires_at。因此 provider_state_expires_at 必须可空，并增加 provider_expiry_source：

- provider：供应商明确返回到期时间。
- configured_estimate：按已知项目保留策略估算，仅用于提示。
- unknown：没有可靠 TTL，界面显示“状态可能已过期”。

继续编辑前先调用 interactions.get 探测父 Interaction。探测成功才提交新的付费生成；404、已删除或无效状态直接进入过期回退，不以“试生成”充当探测。

当 Interaction 已过期、被删除或供应商返回 previous_interaction_id 无效时：

1. 不自动静默重试并产生未知费用。
2. 将该版本标记为 provider_state_status=expired。
3. 如果本地 MP4 仍存在，提示用户可“以上一版本视频重新开始编辑”。
4. 用户确认后重新上传本地 MP4，以新的 client_action_id 创建新链。

重新上传只能近似延续视觉内容，不能恢复供应商隐藏的历史上下文，界面必须明确说明。

## 9. 视频生成与视频智能体接入

### 9.1 直接视频生成

修改：

- frontend/src/modules/koubo/UploadAssetLibrary/components/VideoWorkspaceLibrary.jsx：增加新建、上传编辑、继续编辑和版本树入口。
- frontend/src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js：读取 tasks、stateful_edit、supports_video_input 等结构化能力，不把展示型 capabilities 当任务枚举。
- frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx：在任务上下文内恢复当前视频线程。

成功结果继续进入现有素材库和视频工作区，不创建只存在于聊天中的临时视频。

### 9.2 视频智能体

修改：

- VideoAgentPanel.jsx：展示当前视频版本、链状态和继续编辑提示。
- agent_chat_routes.py：把智能体生成意图映射为 operation 和 parent_turn_id。
- 现有 VIDEO_GENERATION_REQUEST 协议：增加内部状态字段，但对模型提示只暴露 OpenCrew turn_id，不暴露 Google interaction_id。

智能体生成成功事件应同时返回 video_turn_id。后续对话由后端将该 turn 解析为上一 Interaction。

## 10. 输出、校验和素材入库

### 10.1 输出处理

- 小结果允许解析内联 base64，但不得把完整数据写入日志。
- 生产默认使用 URI 交付，下载时使用现有密钥认证并限制重定向目标。
- 下载到临时文件后再原子移动到任务目录。
- 对 MIME、文件头、大小和 ffprobe 结果进行验证。
- 强制复用 asset_video_generation_services.py 已有的 download_video_binary 和 sanitize_video_output，最终由 services/media_sanitize.py 的 sanitize_video_file_metadata 去除 MP4 容器元数据；不得为 Omni 另写一条绕过清洗的下载落盘路径。
- 清洗后扫描可见容器 tag、文件名和公开 sidecar，确认不含 provider、内部模型名、供应商 URI 或 interaction_id。
- 失败或取消时清理未入库的临时文件。

元数据清洗不能移除 Gemini 生成视频中的 SynthID 隐形水印。官方明确所有 Omni 生成视频都含可检测的 SynthID；产品口径应是“清除可见容器元数据和内部标识，但保留供应商强制的来源水印”。这属于 docs/model_leakage_audit_2026-07-09.md 已列明、需要业主知情接受的残留风险，不能在验收中宣称供应商不可识别。

### 10.2 素材元数据

本地生成视频继续写入现有资产元数据，并补充内部 provenance：

- operation
- video_thread_id
- video_turn_id
- parent_turn_id
- source_asset_id
- stateful
- usage 或可得的计费信息

面向用户的公开元数据可以显示“有状态编辑版本”，但不显示 provider、内部模型名或原始 Interaction ID。

## 11. 错误模型

建议增加稳定错误码：

- gemini_omni_disabled
- gemini_omni_model_unavailable
- gemini_omni_paid_tier_required
- gemini_omni_store_required
- gemini_omni_file_processing_failed
- gemini_omni_interaction_expired
- gemini_omni_previous_interaction_invalid
- gemini_omni_video_output_missing
- gemini_omni_region_unsupported
- gemini_omni_content_filtered
- video_stateful_edit_in_progress

日志可以记录供应商状态码、内部 request/interaction 标识和重试次数，但必须经过敏感信息过滤。公开错误映射成用户可操作的信息，例如重新上传、从本地版本新建链或稍后重试。

## 12. 计费、配额与审计

- 阶段零先完成测试/生产 Gemini Key 拆分。macmini-4 使用独立 Google 项目或独立 Key、配额和 secrets.enc；macmini-1 保留生产 Key。拆分完成前禁止再次运行付费 Omni 探针。
- 每轮生成和编辑分别记账，不能只记线程首轮。
- 记录请求类型、输出时长、分辨率、开始/结束时间和供应商 usage 字段。
- 如果供应商未返回完整 usage，使用 ffprobe 得到的输出时长作为本地估算依据，并标记 estimated。
- 在提交“继续编辑”前显示这是一次新的付费生成。
- 为单任务并发数、单用户小时次数、每日金额和失败重试设置服务端硬上限；不能只依赖前端提示。
- 对 429、5xx 使用有限次数指数退避；内容过滤、参数错误和余额/权限错误不自动重试。
- client_action_id 的唯一约束和 §8 的恢复规则构成供应商调用幂等边界；usage_request_id 与现有 local_usage_log 唯一索引构成本地计费幂等边界。前端断线既不能触发新的供应商请求，也不能重复记账。

阶段二的两轮状态链验收采用一次性硬预算：

- 最多 2 次生成调用。
- 每轮请求当前最短 3 秒，总请求时长最多 6 秒。
- OPENCREW_GEMINI_OMNI_SMOKE_MAX_USD 默认 0；验收时显式设置且不得高于 USD 1.20。该值是按约 US$0.10/秒价格快照再乘 2 倍安全系数得到的门禁上限，最终两轮视频输出名义约 US$0.60。
- 以运行时最新价格和当前模型最短时长在调用前估算；价格缺失、估算超过上限或供应商不接受 3 秒时长时停止，不自动放宽预算。

## 13. 测试方案

### 13.1 单元与契约测试

适配器：

- tasks 与 capabilities 字段不混用，公开别名能通过 canonical_agent_model_alias_target 解析到目标内部模型。
- text_to_video、image_to_video、reference_to_video、edit 请求结构。
- store=false 与 URI 交付在本地直接拒绝。
- Files API 上传、PROCESSING 到 ACTIVE、失败和超时。
- Interaction 后台轮询的 completed、failed、取消和超时。
- 内联视频与 URI 视频两种解析。
- 下载失败、非视频响应和 ffprobe 失败不推进链头。
- 新建发送 task、画幅和时长；连续编辑省略当前 API 禁止的 task并重发画幅/时长；上传视频编辑使用 `document` URI 且省略 task、画幅和时长。

状态层：

- task_id 和用户归属校验。
- 客户端不能注入 previous_interaction_id。
- client_action_id 重放返回同一 pending/completed turn，不产生第二个供应商请求；同键但 operation 或输入不同会返回幂等冲突。
- 同一 turn 的 usage_request_id 由 stable_usage_request_id() 稳定推导并传给 record_storyboard_usage；重放只命中原 local_usage_log，不产生第二条计费记录。
- client_action_id、turn_id、usage_request_id 和 local_usage_id 的一对一映射可查询、可对账，公开响应只返回 client_action_id 和 turn_id。
- provider_result_unknown 禁止自动重建 Interaction。
- 正常续写、从历史版本分支和并发冲突。
- 数据库行锁、租约续期、陈旧租约接管和 row_version 冲突。
- 失败轮次不更新 head。
- 服务重启后有 interaction_id 的 pending turn 只恢复轮询，没有 ID 且确认未发送的 turn 才允许原键重试。
- Interaction 过期后通过本地 MP4 新建链。
- provider_state_expires_at 缺失时标记 unknown，续写前先 interactions.get 探测。
- 删除线程触发远端删除并可重试。

迁移：

- migration_0024_video_interaction_threads（或合并时下一个连续编号）已加入 MIGRATIONS，基线契约的 migration IDs 同步更新。
- 全新数据库和从旧基线升级后的数据库都包含两张表、字段、外键、租约索引、client_action_id 唯一索引和 usage_request_id 唯一索引。
- migration 重复运行保持幂等；PostgreSQL 集成测试覆盖同线程并发租约竞争。

安全：

- API Key、interaction_id 和内部模型名不出现在公开响应、SSE 或浏览器日志。
- 上传文件类型、大小、路径和任务归属校验。
- 跨任务 parent_turn_id 被拒绝。
- 生成 MP4 强制经过现有 media_sanitize 路径；容器 metadata 可清除，SynthID 按已接受残留风险展示。

### 13.2 前端测试

视频生成：

- 仅 Omni 模型显示“编辑视频”和“继续编辑”。
- 历史版本可分支，进行中禁用重复提交。
- 过期状态给出重新上传入口。
- 关闭功能开关后不再展示模型入口。

视频智能体：

- 第一轮创建线程，第二轮自动关联最近成功 turn。
- 失败轮次后仍从上一个成功版本继续。
- 新聊天或切换模型不会误用旧链。
- 刷新页面后能恢复当前版本。

### 13.3 Preview 证据与非付费检查

阶段零使用：

~~~text
backend/tests/integration/gemini_omni_live_probe.py
backend/tests/artifacts/gemini_omni_probe_2026-07-22.json
backend/tests/contracts/test_gemini_omni_probe_artifact_contract.py
~~~

默认运行只允许模型目录 GET，不创建视频。探针输出为白名单投影：

- 保留 HTTP 状态、模型名、Interaction 状态、视频 part 数量、字节数和 SHA-256。
- 移除 API Key、原始 interaction_id、视频 base64、供应商下载 URI 和完整提示中的客户内容。
- 记录官方文档 URL、获取日期和当前限制快照。

模型发现 artifact 必须同时记录 evidence_scope=model_metadata_only 和 interactions_endpoint_proven=false。当前在线结果的 supported_generation_methods 是 generateContent、countTokens，这个目录字段没有通告 Interactions，因此契约测试必须断言“模型发现不证明 `/v1beta/interactions` 兼容”，不能把 HTTP 200 推导成有状态接口已经可用。

契约测试对 §3 每条产品约束做显式断言，但应区分三类证据：模型发现是当前可在线复现的机器证据；官方限制是带日期的文档快照；单轮 Interactions 成功是同日付费调用的脱敏重建投影。在独立测试 Key 下重新运行付费探针之前，后两者不能冒充持续在线回归。由于它是 Preview API，上线前必须重新运行非付费模型发现、人工对照官方文档，并按 §13.4 执行受控付费兼容性检查。

### 13.4 付费在线冒烟测试

付费测试必须满足预算和显式授权；常规重复执行还应使用独立测试 Key：

- 优先把 macmini-4 换成独立测试 Key 和独立配额；只有人工核对后才可设置 OPENCREW_GEMINI_OMNI_TEST_KEY_ISOLATED=1。
- 尚未物理隔离时，必须取得用户对当前这次真实费用的明确授权，不得由自动回归或定时任务触发。
- OPENCREW_RUN_PAID_GEMINI_OMNI_SMOKE=1。
- 每个付费脚本都显式设置自己的最大调用数、总秒数和 USD 上限；两轮状态链上限仍不超过 USD 1.20，上传编辑另设一次调用门禁。
- 探针计算的最多调用次数和总请求时长没有超过对应场景的硬上限。

分四级执行：

1. 免费/低风险：模型目录 GET，确认 Key 和模型可见。
2. 单轮真实生成：最短允许时长、简单提示、非客户素材。
3. 状态链验收：store=true 创建首轮，取得 interaction_id，再以 previous_interaction_id 做一次最小编辑。
4. 上传编辑验收：通过浏览器选择一段非客户短视频，经 Files API 上传后执行一次最小编辑。

第三级和第四级仅在适配器、计费提示和测试清理逻辑完成后运行。测试应验证：

- 第二轮结果来自指定父版本。
- 上传编辑结果继承输入视频的画幅和时长，且请求不携带 API 禁止的 task、aspect ratio 或 duration。
- OpenCrew 保存两个本地 MP4 和父子关系。
- 清除云端上下文后不能继续旧链。
- 本地素材仍可播放。

测试输出写入专用测试任务，不使用客户素材。所有在线测试记录模型、状态、耗时和结果哈希，不记录 Key 明文。任一预算、显式授权或证据落盘前置条件不满足时直接退出；共享账单配置只能用于本次用户明确授权的人工验收，不能成为自动化默认值。

## 14. 文件级改动清单

| 文件 | 计划改动 |
| --- | --- |
| ModelConfig/backend/opcrew_model_config/media_model_config.py | 注册 Omni 模型、tasks/stateful_edit 等机器能力，并补 canonical_agent_model_alias_target 别名映射。 |
| backend/opcrew_backend/koubo/koubo_storyboard/gemini_omni_video_services.py（新增） | 实现 Interactions/Files 调用、状态轮询、结果解析、错误映射和供应商状态清理。 |
| backend/opcrew_backend/koubo/koubo_storyboard/video_interaction_repository.py（新增） | 封装 thread/turn 事务、client_action_id 去重、租约、恢复扫描和用量映射；禁止用 JSON sidecar 参与并发决策。 |
| backend/opcrew_backend/koubo/koubo_storyboard/asset_video_generation_services.py | 增加模型分派，复用 download_video_binary、sanitize_video_output、素材入库和 stable_usage_request_id/record_storyboard_usage 链路。 |
| backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py | 扩展直接视频生成的请求、SSE 恢复与公开响应字段。 |
| backend/opcrew_backend/koubo/koubo_storyboard/agent_chat_routes.py | 将视频智能体意图映射到 operation、thread_id、parent_turn_id 和 client_action_id。 |
| backend/opcrew_backend/db/schema.py | 定义 video_interaction_threads、video_interaction_turns 及索引。 |
| backend/opcrew_backend/db/migrations.py | 新增 migration_0024_video_interaction_threads（或合并时下一个连续编号）并加入 MIGRATIONS。 |
| backend/opcrew_backend/services/media_sanitize.py | 原则上复用现有 sanitize_video_file_metadata；只有 URI 流缺少可复用入口时才做最小扩展，任何分支都不得绕开清洗。 |
| frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js | 增加 client_action_id、状态线程字段、续写/查询/清理请求及 SSE 重连复用。 |
| frontend/src/modules/koubo/UploadAssetLibrary/UploadAssetLibraryOverlay.jsx | 恢复任务内当前视频线程和版本状态。 |
| frontend/src/modules/koubo/UploadAssetLibrary/components/VideoWorkspaceLibrary.jsx | 增加上传编辑、继续编辑、版本树、过期回退和费用提示。 |
| frontend/src/modules/koubo/UploadAssetLibrary/components/VideoAgentPanel.jsx | 展示并驱动视频智能体当前版本、链状态和连续编辑。 |
| frontend/src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js | 读取 tasks/stateful_edit 等机器能力，不复用展示型 capabilities。 |
| backend/tests/contracts/test_migration_baseline_contract.py | 同步 migration ID 基线，验证新表、字段、外键、索引和重复迁移幂等。 |
| backend/tests/contracts/test_gemini_omni_probe_artifact_contract.py | 固化证据等级、Preview 约束和付费探针预算闸门。 |
| backend/tests/integration/gemini_omni_live_probe.py | 保留默认非付费模型发现；在隔离 Key 下执行受控单轮/两轮在线兼容性验证。 |
| backend/tests/artifacts/gemini_omni_probe_2026-07-22.json | 保存脱敏证据及其适用范围，不把模型发现当作 Interactions 证明。 |

实现时还要新增适配器、状态仓储、路由和前端对应的单元/集成测试；上表是生产代码与关键契约的最低清单，不表示只修改这些测试文件。

## 15. 分阶段实施

### 阶段零：证据、密钥和预算闸门

- 提交并运行脱敏探针、证据 artifact 和契约测试。
- 重新核对官方 Preview 文档，确认任务、限制、存储和价格快照。
- 将 macmini-4 Gemini Key/Google 项目与 macmini-1 生产配置拆分。
- 落地付费测试最大调用数、总时长和 USD 预算硬闸门。

退出条件：证据等级和适用范围可由契约复查，非付费模型发现可重复运行且不会被误解为 Interactions 证明，默认测试不付费，测试调用不再污染生产计量线。Interactions 的在线兼容性由阶段一单轮和阶段二两轮受控付费验收补齐。

### 阶段一：模型目录与无状态适配

- 增加模型配置和能力字段。
- 实现 Interactions 适配器、输出下载和素材入库。
- 支持文本/图片生成及上传视频单轮编辑。
- 复用现有视频 sanitize sink，并验证公开输出不含容器真名；记录 SynthID 残留口径。
- 完成模拟契约测试和受控单轮在线冒烟。

退出条件：与现有 Veo 并存，关闭开关后完全不影响原流程。

### 阶段二：数据库状态链与视频生成页面

- 按 §8.2 增加 schema、migration_0024_video_interaction_threads（或下一个连续编号）和 baseline migration 契约，再增加 thread/turn 状态仓储、两层幂等键、行锁租约和重启恢复任务。
- 实现继续编辑、历史分支、过期回退和云端清理。
- 增加费用与隐私提示。
- 执行两轮付费状态链验收。

退出条件：刷新、重启、失败重试和分支均不丢链。

### 阶段三：视频智能体

- 把智能体生成请求关联到 video_turn_id。
- 自动选择最近成功版本。
- 处理切换模型、新聊天、失败轮次和历史分支。

退出条件：智能体连续两轮编辑稳定，公开响应不泄露内部供应商信息。

### 阶段四：灰度与运维

- 在 macmini-4 测试环境完成完整回归。
- 按连接配置和服务端开关灰度。
- 观察成功率、生成耗时、429、内容过滤和单位视频成本。
- 达到阈值后再按两节点发布流程进入 macmini-1。

## 16. 验收标准

- Preview 探针、脱敏 evidence artifact 和约束契约已进入仓库，非付费模型发现可复现。
- macmini-4 使用经用户明确授权的 Key，付费验收单次探针有最多 2 次、总请求 6 秒且不高于 USD 1.20 的硬闸门；测试/生产 Key 是否物理隔离仍作为上线运维检查项。
- 当前授权 Key 已完成真实两轮 Interactions 直接状态链、真实 Chromium OpenCrew 连续编辑产品链及上传视频编辑产品链；请求结构、Files API、持久化、媒体落盘、恢复、实际时长计量、云端清理和公开字段隔离均有脱敏 artifact 与契约覆盖。
- 视频生成页面能完成文本生成、上传视频编辑和两轮连续编辑。
- 每轮结果是独立素材，能查看父版本并从历史版本分支。
- 视频智能体后续指令准确引用同一线程最近一次成功结果。
- 服务重启和页面刷新后状态链可以恢复。
- 相同 client_action_id 的断线重放不会创建第二个收费 Interaction；同一 turn 的 usage_request_id 重放不会重复记账；不明供应商状态不会自动重试。
- migration 编号与 MIGRATIONS/baseline 契约一致，全新建库、旧库升级和重复执行均通过，新表及唯一索引可验证。
- 数据库租约过期后可安全接管，不会永久锁死或并发覆盖。
- 失败、取消或本地落盘失败不会推进链头。
- TTL 缺失时状态显示 unknown 并在续写前探测；Interaction 过期时可以从本地视频新建链，并向用户说明上下文差异。
- 用户能清除云端上下文，删除失败有后台重试记录。
- 公开 API、SSE、日志和前端不泄露 Key、原始 interaction_id 或受屏蔽模型信息。
- 每轮生成都有计费/用量记录，并有并发与重试上限。
- 输出视频经过现有 metadata sanitize；产品明确说明 SynthID 不可由该清洗移除。
- 关闭 OPENCREW_GEMINI_OMNI_ENABLED 后，现有 Veo 视频生成和视频智能体不受影响。

## 17. 风险与回滚

主要风险：

- Preview API schema、模型名称和价格可能变化。
- 状态保存期限有限，过期后不能原样恢复隐藏上下文。
- 每次编辑都生成新视频，连续尝试可能快速增加成本。
- URI 下载、Files API 处理和长耗时 Interaction 增加故障点。
- 模型的区域和内容安全限制可能造成不同用户结果不一致。

控制措施：

- 分级探针、脱敏 artifact 和 Preview 约束契约先于适配器开发，并明确模型目录、官方快照和付费 Interactions 观察三者的证据强度不同。
- 遵循现有扁平服务目录的独立适配器与结构化 tasks 能力判断，不污染 Veo 分支或展示型 capabilities。
- 服务端功能开关、连接级启用和付费测试开关三层控制。
- 测试/生产 Key 与配额拆分，状态链验收使用独立 USD 硬预算。
- 数据库事务、幂等唯一键和可续租租约处理并发及重启。
- 保存每一轮本地 MP4，供应商状态失效时仍可重新上传。
- 限制并发、自动重试次数和任务预算。
- 强制复用现有 metadata sanitize；SynthID 作为业主知情接受风险，不做“不可识别供应商”的承诺。
- 监控按模型和 operation 分组，不依赖前端上报。

回滚不删除本地生成素材，只执行：

1. 关闭 OPENCREW_GEMINI_OMNI_ENABLED。
2. 隐藏 Omni 模型和继续编辑入口。
3. 停止创建新 Interaction。
4. 保留只读版本历史，并继续处理已排队的远端清理。

现有 Veo predictLongRunning 流程保持原样，因此不需要数据库或素材文件回滚。

## 18. 官方参考

- [Gemini Omni Flash 官方文档](https://ai.google.dev/gemini-api/docs/omni?hl=zh-cn)
- [Gemini Interactions API 概览](https://ai.google.dev/gemini-api/docs/interactions-overview)
- [Gemini API 定价](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini Files API](https://ai.google.dev/gemini-api/docs/files)
