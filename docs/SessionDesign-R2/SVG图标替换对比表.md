# SVG 图标替换对比表

检查范围：
- Gemini 图标来源：`docs/SessionDesign-R2/icons.tsx`
- Gemini 实战弹窗来源：`docs/SessionDesign-R2/ExecutionPlanModal.tsx`
- 当前前端图标：`OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardIcons.jsx`
- 当前 Analysis_V1 图标：`OpenCrew/OpenClip/frontend/src/AnalysisV1/analysisV1Icons.jsx`
- 当前模型配置图标：`OpenCrew/ModelConfig/frontend/src/shared/icons.tsx`

说明：`docs/SessionDesign-R2` 目录下没有独立 `.svg` 文件，Gemini 生成的是内联 SVG React 组件，主要集中在 `icons.tsx`；`ExecutionPlanModal.tsx` 里也内置了一套局部图标，而且已经按 Generation Plan 的真实 UI 场景做过语义分配。

## 总体判断

Gemini 这套图标整体更统一，优点是：
- 统一 `24x24`、`currentColor`、`strokeLinecap/strokeLinejoin`，适合直接接入现有按钮色彩系统。
- Audio / Image / Video / Workflow / Layout / Operation 类图标语义更清楚。
- `createIcon` 工厂能减少重复 SVG 外壳，后续维护比多个手写 `<svg>` 更干净。

优先替换区域建议：
1. `KouboVideoPlanModal` 的任务统计和 pipeline 图标，当前语义最混乱，audio 用了 `PauseIcon`，lipsync 用了 `SpeedIcon`。
2. `KouboStoryBoard` 的图片、视频、操作类图标，当前可用但偏简陋。
3. `Analysis_V1` 的工具栏和 TTS Builder，可逐步统一为同一套图标语言。
4. `ModelConfig` 保留模型特化图标，不必全量替换，只替换通用音频波形和图片图标。

## `ExecutionPlanModal.tsx` 里最值得直接采用的图标映射

这份文件比 `icons.tsx` 更有参考价值的一点是：它不只是提供图标，还已经把图标放进了一个完整的 Generation Plan 弹窗语境里。当前 Koubo 的 `KouboVideoPlanModal.jsx` 正好是同类场景，可以按这个映射直接改。

| Generation Plan 语义 | `ExecutionPlanModal.tsx` 用法 | 当前 Koubo 问题 | 建议 |
|---|---|---|---|
| 弹窗主标识 | `Workflow` | 当前也是流程图标，方向正确 | 可换成 Gemini `Workflow`，和弹窗内其他图标统一 |
| Shot 统计 | `Film` | 当前 `FilmIcon` 可用，但较简化 | 换 `Film` |
| Scene 统计 | `Layers` | 当前用了 `WorkflowIcon`，和主标识重复 | 换 `Layers`，更像层级/场景集合 |
| Segment 统计 | `LayoutList` | 当前用了 `SpeedIcon`，语义错误 | 必换 `LayoutList` |
| Audio 任务 | `Mic` | 当前用了 `PauseIcon`，语义错误 | 必换 `Mic`；如果表示已有音频素材再用 `AudioLines` |
| Image / First Frame 任务 | `ImageIcon` | 当前可用但风格简化 | 换 `ImageIcon` |
| Video 任务 | `Clapperboard` | 当前用 `FilmIcon`，和 Shot 统计重复 | 换 `Clapperboard`，区分“视频生成任务”和“镜头片段” |
| Lip Sync / Sync 任务 | `AudioLines` | 当前用了 `SpeedIcon`，语义错误 | 先换 `AudioLines`，后续补专用 `LipsyncIcon` |
| 执行计划 | `Play` / `Clock` | 当前播放图标可用 | 可保留当前实心播放；计划执行按钮想统一时再换 |
| 完成状态 | `CheckCircle2` / `Circle` / `Check` | 当前分散 | 可作为后续状态图标统一来源 |

## 替换对比表

| 类别 | 当前图标/位置 | 建议 Gemini 图标 | 替换优先级 | 建议替换位置 | 理由 | 注意事项 |
|---|---|---|---:|---|---|---|
| Audio 任务 | `PauseIcon` 用作 Audio Tasks：`KouboVideoPlanModal.jsx` | `ExecutionPlanModal.tsx` 的 `Mic` | P0 | 顶部任务统计、segment pipeline 的 `AUDIO` badge | 当前 pause 代表播放状态，不代表音频生成任务；Gemini 在实战弹窗里已经用 `Mic` 表示 Audio Tasks | TTS/生成声音推荐 `Mic`；已有音频素材/波形展示推荐 `AudioLines` |
| Lipsync/同步任务 | `SpeedIcon` 用作 Sync Tasks：`KouboVideoPlanModal.jsx` | `AudioLines` 暂代，或后续新增 Lipsync 专用图标 | P0 | 顶部任务统计、segment pipeline 的 `SYNC` badge | speed 表示速度设置，不表示唇形同步；至少应脱离速度表语义 | Gemini 现有图标没有真正 lipsync，短期可用 `AudioLines`，长期建议单独做“嘴型+波形” |
| Image/首帧图 | `ImageIcon`：Koubo、Analysis_V1、ModelConfig 均有手写版本 | `ImageIcon` | P1 | `DialogueCard.jsx` 原图/新图槽位、`KouboSidebar.jsx` 绑定状态、`KouboVideoPlanModal.jsx` Frame Tasks | Gemini 版本构图更完整，图片山形更清晰，和计划弹窗的视觉语言统一 | 替换后检查 12px 小尺寸是否仍清晰 |
| Video/视频 | `FilmIcon`：Koubo/StoryBoard 手写版本 | `Film` 或 `Clapperboard` | P1 | 视频素材槽、Shots 统计、Video Tasks | `Film` 适合“视频素材/片段”，`Clapperboard` 适合“生成视频任务/开拍动作”，语义可分层 | 不建议所有视频位置都用同一个图标；素材用 Film，执行/生成用 Clapperboard |
| Shot/镜头统计 | `FilmIcon` 用于 Shot | `Film` | P1 | `KouboVideoPlanModal.jsx` 顶部 Shot 指标、左侧 shot 节点 | Gemini `Film` 的胶片孔更细致，作为 shot/片段更直观 | 和 Video Tasks 若都用 Film 会重复，可把 Video Tasks 改 `Clapperboard` |
| Scene/场景层级 | `WorkflowIcon` | `ExecutionPlanModal.tsx` 的 `Layers` | P0 | `KouboVideoPlanModal.jsx` 顶部 Scene 指标 | `ExecutionPlanModal.tsx` 已经把 Scene Count 映射成 `Layers`，比重复使用 Workflow 更清楚 | 主标题保留 Workflow，Scene 指标用 Layers |
| Segment/片段列表 | `SpeedIcon` 用作 Segment | `ExecutionPlanModal.tsx` 的 `LayoutList` | P0 | `KouboVideoPlanModal.jsx` 顶部 Segments 指标 | Segment 是列表/切片，不是速度；`LayoutList` 语义更准 | 这是一个低风险高收益替换 |
| 生成/执行 | `PlayIcon` | `Play` 或 `PlayCircle` | P2 | VideoPlan 第一个可执行节点、Run Model 按钮 | Gemini `Play` 描边版和其他线框图标更统一；`PlayCircle` 更适合明确“执行”按钮 | 当前实心三角在主按钮中也成立，可按视觉一致性决定 |
| 操作：新增 | `PlusIcon` | `Plus` | P2 | Dialogue 新增、素材添加、版本添加 | 差异不大，但 Gemini 统一封装后可减少重复 | 功能无语义问题，放在第二批 |
| 操作：切分 | `ScissorIcon` / `ScissorsIcon` | `Scissors` | P1 | split scene / split shot、剪辑动作 | Gemini 版本更接近常见 scissors 图标，线条完整 | 注意小按钮中文本 `Scene/Shot` 与图标间距 |
| 操作：删除/关闭 | `XIcon`、`TrashIcon` | `X`、`Trash2` | P2 | 删除 dialogue、关闭弹窗、删除资源 | 当前可用；Gemini 统一度更好 | 删除应保留 `Trash2`，关闭才用 `X`，不要混用 |
| 操作：保存 | `SaveIcon` | 暂不替换，Gemini 当前没有 Save | 暂缓 | Header 保存、TTS 保存 | Gemini 包没有 Save，当前图标语义明确 | 可后续让 Gemini 补一枚同风格 Save |
| 操作：刷新 | `RefreshIcon` | `RefreshCw` | P1 | timing 刷新时长、reload | Gemini 版本更完整，视觉平衡好 | 替换后保持按钮 title/aria-label |
| 操作：上传 | `UploadIcon` | `UploadCloud` 或 `ArrowUpToLine` | P1 | TTS 上传参考声音、资源上传入口 | `UploadCloud` 适合云端/素材上传，`ArrowUpToLine` 适合本地导入 | 根据入口语义选择，不要统一硬换 |
| 操作：设置/参数 | `SlidersIcon` | `Settings2` | P1 | Prompt Builder、参数面板、Builder-G Timing | `Settings2` 比竖向 sliders 更紧凑，适合工具按钮 | 若是“调参滑杆”界面，保留 Sliders 也合理 |
| 操作：拖拽排序 | `GripIcon`/无统一 | `GripVertical` | P1 | 可拖动列表、shot/scene 重排入口 | Gemini 点阵 grip 更标准 | 当前页面若没有显式拖拽按钮，可先不加 |
| 文本/对白 | `MessageIcon`、`SpeechIcon` | `MessageSquareText` | P1 | dialogue 节点、TTS 文案提示区 | Gemini 版本带多行文本，更适合“对白/文本内容” | 纯聊天气泡可保留现有 MessageIcon |
| 文档/提示词 | `DocumentIcon` | `FileText` | P1 | 查看最终提示词、计划详情文档入口 | Gemini `FileText` 文档折角和文本线更完整 | 和 CodeIcon 搭配时视觉更统一 |
| 代码/模型调用 | `CodeIcon` | `Code` | P2 | 生成复杂提示词、Task Assistant | 差异小，主要为了统一封装 | 当前图标已经清楚，低优先 |
| 图层/场景层级 | 当前少用或用 `WorkflowIcon` | `Layers` | P1 | scene_count、素材层、计划层级摘要 | `Layers` 很适合 scene/layer/count 语义 | 可替代部分 Workflow，减少 Workflow 滥用 |
| 模板/布局 | 当前无明显专用图标 | `LayoutTemplate` | P2 | 模板选择、版式/Storyboard 布局入口 | Gemini 包里可直接补足空缺 | 需要先确认 UI 是否有该入口 |
| 自动生成/智能建议 | `SparkIcon` | `Sparkles` | P1 | 自动生成 storyboard、AI 建议、重写建议 | Gemini `Sparkles` 更丰富，也更像 AI 操作 | 避免在普通保存/刷新动作上滥用 |
| 重新组织/打乱 | `ShuffleIcon` | `Shuffle` | P2 | Reorganize / Shuffle | Gemini 线条更完整 | 当前图标语义无误，低优先 |
| 展开/进入详情 | `ArrowIcon`、`Chevron` 分散 | `ChevronRight` / `ChevronDown` / `Maximize2` | P2 | 折叠展开、进入详情、放大预览 | Gemini 图标更齐全 | 需要按交互方向逐个替换 |
| 明暗主题 | `SunIcon`/`MoonIcon` | `Sun` / `Moon` | P2 | Sidebar theme toggle | Gemini 统一但视觉差距不大 | 低风险但非关键 |

## 建议的落地顺序

第一批只动语义明显错误、影响理解的位置：
- `KouboVideoPlanModal.jsx`
  - 直接复用 `ExecutionPlanModal.tsx` 的映射：`Shot=Film`、`Scene=Layers`、`Segment=LayoutList`、`Audio=Mic`、`Image=ImageIcon`、`Video=Clapperboard`、`Sync=AudioLines`
  - 当前 `PauseIcon` for Audio -> `Mic`
  - 当前 `SpeedIcon` for Segment -> `LayoutList`
  - 当前 `SpeedIcon` for Sync -> `AudioLines`，后续补 lipsync 专用图标
  - 当前 `FilmIcon` for Video Tasks -> `Clapperboard`

第二批统一 Koubo StoryBoard 的媒体槽：
- `DialogueCard.jsx`
  - Audio label/empty state：`VolumeIcon` 可按语义拆分，播放/声音输出保留 speaker，音频素材槽换 `AudioLines`
  - 原图/新图：换 Gemini `ImageIcon`
  - 视频素材：换 Gemini `Film`
  - split scene / split shot：换 Gemini `Scissors`
- `KouboSidebar.jsx`
  - 绑定状态里的 audio/image/video 图标与 DialogueCard 同步

第三批再考虑跨模块统一：
- `Analysis_V1/analysisV1Icons.jsx`：Image、Waveform、Sliders、Upload、FileText、Code 可逐步迁到同一套。
- `ModelConfig/shared/icons.tsx`：保留模型特化图标，只统一通用 `AudioWaveIcon`、`ImageModelIcon` 的线条风格。

## 本轮全系统替换范围

已按 P0/P1 扩展到整个 OpenCrew 前端系统，而不只限于口播故事版：
- 主壳 `OpenCrew/frontend/src/App.jsx`：导航、AudioWave、ImageModel、Install、Storyboard 入口图标。
- 主壳 Debug Console：展开、复制、清空、加载历史从字母占位换成真实图标。
- `WorkflowAssistantDrawer.jsx`：Reload 换成 Gemini 风格刷新图标。
- `OpenClipModule.jsx` / `OCRebuildModule.jsx`：Prompt Builder、文档、刷新、上传等通用工具图标。
- `OCStoryBoard`：图片、视频、拍板、智能生成、Builder-G 语音入口。
- `Analysis_V1`：图片、波形、设置、上传、刷新。
- `ModelConfig`：通用音频波形、图片模型图标。

## 不建议直接替换的点

| 当前位置 | 原因 |
|---|---|
| `ModelConfig` 的 `TTSModelIcon`、`VideoModelIcon`、`LipSyncModelIcon` | 这些是模型类型专用图标，当前语义比 Gemini 通用图标更细，不应一刀切 |
| 播放控件中的 `PlayIcon` / `PauseIcon` | 播放状态用实心图标更醒目，Gemini 描边版更适合任务入口，不一定适合播放器 |
| 保存按钮 `SaveIcon` | Gemini 当前没有同风格 Save，先保留 |
| 价格相关 `PriceListIcon`、`MinimumUnitPriceIcon` | Gemini 包没有对应语义，不应替换 |

## 建议新增的 Gemini 风格补充图标

Gemini 这批已经覆盖多数通用动作，但还缺几个 OpenCrew 常用语义：
- `LipsyncIcon`：嘴型轮廓 + 小波形，替代当前 `SpeedIcon`。
- `SaveIcon`：软盘或托盘保存，统一现有保存按钮。
- `VideoCameraIcon`：模型配置里“视频模型”比 `Film/Clapperboard` 更贴近生成服务。
- `VoiceTimingIcon`：麦克风 + 时钟，替代 Builder-G Timing 的普通音量图标。
