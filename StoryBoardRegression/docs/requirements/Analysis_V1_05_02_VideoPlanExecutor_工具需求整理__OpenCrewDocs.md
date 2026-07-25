# Analysis_V1 05_02_VideoPlanExecutor 工具需求整理

版本：v0.4

状态：需求确认稿。本文用于指导 `05_02_VideoPlanExecutor.py` 的实现与测试。

## 1. 背景

`05_01_VideoPlanGenerator.py` 已经把当前 StoryBoard 范围拆成可执行的视频生成计划：

```text
SessionOutput/storyboard/video_generation_plan.json
```

`05_02_VideoPlanExecutor.py` 的任务不是重新规划，而是读取这个唯一执行计划，按计划逐段执行：

1. 必要时生成 Dialogue 音频。
2. 必要时生成图片 Prompt。
3. 必要时生成新图片。
4. 必要时生成视频 Prompt。
5. 必要时生成对嘴前 raw video。
6. 如果计划要求对嘴型，使用该 segment 音频和 raw video 执行单段对嘴型。
7. 抽取最终视频尾帧。
8. 把最终图片、最终视频和必要业务审计文件同步回 `SessionOutput/storyboard/Working/`。

本工具的核心原则：

1. 不再用大模型自由生成图片或视频 Prompt。
2. 图片 Prompt 和视频 Prompt 必须从参考模板、StoryBoard、video generation plan、Scene Profile、人物/产品一致性参考中确定性拼接。
3. 每一次图片或视频生成前，最终 Prompt 必须先写入本工具 `Prompt/`。
4. 模型调用只能读取 `Prompt/` 中已经落盘的 Prompt 文件或请求文件，不能在代码里隐藏拼接。
5. 中间视频文件只进入本工具 `Working/`，不得进入 `Output/` 或 `SessionOutput/storyboard/Working/`。
6. 最终图片、segment 最终音频和最终视频才进入本工具 `Output/`，再复制到 `SessionOutput/storyboard/Working/`。
7. 同步到 StoryBoard Working 的业务审计文件包括最终图片 Prompt JSON 和最终视频 Prompt JSON；模型中间响应只留在本工具目录。

### 1.1 StoryBoard 工具归属边界（强制合同）

StoryBoard 编辑页只有“刷新 Session Variables”和“重新加载提示词”允许按 workflow/profile 分流。其余计划生成、计划执行、状态读取、素材绑定、尾帧物化和合成都属于 `Analysis_V1`；Video Plan 必须固定执行 `Analysis_V1/05_01 -> Analysis_V1/05_02`。`TalkingHead_V1/05_02` 只用于人物口播一键成片，或在“重新加载提示词”时作为 Prompt 构建模块被局部加载。

禁止在 `video_plan_execution_script_path()` 中按 `workflow_id=person_talking_head_v1` 替换 StoryBoard 的 05_02。该错误会把 Analysis 05_01 plan 交给 TalkingHead 05_02，导致 `Lip-sync is required but disabled` 或 `VIDEO_OPENROUTER_POSITIVE_BASE` 模板标记缺失。合同测试必须锁定人物口播 workflow 下 StoryBoard 仍返回 Analysis 05_02。

### 1.2 Task #31 图片生成实跑踩坑与错误记录

以下问题已经在 Task #31 / Session #87 的 05-02 图片生成实跑中出现过，后续实现、重构和回归测试必须保留这些防线：

1. 图片模型不能只接收原图或只接收 Prompt。需要把 `TARGET_FRAME`、`HOST_REFERENCE`、`PRODUCT_REFERENCE` 都复制到 `S9_05_02_VideoPlanExecutor/Working/`，并作为多图输入传给图片编辑模型。
2. Prompt 必须明确三张图的职责：`TARGET_FRAME` 锁定原图构图、姿势、手部、机位、背景、光线和手机视频质感；`HOST_REFERENCE` 只用于替换人物身份、脸、发型、服装和气质；`PRODUCT_REFERENCE` 只用于替换产品包装身份、颜色、文字和外形。不能写成泛泛的“保持一致性”。
3. 图片生成的目标不是自由重绘海报，而是把 `TARGET_FRAME` 中的人物和产品替换成一致性参考中的人物和产品，同时保留原图的构图和真实感。模型请求中必须能审计到这三个输入图片路径和最终渲染 Prompt。
4. 如果缺少人物或产品一致性参考，05-02 不能假装已经传入参考图。执行应按计划和前端提示降级：缺少哪类参考就明确写入 warning / blocked reason 或降级 Prompt，不能在 UI 状态里显示“已满足一致性”。
5. `SessionContext/Variables.json` 只提供 `default_image_config.provider/model/api_key_ref` 等公开配置。真实 API key 必须在运行时通过数据库/secret store 读入内存，不能写入 `Variables.json`、Prompt、request JSON、response JSON、Report 或 `SessionOutput`。
6. 如果本地代理不可用，图片模型调用可能出现连接失败；这类错误要记录为 provider/network error，不能误判为 Prompt 错误或图片文件错误。
7. `invalid_image_file` 或类似 provider 报错通常说明传给图片模型的输入不是可读取图片、路径指向视频/空文件、文件扩展和实际内容不一致，或没有把参考图 materialize 到工具 Working。05-02 在调用模型前必须校验输入图片存在、非空、可解码，且不能把 bound video 路径当作图片输入。
8. 图片生成成功后，不能只把图片文件写到 `SessionOutput/storyboard/Working/`。文件落盘只是素材可用，不等于 StoryBoard 已绑定；还必须同步回 `srt_storyboard.json` 对应 segment 第一个 dialogue 的 `working_assets.images[0]` 和 `bound_image_path`。
9. 已经踩过的故障模式：图片确实存在于 StoryBoard Working，但没有写回 JSON，导致页面仍显示“新图槽为空”，后续 05-01 重新生成 plan 时也无法识别该 dialogue 已有新图，只能继续按缺失素材处理。05-02 每次发布图片后必须校验“文件存在 + JSON 路径存在 + JSON 路径指向同一个文件”三者一致。
10. StoryBoard 页面优先读取 `SessionOutput/storyboard/koubo_storyboard_edit.json`。如果编辑态文件存在，05-02 发布新图片时必须同步同一个 dialogue 的 `working_assets.images[0]` 和 `bound_image_path`；否则真实图片已经生成，但页面仍显示“新图未绑定”。
11. `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 同时存在时，不能只写其中一个。05-02 必须把最终图片、最终视频和相关 bound path 同步到两个 JSON 的同一个 dialogue；如果任意一个 JSON 更新失败，本次发布应标记 failed 或 warning，不允许静默成功。
12. 已经踩过的故障模式：图片模型返回 `1024x1536` 这类 2:3 竖图，StoryBoard 页面看起来变窄，后续视频模型按 9:16 使用时会拉伸或重构图。图片 Prompt 必须明确要求输出竖屏 9:16、保持 TARGET_FRAME 的画幅比例；05-02 发布图片前必须校验并归一化为 TARGET_FRAME 的 9:16 尺寸，例如 `720x1280`。非 9:16 图片不能直接进入 StoryBoard Working 或传给 Video。
13. 如果图片已生成并进入 StoryBoard Working，后续 Video 或 Sync 失败不能把 Image step 回退成失败或灰色。Image step 必须保持 `completed_working`，失败只能落在 Video / Sync 对应步骤。
14. 当重新生成 05-01 plan 后，当前 plan 可能因为已绑定最终视频而把 `first_frame.source_type` 改成 `bound_video`。这时 UI 和状态聚合仍必须能从旧 execution state/result 或 StoryBoard Working 识别“新图已经生成”，不能简单按 `bound_video` 把 First Frame 显示为不执行灰色。
15. 图片模型请求审计文件只能记录 provider、model、api_key_ref、Prompt 路径、输入图片相对路径、输出目标相对路径和非敏感参数；不能保存 API key、Authorization header、cookie、数据库连接串或 provider 返回的带鉴权下载链接。
16. 空镜 / 产品特写 segment 不能按口播人物处理。`tasks.lipsync_reason=user_marked_cutaway`、`product_closeup`、`no_visible_face` 或等价标记出现时，05-02 图片 Prompt 必须进入 product-only cutaway 模式：只使用 `TARGET_FRAME` 和 `PRODUCT_REFERENCE`，不使用 `HOST_REFERENCE`，不生成主播、人物、人脸、手、眼睛、嘴、牙齿、嘴唇、麦克风或任何说话主体。
17. 空镜产品替换必须是完整、完全、覆盖整个可见包装的替换。不能只替换 logo、局部贴图、局部标签、主色或正面一小块；旧产品包装的名称、logo、颜色、文字、侧面、封口、瓶盖/盒型、图案和任何残留都必须清除，不能出现新旧产品混合包装。
18. 空镜视频 Prompt 必须是 product-only cutaway，不是 talking-head。不能写自然口型、mouth movement、blink、lip-sync、speaking face 等口播人物动作；如果产品包装上有印刷人物或脸，只能作为静态平面包装图案，不能眨眼、开口、说话、对嘴或变成活人。
19. Generation Plan 中每个小块任务变绿，必须表示“业务产物已进入 `SessionOutput/storyboard/Working/` 且 StoryBoard JSON 已完成绑定”。不能先把 tracker step 标记为 `completed_working`，再异步或等待全流程结束才写 `srt_storyboard.json` / `koubo_storyboard_edit.json`；否则用户刷新主界面时仍看不到已经生成的素材。
20. 图片小块完成时，必须同步绑定对应 dialogue 的 `working_assets.images[0]` 和 `bound_image_path`；音频小块完成时，必须同步绑定对应 dialogue 的 `working_assets.audio` / `Audio_Final`；视频小块完成时，必须同步绑定 `working_assets.video`。如果是需要 Sync 的口播段，raw video 或最终 video 进入可交付路径后也要先完成 JSON 写入；Sync 完成后再次确认最终路径仍绑定到同一 dialogue。
21. 用户当前接受“手动刷新主界面看到已生成素材”，不要求主界面自动刷新，也不要求额外刷新按钮。因此后端契约是：每个小块任务绿灯出现时，刷新页面即可从主界面读到对应图片或视频槽位；不能把素材绑定延迟到全部 segment 执行结束。
22. `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 的写入要按单次执行做一次性备份，不能每完成一个小块都制造一批重复历史备份；但如果任何一个 JSON 写入失败，本小块不能静默显示绿色成功。
23. 对 `audio_replace_retime` 或绑定视频转最终视频的非对嘴型段，如果 `video_plan_execution_state.json` 被清理但 StoryBoard Working 中已有最终视频且 segment 音频也存在，前端状态可以从“最终视频 + segment audio + 不需要 lipsync”推断 Sync 已完成。该推断只适用于 `need_lipsync=false` 的音频替换/重定时场景；`need_lipsync=true` 的对嘴段不能仅凭最终视频文件推断 Sync 完成，必须保留明确的 execution state/result 证据。
24. Video Only Plan 的首帧步骤如果来源是 `previous_segment_tail_frame` 或 `previous_scene_tail_frame`，状态优先级必须高于“Raw / Final 已存在则跳过新图”的通用规则。上游 TailFrame 未生成时显示白色等待；TailFrame 已存在但本段 `Image_New` 未绑定时显示白色可物化。点击“尾帧”只允许把上游 TailFrame 复制到本段标准 `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*`，并同步写回 `srt_storyboard.json` 与 `koubo_storyboard_edit.json` 的 `working_assets.images[0]` 和 `bound_image_path`，不能直接把上游 TailFrame 路径绑定成当前新图。
25. `Video_Final` 与 `TailFrame` 必须成对维护：任何 `05_02` 成功发布的最终视频，无论来自对口型、空镜音频替换 / 重定时，还是绑定视频重同步，都必须从最终 `Video_Final` 抽取 `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_TailFrame.png`。对嘴型失败时允许保留 Raw 供诊断，但 Raw 尾帧不得解除后续 segment 的尾帧依赖；后续只认成功 Final 对应的 TailFrame。

## 2. 工具定位

新增工具：

```text
05_02_VideoPlanExecutor.py
```

推荐 Tool Use Session 步骤目录：

```text
S9_05_02_VideoPlanExecutor/
```

当前主链路：

```text
S1_00_PrepareSessionVariables
S2_01_VideoProbeMetadata
S3_02_01_AudioASR
S4_02_02_VideoSRTFrame
S5_03_01_TTSBuilderG
S6_04_01_SRTRewrite
S7_04_02_StoryBoard
S8_05_01_VideoPlanGenerator
S9_05_02_VideoPlanExecutor
```

边界：

1. 只执行 `video_generation_plan.json` 中的 planned segment。
2. 不重新拆 Scene、Shot 或 Dialogue。
3. 不修改 `srt_storyboard.json` 的结构。
4. 可以根据实际执行结果产出一个独立 execution result JSON。
5. 可以把最终生成素材和业务 Prompt 同步到 `SessionOutput/storyboard/Working/`，供 UI 和后续拼接工具使用。
6. 不直接做最终 Scene / Shot / Task 拼接；拼接应由后续 Compose 工具处理。
7. 对嘴型在本工具内逐个视频 segment 执行；本工具只产出单段最终视频，不拼接多段视频。

## 3. 目录合同

### 3.1 工具目录

本工具必须创建四个一级目录：

```text
S9_05_02_VideoPlanExecutor/
  Working/
  Output/
  Prompt/
  Report/
```

硬性规则：

1. `Working/`、`Output/`、`Prompt/`、`Report/` 内部不得创建子目录。
2. 所有文件必须平铺在对应一级目录下。
3. 文件名必须包含稳定的 `dialogue_asset_key` 或 `segment_id`，避免不同 segment 冲突。
4. 本工具可以读取上游文件，但 run 阶段正式逻辑必须读取本工具 `Working/` 中的快照。
5. 本工具可以读取参考模板，但 prepare 阶段必须先复制到本工具 `Prompt/`。

### 3.2 SessionOutput 目录

本工具同步最终业务素材到：

```text
SessionOutput/storyboard/Working/
```

同步规则：

1. 图片最终落点：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_01.{ext}`
2. 图片 Prompt 落点：`SessionOutput/storyboard/Working/{dialogue_asset_key}_ImagePrompt.json`
3. 视频最终落点：`SessionOutput/storyboard/Working/{first_dialogue_asset_key}_Video_Final.{ext}`，固定表示最终可交付视频；有人脸且 `need_lipsync=true` 时是对嘴后视频，无人脸或前端关闭对嘴时是 raw video 校验后晋升的最终视频。
4. 视频 Prompt 落点：`SessionOutput/storyboard/Working/{first_dialogue_asset_key}_VideoPrompt.json`
5. 尾帧最终落点：`SessionOutput/storyboard/Working/{first_dialogue_asset_key}_TailFrame.png`
6. 音频最终落点：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.{ext}`
7. Segment 合成音频最终落点：`SessionOutput/storyboard/Working/{first_dialogue_asset_key}_SegmentAudio_Final.{ext}`

这些 SessionOutput 文件是 UI 和后续工具的业务读取对象。

中间视频文件规则：

1. 对嘴前 raw video、对嘴临时视频、模型下载临时文件、探测临时文件都只能放在 `S9_05_02_VideoPlanExecutor/Working/`。
2. `Output/` 不保存 raw video 或其它中间视频，只保存最终图片、segment 最终音频、最终视频、最终尾帧、必要 Prompt JSON、最终执行结果。
3. `SessionOutput/storyboard/Working/` 不保存 raw video 或其它中间视频，只保存 UI 和后续拼接需要的最终业务文件。
4. 后续 segment 链式生成使用的尾帧必须来自最终 `Video_Final` 对应的 `TailFrame.png`；如果 `need_lipsync=true` 且对嘴失败，Raw 尾帧只能作为诊断产物，不能继续链式生成。
5. 任意覆盖、重发布或补绑定 `Video_Final` 时，都必须重新抽取同 key `TailFrame.png`；覆盖旧 TailFrame 前按 history 规则备份。
6. 本规则限定图片/视频媒体文件；音频最终文件和业务 Prompt JSON 按本工具各自合同保存，模型中间响应不进入 StoryBoard Working。

历史备份规则：

1. `--force` 只清理本工具目录，不删除 `SessionOutput/storyboard/Working/` 中已有文件。
2. 工具成功运行并准备覆盖 StoryBoard Working 中同名图片、音频、视频、尾帧或 Prompt JSON 前，必须先把旧文件复制到统一的 `SessionOutput/storyboard/assets/history/`。
3. 每次覆盖备份必须写入 `assets/history/batch_*_05_02_overwrite_backup/manifest.json`，不得再创建或写入 `SessionOutput/storyboard/History/`。
4. 备份范围包括最终图片、Dialogue 音频、Segment 合成音频、最终视频、最终尾帧、`ImagePrompt.json`、`VideoPrompt.json`。
5. 如果备份失败，本次覆盖必须 blocked 或 failed，不能直接覆盖用户可见资产。

## 4. 输入

### 4.1 必需输入

prepare 阶段从 workspace 读取：

```text
SessionContext/Variables.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/video_generation_plan.json
OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02_VideoPlanExecutor_PromptTemplates.md
```

复制到本工具：

```text
S9_05_02_VideoPlanExecutor/Working/InputFrom_0_Variables.json
S9_05_02_VideoPlanExecutor/Working/InputFrom_7_srt_storyboard.json
S9_05_02_VideoPlanExecutor/Working/InputFrom_8_video_generation_plan.json
S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_VideoPlanExecutor_PromptTemplates.md
```

### 4.2 Scene Profile 输入

优先读取：

```text
SessionOutput/tts/tts_builder_candidates.json
```

复制到：

```text
S9_05_02_VideoPlanExecutor/Working/InputFrom_5_tts_builder_candidates.json
```

降级读取：

```text
S5_03_01_TTSBuilderG/Output/scene_profile_response.json
```

复制到：

```text
S9_05_02_VideoPlanExecutor/Working/InputFrom_5_scene_profile_response.json
```

如果二者都不存在，允许继续执行，但 Prompt 中只能使用保守默认 Scene Profile，不允许编造视觉细节。

### 4.3 人物和产品一致性参考

如果当前 Session 有人物一致性参考和产品一致性参考，必须复制到本工具 `Working/`，并使用平铺命名。

推荐来源：

```text
SessionContext/Variables.json -> host_reference.path
SessionContext/Variables.json -> product_reference.path
SessionOutput/storyboard/consistency_references/host/HOST.png
SessionOutput/storyboard/consistency_references/product/PRODUCT.png
```

复制到：

```text
S9_05_02_VideoPlanExecutor/Working/Ref_Host.png
S9_05_02_VideoPlanExecutor/Working/Ref_Product.png
```

如果存在 manifest：

```text
host_reference_manifest.json
product_reference_manifest.json
```

复制到：

```text
S9_05_02_VideoPlanExecutor/Working/Ref_HostManifest.json
S9_05_02_VideoPlanExecutor/Working/Ref_ProductManifest.json
```

规则：

1. `Ref_Host.*` 是人物身份锚点，不是构图锚点。
2. `Ref_Product.*` 是产品包装身份锚点，不是构图锚点。
3. 当前 segment 的首帧 / 原图 / 尾帧才是构图、姿势、手部、机位、光线和手机质感锚点。
4. 缺少人物或产品参考时，不 blocked；Prompt 必须降级为“保持输入图片中已确定的人物/产品身份”。

### 4.4 模型配置输入

`05_02` 默认从 `SessionContext/Variables.json` 读取图片、视频和对嘴型模型配置。

`00_PrepareSessionVariables.py` 需要在 `Variables.json` 中准备以下公开配置：

```text
default_image_config
default_video_config
default_lipsync_config
```

每个配置只允许包含公开字段：

```json
{
  "provider": "provider_name",
  "model": "model_name",
  "api_key_ref": "configured_secret_reference",
  "has_api_key": true,
  "source": "database_public_config",
  "enabled": true,
  "active": true,
  "extra": {},
  "extra_json": {}
}
```

规则：

1. `05_02` 调用图片模型时，默认使用 `default_image_config.provider/model`。
2. `05_02` 调用视频模型时，默认使用 `default_video_config.provider/model`。
3. `05_02` 调用对嘴型模型时，默认使用 `default_lipsync_config.provider/model`。
4. 命令行可以覆盖 provider/model，但不能通过命令行传入 API key。
5. 真实 API key 不写入 `Variables.json`、`Working/`、`Prompt/`、`Output/`、`Report/` 或 `SessionOutput/`。
6. `00_PrepareSessionVariables.py` 必须把对嘴型模型的非密钥参数完整写入 `default_lipsync_config`，包括 `provider`、`model`、`api_key_ref`、`has_api_key`、`enabled`、`active`、`extra/extra_json`。例如 Sync.so 卡片的 `sync-3`，或 HeyGen 卡片的 `speed` / `precision`。
7. `05_02` 默认执行对嘴型时，`provider/model/extra_json` 以 `SessionContext/Variables.json -> default_lipsync_config` 为事实来源；不得为了选择默认对嘴模型而重新读取数据库。
8. 运行时允许按 `kind + provider + model/api_key_ref` 从数据库读取真实 API key 到内存中使用。
9. 这个数据库读取只允许用于模型密钥解析，不允许读取 Task、Session、StoryBoard、Prompt、模型参数或业务状态。
10. `00_PrepareSessionVariables.py` 必须补充以上三个默认配置；缺失时 `05_02` 不自行推断模型，只返回 blocked。

### 4.5 不同模型网络调用边界

`05_02` 是多模型执行器，但不同 provider / model 的网络访问方式不能抽象成一个通用函数后统一套用。已经实跑踩过的坑是：图片、TTS、xAI Video、Sync.so 都能因为被通用代理函数覆盖成 `127.0.0.1:7890` 而报 `Connection refused`，但真实问题不是模型 API endpoint 改了，而是运行时网络路径被错误改写。

硬性规则：

1. 每个 provider / model 必须有独立的调用适配器，适配器内明确 endpoint、鉴权方式、请求体、轮询方式、下载方式和代理策略。
2. 禁止在 `generate_image_with_provider()`、`generate_video_with_provider()`、`generate_tts_with_provider()`、`run_lipsync_with_provider()` 入口处无差别调用同一个通用网络准备函数。
3. 禁止把 `apply_provider_proxy()`、`ensure_provider_proxy_available()` 或类似逻辑批量套到所有海外 provider。只能在某个 provider 的适配器已经真实验证需要时单独使用。
4. `Connection refused` 必须优先判断是本地代理端口拒绝还是远端 API 拒绝。错误展示要保留 provider endpoint，但不得泄露 key；如果是本地代理端口，应在诊断里明确“本地网络路径/代理不可达”，不能误判为 Prompt、模型名或图片文件错误。
5. 如果系统刚切换过 mihomo Tun Mode / VPN / 代理模式，必须优先重启 OpenCrew backend，再重试 `05_02`。`05_02` 是 backend 拉起的 Python 子进程，会继承 backend 启动时的网络环境；Tun Mode 已经改变系统路由，但旧 backend 仍可能携带旧的 `HTTP_PROXY` / `HTTPS_PROXY` / `OPENCREW_MIHOMO_PROXY_URL` 状态，导致 provider 调用继续打到不可用的本地代理端口并报 `Connection refused`。
6. 每次修改某个 provider 的网络调用，只允许改该 provider 的适配器，并补一个只覆盖该 provider 的回归测试；不能顺手改其它 provider。

当前已经确认的调用边界：

| kind | provider/model | 调用方式 | 代理/网络要求 | 不允许再犯的坑 |
| --- | --- | --- | --- | --- |
| image | `openai` / GPT image | OpenAI Images API；有参考图走 `https://api.openai.com/v1/images/edits` multipart，无参考图走 `https://api.openai.com/v1/images/generations` JSON；鉴权 `Authorization: Bearer ...` | 保持 OpenAI 既有访问方式；修改 Gemini 或其它 provider 时不能影响 OpenAI | 不能为了支持 Nano Banana 改掉 OpenAI endpoint、multipart 字段或 Bearer header |
| image | `gemini` / `google` / Nano Banana | `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=...`；payload 使用 `contents[].parts[]`，文本 + 多个 `inline_data` 图片；`generationConfig.responseModalities` 包含 `TEXT` 和 `IMAGE` | 按 Google/Gemini 图片适配器独立处理；不强制覆盖到本地 `7890` | 不能把 Google 图片改成和 OpenAI 一样；不能把 API key 写入审计文件；不能因旧 Working 文件存在就把本次失败显示成成功 |
| audio | `google` / Gemini TTS | 与 `03_01_TTSBuilderG.py` 对齐：`v1beta/models/{model}:generateContent?key=...`；payload 为 `responseModalities: ["AUDIO"]` 和 `speechConfig.voiceConfig.prebuiltVoiceConfig.voiceName` | 跟随 03 步已验证可用方式；不在 TTS 函数里强制 mihomo 预检查 | 不能改成 `x-goog-api-key` 后破坏 03 已通的访问方式；不能漏传 headers 参数导致调用时报错 |
| video | `xai` / `grok-imagine-video` | `POST https://api.x.ai/v1/videos/generations`；鉴权 `Authorization: Bearer ...`；返回 request/id 后按 xAI video id 轮询，完成后下载 URL | 使用当前运行环境已有网络路径；不得把可用的 `HTTP_PROXY/HTTPS_PROXY` 覆盖成默认 `http://127.0.0.1:7890` | 不能在 xAI Video 前调用通用 mihomo 端口检查；`Connection refused` 多半是本地代理被错误覆盖，不代表 xAI endpoint 变了 |
| lipsync | `sync.so` / `lipsync-2`、`lipsync-2-pro`、`sync-3` | `requests.post("https://api.sync.so/v2/generate")` multipart 上传 video/audio；header `x-api-key`；随后 `requests.get("https://api.sync.so/v2/generate/{id}")` 轮询，最后下载 `outputUrl` | 使用 Sync.so 自己的 `requests` 调用链；不得在入口处强制 mihomo 预检查 | 不能把 Sync.so 套进通用 provider proxy；不能因 Sync 失败回退或覆盖已成功的 Image / Video 状态 |
| lipsync | `heygen` / `speed`、`precision` | `POST https://api.heygen.com/v3/assets` 上传 video/audio；`POST https://api.heygen.com/v3/lipsyncs` 创建任务；`GET https://api.heygen.com/v3/lipsyncs/{lipsync_id}` 轮询；完成后下载 `video_url` | 使用 HeyGen 自己的 `requests` 调用链；`model` 映射为 HeyGen `mode`；不得在入口处强制 mihomo 预检查 | 不能把 HeyGen 写进 Sync.so 模块；不能把 `speed/precision` 当成 endpoint；不能把 API key 或 signed token 写入审计 |
| video | `gemini` / Veo | `v1beta/models/{model}:predictLongRunning?key=...`，按 operation 轮询 | 只能在 Veo 适配器内调整；不要复用 Gemini image/TTS 的 `generateContent` 逻辑 | 图片、TTS、Video 都叫 Gemini，但 endpoint 和返回结构不同，不能合并成一个 Gemini 通用函数 |
| video | `wan` / DashScope | DashScope 上传和异步 video synthesis 调用，使用 DashScope 自己的 header 和 OSS 上传流程 | 只在 Wan 适配器内处理 | 不能被海外 provider 的 proxy 策略影响；不能复用 xAI/Gemini video 轮询结构 |

实现约束：

1. `apply_provider_proxy(provider)` 不能作为 provider 适配器的默认前置步骤。要么不调用，要么由该 provider 的测试证明必须调用，且不会覆盖当前可用网络路径。
2. `OPENCREW_MIHOMO_PROXY_URL` 的默认值 `http://127.0.0.1:7890` 只能在明确使用 mihomo 的 provider 适配器中使用；不能因为 provider 被归类为海外服务就自动覆盖当前进程已有代理。
3. 请求审计文件必须记录实际 endpoint、provider、model、prompt path、输入文件路径、非敏感参数和 provider 实参，例如 xAI 的整数秒 duration；不得记录真实 API key、Bearer token、`x-api-key` 或 `?key=` 的真实值。
4. UI 状态必须以当前 execution state/result 的 step 状态优先。当前 step 为 `failed` 时，即使 `SessionOutput/storyboard/Working/` 里存在旧文件，也不能把该 step 计为本次成功。
5. 如果某一步当前失败，但旧文件存在，UI 可以另外提示“旧产物存在”，但不能用绿色完成态混淆“本次调用成功”。
6. 后续新增 provider 时，必须先把该 provider 的 endpoint、鉴权、proxy 策略、轮询和下载方式补进本表，再编码。

### 4.6 Segment 首帧输入

每个 segment 执行前，必须把实际用到的首帧或图片参考复制到 `Working/`。

示例：

```text
S9_05_02_VideoPlanExecutor/Working/srt_0001_01_SourceFrame.jpg
S9_05_02_VideoPlanExecutor/Working/srt_0001_01_FirstFrame.png
S9_05_02_VideoPlanExecutor/Working/srt_0001_01_PreviousTailFrame.png
```

规则：

1. `original_image` 复制为 `*_SourceFrame.*`，只给图片生成使用。
2. `generated_image` 或 `placed_uploaded_image` materialize 后复制为 `*_FirstFrame.*`，给视频生成使用。
3. `previous_segment_tail_frame` / `previous_scene_tail_frame` 复制为 `*_PreviousTailFrame.*`，给视频生成使用。
4. `Working/` 中这些文件都是执行快照；最终业务文件仍以 `SessionOutput/storyboard/Working/` 为准。

### 4.7 对嘴型计划字段输入

`05_01_VideoPlanGenerator.py` 需要在每个 video segment 中写入对嘴型控制字段：

```json
{
  "need_lipsync": true,
  "lipsync_disabled_by_ui": false,
  "lipsync_reason": "visible_face"
}
```

规则：

1. 默认 `need_lipsync=true`。
2. 如果前端明确关闭该 video segment 的对嘴型，`need_lipsync=false` 且 `lipsync_disabled_by_ui=true`。
3. 如果计划或 StoryBoard 明确标记无人脸 / 产品特写 / 不需要口型，`need_lipsync=false`。
4. `05_02` 不用视觉模型重新判断是否有人脸；按计划字段执行。
5. 缺少该字段时按兼容策略视为 `need_lipsync=true`。

### 4.8 空镜 / 产品特写 Prompt 规则

当 `05_01` 计划中出现以下任一条件时，当前 segment 必须视为空镜 / 产品特写：

```text
tasks.need_lipsync = false
tasks.lipsync_reason = user_marked_cutaway
tasks.lipsync_reason = product_closeup
tasks.lipsync_reason = no_visible_face
tasks.lipsync_reason = no_face
tasks.lipsync_decision_source = user_marked_cutaway
```

图片 Prompt 规则：

1. 空镜只允许替换产品，不允许替换或生成主播人物。
2. 只传 `TARGET_FRAME` 和 `PRODUCT_REFERENCE`；`HOST_REFERENCE` 不适用，不能作为输入图传给图片模型。
3. `reference_priority.host_reference` 必须标记为 `not_used_for_product_only_cutaway` 或等价语义。
4. 正向提示词必须明确 `product-only cutaway`、`Do not use HOST_REFERENCE`、`only the product identity may change`。
5. 产品替换必须覆盖完整可见包装：正面、侧面、封口、瓶盖/盒型、可见标签、颜色、logo、文字和图案都必须替换为 `PRODUCT_REFERENCE` 身份。
6. 负向提示词必须明确禁止：`partial product replacement`、`only logo replaced`、`sticker overlay`、`mixed old and new packaging`、旧包装残留。
7. 负向提示词必须明确禁止真人脸、人物、主播、身体、手、眼睛、嘴、牙齿、嘴唇、说话主体、会动的包装人物。
8. 如果产品包装上有印刷人物、脸或肖像，只能作为静态平面包装图案，不能变成立体脸、真实人物或说话主体。

视频 Prompt 规则：

1. 空镜视频必须使用 `VIDEO.POSITIVE_CUTAWAY_PRODUCT_ONLY` 或等价模板块。
2. 不能使用 talking-head 模板，不能写 `Natural mouth movement base`、`realistic blinking`、`mouth movement`、`lip-sync`、`speaking lips` 等口播人物正向描述。
3. 正向提示词必须强调产品、包装、背景、机位、光线和手机视频质感的连续性。
4. 负向提示词必须禁止真人脸、人物、主播、talking head、说话嘴型、对嘴、包装人物眨眼、包装肖像开口、产品包装上的人说话。
5. 空镜的 dialogue 文本只能作为语义背景，不能驱动画面中任何对象说话。

## 5. 参数

建议参数：

```text
--workspace <workspace>
--provider-profile grok_single_image|veo_lite_single_image|wan27_i2v|veo31_standard
--image-provider <provider>
--image-model <model>
--video-provider <provider>
--video-model <model>
--lipsync-provider <provider>
--lipsync-model <model>
--audio-provider <provider>
--audio-model <model>
--execute-audio
--execute-image
--execute-video
--execute-lipsync
--prompt-only
--force
--resume
--print-json
```

参数规则：

1. 默认 `provider-profile = grok_single_image`。
2. 图片、视频、对嘴型 provider/model 默认来自 `SessionContext/Variables.json` 中的 `default_image_config`、`default_video_config`、`default_lipsync_config`。
3. 对嘴型的默认 provider/model/extra_json 必须来自 `default_lipsync_config`；数据库只在模型调用前按 `api_key_ref` 补真实 API key 到内存。
3. `--prompt-only` 只生成 Prompt 和 execution plan audit，不调用图片、视频、对嘴型或音频生成。
4. `--execute-image=false` 时，如果 segment 需要新图片，则该 segment blocked，除非新图片已经存在。
5. `--execute-video=false` 时，可以完成图片和 Prompt，但视频 segment 标记为 pending / skipped_by_flag。
6. `--execute-lipsync=false` 时，可以生成 raw video，但不得把 raw video 写入 `Output/` 或 `SessionOutput/storyboard/Working/`；该 segment 标记为 `partial_raw_video_completed`。
7. `--force` 只清理并重建本工具拥有的 `Working/`、`Output/`、`Prompt/`、`Report/`，不清理 `SessionOutput/storyboard/Working/`；成功运行覆盖 StoryBoard Working 前必须先备份到 assets/history。
8. `--resume` 仅在输入 hash、模板 hash、plan hash、StoryBoard hash、reference manifest hash 与状态记录一致时复用已有中间结果。

## 6. Prompt 透明化合同

### 6.1 模板复制

运行开始时，必须把参考模板复制到：

```text
S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_VideoPlanExecutor_PromptTemplates.md
```

本工具运行过程中只能读取这个 Prompt 目录内的模板快照，不应直接读取源模板文件。

### 6.2 每次图片生成前的 Prompt 文件

每个需要生成图片的 segment，必须先写入：

```text
S9_05_02_VideoPlanExecutor/Prompt/PromptVariables_{dialogue_asset_key}_Image.json
S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_{dialogue_asset_key}_ImagePrompt.json
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{dialogue_asset_key}_Image_request.json
```

如果有模型响应，也写入：

```text
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{dialogue_asset_key}_Image_response.json
```

生成图片时必须使用 `PromptRendered_{dialogue_asset_key}_ImagePrompt.json` 或 `ModelCall_{dialogue_asset_key}_Image_request.json` 中的内容。不得在代码里另行拼接隐藏 Prompt。

### 6.3 每次视频生成前的 Prompt 文件

每个需要生成视频的 segment，必须先写入：

```text
S9_05_02_VideoPlanExecutor/Prompt/PromptVariables_{first_dialogue_asset_key}_Video.json
S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_{first_dialogue_asset_key}_VideoPrompt.json
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{first_dialogue_asset_key}_Video_request.json
```

如果有模型响应，也写入：

```text
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{first_dialogue_asset_key}_Video_response.json
```

生成视频时必须使用 `PromptRendered_{first_dialogue_asset_key}_VideoPrompt.json` 或 `ModelCall_{first_dialogue_asset_key}_Video_request.json` 中的内容。不得在代码里另行拼接隐藏 Prompt。

### 6.4 音频 Prompt

如果本工具执行音频生成，每个需要生成音频的 Dialogue 必须先写入：

```text
S9_05_02_VideoPlanExecutor/Prompt/PromptVariables_{dialogue_asset_key}_Audio.json
S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_{dialogue_asset_key}_AudioPrompt.json
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{dialogue_asset_key}_Audio_request.json
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{dialogue_asset_key}_Audio_response.json
```

如果音频由已有 Dialogue 音频槽提供，则不需要生成音频 Prompt，但 execution result 必须记录 `audio_source = existing_dialogue_audio`。

### 6.5 对嘴型模型调用审计

对嘴型通常不需要文本 Prompt，但每次调用前后都必须写入模型调用审计文件：

```text
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{first_dialogue_asset_key}_LipSync_request.json
S9_05_02_VideoPlanExecutor/Prompt/ModelCall_{first_dialogue_asset_key}_LipSync_response.json
```

规则：

1. request JSON 只能记录 provider、model、api_key_ref、输入 raw video 相对路径、输入音频相对路径、输出目标相对路径、非敏感参数。
2. request JSON 和 response JSON 不得包含真实 API key、authorization header、cookie、下载签名 URL 中的敏感 token。
3. 如果对嘴模型支持文本 Prompt 或额外指令，也必须先写入 `PromptRendered_{first_dialogue_asset_key}_LipSyncPrompt.json`，再发起调用。

### 6.6 Prompt 同步到 StoryBoard Working

成功生成或确认 Prompt 后，必须把业务 Prompt JSON 复制到：

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_ImagePrompt.json
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_VideoPrompt.json
```

这些文件的内容应与 `PromptRendered_*` 的业务 Prompt payload 一致，或包含同样的 `positive_prompt`、`negative_prompt`、`structured_prompt`、`template_blocks[]`、`extracted_fields`。

前端修改边界：

1. 前端修改 StoryBoard Working 中的 Prompt JSON 走单独 API，不属于本工具职责。
2. 本工具运行时不读取前端修改后的 Prompt JSON 作为输入覆盖自己的执行逻辑。
3. 本工具成功运行并准备覆盖已有 Prompt JSON 前，必须先按 History 规则备份旧 Prompt JSON。
4. 如果用户需要按前端改过的 Prompt 重新生成，应由前端对应 API 或后续专门执行入口调用，不走本工具的默认 deterministic prompt 流程。

## 7. 执行顺序

### 7.1 Prepare 阶段

1. 创建 `Working/`、`Output/`、`Prompt/`、`Report/`。
2. 校验四个目录内部无子目录。
3. 复制 `Variables.json` 到 Working。
4. 复制 `srt_storyboard.json` 到 Working。
5. 复制 `video_generation_plan.json` 到 Working。
6. 复制 Scene Profile 到 Working。
7. 复制人物一致性图片到 Working。
8. 复制产品一致性图片到 Working。
9. 复制人物 / 产品 manifest 到 Working。
10. 复制模板文档到 Prompt。
11. 计算输入 hash，写入 `Working/State_progress.json`。

### 7.2 Segment 执行阶段

按 `video_generation_plan.json` 中 Shot -> Scene -> Segment 顺序执行。

每个 segment 的顺序：

1. 读取 segment tasks。
2. 校验依赖：上一段最终视频尾帧、已有首帧、materialize source、计划输出路径、模型配置。
3. 执行 Dialogue 音频任务。
4. 按 segment 内 dialogue 顺序合成 `SegmentAudio_Final`，作为视频对嘴型输入；如果 segment 只有一个 Dialogue，也必须生成或确认 segment 级音频记录。
5. 将 Segment 合成音频写入 `Output/`，再同步到 `SessionOutput/storyboard/Working/`。
6. 如果 `need_image_prompt=true`，生成图片 Prompt 文件到 `Prompt/`。
7. 如果 `need_image=true`，调用图片生成，最终图片产物写入 `Output/`。
8. 将生成图片从 `Output/` 复制到 `SessionOutput/storyboard/Working/`。
9. 如果首帧来自上传/原素材新图槽，执行 `materialize_first_frame`，并把最终首帧图片写入 `Output/` 和 `SessionOutput/storyboard/Working/`。
10. 生成视频 Prompt 文件到 `Prompt/`。
11. 将视频 Prompt 同步到 `SessionOutput/storyboard/Working/`。
12. 如果 `need_video=true`，调用视频模型生成 raw video，raw video 只能写入 `Working/`。
13. 如果 `need_lipsync=true`，写入对嘴型模型调用 request 审计到 `Prompt/`。
14. 如果 `need_lipsync=true`，使用 Segment 合成音频和 raw video 调用对嘴型模型，生成对嘴后最终视频。
15. 如果 `need_lipsync=false`，将 raw video 校验通过后晋升为最终视频。
16. 将最终视频写入 `Output/`。
17. 覆盖 StoryBoard Working 中同名文件前，按 History 规则备份旧文件。
18. 将最终视频从 `Output/` 复制到 `SessionOutput/storyboard/Working/`。
19. 从最终 `Video_Final` 抽取 `TailFrame.png`，先写入 `Output/`。
20. 将最终尾帧从 `Output/` 复制到 `SessionOutput/storyboard/Working/`；只有该 TailFrame 成功发布后，后续 segment 的尾帧依赖才可解除。
21. 更新 `Working/State_progress.json`。
22. 继续下一个 segment。

Dialogue 音频回绑规则：

1. 生成或复制后的 Dialogue 音频发布到 `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.*` 后，必须立即同步写回 `srt_storyboard.json` 和 `koubo_storyboard_edit.json` 的对应 Dialogue 音频槽。
2. 音频回绑使用 Dialogue 级 `working_assets.audio` / `Audio_Final`，不能写入 Segment 级 `SegmentAudio_Final`。
3. 如果音频文件已落盘但 StoryBoard JSON 任一写回失败，Audio step 不能显示绿色完成，必须暴露绑定失败原因。
4. 下一次 `05_01` / `05_05` 生成计划时，应能从 StoryBoard JSON 或标准 Working 音频文件识别 Dialogue 音频已完成，不能再次误判缺音频。

中间视频约束：

1. raw video 文件命名建议为 `{first_dialogue_asset_key}_Video_Raw.{ext}`，只能保存在 `Working/`。
2. 对嘴模型下载的临时文件、重试文件、探测文件也只能保存在 `Working/`。
3. `Output/` 中的 `{first_dialogue_asset_key}_Video_Final.{ext}` 必须是最终可交付视频。
4. `SessionOutput/storyboard/Working/` 中的 `{first_dialogue_asset_key}_Video_Final.{ext}` 必须是最终可交付视频。
5. `SessionOutput/storyboard/Working/` 中的 `{first_dialogue_asset_key}_TailFrame.png` 必须从当前最终视频抽取，不能复用旧 Final 或 Raw 的尾帧。

### 7.3 失败继续策略

1. 某个 segment 生成失败，不应阻断后续不依赖它的 segment。
2. 依赖失败 segment 最终视频尾帧的后续 segment 必须 blocked。
3. 后续 segment 如果有自己的新图或原图触发新图生成，可以继续执行。
4. 对嘴型失败时，raw video 可以保留在 `Working/` 供排查，但不能写入 `Output/` 或 `SessionOutput/storyboard/Working/` 充当最终视频。
5. 对嘴型失败的 segment 状态为 `partial_raw_video_completed` 或 `failed_lipsync`；依赖该 segment 尾帧的后续 segment 默认 blocked。
6. 如果 `need_lipsync=false`，raw video 校验通过后可以晋升为最终视频，不视为中间视频泄漏。
7. 视频生成或对嘴型调用超时，状态为 `failed_timeout`，不进入 pending。
8. 失败原因必须写入 execution result。
9. 工具整体状态按实际结果汇总为：
   - `completed`
   - `completed_with_pending_items`
   - `completed_with_blocked_items`
   - `completed_with_failed_items`
   - `blocked`

### 7.3.1 执行态回传给 VideoPlan Modal

`05_02` 的 execution result 需要能驱动 VideoPlan Modal 的 Segment pipeline 展示。每个 Segment 至少回传 AUDIO / First Frame / VIDEO / SYNC 四类步骤的状态：

```json
{
  "source_plan_hash": "sha256...",
  "source_plan_run_id": "vp_20260531_000001",
  "segment_id": "shot_001_scene_001_segment_001",
  "steps": {
    "audio": {
      "status": "completed",
      "output_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_SegmentAudio_Final.wav"
    },
    "first_frame": {
      "status": "completed",
      "output_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_Image_01.png",
      "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_srt_0001_01_ImagePrompt.json"
    },
    "video": {
      "status": "completed",
      "output_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_Video_Final.mp4",
      "prompt_path": "S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_srt_0001_01_VideoPrompt.json"
    },
    "sync": {
      "status": "skipped_by_cutaway",
      "reason": "user_marked_cutaway",
      "decision_source": "dialogue.video_plan.is_talking_head"
    }
  }
}
```

状态语义：

1. `running`：前端显示黄色旋转，颜色与 TTS 生成中状态一致。
2. `completed`：前端显示绿色，点击可查看对应产物和 prompt / audit。
3. `skipped_by_cutaway`：前端显示灰色禁用态，表示 Dialogue 已人工标记为空镜，Sync 不运行。
4. `failed` / `failed_timeout`：前端显示错误态，点击或悬停查看失败原因。
5. First Frame、Video、Sync 的 `output_path` 和 prompt / audit path 必须足够支撑 Modal 点击查看。
6. `source_plan_hash` / `source_plan_run_id` 必须与 `05_01` 当前 plan 的标识一致；不一致时前端只能把该结果作为旧结果查看，不能绑定为当前 plan 的完成态。

如果 `need_lipsync=false` 且 `lipsync_reason=user_marked_cutaway`：

1. `05_02` 不调用对嘴型模型。
2. raw video 校验通过后晋升为最终视频。
3. Sync step 记录为 `skipped_by_cutaway`。
4. 该跳过状态不算失败，也不阻断后续 tail frame 依赖。

如果用户在执行后设置空镜或恢复口播：

1. 只重新生成 `05_01` plan，不自动删除或覆盖 `05_02` execution result。
2. 旧 execution result 因 plan 标识不匹配而从当前完成态解绑，但仍允许查看。
3. 只有用户在 Modal 中确认 `清除上次执行内容` 后，才清理 `05_02` 工具状态并允许下一次从当前 plan 重新执行。
4. 清理前，已发布到 StoryBoard Working 的旧 Sync 视频、旧最终视频、prompt 和 audit 必须按 History 规则备份；不能静默丢弃。
5. `05_02` 正在运行时，所有清理和 Dialogue 口播状态修改入口都必须 disabled，只允许查看资源。

### 7.4 视频生成全流程确认版

本工具对每个 video segment 的完整媒体链路如下：

1. 读取计划：从 `video_generation_plan.json` 取 segment、dialogue、首帧依赖、计划时长和输出 key。
2. 准备模型：从 `Variables.json` 取 `default_image_config`、`default_video_config`、`default_lipsync_config` 的 provider/model/api_key_ref。
3. 运行时取密钥：只在模型调用前按配置从数据库读取 API key 到内存。
4. 准备音频：确认该 segment 对应 dialogue 音频；缺失时按音频任务生成或标记 blocked；然后按 dialogue 顺序合成 segment 级音频。
5. 准备首帧：旧图先生成新图；新图槽位图直接 materialize；链式段使用上一段最终视频尾帧。
6. 生成最终图片：最终图片进入 `Output/`，再同步到 `SessionOutput/storyboard/Working/`。
7. 生成视频 Prompt：Prompt 先落到 `Prompt/`，再调用视频模型。
8. 生成 raw video：raw video 只进入 `Working/`，不得进入 `Output/` 或 StoryBoard Working。视频模型调用的目标时长必须来自当前 segment 的 `planned_video_duration` / `video_duration_seconds`；Grok / xAI 不允许写死 10 秒，provider 只接受整数秒时按计划时长向上取整，并在模型请求审计中同时记录原始目标时长和 provider 实参时长。
9. 执行单段对嘴型：`need_lipsync=true` 时用 raw video + segment 音频调用对嘴型模型，一个 video segment 调用一次。
10. 生成最终视频：`need_lipsync=true` 时对嘴后视频成为 `{first_dialogue_asset_key}_Video_Final.{ext}`；`need_lipsync=false` 时 raw video 校验后复制为 `{first_dialogue_asset_key}_Video_Final.{ext}`。
11. 抽取最终尾帧：从最终视频抽取 `TailFrame.png`，供后续链式 segment 使用；抽取失败时 Final 不能视为完整可消费。
12. 不处理短句视频裁剪：视频模型最小时长超过 dialogue 时长时，裁剪和时间轴对齐交给后续拼接工具。
13. 不做拼接：本工具不拼 Scene、Shot 或 Task 成片，拼接交给后续工具。

## 8. 文件命名

### 8.1 Working 快照

```text
InputFrom_0_Variables.json
InputFrom_7_srt_storyboard.json
InputFrom_8_video_generation_plan.json
InputFrom_5_tts_builder_candidates.json
InputFrom_5_scene_profile_response.json
InputParams_video_plan_executor.json
State_progress.json
Ref_Host.png
Ref_Product.png
Ref_HostManifest.json
Ref_ProductManifest.json
{dialogue_asset_key}_SourceFrame.{ext}
{dialogue_asset_key}_FirstFrame.{ext}
{dialogue_asset_key}_PreviousTailFrame.png
{first_dialogue_asset_key}_SegmentAudio_Working.{ext}
{first_dialogue_asset_key}_Video_Raw.{ext}
{first_dialogue_asset_key}_LipSync_Temp.{ext}
{first_dialogue_asset_key}_LipSync_Download.{ext}
```

### 8.2 Prompt 文件

```text
Ref_05_02_VideoPlanExecutor_PromptTemplates.md
PromptVariables_{dialogue_asset_key}_Image.json
PromptRendered_{dialogue_asset_key}_ImagePrompt.json
ModelCall_{dialogue_asset_key}_Image_request.json
ModelCall_{dialogue_asset_key}_Image_response.json
PromptVariables_{first_dialogue_asset_key}_Video.json
PromptRendered_{first_dialogue_asset_key}_VideoPrompt.json
ModelCall_{first_dialogue_asset_key}_Video_request.json
ModelCall_{first_dialogue_asset_key}_Video_response.json
ModelCall_{first_dialogue_asset_key}_LipSync_request.json
ModelCall_{first_dialogue_asset_key}_LipSync_response.json
PromptVariables_{dialogue_asset_key}_Audio.json
PromptRendered_{dialogue_asset_key}_AudioPrompt.json
ModelCall_{dialogue_asset_key}_Audio_request.json
ModelCall_{dialogue_asset_key}_Audio_response.json
```

### 8.3 Output 文件

```text
video_plan_execution_result.json
{dialogue_asset_key}_Audio_Final.{ext}
{first_dialogue_asset_key}_SegmentAudio_Final.{ext}
{dialogue_asset_key}_Image_01.{ext}
{dialogue_asset_key}_ImagePrompt.json
{first_dialogue_asset_key}_VideoPrompt.json
{first_dialogue_asset_key}_Video_Final.{ext}
{first_dialogue_asset_key}_TailFrame.png
```

`Output/` 不允许保存 `{first_dialogue_asset_key}_Video_Raw.{ext}`、`*_LipSync_Temp.*`、`*_LipSync_Download.*` 或其它中间视频。

### 8.4 StoryBoard Working 同步文件

```text
SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.{ext}
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_SegmentAudio_Final.{ext}
SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_01.{ext}
SessionOutput/storyboard/Working/{dialogue_asset_key}_ImagePrompt.json
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_VideoPrompt.json
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_Video_Final.{ext}
SessionOutput/storyboard/Working/{first_dialogue_asset_key}_TailFrame.png
```

## 9. 输出 JSON

### 9.1 Output/video_plan_execution_result.json

```json
{
  "schema_version": "analysis_v1_video_plan_execution_result_0.1",
  "tool": "05_02_VideoPlanExecutor",
  "tool_version": "0.1.0",
  "source_plan_path": "SessionOutput/storyboard/video_generation_plan.json",
  "source_storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
  "template_path": "OpenCrew/ToolLibrary/Analysis_V1/Reference/05_02_VideoPlanExecutor_PromptTemplates.md",
  "template_prompt_snapshot": "S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_VideoPlanExecutor_PromptTemplates.md",
  "settings": {
    "provider_profile": "grok_single_image",
    "prompt_only": false,
    "execute_audio": true,
    "execute_image": true,
    "execute_video": true,
    "execute_lipsync": true
  },
  "summary": {
    "segment_count": 0,
    "completed_segment_count": 0,
    "pending_segment_count": 0,
    "blocked_segment_count": 0,
    "failed_segment_count": 0,
    "generated_audio_count": 0,
    "generated_image_count": 0,
    "generated_raw_video_count": 0,
    "generated_final_video_count": 0,
    "lipsync_completed_count": 0,
    "lipsync_failed_count": 0,
    "generated_tail_frame_count": 0
  },
  "segments": [
    {
      "segment_id": "",
      "status": "completed",
      "dialogue_ids": [],
      "tasks": {
        "need_audio": true,
        "need_image_prompt": true,
        "need_image": true,
        "need_video_prompt": true,
        "need_video": true,
        "need_lipsync": true
      },
      "working_inputs": {
        "source_frame": "",
        "first_frame": "",
        "previous_tail_frame": "",
        "raw_video_path": "S9_05_02_VideoPlanExecutor/Working/srt_0001_01_Video_Raw.mp4",
        "host_reference": "S9_05_02_VideoPlanExecutor/Working/Ref_Host.png",
        "product_reference": "S9_05_02_VideoPlanExecutor/Working/Ref_Product.png"
      },
      "prompt_files": {
        "image_prompt": "S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_srt_0001_01_ImagePrompt.json",
        "video_prompt": "S9_05_02_VideoPlanExecutor/Prompt/PromptRendered_srt_0001_01_VideoPrompt.json",
        "lipsync_request": "S9_05_02_VideoPlanExecutor/Prompt/ModelCall_srt_0001_01_LipSync_request.json",
        "lipsync_response": "S9_05_02_VideoPlanExecutor/Prompt/ModelCall_srt_0001_01_LipSync_response.json"
      },
      "tool_outputs": {
        "audio_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_Audio_Final.wav",
        "segment_audio_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_SegmentAudio_Final.wav",
        "image_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_Image_01.png",
        "image_prompt_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_ImagePrompt.json",
        "video_prompt_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_VideoPrompt.json",
        "video_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_Video_Final.mp4",
        "tail_frame_path": "S9_05_02_VideoPlanExecutor/Output/srt_0001_01_TailFrame.png"
      },
      "storyboard_outputs": {
        "audio_path": "SessionOutput/storyboard/Working/srt_0001_01_Audio_Final.wav",
        "segment_audio_path": "SessionOutput/storyboard/Working/srt_0001_01_SegmentAudio_Final.wav",
        "image_path": "SessionOutput/storyboard/Working/srt_0001_01_Image_01.png",
        "image_prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_ImagePrompt.json",
        "video_prompt_path": "SessionOutput/storyboard/Working/srt_0001_01_VideoPrompt.json",
        "video_path": "SessionOutput/storyboard/Working/srt_0001_01_Video_Final.mp4",
        "tail_frame_path": "SessionOutput/storyboard/Working/srt_0001_01_TailFrame.png"
      },
      "blocked_reason": "",
      "failed_reason": ""
    }
  ],
  "created_at": "2026-05-30T00:00:00Z"
}
```

同一 JSON 复制到：

```text
S9_05_02_VideoPlanExecutor/Output/video_plan_execution_result.json
SessionOutput/storyboard/video_plan_execution_result.json
```

### 9.2 Report/Result.json

```json
{
  "tool": "05_02_VideoPlanExecutor",
  "status": "completed",
  "inputs": {
    "variables": "S9_05_02_VideoPlanExecutor/Working/InputFrom_0_Variables.json",
    "storyboard": "S9_05_02_VideoPlanExecutor/Working/InputFrom_7_srt_storyboard.json",
    "plan": "S9_05_02_VideoPlanExecutor/Working/InputFrom_8_video_generation_plan.json",
    "template": "S9_05_02_VideoPlanExecutor/Prompt/Ref_05_02_VideoPlanExecutor_PromptTemplates.md"
  },
  "outputs": {
    "tool_output": "S9_05_02_VideoPlanExecutor/Output/video_plan_execution_result.json",
    "session_output": "SessionOutput/storyboard/video_plan_execution_result.json"
  },
  "summary": {},
  "warnings": [],
  "errors": []
}
```

## 10. srt_storyboard.json 写回策略

第一版建议不直接改写 `srt_storyboard.json` 的 Shot / Scene / Dialogue 结构。

但本工具可以选择性更新 Dialogue 的素材槽位，前提是 UI 和后端已确认支持：

1. 生成或复用 Dialogue 音频后，只能写入对应 Dialogue 的标准槽位路径：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.*`。
2. 生成图片后，只能写入对应 Dialogue 的标准槽位路径：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_New.*`。
3. 生成视频后，只能写入对应 Dialogue 的标准槽位路径：`SessionOutput/storyboard/Working/{dialogue_asset_key}_Video_Final.*`。
4. 如果需要使用工具内部临时文件，必须先发布到 `SessionOutput/storyboard/Working/` 或只保留在执行结果中；不得把工具内部临时路径写入 `working_assets`。

禁止写回 StoryBoard 当前槽位的路径：

```text
S9_05_02_VideoPlanExecutor/Working/*_DialogueAudio.*
S9_05_02_VideoPlanExecutor/Working/*_SegmentAudio_Final.*
S9_05_02_VideoPlanExecutor/Output/*
任意 S*_*/Working/* 工具临时文件
```

其中：

- `*_DialogueAudio.*` 是本工具为了合成 Segment 音频复制出的临时输入，只能存在于工具 `Working/`。
- `*_SegmentAudio_Final.*` 是 Segment 级合并音频，可以作为计划输出和后续拼接输入，但不能覆盖任何 Dialogue 的 `working_assets.audio.path`。
- `Audio_Final` 才是 Dialogue 级当前音频槽位，`srt_storyboard.json` 和 `koubo_storyboard_edit.json` 必须保持一致。

如果第一版不做槽位写回，则必须依赖：

```text
SessionOutput/storyboard/Working/
SessionOutput/storyboard/video_plan_execution_result.json
```

供 UI 或后续工具索引生成结果。

无论是否写回槽位，都不得重排 `shots[]`、`scenes[]` 或 `dialogue_items[]`。

## 11. Rerun / Resume

### 11.1 force

`--force` 只清理本工具目录：

```text
S9_05_02_VideoPlanExecutor/Working/
S9_05_02_VideoPlanExecutor/Output/
S9_05_02_VideoPlanExecutor/Prompt/
S9_05_02_VideoPlanExecutor/Report/
```

`--force` 不删除 StoryBoard Working 文件，也不删除 `SessionOutput/storyboard/video_plan_execution_result.json`。如果本次运行成功并需要覆盖 StoryBoard Working 中的同名文件，必须先备份到 `SessionOutput/storyboard/assets/history/`，然后再覆盖。

禁止清理：

```text
SessionOutput/storyboard/Working/
SessionOutput/storyboard/assets/history/
SessionOutput/storyboard/video_plan_execution_result.json
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/video_generation_plan.json
SessionOutput/storyboard/assets/
SessionContext/
上游工具目录
```

### 11.2 resume

`--resume` 可复用的条件：

1. `InputFrom_0_Variables.json` hash 一致。
2. `InputFrom_7_srt_storyboard.json` hash 一致。
3. `InputFrom_8_video_generation_plan.json` hash 一致。
4. `Ref_05_02_VideoPlanExecutor_PromptTemplates.md` hash 一致。
5. `Ref_Host.*` hash 一致或缺失状态一致。
6. `Ref_Product.*` hash 一致或缺失状态一致。
7. reference manifest hash 一致或缺失状态一致。
8. segment 输出文件真实存在。
9. Prompt 文件真实存在且 `template_blocks[]` 合法。
10. 输出 JSON schema 校验通过。

任一条件不满足，该 segment 必须重新生成 Prompt；如果素材缺失则重新生成素材或 blocked。

已提交但未完成的异步模型任务如果超过工具配置 timeout，恢复后标记为 `failed_timeout`，不继续 pending，也不重复提交同一模型任务；如需重试必须显式重新运行。

## 12. Blocked / Failed 条件

Blocked 条件：

1. `video_generation_plan.json` 缺失。
2. `srt_storyboard.json` 缺失。
3. 模板文件缺失。
4. segment 缺少 `segment_id`。
5. segment 缺少可解析的 `dialogue_ids[]`。
6. plan 指向的首帧、旧图、materialize source 或 previous tail 不存在。
7. `need_image=true` 但图片生成被禁用且没有可用新图。
8. `need_video=true` 但视频生成被禁用且没有可用视频。
9. `need_lipsync=true` 但对嘴型生成被禁用且没有可用最终视频。
10. 依赖上一个 segment 最终视频尾帧，但该尾帧未生成。
11. `default_image_config`、`default_video_config` 或 `default_lipsync_config` 缺少必要 provider/model/api_key_ref。
12. 覆盖 StoryBoard Working 同名文件前无法完成 History 备份。

Failed 条件：

1. Prompt 生成失败。
2. 图片模型调用失败。
3. 视频模型调用失败。
4. 音频模型调用失败。
5. 对嘴型模型调用失败。
6. 视频模型或对嘴型模型调用超时。
7. 生成产物存在但格式不合法。
8. 最终产物无法复制到 Output 或 SessionOutput。
9. 尾帧抽取失败。

Failed 和 blocked 的区别：

```text
blocked = 输入条件或依赖条件不满足，尚未进入对应生成调用。
failed = 已进入执行步骤，但生成、解析、复制或校验失败。
```

## 13. 敏感信息与安全

1. `Prompt/`、`Output/`、`Report/` 中不得写入 API key、数据库连接串、cookie、access token、authorization header。
2. `Working/` 中的中间视频和状态文件也不得写入 API key、数据库连接串、cookie、access token、authorization header。
3. 模型调用 request JSON 中只能记录 provider、model、api_key_ref、prompt path、输入文件相对路径和非敏感参数。
4. 如果实际调用必须使用密钥，密钥只能在进程内读取，不进入任何文件。
5. `Result.json` 必须执行敏感信息扫描。
6. 如果发现敏感字段，工具状态应为 `blocked` 或 `failed_sensitive_output_scan`。

## 14. 测试计划

### 14.0 真实模型调用验收命令

项目根目录的 pytest 默认参数包含 `--block-network`，真实模型验收必须显式覆盖该默认项，否则测试会被本地网络拦截而不是执行 provider 调用。

```bash
OPENCREW_REAL_MODEL_TESTS=1 .venv/bin/python -m pytest -q -o addopts='' tests/analysis_v1/test_video_plan_executor_real_models.py -s
```

该测试必须真实读取 `tool_media_provider_configs` 中启用的 image / video / lipsync 配置，实际调用图片模型、视频模型和对嘴型模型，并验证最终图片、Segment 音频、最终视频、尾帧都进入 `SessionOutput/storyboard/Working/`。

### 14.1 目录和快照

1. `test_creates_flat_working_output_prompt_report_dirs`
2. `test_no_subdirectories_inside_tool_dirs`
3. `test_copies_template_to_prompt_dir`
4. `test_copies_plan_storyboard_variables_to_working`
5. `test_copies_scene_profile_to_working_when_available`
6. `test_copies_host_product_references_to_working_when_available`
7. `test_missing_host_product_references_does_not_block_prompt_generation`
8. `test_raw_video_and_lipsync_temp_files_stay_in_working_only`
9. `test_default_model_configs_missing_blocks`

### 14.2 Prompt 透明化

1. `test_image_prompt_written_before_image_generation`
2. `test_video_prompt_written_before_video_generation`
3. `test_audio_prompt_written_before_audio_generation_when_audio_enabled`
4. `test_model_call_uses_prompt_file_content_not_hidden_code_concat`
5. `test_prompt_json_contains_template_blocks`
6. `test_image_prompt_contains_reference_priority`
7. `test_video_prompt_contains_structured_prompt`
8. `test_storyboard_reference_policy_included_when_reference_seed_exists`
9. `test_prompt_files_are_copied_to_storyboard_working`
10. `test_lipsync_model_call_request_written_without_secret`
11. `test_frontend_modified_prompt_is_not_consumed_by_default_executor`

### 14.3 执行计划消费

1. `test_consumes_existing_video_generation_plan_only`
2. `test_does_not_replan_segments`
3. `test_original_image_segment_generates_image_then_video`
4. `test_new_image_segment_skips_image_generation_and_generates_video`
5. `test_materialize_uploaded_image_copies_final_image_to_output_and_storyboard_working`
6. `test_previous_tail_segment_uses_tail_frame_as_first_frame`
7. `test_tail_dependency_missing_blocks_dependent_segment`
8. `test_independent_later_segment_continues_after_failed_prior_segment`
9. `test_each_raw_video_runs_lipsync_before_final_video_output`
10. `test_lipsync_failure_does_not_publish_raw_video_as_final`
11. `test_segment_audio_composed_from_dialogues_in_order`
12. `test_need_lipsync_false_promotes_valid_raw_video_to_final`
13. `test_short_video_extra_duration_left_for_compose_tool`

### 14.4 产物同步

1. `test_generated_image_written_to_output_then_storyboard_working`
2. `test_raw_video_written_to_working_only`
3. `test_lipsynced_final_video_written_to_output_then_storyboard_working`
4. `test_tail_frame_extracted_from_final_video_written_to_output_then_storyboard_working`
5. `test_generated_prompt_written_to_output_then_storyboard_working`
6. `test_execution_result_written_to_output_and_session_output`
7. `test_storyboard_structure_not_reordered`
8. `test_segment_audio_written_to_output_then_storyboard_working`
9. `test_storyboard_working_existing_files_backed_up_to_history_before_overwrite`

### 14.5 Provider profile

1. `test_grok_prompt_contains_body_ratio_and_microphone_constraints`
2. `test_veo_lite_prompt_contains_product_continuity_and_no_action_script`
3. `test_wan_prompt_contains_exposure_white_balance_and_audio_mouth_only`
4. `test_veo31_prompt_contains_no_hard_cut_and_no_product_regeneration`
5. `test_provider_profile_recorded_in_video_prompt_json`
6. `test_default_image_video_lipsync_models_read_from_variables`
7. `test_api_keys_loaded_from_database_memory_only`

### 14.6 force / resume

1. `test_force_cleans_only_tool_owned_dirs_and_outputs`
2. `test_force_does_not_delete_storyboard_assets_or_generation_plan`
3. `test_resume_reuses_segment_when_hashes_and_files_match`
4. `test_resume_regenerates_prompt_when_template_hash_changes`
5. `test_resume_regenerates_prompt_when_host_reference_hash_changes`
6. `test_resume_regenerates_prompt_when_product_reference_hash_changes`
7. `test_resume_blocks_when_output_file_missing`
8. `test_async_provider_timeout_marks_failed_timeout`

### 14.7 安全和错误

1. `test_missing_video_generation_plan_blocks`
2. `test_missing_storyboard_blocks`
3. `test_missing_template_blocks`
4. `test_missing_first_frame_blocks_segment`
5. `test_image_generation_failure_marks_segment_failed`
6. `test_video_generation_failure_marks_segment_failed`
7. `test_lipsync_generation_failure_marks_segment_partial_or_failed_lipsync`
8. `test_tail_frame_extract_failure_marks_segment_failed`
9. `test_sensitive_output_scan_blocks_secret_leak`
10. `test_no_api_key_written_to_prompt_working_output_report_or_session_output`
11. `test_history_backup_failure_blocks_overwrite`

### 14.8 Provider 网络调用边界

1. `test_openai_gpt_image_generation_still_uses_openai_endpoints`
2. `test_gemini_image_generation_uses_nano_banana_v1beta_query_key_payload`
3. `test_gemini_tts_matches_step03_query_key_access`
4. `test_xai_video_does_not_force_mihomo_proxy`
5. `test_sync_lipsync_does_not_force_mihomo_proxy`
6. `test_provider_network_adapters_do_not_share_global_proxy_precheck`
7. `test_failed_execution_step_overrides_stale_storyboard_working_file_in_ui_counts`
8. `test_provider_error_messages_redact_keys_but_preserve_endpoint_and_step`

### 14.9 空镜 / 产品特写 Prompt

1. `test_cutaway_prompts_are_product_only_without_host_or_talking_face`
2. `test_cutaway_image_references_exclude_host_reference`
3. `test_cutaway_image_prompt_requires_complete_product_replacement`
4. `test_cutaway_image_negative_prompt_blocks_partial_product_replacement`
5. `test_cutaway_video_prompt_uses_product_only_template`
6. `test_cutaway_video_prompt_does_not_contain_talking_head_or_mouth_movement_positive`
7. `test_cutaway_video_negative_prompt_blocks_talking_package_face`

## 15. 验收标准

1. 运行后存在：

```text
S9_05_02_VideoPlanExecutor/Working/
S9_05_02_VideoPlanExecutor/Output/
S9_05_02_VideoPlanExecutor/Prompt/
S9_05_02_VideoPlanExecutor/Report/
```

2. 四个目录内部均无子目录。
3. 模板文档复制到 `Prompt/Ref_05_02_VideoPlanExecutor_PromptTemplates.md`。
4. `video_generation_plan.json`、`srt_storyboard.json`、`Variables.json`、Scene Profile 复制到 Working。
5. 人物和产品一致性图片复制到 Working。
6. 每个图片生成前都有 `PromptRendered_*_ImagePrompt.json`。
7. 每个视频生成前都有 `PromptRendered_*_VideoPrompt.json`。
8. 图片和视频生成调用可以从 Prompt 文件复现。
9. 生成图片先进入 Output，再进入 `SessionOutput/storyboard/Working/`。
10. raw video 和对嘴临时视频只进入 Working，不进入 Output 或 StoryBoard Working。
11. Segment 合成音频先进入 Output，再进入 `SessionOutput/storyboard/Working/`。
12. 最终视频先进入 Output，再进入 `SessionOutput/storyboard/Working/`；`need_lipsync=true` 时是对嘴后视频，`need_lipsync=false` 时是 raw video 校验后晋升的最终视频。
13. 最终尾帧从最终视频抽取，先进入 Output，再进入 `SessionOutput/storyboard/Working/`。
14. 覆盖 StoryBoard Working 同名文件前，旧文件已经备份到 `SessionOutput/storyboard/assets/history/`。
15. Prompt JSON 也同步到 `SessionOutput/storyboard/Working/`。
16. `Output/video_plan_execution_result.json` 和 `SessionOutput/storyboard/video_plan_execution_result.json` 内容一致。
17. `Report/Result.json` 与 `--print-json` 结构一致。
18. 不泄露 API key、数据库连接串或 token。

## 16. 工具通用问题逐条回答

本章对应 `Analysis_V1_SRT_Detail_工具迁移实现路径.md` 中“每个工具实现前必须回答的问题”。`05_02_VideoPlanExecutor.py` 在编码前按以下答案执行。

### 16.1 是否最小程度生成中间文件和产出物？

答案：是，但本工具属于多模型执行器，需要保留可审计 Prompt、模型请求、模型响应、生成产物和断点状态；这些文件都必须服务于执行复现、页面绑定、下游拼接或错误恢复。

允许生成：

1. `Working/`：只保存输入快照、引用素材快照、首帧快照、尾帧依赖快照、raw video、对嘴临时视频、`State_progress.json`。
2. `Prompt/`：只保存模板快照、Prompt 变量、渲染后的图片/视频/音频 Prompt、模型请求和响应审计。
3. `Output/`：只保存本工具实际生成或 materialize 后要交付给 StoryBoard 的音频、最终图片、最终视频、最终尾帧、Prompt JSON、`video_plan_execution_result.json`。
4. `Report/`：只保存 `Result.json` 和人工/自动 QA 所需报告。
5. `SessionOutput/storyboard/Working/`：只同步 UI 和后续工具需要读取的最终业务文件。

不允许生成：

1. 重复的 Markdown 摘要。
2. 与 `video_plan_execution_result.json` 等价的重复 Manifest。
3. `Working/`、`Prompt/`、`Output/`、`Report/` 内部子目录。
4. 调试用临时文件长期留存到正式输出合同。

### 16.2 是否需要连接数据库？

答案：需要受限读取，但只允许用于模型 API key 解析。

本工具不得查询 Task、Session、Attempt、Prompt Version、StoryBoard 或业务状态。所有业务运行信息必须来自：

1. `SessionContext/Variables.json`
2. `SessionOutput/storyboard/srt_storyboard.json`
3. `SessionOutput/storyboard/video_generation_plan.json`
4. Scene Profile 快照
5. 当前 StoryBoard 中已经落位的人物/产品/图片/视频素材

允许的数据库访问只有一类：根据 `Variables.json` 中的 `default_image_config`、`default_video_config`、`default_lipsync_config`，读取对应模型 API key 到进程内存中用于调用。provider/model 必须来自 Variables 快照；命令行同名参数只能作为兼容校验，禁止选择或覆盖不同模型。对嘴型默认模型的 provider/model/extra_json 不从数据库重新选择，必须使用 `default_lipsync_config` 快照。

规则：

1. API key 只进内存，不落盘。
2. 模型调用审计只记录 provider、model、api_key_ref、has_api_key 和非敏感参数。
3. 如果 `Variables.json` 中模型 provider/model 或 api_key_ref 信息不足，本工具必须返回 `blocked` 或将对应 segment 标记为 `blocked`。
4. 不得通过数据库补查 StoryBoard、Prompt、Task 参数或业务素材。

#### 16.2.1 踩坑记录：数据库 active 模型覆盖 Session 模型

已踩坑：Session 的 `default_video_config` 已保存 `openrouter / bytedance/seedance-2.0`（Max SD 2），但后端启动 `05_02` 时又从 `tool_media_provider_configs` 读取数据库全局 `active=true` 的 `xai / grok-imagine-video`，并拼成 `--video-provider/--video-model`。由于旧执行器让命令行参数优先于 Variables，最终错误路由到 `video_grok.py` 并复制 `Video_Grok.md`。

永久约束：

1. `05_02` 的图片、视频、对嘴型执行模型唯一业务来源是 `SessionContext/Variables.json` 对应的 `default_*_config`。
2. 数据库全局 active 模型不得进入 `05_02` 启动命令，也不得参与 provider/module/template 路由。
3. 数据库只按 Variables 已选 provider/model 解析 API key 等秘密到进程内存。
4. 非空命令行 provider/model 与 Variables 不一致时必须阻断，禁止静默覆盖。
5. 视频 Prompt 必须按 `default_video_config -> video_module_for() -> 模块模板` 生成；Max SD 2 必须命中 `video_openrouter.py -> Video_OpenRouter.md`。

### 16.3 是否需要产出或更新 SessionContext？

答案：否。

`05_02` 只读 `SessionContext/Variables.json`，不写回 `Variables.json`，也不新增 `SessionContext` 全局文件。

原因：

1. 本工具产物是 StoryBoard 业务素材，不是全局输入。
2. 图片、Segment 合成音频、视频、尾帧、Prompt 和执行结果应归属 `SessionOutput/storyboard/`。
3. 执行状态应归属本工具 `Working/State_progress.json` 和 `Report/Result.json`。
4. 避免后续工具误把一次执行的素材状态当成全局会话状态。

### 16.4 本工具产出物是什么？给后面哪一步使用？

核心产出物：

1. `Output/video_plan_execution_result.json`
2. `SessionOutput/storyboard/video_plan_execution_result.json`
3. `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.{ext}`
4. `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_SegmentAudio_Final.{ext}`
5. `SessionOutput/storyboard/Working/{dialogue_asset_key}_Image_01.{ext}`
6. `SessionOutput/storyboard/Working/{dialogue_asset_key}_ImagePrompt.json`
7. `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_VideoPrompt.json`
8. `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_Video_Final.{ext}`
9. `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_TailFrame.png`
10. `SessionOutput/storyboard/assets/history/batch_*_05_02_overwrite_backup/*` 覆盖前备份文件和 manifest。
11. `Report/Result.json`

消费者：

1. StoryBoard UI：读取 `SessionOutput/storyboard/Working/` 展示图片、视频、音频、Prompt 和状态。
2. 后续视频拼接工具：读取每个 segment 的最终视频、Segment 合成音频、尾帧、计划执行结果。
3. 人工 QA 报告：读取 Prompt JSON、模型请求/响应审计和 execution result。
4. 断点续跑：读取 `Working/State_progress.json`、Prompt hash、输入 hash 和已生成文件 hash。

StoryBoard 写回约束：

1. Dialogue 当前音频槽位只能引用 `SessionOutput/storyboard/Working/{dialogue_asset_key}_Audio_Final.{ext}`、上传音频素材池路径或原始音频素材路径。
2. Segment 合成音频 `SessionOutput/storyboard/Working/{first_dialogue_asset_key}_SegmentAudio_Final.{ext}` 只能由计划、执行结果或后续拼接工具引用，不得写入 Dialogue `working_assets.audio.path`。
3. 工具 `Working/` 中的 `*_DialogueAudio.*`、raw video、对嘴临时视频、首帧临时图、依赖尾帧临时图都不能反写到 `srt_storyboard.json` 或 `koubo_storyboard_edit.json` 当前槽位。
4. 写回后，同一 Dialogue 在 `srt_storyboard.json` 与 `koubo_storyboard_edit.json` 中的 `Audio_Final`、`Image_New`、`Video_Final` 路径必须一致；不一致时视为状态错误，不能让 UI 以目录扫描或缓存兜底。
5. 生成 / 复制 Dialogue 音频后，如果只写文件、不写回 StoryBoard JSON，视为未完成；后续计划误判缺音频属于需求不满足。

缺失策略：

1. 缺少 `video_generation_plan.json` 或 `srt_storyboard.json`：工具级 `blocked`。
2. 某个 segment 缺少必需首帧且无法从前段尾帧取得：segment `blocked`，继续执行不依赖它的后续 segment。
3. 某个图片、视频、音频或对嘴型生成失败：segment `failed` 或 `failed_lipsync`，依赖它最终尾帧的后续 segment `blocked`。
4. 缺少人物/产品一致性参考：不阻断；Prompt 降级为保持输入图中已确定的人物/产品身份。

### 16.5 是否按照 Rerun 和断点继续方式实现？

答案：是。

执行结构必须为：

```text
prepare -> run -> finalize
```

原始状态：

1. `S9_05_02_VideoPlanExecutor/` 不存在，或其中四个一级目录为空。
2. 本工具本次 run 尚未产生新的 `Output/video_plan_execution_result.json`。
3. `SessionOutput/storyboard/Working/` 可能已有前端或历史运行生成的业务文件；这些文件不属于 `--force` 原始状态清理范围。

`--force` 行为：

1. 清理并重建 `S9_05_02_VideoPlanExecutor/Working/`。
2. 清理并重建 `S9_05_02_VideoPlanExecutor/Output/`。
3. 清理并重建 `S9_05_02_VideoPlanExecutor/Prompt/`。
4. 清理并重建 `S9_05_02_VideoPlanExecutor/Report/`。
5. 不删除 `SessionOutput/storyboard/video_plan_execution_result.json`。
6. 不删除或清理 `SessionOutput/storyboard/Working/` 中任何文件。
7. 本次运行成功并准备覆盖 StoryBoard Working 同名文件前，必须先复制旧文件到 `SessionOutput/storyboard/assets/history/`。
8. 不删除 `srt_storyboard.json`、`video_generation_plan.json`、StoryBoard assets、`SessionContext`、上游工具目录或用户手动落位素材。

`--resume` 行为：

1. 必须重新执行依赖自检。
2. 必须比较输入 hash、模板 hash、plan hash、StoryBoard hash、Scene Profile hash、reference hash。
3. 已完成 segment 只有在输出文件真实存在、Prompt 文件真实存在、hash 匹配、schema 校验通过时才能复用。
4. 任一条件不满足，该 segment 必须重新生成 Prompt；如素材缺失则重新生成素材或标记 `blocked`。
5. `resume` 不允许跳过 Prompt 透明化，不允许直接复用代码内隐式 Prompt。

## 17. 已确认需求摘要

| 问题 | 结论 |
| --- | --- |
| 工具名 | `05_02_VideoPlanExecutor.py` |
| 步骤目录 | `S9_05_02_VideoPlanExecutor/` |
| 是否复制模板到 Prompt | 是，复制 `05_02_VideoPlanExecutor_PromptTemplates.md` 到 Prompt |
| Prompt 目录是否有子目录 | 否，必须平铺 |
| Working 目录是否有子目录 | 否，必须平铺 |
| Output 目录是否有子目录 | 否，必须平铺 |
| 是否复制 plan/storyboard/scene profile 到 Working | 是 |
| 是否复制人物/产品一致性图片到 Working | 是 |
| Prompt 是否可以隐藏在代码里 | 不可以，生成前必须落盘 |
| 图片产物先到哪里 | 最终图片先到工具 Output，再同步到 StoryBoard Working |
| Dialogue 音频是否写回 Dialogue 音频槽 | 是，`Audio_Final` 发布到 StoryBoard Working 后必须同步写回 `working_assets.audio` / `Audio_Final` |
| Segment 音频产物先到哪里 | Segment 合成音频先到工具 Output，再同步到 StoryBoard Working |
| Segment 音频是否写回 Dialogue 音频槽 | 否，`SegmentAudio_Final` 只属于 Segment Plan / 执行结果，不能覆盖 `working_assets.audio.path` |
| 工具 Working 的 DialogueAudio 是否写回 StoryBoard | 否，`DialogueAudio` 只是执行器临时输入，不能写入 `srt_storyboard.json` 或 `koubo_storyboard_edit.json` |
| raw video / 对嘴临时视频先到哪里 | 只进入工具 Working，不进入 Output 或 StoryBoard Working |
| 最终视频产物先到哪里 | 最终视频先到工具 Output，再同步到 StoryBoard Working；有人脸且 `need_lipsync=true` 时是对嘴后视频，无人脸或前端关闭对嘴时是 raw video 晋升最终视频 |
| 业务最终同步到哪里 | `SessionOutput/storyboard/Working/` |
| Prompt 是否同步到 StoryBoard Working | 是 |
| 前端修改 Prompt JSON 是否由本工具处理 | 否，走单独 API；本工具只负责生成和覆盖前备份 |
| 是否每个视频都执行对嘴型 | 否，按计划字段 `need_lipsync` 执行；前端可明确关闭，无人脸视频不用对嘴型 |
| `05_01` 是否要规划对嘴字段 | 是，新增 `need_lipsync`、`lipsync_disabled_by_ui`、`lipsync_reason` |
| 是否在 05_02 内拼接视频 | 否，拼接放到后续工具 |
| 短句视频超出原时长如何处理 | `05_02` 不裁剪，后续拼接工具处理 |
| `--force` 是否清理 StoryBoard Working | 否，只清理工具目录；成功覆盖前先备份到 assets/history |
| 超时如何处理 | `failed_timeout` |
| `00` 是否需要补默认模型配置 | 是，必须补 `default_image_config`、`default_video_config`、`default_lipsync_config` |
| 默认图片模型从哪里读 | `SessionContext/Variables.json -> default_image_config` |
| 默认视频模型从哪里读 | `SessionContext/Variables.json -> default_video_config` |
| 默认对嘴型模型从哪里读 | `SessionContext/Variables.json -> default_lipsync_config` |
| 对嘴型模型参数从哪里读 | `default_lipsync_config.provider/model/extra_json`；数据库只补 API key |
| API key 如何使用 | 运行时从数据库读到内存，不落盘 |
| 是否改写 StoryBoard 结构 | 否 |
| 是否允许 prompt-only | 允许 |
| 是否支持 resume | 支持，但必须 hash 校验 |

## 18. Max SD 2 非空镜口播的 SDR2V 路由（2026-07-14）

1. 模型来源只能是 `SessionContext/Variables.json -> default_video_config`。当 `provider=openrouter` 且 `model=bytedance/seedance-2.0` 时识别为 Max SD 2；不得读取 `talking_head.video_model`，不得用数据库业务字段覆盖 Variables 中的默认模型。数据库只允许补充密钥和连接参数。
2. 非空镜、非 DanceMimic 的口播 Segment 必须使用 `Reference/05_02/Video_SDR2V.md`，并把模板快照平铺复制为 `Prompt/Ref_05_02_Video_SDR2V.md`。
3. 参考视频解析优先级为：Segment 显式 `provider_reference_video_path`、`source_face_masked_reference_video_path`、`reference_video_path`；三者均不存在时，使用固定兜底 `Reference/05_02/Video_SDR2V.mp4`，并复制到工具 Working。
   - Tool Library 标准兜底素材必须小于等于 `15.0s`，Working 中不得遗留旧的超长副本。
4. 首帧/人物参考图与参考视频必须共同进入 OpenRouter 的 `input_references`；参考视频只提供表情、动作、姿态、节奏、手势和镜头运动，不得提供参考人物身份与场景。
5. 空镜继续使用 `Video_OpenRouter.md` 且不附加 SDR2V 兜底视频；DanceMimic 显式链路优先，继续使用自身模板与分段参考视频。
6. `05_02` 与 `05_06` 的 Prompt、Video、Prompt + Video 路径必须复用同一判断和准备逻辑。

### 踩坑记录

- 默认模型为 Max SD 2 不会让 `video_openrouter.py` 自动改模板；必须显式传 `prompt_template=Video_SDR2V.md`。
- 只复制 `Video_SDR2V.mp4` 不代表模型已使用；还必须传入 `reference_videos`，由 OpenRouter 模块写入 `input_references[].video_url`。
- Analysis_V1 使用 `Video_SDR2V.md`，TalkingHead_V1 使用 `Video_SDR2V_TalkingHead.md`，不得跨工具误拷贝。

## 19. OpenRouter 参考视频 R2 传输回归要求（2026-07-14）

- 完整 `OPENCREW_PUBLIC_ASSET_R2_*` 配置存在时必须上传 R2，并用预签名 HTTPS URL 写入 `input_references[].video_url`，不得回退 tmpfiles。
- 审计只记录 provider、endpoint、bucket、region、prefix、TTL 等非敏感字段；Access Key 与 Secret 只能在运行时内存中使用，禁止进入 Session 或审计 JSON。
- 更新 `~/.opencrew/public_assets_r2.env` 后必须重启后端；回归需同时验证 `public_asset_provider=r2` 与预签名 URL 可 GET/Range。
