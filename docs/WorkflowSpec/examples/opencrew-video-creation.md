# 映射示例④ · OpenCrew AI 视频创作与交付

> **状态 `[implemented/partial] + [proposed]`**：这是 FlowSpec 的来源域。Workspace、storyboard、多类媒体资产、计划执行快照、OpenCode Agent 与领域执行服务已存在；下述统一 Process Registry、通用 fanout/资源池/人工 Gate/AIExecutionProfile 仍是目标形态，不能把整个 JSON 当作现行 runner 配置。

> 配套可执行验证：[OpenCrew AI 视频 demo](../demos/opencrew-video/index.html)（独立 HTML + Process/Tool Registry + Mock Tools + 双 Run 记录）。demo 的统一 Runtime 仍是文档级目标实现，不等于现行口播领域路由已迁移到 FlowSpec。

## 0. 业务定义与成功标准

**业务产品**：根据创作 brief、脚本、人物/产品参考和渠道要求，生成可审核、可修改、可发布的口播/短视频。一次交付不是“模型返回一个 MP4”，而是由创意方案、分镜、对白音频、关键帧、视频片段、合成成片和授权/审核记录组成的**版本化资产包**。

**主要角色**：创作者/客户（brief 与最终选择）、内容编辑（脚本/分镜）、品牌或合规审核人、AI/Agent、媒体生成供应商、本地合成与质检服务、发布渠道。

**完成标准**：选定版本的脚本和分镜已确认；每个 slot 的 audio/image/raw/final 资产与同一 `dialogue_asset_key` 绑定；媒体可解码且参数符合渠道要求；人物/产品引用、模型/provider、prompt、授权和费用可追溯；最终人工批准后才允许发布。

**关键风险**：错误音色、人物/产品漂移、生成资产绑定到错误对白、输入变化后旧结果仍显示完成、provider 已收费但客户端丢响应、流式聊天被误当最终方案、Agent 越权读写 Workspace、成片含政策/版权/品牌风险。

## 1. 领域 → FlowSpec 映射

| FlowSpec 要素 | OpenCrew 视频域 |
|---|---|
| **session + task** | session 提供 Workspace/事件与稳定创作身份；task 保存 brief、workflow mode、模型选型、当前 storyboard/plan/attempt 指针 |
| **Run** | 一次基于冻结 brief + storyboard + 模型配置的生产执行；修改脚本、音色或参考图后生成新 Run |
| **Agent Tool** | 创意研究、脚本/分镜建议、prompt 评议；OpenCode Session 只是可选多轮上下文 |
| **Model Tool** | TTS、图像生成、视频生成、多模态 QA；每次 provider 调用都是独立 billable operation |
| **Artifact** | storyboard JSON、PromptManifest、Audio/Image/RawVideo/FinalVideo、QAReport、DeliveryManifest |
| **fanout/fan-in** | 按对白/镜头并行生成；合成按 `dialogue_asset_key` 汇合音频、画面和视频，不能只按数组位置或路径猜 |
| **Human Gate** | 方案确认、异常 slot 选择/重做、成片发布批准 |
| **Resource** | LLM/token 速率、图像/视频 provider 并发、本地 GPU/编码器 slot、每 task 合成互斥 |

## 2. 目标 Process 骨架

```jsonc
{
  "process_id":"opencrew_video_creation_v1", "version":"1.0.0",
  "title":"OpenCrew AI 视频创作与交付", "failure_propagation":"continue_independent",
  "resource_pools":{
    "llm_tokens":{"type":"rate_limit","per_minute":200000},
    "image_provider":{"type":"semaphore","limit":8},
    "video_provider":{"type":"semaphore","limit":4},
    "local_encoder":{"type":"semaphore","limit":2}
  },
  "defaults":{"retry":{"policy":"fail_fast"}},
  "stages":["intake","plan","generate","compose","approve","deliver"],
  "steps":[
    {"id":"S1_brief_validate","tool":"creative_brief_validator","stage":"intake",
     "reads":["brief_ref","channel_profile","rights_declarations"],
     "side_effect_class":"pure","produces":["CreativeBrief.json"],"writes":["brief_hash"]},

    {"id":"S2_storyboard_plan","tool":"storyboard_agent","type":"agent","stage":"plan",
     "consumes":["CreativeBrief.json"],"ai_profile_ref":"ai://storyboard-agent/v1",
     "resources":[{"kind":"rate_limit","pool":"llm_tokens","amount":12000}],
     "uses_llm":true,"cost_level":"medium","side_effect_class":"idempotent",
     "produces":["StoryboardPlan.json","PromptManifest.json"],"writes":["plan_hash"]},

    {"id":"S3_plan_confirm","tool":"human_confirm","type":"confirm","stage":"plan",
     "depends_on":[{"step_id":"S2_storyboard_plan","statuses":["completed"]}],
     "consumes":["StoryboardPlan.json"],"side_effect_class":"pure",
     "human_gate":{"type":"confirm","form":"storyboard_review","roles":["creator","editor"],"sla_seconds":86400}},

    {"id":"S4_consistency_refs","tool":"consistency_reference_builder","type":"model","stage":"generate",
     "depends_on":[{"step_id":"S3_plan_confirm","statuses":["completed"]}],
     "consumes":["CreativeBrief.json","StoryboardPlan.json"],"ai_profile_ref":"ai://image-reference/v1",
     "resources":[{"kind":"semaphore","pool":"image_provider","amount":1}],
     "uses_llm":false,"cost_level":"high","side_effect_class":"reconcilable",
     "produces":["ConsistencyReferences.json"]},

    {"id":"S5_audio_generate","tool":"tts_generator","type":"model","stage":"generate",
     "depends_on":[{"step_id":"S3_plan_confirm","statuses":["completed"]}],
     "consumes":["StoryboardPlan.json"],"ai_profile_ref":"ai://dialogue-tts/v1",
     "fanout":{"over":"StoryboardPlan.json#/dialogues","as":"dialogue"},
     "uses_llm":false,"cost_level":"high","side_effect_class":"reconcilable",
     "produces":["Audio_{dialogue.asset_key}.wav"]},

    {"id":"S6_visual_prompt","tool":"visual_prompt_generator","type":"model","stage":"generate",
     "consumes":["StoryboardPlan.json","ConsistencyReferences.json"],
     "ai_profile_ref":"ai://visual-prompt/v1",
     "fanout":{"over":"StoryboardPlan.json#/dialogues","as":"dialogue"},
     "resources":[{"kind":"rate_limit","pool":"llm_tokens","amount":3000}],
     "uses_llm":true,"cost_level":"medium","side_effect_class":"idempotent",
     "produces":["VisualPrompt_{dialogue.asset_key}.json"]},

    {"id":"S7_image_generate","tool":"image_generator","type":"model","stage":"generate",
     "consumes":["VisualPrompt_*.json","ConsistencyReferences.json"],
     "ai_profile_ref":"ai://dialogue-image/v1",
     "fanout":{"over":"StoryboardPlan.json#/dialogues","as":"dialogue","concurrency_pool":"image_provider"},
     "resources":[{"kind":"semaphore","pool":"image_provider","amount":1}],
     "uses_llm":false,"cost_level":"high","side_effect_class":"reconcilable",
     "produces":["Image_{dialogue.asset_key}.png"]},

    {"id":"S8_raw_video_generate","tool":"video_generator","type":"model","stage":"generate",
     "consumes":["Image_*.png","VisualPrompt_*.json"],"ai_profile_ref":"ai://dialogue-video/v1",
     "fanout":{"over":"StoryboardPlan.json#/dialogues","as":"dialogue","concurrency_pool":"video_provider"},
     "resources":[{"kind":"semaphore","pool":"video_provider","amount":1}],
     "uses_llm":false,"cost_level":"very_high","side_effect_class":"reconcilable",
     "produces":["RawVideo_{dialogue.asset_key}.mp4"]},

    {"id":"S9_segment_compose","tool":"segment_composer","stage":"compose",
     "consumes":["Audio_*.wav","RawVideo_*.mp4"],
     "resources":[{"kind":"semaphore","pool":"local_encoder","amount":1}],
     "side_effect_class":"idempotent","produces":["FinalSegment_{dialogue.asset_key}.mp4"]},

    {"id":"S10_master_compose","tool":"master_composer","stage":"compose",
     "consumes":["FinalSegment_*.mp4","StoryboardPlan.json"],
     "resources":[{"kind":"mutex","name":"master:{session}","mode":"exclusive"},
                  {"kind":"semaphore","pool":"local_encoder","amount":1}],
     "side_effect_class":"idempotent","produces":["MasterVideo.mp4","DeliveryManifest.json"]},

    {"id":"S11_qa","tool":"media_and_content_qa","stage":"approve",
     "consumes":["MasterVideo.mp4","DeliveryManifest.json","CreativeBrief.json"],
     "side_effect_class":"pure","produces":["QAReport.json"],"writes":["qa_passed"]},

    {"id":"S12_publish_confirm","tool":"human_confirm","type":"confirm","stage":"approve",
     "depends_on":[{"step_id":"S11_qa","statuses":["completed"]}],
     "consumes":["MasterVideo.mp4","QAReport.json"],"side_effect_class":"pure",
     "when":{"variable":"qa_passed","equals":true},
     "human_gate":{"type":"confirm","form":"final_publish_review","roles":["creator","brand_reviewer"]}},

    {"id":"S13_deliver","tool":"channel_delivery","type":"service","stage":"deliver",
     "depends_on":[{"step_id":"S12_publish_confirm","statuses":["completed"]}],
     "consumes":["MasterVideo.mp4","DeliveryManifest.json"],
     "side_effect_class":"reconcilable","produces":["DeliveryReceipt.json"]}
  ]
}
```

## 3. 非单链条、恢复与版本语义

- **多链条**：方案确认后，S4 参考板与 S5 音频可并行；视觉链 S4→S6→S7→S8 与音频链在 S9 按稳定 asset key 汇合。Run 级 `continue_independent` 只说明视觉链失败时无依赖的音频链仍可跑；S8 内某 slot 失败后其他 slot 是否继续属于 fanout 子项策略，仍需 typed fanout runtime 明确定义，不能混用这两个开关。S9 只可在所需子项全部齐全且绑定有效后完成。
- **多对一**：S9 不是“目录里有 WAV 和 MP4 就开始”，而是对每个 `dialogue_asset_key` 验证 Audio/RawVideo 的输入版本与绑定，再做 fan-in；S10 再按确认后的 storyboard 顺序合成。
- **昂贵调用恢复**：图像/视频 provider 请求已提交但响应未知时进入 reconcilable，不自动生成第二份收费资产；恢复 worker按 provider request/interaction ID 对账。
- **局部重做**：用户替换某句音色、图片或视频是业务修订，创建新 Run 或显式 scoped rerun；新输入哈希只使受影响 slot 及其后代 stale，不能把全片无关资产全删，也不能沿用旧绿态。
- **Agent 对话**：OpenCode 可在 business-session scope 持续讨论，但聊天中的建议不会直接改 storyboard 或启动生成；“应用方案”通过受审计命令创建版本/Run。

## 4. 专业控制与验收

- **创意/合规**：brief 明确平台、受众、时长、语言、品牌禁用项、版权/肖像/声音授权；模型安全拦截与人工合规结论分开记录。
- **媒体技术 QA**：容器/codec、尺寸、帧率、音轨、响度、时长、黑帧/静帧、解码完整性、A/V 同步；“文件存在”不是成功。
- **内容 QA**：脚本与字幕一致、发音、品牌/产品一致性、事实/声明、敏感内容、画面与对白语义匹配；自动 QA 只能标记，发布仍按风险级别人工确认。
- **成本**：费用汇总到 Run，但每次 TTS/image/video invocation 独立去重与核算；prompt 格式修复、变体生成也是新的收费操作。
- **可观测**：同时展示执行状态与 Artifact validity；流式 Agent 文本、provider polling 与最终选中资产是三种不同事实。

**结论**：该场景证明 AI/Agent 不是额外流程层，而是 Tool 的高风险执行 Profile；真正需要进入通用核心的仍是 Artifact、绑定、资源、人工卡点、审计、幂等/对账和跨 Run 修订。
