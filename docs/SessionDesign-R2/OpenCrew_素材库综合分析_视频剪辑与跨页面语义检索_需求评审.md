# OpenCrew 素材库综合分析、视频剪辑与跨页面语义检索需求评审

版本：v0.9.5

日期：2026-07-22

状态：M0–M4 已完成产品基线；专项 v0.3.1 的 R0A–R4 已于 2026-07-22 完成本地实现与验收，当前增量合同以专项 v0.3.1 为准

文档导航：[OpenCrew 素材库文档索引](./OpenCrew_素材库文档索引.md)

变更记录：

| 版本 | 日期 | 变更 |
| --- | --- | --- |
| v0.9 | 2026-07-20 | 对白优先的 M0–M4 产品首版合同。 |
| v0.9.1 | 2026-07-21 | 明确 M0–M4 是必要技术基线，但对“无声素材为主”的目标客户不是上线充分条件；该客户必须补齐无声原视频按画面召回的 P0 价值闭环。未在本补丁版本中提前宣称专项变更已交付。 |
| v0.9.2 | 2026-07-22 | 文档治理更新：把第 4 节明确标为实施前历史基线，避免把已经由 M0–M4 补齐的缺口误读成当前现状；新增统一文档索引，不改变产品范围。 |
| v0.9.3 | 2026-07-22 | 固定 R1 视觉质量门禁：每个不超过 15 秒的画面分析片段按 12.5%/37.5%/62.5%/87.5% 取四帧，在一次多图 VLM 请求中生成中文语义；稀疏四帧仍不得包装成连续动作识别。现有单中点结果保持只读兼容但不能作为 R1 完成证据。 |
| v0.9.4 | 2026-07-22 | 同步专项 v0.3 实施验收：0023/0024、四帧无声原视频三入口视觉召回、派生片段显式全局复用与完整发布门禁已通过；本文旧“对白首版”段落仅作为 M0–M4 历史基线。 |
| v0.9.5 | 2026-07-22 | 同步专项 v0.3.1 最简发布决定：R1/R2 在环境变量缺失时默认启用，继续复用已有两个开关做显式关闭/回退，不新增变量；单个派生片段仍默认不公开。 |

本版本目标是用最小但真实可用的能力边界，尽快交付同时包含对白检索、综合分析和视频剪辑的产品首版。实施过程可以按独立里程碑验证，但产品首版必须完成本文明确的首版发布门禁；中文 FTS、LLM 重排、VLM 质量增强、批量回填和向量召回不阻塞首版。首版检索按不超过 500 条原始视频的小规模试运行设计，目标为 P95 不超过 3 秒；超过这一边界后必须进入中文全文检索质量与性能增强，不能继续把简单子串召回描述为无规模限制的正式方案。

业务适用性说明：本文 v0.9 的 M0–M4 完成判定证明对白优先技术闭环；[《无声素材视觉检索与派生片段全局复用》v0.3.1](./OpenCrew_无声素材视觉检索与派生片段全局复用_需求变更与开发实施设计_v0.2.md)现已追加完成四帧无声视觉召回与派生片段全局复用，并默认启用 R1/R2。本文后续仍出现的“只召回对白/只允许原视频”等表述均是明确标注的 M0–M4 历史阶段合同，不覆盖当前专项增量实现。

关联页面：

- 全局素材库：`#/media-library`
- 素材详情：`#/media-library/{asset_id}`
- 故事板（口播）：`#/koubo-storyboard/tasks/{task_id}`
- 任务内 Agent - Asset Library：`#/koubo-asset-library/tasks/{task_id}`

## 1. 评审结论

需求方向成立，建议通过，但不能把四项能力作为一个无边界的大功能直接实现。它们实际上包含四个相互依赖、职责不同的子系统：

1. 素材分析：实施时先发布对白索引，再在同一产品首版内补齐最小视觉语义和综合分析索引。
2. 素材检索：建立跨 Task 的全局素材库检索服务，并分阶段增强语义匹配质量。
3. 视频剪辑：基于虚拟时间片段进行选择和预览，用户确认后才生成物理视频片段。
4. 跨业务导入：把用户确认的原始视频或剪切片段复制到指定 StoryBoard Task 的 Asset Pool。

建议采用以下产品和技术原则：

1. 全局素材库的主实体始终是原始视频。
2. 对白、画面、综合分析结果都是指向原视频时间范围的虚拟索引，不是预先生成的 MP4。
3. 用户执行“剪切”后产生派生视频文件；它不伪装为全局原始视频，但可按专项 v0.3 显式加入派生片段检索集合。
4. 故事板、Agent 和 editor 同时支持原视频与显式开放的派生片段 Candidate；原视频命中保留真实时间范围，派生片段使用自身精确预览和 <code>import_clip</code>。
5. 综合分析不重新读取视频，只消费已完成的对白分析和画面分析结果。
6. LLM 负责语义融合、描述和排序信息生成；起止时间、上游引用和素材身份必须受结构化约束，不能由 LLM 自由编造。
7. 新的全局素材库检索能力应抽成共享后端服务，供素材详情、故事板和 Agent - Asset Library 三个界面复用。
8. 首版综合分析必须先新增最小可用的 Keyframe 视觉语义描述；仅有 Scene Detect 和 Keyframe 时不能启动综合分析，但这不阻塞对白检索基础里程碑。
9. 首版检索交付对白精确/短语检索和确定性 token 重排；发布后的搜索质量增强再引入 PostgreSQL `tsvector + GIN`、中文分词和 top-N LLM 重排。embedding 与向量召回另立第二迭代。
10. 所有新增存储和 API 时间字段使用整数毫秒，秒只在 UI 和 FFmpeg 适配边界出现。
11. 当前素材库按 OpenCrew 部署全局共享，不使用 `session_id` 虚构用户或租户隔离。
12. 首版视频剪辑页的“素材搜索”同时包含外部 provider 和全局素材库两个来源；二者复用查询上下文，但保持各自的资格、授权和导入规则。外部候选首版只能整条导入 StoryBoard，不能直接进入剪辑页；只有全局素材库原始视频可以进入本需求的剪辑器。
13. 首版视频剪辑采用最小可用时间轴：保留长视频必需的缩放、水平滚动和可见窗口渲染；键盘步进、刷新状态恢复、同轨重叠子行和密集片段聚合不阻塞首版。
14. 全局素材库原始视频不可原地替换；替换内容必须重新上传并产生新的 `asset_id`。上传完成时计算内容哈希，并以此形成稳定 `source_version`。
15. 分析历史在后端保留用于审计，但普通用户首版只使用最新成功结果，不提供把旧结果重新激活为当前版本的入口。
16. `stale` 结果可以只读查看，但不能参与检索、不能作为默认剪切建议，也不能被新导入或搜索流程当作当前有效结果。
17. 从 StoryBoard 检索结果打开剪辑页时，保留来源 Task、Dialogue、搜索运行和建议时间范围；默认导入原 Task，允许用户在剪辑页更换有效目标 Task。
18. 首版剪切任务只在当前后端服务进程中运行，不承诺服务重启后的任务恢复；中断后由用户重新创建任务，但系统必须清理遗留临时文件，已经成功登记的派生片段不受影响。
19. 业务常见视频长度在 10 分钟以内；首版以 10 分钟代表视频作为时间轴、尾部精确剪切和性能发布门禁。30 分钟 synthetic 视频只作为非阻塞压力测试，不作为发布承诺。

### 1.1 必须先解决的现状阻塞

2026-07-20 实机验证发现，当前素材详情页运行“对白分析”时，`02_01_AudioASR` 会返回：

```text
cloud_asr_data_transfer_not_authorized
```

原因是当时的素材库自动化流程选择了默认云 ASR，却没有提供在 Session 准备阶段记录本次音频传输授权的完整入口，也没有显式选择本地 ASR。页面最终只显示“工具 02_01 返回 blocked”，没有向用户展示真实阻塞原因。

2026-07-20 修复状态：已在当前代码版本实现素材库对白分析的单次云 ASR 授权勾选、API 授权字段传递、Tool Session `Variables.json` 授权记录和结构化 blocked 原因展示；相关合同测试与前端生产构建已通过。真实素材不会被自动重跑，仍需用户在界面明确授权后触发。修复后的机制仍要求每次运行显式授权，因此某次运行未勾选授权而停在 `02_01 blocked` 是预期行为，不是旧缺陷回归。

2026-07-21 无音轨素材实机补充：`02_01_AudioASR` 返回 `video_has_no_audio` 时，业务态必须显示“无音轨 / 对白分析不可用”，不得误投影为“等待授权”，也不得向普通用户展示内部错误码或英文工具消息。云 ASR 勾选只在当前打开的面板和本次运行中保留，关闭面板后重置；无音轨一经确认，页面改为说明“无需 ASR 授权”。无音轨素材仍可完成画面结构、视觉语义、预览和剪辑。v0.9 原边界只允许管理页名称/文件名/人工标签查找；专项 v0.3 现已补齐合格四帧 visual semantic 的跨页面画面召回，无音轨事实与“可按画面检索”能力独立显示。

在对白分析稳定完成之前：

1. 综合分析无法满足上游依赖。
2. “只有完成对白分析的素材才能检索”会导致检索集合为空或严重不完整。
3. 故事板和 Agent 页面无法验收全局素材检索。

因此，“授权入口和真实阻塞原因缺失”属于本需求的 P0 前置修复；“用户未授权时阻止云 ASR”则是必须保留的安全行为。

### 1.2 历史 v0.3 审核意见处理

| 审核意见 | 处理决定 | 说明 |
| --- | --- | --- |
| 改为 MVP 优先排期 | 采纳 | 阶段 0 只解锁对白分析；阶段 1 独立交付对白检索和原始视频导入闭环 |
| fast-follow 先做综合分析、再做 VLM | 不按原顺序采纳 | 综合分析视觉字段必须有 `03_03` 事实来源，因此调整为 VLM 在前、完整综合分析在后 |
| 增加 top-N LLM 重排 | 采纳 | 放入 fast-follow 1；LLM 只重排确定性召回的有限候选，不能承担全库召回 |
| 明确中文分词依赖 | 采纳 | 先用真实语料 spike，再锁定库、版本、词典和索引重建策略；本评审阶段不武断指定未验证的具体库 |
| 增加存量视觉语义回填作业 | 采纳 | 放入 fast-follow 2，默认不自动全库启动，支持暂停、限流、成本显示和优先级 |
| 修复端到端编号并拆分 DoD | 采纳 | 测试按阶段标记，DoD 拆为 MVP Done 和完整需求 Done |

### 1.3 历史 v0.4 审核意见处理

| 审核意见 | 处理决定 | 说明 |
| --- | --- | --- |
| MVP 从第一天记录检索遥测 | 采纳 | 复用现有搜索运行和事件框架，补充结果数量、零结果、耗时、候选排名及最终导入关联；原始查询受脱敏、保留和删除规则约束 |
| 书面对齐 MVP 中文召回偏保守 | 采纳 | MVP 明确优先精确率，召回率跃升放在 fast-follow 1；零结果页提供缩短关键词等建议 |
| 把所有版本、Attempt 和 stale 能力放入 MVP | 部分采纳 | MVP 只实现稳定 run 身份、结果 hash、幂等发布和事务式新旧激活切换；跨 scheme stale 级联及完整 Attempt 历史延后 |
| MVP 只实现原始素材导入 | 采纳 | `media_library_original` 属于 MVP；`media_library_clip` 随剪辑页交付；外部 provider 保持既有路径，不要求 MVP 重构 |
| 原样复用现有 `import_asset_search_candidates` | 不直接采纳 | 复用运行记录、去重、manifest 和审计框架，但必须为 `media_library_original` 增加权威记录重读和跨 Session 安全复制分支，不能走外部 provider 下载流程 |

### 1.4 v0.5 用户澄清结论

以下五项已经由用户明确确认，不再作为开放问题。若 v0.3/v0.4 审核记录中的 “MVP / fast-follow” 排期措辞与本节或 §13 冲突，以 v0.5 的首版阶段和发布门禁为准。

| 澄清事项 | 已确认答案 | 对范围和排期的影响 |
| --- | --- | --- |
| 检索主要匹配“说了什么”还是“画面是什么” | 主要依赖对白 | 首版检索资格继续以对白分析完成为准；无对白 B-roll 的画面检索不是首版检索门禁 |
| 是否接受首版为 LLM 查询扩展加关键词/短语命中 | 接受快速交付第一版的设计 | PostgreSQL FTS、LLM top-N 重排和向量召回不阻塞首版，但必须诚实标注能力边界 |
| 剪辑页“素材搜索”的来源 | 外部 provider + 全局素材库 | 剪辑页必须同时接现有联网搜索和共享 `MediaLibrarySearchService`，并清楚标注来源 |
| 综合分析和视频剪辑是否可以发布后再做 | 不可以，二者首版就要交付 | 原 fast-follow 2–4 改为首版阶段；首版发布门禁位于最小 VLM、综合分析和剪辑页全部完成之后 |
| 综合分析提示词是否允许用户编辑 | 首版由系统维护 | 首版只展示提示词/模型版本，不提供普通用户编辑或覆盖全局默认的入口 |

### 1.5 v0.6 剪辑页最小范围审核意见处理

| 审核意见 | 处理决定 | 说明 |
| --- | --- | --- |
| “最小可用版”没有传导到 §6 | 采纳 | §6、§13、§14、§15 和 §16 统一拆分首版门禁与发布后增强 |
| 首版是否可以取消全部缩放和滚动 | 不采纳 | 缩放、适应视图、水平滚动、自适应刻度和可见窗口渲染是长视频可剪辑的最低可用性条件 |
| 键盘步进、刷新状态恢复、重叠子行和密集聚合是否阻塞首版 | 不阻塞 | 四项明确移入发布后剪辑增强；首版用焦点置顶和联动索引列表处理重叠选择 |

### 1.6 v0.7 分析流水线基线审核意见处理

| 审核意见 | 处理决定 | 说明 |
| --- | --- | --- |
| 两份 run 内副本都归因于兼容层不准确 | 采纳并修正归因 | prepare 经 `tool_sessions/io.py` 生成 `0_SessionContext` 隔离快照；`framework_bridge.py` 只负责再生成旧式 `SessionContext` 兼容副本 |
| 未授权导致对白 blocked 是预期行为 | 采纳 | 修复目标是提供本次授权入口和结构化原因，不是自动补授或绕过授权 |
| Tool Session 缺少 finalize/result-sync 和产物登记 | 采纳并纳入阶段 0 | 分析终态、结果索引和 `session_files` 登记成为对白检索索引发布前置 |
| run 内兼容媒体造成存储放大 | 采纳并纳入阶段 0 | 保留每个 run 的一份隔离快照；旧式兼容目录改用引用/硬链接，避免第二次物理复制 |
| `thumbnail_url / preview_url` 为 `null` 代表未生成缩略图 | 核查后不按缺陷处理 | API 会派生受控 URL，缩略图首次请求后写入 `meta/thumbnails` 可再生缓存；缓存不属于必须登记的分析产物 |

### 1.7 v0.8 七项补充审核决定

| 审核问题 | 已确认决定 | 对正文的影响 |
| --- | --- | --- |
| 外部 provider 候选导入后如何剪辑 | 首版只允许整条导入 StoryBoard，不支持直接剪辑 | 删除“外部候选先导入再进入剪辑”的首版承诺；只有全局素材库原始视频可打开剪辑页 |
| 原始素材能否替换、旧分析能否恢复 | 原始视频不可替换；替换必须新上传。后台保留分析历史，但普通用户只使用最新成功结果 | 定义稳定 `source_version`，移除普通用户激活旧分析结果的接口和交互 |
| `blocked / partial / stale / failed` 如何展示 | 分别展示；`stale` 只读可见，但不参与检索或默认剪切 | 分离业务分析状态和 Tool Session 状态，补齐 `stale` 派生规则 |
| 首版检索规模和性能 | 小规模试运行，不超过 500 条视频，P95 不超过 3 秒 | 将容量和延迟作为发布门禁；超过边界后进入全文检索增强 |
| 剪切任务是否跨服务重启恢复 | 不恢复；服务重启后用户重新创建 | 首版使用进程内任务，明确 `clip_job_lost`、启动清理和成功结果持久化边界 |
| StoryBoard 打开剪辑页是否保留上下文 | 保留来源 Task、Dialogue、search 和建议时间；默认原 Task但允许更换 | 新增受控导航上下文、目标 Task 列表和服务端重校验 |
| M0–M4 单 Keyframe 能否推断连续动作 | 不能；只描述单帧直接可见事实，无法证明的连续动作留空 | 修正 `03_03` 与综合分析示例及验收标准 |
| R1 四帧能否推断连续动作 | 仍不能；四张稀疏静帧只提高人物、物体和场景覆盖，`action` 继续留空 | 固定 `scene_uniform_4_v1` 和四图单次请求，不扩大到视频动作理解 |

### 1.8 v0.9 技术实施复核决定

| 审核意见 | 处理决定 | 说明 |
| --- | --- | --- |
| `-ss` 放在 `-i` 后导致深切点慢 | 采纳问题，修正实现方案 | 使用输入侧单 `-ss + accurate_seek`；不采用需要额外编码关键帧索引的双 `-ss` |
| 源视频仍为 5 份物理复制 | 修正事实，不改变 run 隔离决定 | 当前正确基线是原始文件加 dialogue/visual 两份 run 快照，共 3 个物理 inode；两个 legacy 路径分别硬链接同 run 快照 |
| FFmpeg 7.0 与本机 8.1.1 不一致 | 采纳澄清 | 仓库内置运行合同为 7.0，开发机 PATH 可同时存在 8.1.1；实现必须记录并固定可执行文件解析顺序 |
| 250 ms 最短片仍允许固定 ±250 ms | 采纳 | 输出时长改用“一个视频帧、一个音频编码帧、请求时长 5% 三者最大值，再以 250 ms 封顶”的动态容差 |
| 30 分钟 fixture 不符合常见业务长度 | 采纳 | 发布门禁改为 10 分钟代表视频和尾部非关键帧剪切；30 分钟只作非阻塞压力测试 |
| editor 与 detail 路由可能同时命中 | 采纳为合同测试 | editor 必须先匹配，detail 必须有查询或字符串结束尾锚；未知子路径不得命中 detail |
| `GET editor` 一次返回全部 fragments | 首版保留 | 10 分钟以内单视频不分页、不静默截断；记录 fragment 数和响应字节，达到容量告警后再设计分页或窗口增量接口 |

## 2. 用户原始需求

最新增加了“素材库”页面，入口位于左侧导航栏 `Connection` 条目之下。这个页面的目的是对素材视频进行分析以及备选使用。以这个页面为基础，需要实现以下功能。

### 2.1 综合分析

完成“综合分析”。综合分析的目的是把对白分析和画面分析的结果综合起来，得到一个对视频内容描述的终极索引。

综合分析中不需要再次访问视频文件本身，只需要使用对白分析和画面分析的结果。综合分析需要基于“综合分析提示词”调用 LLM 生成分析结果。

### 2.2 视频剪切页面

在“综合分析”按钮右边添加“剪映”按钮，点击后打开视频剪切页面。视频剪切页面需要展示类似主流视频剪辑工具的视频时间轴预览，并能加载：

1. 对白分析结果。
2. 画面分析结果。
3. 综合分析结果。

视频剪切页面需要提供三种功能：

1. 用户选择对白分析、画面分析或综合分析索引后，可以执行“素材搜索”，利用系统已有的素材搜索功能，根据该索引的语义联网搜索匹配的视频候选。
2. 用户可以在三种分析索引上选择并剪切视频片段，也可以手动指定开始和结束时间。用户点击“剪切”后，系统生成一个新的视频片段文件，并提示用户输入文件名。
3. 用户可以把剪切生成的视频片段，或者选中的视频搜索结果，放入指定 Task / Session 的故事板素材库。

### 2.3 故事板中的“检索素材”

假设全局素材库中已经有很多上传视频。为了给一个 Task 的“故事板（口播）”寻找合适的视频素材，当用户选择一个 Dialogue 后，右侧 `Asset Pool` 面板的“上传素材”“Agent”按钮右侧需要出现“检索素材”按钮。

点击后调用 LLM，根据当前选中 Dialogue 的语义，从全局素材库中搜索语义匹配的素材视频。只有完成对白分析的素材视频可以被检索。

### 2.4 Agent - Asset Library 中检索全局素材库

Agent - Asset Library 页面的素材检索智能体也需要支持从全局素材库检索原始视频。

### 2.5 原始视频与剪切片段的业务边界

第 2.3、2.4 节的检索对象都是全局素材库中的原始视频，不包括剪切生成的派生片段。

业务流程应先找到可用的原始视频，再根据具体使用范围进行剪切。

## 3. 术语和范围修订

### 3.1 “素材库”与“Asset Library”

本文严格区分：

| 名称 | 含义 | 是否跨 Task |
| --- | --- | --- |
| 全局素材库 | 左侧一级导航中的“素材库”，主实体为用户上传的原始视频 | 是 |
| StoryBoard Asset Pool | 故事板右侧素材面板，服务当前 Task | 否 |
| Agent - Asset Library | 当前 Task 的图片、视频、音频和素材检索 Agent 工作台 | 否 |

所有新增接口、代码目录和文案必须避免只写模糊的 `asset library`，应明确使用 `media_library` 或 `storyboard_asset_library`。

### 3.2 “剪映”命名

用户已确认，“剪映”只是用于说明工具的性质、交互形态和大概应有的样子，不表示接入字节跳动的剪映产品、工程文件或开放平台。

为避免正式产品文案产生第三方集成歧义，按钮建议命名为：

```text
视频剪辑
```

本文后续统一使用“视频剪辑”。页面在布局上参考主流视频剪辑工具的播放器、时间轴、入点、出点和索引轨道，但本需求不包含任何第三方剪辑产品集成。

### 3.3 “索引”和“片段”

分析索引中的一条记录是虚拟片段：

```text
asset_id + scheme + fragment_id + start_ms + end_ms
```

它不代表磁盘上已经存在独立视频文件。只有用户点击“剪切”并执行成功后，系统才产生物理片段文件。

### 3.4 时间单位规范

新增分析、检索、剪切和导入合同的唯一规范时间单位是整数毫秒：

```text
start_ms
end_ms
duration_ms
keyframe_time_ms
```

规则如下：

1. LLM/VLM 结构化输入输出、结果文件、数据库和 API 一律使用带 `_ms` 后缀的整数。
2. 禁止在新增合同中使用含义不明的 `start / end / duration / time` 浮点字段。
3. 前端状态内部也保存整数毫秒，只在 UI 层格式化成 `HH:MM:SS.mmm` 或帧时间码。
4. FFmpeg 适配器可以在进程调用边界把毫秒转换成十进制秒，但不得把转换后的浮点值写回业务模型。
5. 当前 OpenCut 旧产物中的浮点秒由发布适配器统一执行一次确定性换算；原始旧产物不原地改写。
6. 区间统一使用左闭右开 `[start_ms, end_ms)`。旧工具秒字段转换后，尾部因探测/四舍五入超过 `duration_ms` 的 `end_ms` 先钳制到素材时长，钳制后退化为 `end_ms <= start_ms` 的条目跳过；已发布条目仍断言 `0 <= start_ms < end_ms <= duration_ms`，负数起点拒绝发布。
7. `duration_ms` 必须等于 `end_ms - start_ms`；任何 ×1000、非整数或单位不明的输入都拒绝发布。

### 3.5 “搜索原始视频”

故事板和 Agent 页面返回的主结果必须是一条原始视频素材。为了说明为什么命中，可以同时返回一到多个匹配时间范围：

```text
原始视频 A
  -> 命中范围 00:12.30 - 00:18.80
  -> 命中对白
  -> 命中原因
  -> 代表 Keyframe
```

匹配时间范围只用于解释、跳转预览和后续剪切，不能被当成全局素材库中的独立可检索素材。

### 3.6 原始素材、源版本和分析运行身份

首版全局素材库采用不可变原始视频模型：

1. 一个 `asset_id` 在生命周期内只对应同一份源视频内容。
2. 不提供“保留 `asset_id` 但替换源视频文件”的入口；用户要替换内容时必须重新上传，并产生新的 `asset_id`。
3. 上传完成时在合并文件的同一次流式读取中计算 `content_sha256`；数据库保存 `content_sha256`，首版 `source_version` 直接使用规范化的小写 SHA-256。
4. 文件名、路径、大小、mtime 和 Session ID 都不能单独充当 `source_version`。
5. 启动分析、创建剪切选区和生成派生片段时都冻结 `asset_id + source_version`；后端执行前重新校验。

分析身份严格区分：

```text
analysis_run_id       某一 scheme 的一次业务分析运行
tool_use_session_id   本次业务运行对应的 Tool Session
attempt_id            Tool Session 内部的执行 Attempt
result_hash           规范化发布结果及 schema 版本的内容哈希
```

每个 `analysis_run_id` 只能关联一个素材、一个 `source_version`、一个 scheme 和一个 `tool_use_session_id`。同一业务运行允许 Tool Session 内部发生可审计重试，但不能因此生成多个业务运行身份。

后端保留历史运行及产物用于审计，但普通用户首版不提供多版本列表、旧运行选择、恢复旧结果或手动调用 `activate` 的入口。当前结果因上游变化转为 `stale`、且尚未有新的成功结果替代时，普通用户仍可只读查看这一当前 stale 结果；新的成功结果自动激活后，更旧运行只保留在后台审计记录中。只有仍与当前 `source_version` 和上游版本一致的 current active 结果才能参与检索和默认剪切建议。

如果已有 current ready 结果后的重跑失败或 blocked，旧 current 与 active fragments 继续可用，但详情页必须明确显示“本次重新运行未成功，仍在使用上一次成功结果”及安全错误原因，不能仅恢复 ready 投影而隐藏最新尝试失败。

## 4. 实施前实现评估（2026-07-20 历史基线）

本节记录 v0.9 开始实施前的仓库状态，用于解释 M0–M4 的来源，不再代表当前代码现状。M0–M4 的最终实现和测试证据见[验收记录](./OpenCrew_素材库综合能力_M0-M4_验收记录_2026-07-21.md)；其后已完成的四帧无声视觉召回与派生片段复用见[专项 v0.3](./OpenCrew_无声素材视觉检索与派生片段全局复用_需求变更与开发实施设计_v0.2.md)。

### 4.1 实施前已具备

1. 全局素材库列表、详情、上传、删除、归档和基础筛选已存在。
2. 每条新上传素材会创建独立 Session 和 OpenCut Task。
3. 素材详情页已有对白、画面、综合三种按钮和结果 Tab。
4. 对白分析和画面分析已有后端运行入口、状态轮询和结果读取骨架。
5. 任务内 Asset Library 已有外部素材搜索 Agent，支持 Pexels、Pixabay、Wikimedia、Unsplash。
6. 现有素材搜索 Agent 已有查询规划、候选结构、SSE、导入确认和来源记录。
7. 故事板 Asset Pool 已有上传素材和 Agent 入口，也已有当前 Dialogue 选择状态。

### 4.2 实施前尚未具备（M0–M4 后已按基线补齐）

1. 综合分析只有占位 UI，没有运行 API、执行服务、结果文件和持久化状态。
2. 当前画面分析只产出 Scene Detect 边界和 Keyframe；`visual_summary` 实际为空，OpenCut 当前也明确不执行视觉语义理解，因此不能作为综合分析的完整视觉输入。
3. 全局素材库没有跨素材的 fragment 检索表、全文索引或向量索引。
4. 现有素材搜索的 `local` 来源只扫描当前 Task 的 Asset Library manifest，不会搜索全局素材库。
5. 现有 `local` 排序使用 token 伪向量，只适合作为接口占位，不适合作为生产级语义检索。
6. 故事板 Asset Pool 没有“检索素材”入口和全局素材库结果面板。
7. 素材详情页没有视频剪辑路由、时间线、剪切任务或派生片段 manifest。
8. 没有把全局素材库原始视频或剪切片段导入指定 StoryBoard Task 的统一服务。
9. 当前 `media_library_assets` 没有可靠区分原始视频和派生片段的来源字段；若直接把剪切结果写入该表，会违反本需求的检索边界。
10. 当前素材 Task 只保存各分析的 latest 状态，尚未完整实现设计文档所述的多 Attempt、上游版本引用和 stale 失效规则。
11. 当前 `media_library_assets` 尚未保存源文件内容 SHA-256，现有 OpenCut fingerprint 还包含路径和 mtime，不能直接作为跨 run、跨 Session 稳定的 `source_version`。
12. 当前业务分析失败路径会把 Tool Session `blocked` 汇总为业务 `failed`；v0.8 要求业务层单独保留 `blocked`，需要迁移状态合同和页面文案。

### 4.3 完整综合分析的前置依赖与评审决定

原需求同时要求：

1. 综合分析生成 `people / objects / scene / action / visual_summary`。
2. 综合分析不得读取源视频或重新抽帧。
3. 当前画面分析没有描述性视觉语义。

如果不补齐画面分析能力，综合分析在视觉侧没有可消费的事实：相关字段只能为空，或者由 LLM 根据对白猜测画面，后者属于禁止的编造。因此本评审把以下能力定义为首版综合分析开工前的强制依赖，但不把它们列为对白检索基础里程碑的 P0 前置范围：

```text
03_01  Scene Detect
  -> 03_02  Scene Keyframe Index
  -> 03_03  Keyframe Visual Semantic Description
  -> Composite Analysis
```

`03_03` 属于画面分析，不属于综合分析。它允许读取 `03_02` 已发布的 Keyframe 图像，调用支持图像输入的视觉模型，生成可校验的描述性文本和标签。综合分析只消费 `03_03` 发布的结构化文本、标签、时间范围和证据引用，不读取 Keyframe 图像字节。

这项决定同时意味着：

1. 当前仅完成 Scene Detect 和 Keyframe 的历史记录不能被视为“视觉语义已就绪”。
2. 现有 `visual_status = ready` 语义不足以作为综合分析门禁，必须引入独立的结构和语义能力状态。
3. 如果视觉语义阶段失败，画面分析可以显示为 `partial`，但综合分析不能启动。
4. 视觉语义未完成不影响“只依赖对白分析”的全局原始视频检索。
5. 不在视觉语义完成前交付一个填充空视觉字段或根据对白猜测画面的“简化综合分析”；如需提前增强对白索引，应明确命名为“对白索引增强”，不能宣称是完整综合分析。

### 4.4 画面分析新增正式范围

#### 4.4.1 Keyframe 采样

M0–M4 实施前基线中，`03_02` 每个画面分析片段只保存一个中点 Keyframe，适合预览，但容易漏掉片段前后出现的物体和场景。为了避免固定机位视频只产生一个覆盖全片的视觉片段，`03_01` 已对超过 15 秒的检测 Scene 拆成连续、无间隙、无重叠的分析窗口；这些窗口不伪造镜头切换。R1 在不改变窗口边界的前提下，把每个 fragment 升级为 12.5%/37.5%/62.5%/87.5% 四个稳定采样槽，写入 `sampling_strategy = "scene_uniform_4_v1"`，并在一次多图 VLM 请求中生成语义。历史 `scene_midpoint_v1` 保持只读兼容但不能直接进入 R1 视觉索引。

四帧只提高静态可见事实覆盖，不构成连续视频动作证明。R1 继续强制 `action=null`，模型只能客观描述各采样帧直接可见的状态；不得推断未采样过程、因果、意图或把状态差异写成“倒入、走动、拿起”等连续动作。

长镜头窗口必须受单素材视觉调用次数、图片数、四图总负载和估算成本硬门禁约束，不能把 `ceil(duration/15s)` 理解为无限模型预算。R1 每 fragment 一次基础调用但包含四图；10 分钟无切点素材为 40 个窗口、160 张基础输入图片和 40 次基础调用，30 分钟 synthetic 为 120 个窗口、480 张图片和 120 次基础调用，结构化修复会使对应窗口的图片和调用再增加一倍。超过任一门禁必须返回结构化错误，不得继续调用、静默漏帧或降级单帧。具体合同以开发实施设计 v1.1.4 §8.3–8.4 为准。

发布后的 VLM 质量增强再实现版本化采样：

1. 极短 Scene 可以只取一个代表帧。
2. 超过配置阈值的 Scene 默认取开始、中间和结束附近的 2–3 个代表帧。
3. 黑帧、模糊帧和高度重复帧应替换或去重。
4. 每个 Keyframe 必须保留 `keyframe_id / scene_id / time / image_hash / image_path`。
5. 采样数量存在成本上限，不能随视频时长无限增长。

#### 4.4.2 `03_03 Keyframe Visual Semantic Description`

新增画面分析步骤的输入为：

```text
SessionOutput/visual/final_scene_frame_items.json
SessionOutput/visual/scene_frames/*
visual_prompt_version
visual_model_config_id
```

该步骤只读取已经发布的 Keyframe，不重新读取源视频。它调用支持图像输入的视觉模型，按 Scene 输出：

```json
{
  "fragment_id": "scene_0003",
  "start_ms": 12300,
  "end_ms": 18800,
  "keyframe_refs": ["keyframe_0012"],
  "visual_summary": "一名讲解者在室内，手持桌面产品。",
  "people": ["一名讲解者"],
  "objects": ["桌面产品"],
  "scene": "室内演示区",
  "action": null,
  "keywords": ["室内", "手持产品"],
  "claim_evidence": {
    "people": ["keyframe_0012"],
    "objects": ["keyframe_0012"],
    "scene": ["keyframe_0012"],
    "action": []
  },
  "confidence": 0.82,
  "needs_review": false
}
```

上例是已经交付的 M0–M4 `scene_midpoint_v1` 历史示例，因此只有一个 `keyframe_ref`。R1 `scene_uniform_4_v1` 的顶层 `keyframe_refs` 必须按时间包含 `sample-01..04` 四个引用，各字段证据只引用实际支持该字段的采样帧。fragment ID、边界和完整四帧 refs 仍由后端覆盖，禁止视觉模型生成或修改；模型只填写描述字段，返回后执行 JSON Schema、引用、长度、置信度和动作空值校验。

输出文件至少包括：

```text
SessionOutput/visual/visual_semantic_segments.json
SessionOutput/visual/visual_semantic_manifest.json
SessionReport/visual_semantic_quality_check.json
```

#### 4.4.3 视觉语义约束

视觉模型不得：

1. 根据外貌识别或猜测真实身份、种族、健康、政治、宗教等敏感属性。
2. 把屏幕外、帧间不可见或无法确认的信息写成事实。
3. 仅凭单帧或四张稀疏静帧推断完整连续动作、因果关系或人物意图。
4. 用对白文本替代视觉观察；对白只能在综合分析阶段用于对齐，不能成为视觉事实来源。
5. 生成上游不存在的时间边界或 Keyframe 引用。

字段无法确认时使用 `null`、空数组和较低置信度，不使用看似完整但无证据的默认描述。

#### 4.4.4 状态与兼容

新增独立状态：

```text
visual_structure_status
visual_semantic_status
visual_status  # 由前两者派生的页面总状态
```

建议派生规则：

| 结构状态 | 语义状态 | `visual_status` |
| --- | --- | --- |
| 未开始 | 未开始 | `not_analyzed` |
| 运行中 | 任意 | `running` |
| 已完成 | 等待授权/被阻止 | `blocked` |
| 已完成 | 未开始/失败 | `partial` |
| 已完成 | 运行中 | `running` |
| 已完成 | 已完成 | `ready` |
| 已完成 | 已过期 | `stale` |
| 失败且无结构结果 | 任意 | `failed` |

历史数据迁移时，现有 `visual_status = ready` 只能回填为 `visual_structure_status = ready` 和 `visual_semantic_status = not_analyzed`，不能直接视为完整画面分析。

视觉语义结果的缓存键至少包含 `keyframe image_hash + prompt_version + model_config_id + schema_version`。上游 Keyframe 变化后，旧视觉语义和综合分析都必须变为 `stale`。`stale` 结果在详情页保留只读查看和审计引用，但不得进入当前搜索索引，也不得作为视频剪辑页的默认选区来源。

## 5. 功能一：综合分析

### 5.1 目标

综合分析把已完成的对白索引和画面索引融合成默认推荐的内容索引，用于：

1. 理解视频在各时间范围内“说了什么”和“画面发生了什么”。
2. 生成可用于全文和语义检索的标题、摘要、关键词及结构化标签。
3. 给素材挑选和剪切提供推荐时间范围。
4. 保留所有结论的上游证据引用。

### 5.2 依赖条件

只有同时满足以下条件才能启动：

1. 业务层 `dialogue_status = "ready"`。
2. 业务层 `visual_structure_status = "ready"`。
3. 业务层 `visual_semantic_status = "ready"`。
4. `visual_capabilities.semantic_description` 满足综合分析要求的 schema 版本。
5. 对白结果、画面结构结果和画面语义结果文件均存在且通过 schema 校验。
6. 三份结果属于当前素材，且时间范围不超过源视频时长。
7. 当前没有运行中的综合分析。

启动时必须冻结并记录：

```text
source_version
composite_analysis_run_id
dialogue_analysis_run_id
dialogue_result_hash
visual_analysis_run_id
visual_structure_result_hash
visual_semantic_result_hash
visual_semantic_prompt_version
visual_semantic_model_config_id
composite_prompt_version
model_config_id
```

任一上游分析重新运行并产生新 hash 后，旧综合结果应标记为 `stale`，不能继续显示为当前有效结果。旧结果仍可在历史或详情页只读查看，但不得继续发布到当前检索索引，也不得作为剪辑页默认建议。

### 5.3 输入边界

综合分析允许读取：

1. 对白分析的结构化片段、对白文本、时间范围和 Keyframe 引用。
2. 画面分析已经发布的结构化片段、时间范围、Keyframe ID 引用、视觉描述和视觉标签。
3. 源视频基础元数据，例如总时长、宽高和方向。
4. 版本化的综合分析提示词。

综合分析禁止读取：

1. 源视频字节。
2. 新抽取的视频帧或音频。
3. Keyframe 或其他图像文件的字节；视觉模型调用只能发生在上游画面分析阶段。
4. 与当前素材无关的 Task 文件。
5. 上游未写入 manifest 的临时文件。

这里的“不再访问视频或图像”应通过代码和测试保证，而不只是提示词约定。

### 5.4 LLM 职责

LLM 负责：

1. 将对白与画面事件按时间关系对齐。
2. 给出综合片段标题和简短描述。
3. 基于画面分析已提供的视觉字段，归并关键词、人物通用称谓、场景、动作、物体和用途标签。
4. 说明边界采用、合并或冲突处理的理由。
5. 生成用于搜索的规范化文本。

LLM 不负责：

1. 编造源视频中不存在的时间。
2. 生成越界、负数或 `start_ms >= end_ms` 的片段。
3. 凭空识别人物真实身份、品牌或事实。
4. 删除或覆盖对白和画面分析原结果。
5. 直接生成自由文本后由前端猜测结构。
6. 仅根据对白补写上游画面分析中不存在的人物、物体、场景或动作。

后端应向 LLM 提供候选边界和稳定 ID，并要求返回符合 JSON Schema 的结果。返回后必须执行确定性校验和修正；无法修正时本次运行失败，不发布索引。

系统维护的综合提示词必须把引用闭包写成显式合同：每个片段范围覆盖
其全部对白和视觉引用，claim refs 只能引用同一片段已声明且包含精确
上游值的 visual refs，片段排序且不得重叠。唯一一次结构化修复必须
携带完整 `code:detail` 并重新检查全部合同，不能只修首个错误后引入
新的越界引用；修复后仍有未知引用、越界或无证据事实时继续 fail-closed。

综合输出中的视觉事实必须能追溯到至少一个 `visual_ref`。如果上游画面分析没有支持某个视觉字段，该字段必须是 `null` 或空数组，不能让综合 LLM 自行补全。

### 5.5 综合分析提示词

已确认首版采用系统维护、可版本化的默认提示词：

```text
prompt_id
prompt_version
system_prompt
output_schema_version
created_at
```

工具抽屉展示当前提示词版本和模型配置，但不提供普通用户编辑、覆盖或把任意提示词保存为全局默认的入口。未来若增加高级配置，必须单独设计权限、草稿、校验、版本、回滚和审计，不改变首版决定。

### 5.6 推荐输出

```text
SessionOutput/json/composite_semantic_segments.json
SessionOutput/json/composite_fragment_index.jsonl
SessionOutput/manifests/composite_virtual_clips.json
SessionOutput/manifests/search_index_manifest.json
SessionReport/composite_quality_check.json
```

每个综合片段至少包含：

```json
{
  "fragment_id": "composite_0001",
  "asset_id": "mla_...",
  "scheme": "composite",
  "start_ms": 12300,
  "end_ms": 18800,
  "title": "讲解产品的核心卖点",
  "summary": "讲解者手持产品并说明核心用途。",
  "dialogue_text": "……",
  "visual_summary": "……",
  "keywords": ["产品讲解", "演示"],
  "people": ["讲解者"],
  "objects": ["产品"],
  "scene": "室内演示区",
  "action": null,
  "dialogue_refs": ["dialogue_0008"],
  "visual_refs": ["scene_0003"],
  "visual_claim_refs": {
    "people": ["scene_0003"],
    "objects": ["scene_0003"],
    "scene": ["scene_0003"],
    "action": []
  },
  "keyframe_refs": ["keyframe_0012"],
  "boundary_reasons": [],
  "confidence": 0.91,
  "needs_review": false
}
```

### 5.7 推荐接口

```text
POST /api/media-library/{asset_id}/analyses/composite/run
GET  /api/media-library/{asset_id}/analyses/composite/current
GET  /api/media-library/{asset_id}/analyses/composite/runs/{run_id}
```

运行请求首版至少包含：

```json
{
  "force": false,
  "prompt_version": "default",
  "model_config_id": "server-default"
}
```

模型 provider、真实模型名和密钥不得通过普通用户接口泄漏。成功运行在校验和发布完成后自动成为当前 active 结果；`runs/{run_id}` 用于轮询本次已知运行，不提供历史列表发现能力。普通用户首版不允许通过 API 或页面把历史运行重新激活。

### 5.8 验收标准

1. 对白、画面结构或画面语义任一未完成时，综合分析按钮禁用并说明具体缺失项。
2. 仅有 Scene Detect 和 Keyframe 的旧画面结果不能通过综合分析门禁。
3. 综合分析运行期间不读取源视频或 Keyframe 图像文件。
4. 每个非空视觉字段都能回溯到画面语义结果 ID。
5. 上游缺失某类视觉事实时，综合结果保持空值而不是根据对白补写。
6. 输出时间范围全部合法且不超过视频时长。
7. 上游结果变化后，旧综合结果变为 `stale`。
8. LLM 返回非法 JSON、越界时间或未知引用时不发布结果。
9. 详情页综合 Tab 能展示真实片段、状态、失败原因和运行耗时。
10. 综合结果成功后被同步到全局素材检索存储。
11. `stale` 综合结果仍可只读查看，但不进入当前检索，不初始化默认剪切选区。

## 6. 功能二：视频剪辑页面

### 6.1 页面入口和路由

在素材详情头部“综合分析”右侧新增：

```text
视频剪辑
```

推荐路由：

```text
#/media-library/{asset_id}/editor
```

该页面只编辑当前原始视频，不承担多素材、多轨、转场和复杂成片合成，避免把首版扩成通用 NLE。

从 StoryBoard 检索结果打开时，使用受控查询参数携带导航上下文：

```text
#/media-library/{asset_id}/editor
  ?start_ms=42100
  &end_ms=49800
  &target_task_id=27
  &dialogue_asset_key=dialogue_0005
  &search_id=mls_...
  &matched_fragment_id=dialogue_0012
  &return_to=storyboard_dialogue
```

规则如下：

1. `start_ms/end_ms` 只是建议选区，进入页面后仍按当前素材时长和当前 active fragment 重新校验。
2. `target_task_id` 是默认导入目标，用户可以在剪辑页更换为另一个有效 StoryBoard Task。
3. `dialogue_asset_key / search_id / matched_fragment_id` 用于返回原 Dialogue、证据展示和遥测关联，不能作为源文件或目标 Session 的权威信息。
4. `return_to` 只接受后端和前端共同定义的枚举，不能接受任意 URL。
5. 后端根据 `target_task_id` 重新解析目标 Session；任何前端传入的 Session ID、路径或权限结论都不可信。
6. 用户从素材详情直接打开剪辑页时没有来源上下文，在导入前再选择目标 Task。

### 6.2 页面结构

建议从上到下分为：

1. 返回素材详情、素材名称和分析/剪切任务状态。
2. 源视频播放器和当前时间码。
3. 时间轴缩略预览。
4. 对白、画面、综合三条可开关的索引轨道。
5. 当前选择区间和手动 `start / end` 输入。
6. “素材搜索”“剪切”“加入故事板素材库”操作区。
7. 剪切任务、搜索候选和导入结果抽屉。

首版时间轴是单视频、只读索引叠加和范围选择，不实现：

1. 多轨自由编排。
2. 转场、关键帧动画和复杂特效。
3. 多片段拼接成片。
4. 对对白、画面或综合分析结果的原地编辑。

#### 6.2.1 首版最小可用范围与发布后增强

“最小可用”不能退化为固定宽度、不可缩放的静态条。为保证业务常见的 10 分钟以内视频在任意位置仍能定位和剪切，首版必须包含：

1. 播放器、时间码、播放游标与剪切选区同步。
2. 时间轴缩放、适应视图、水平滚动和自适应时间刻度。
3. 当前可见时间窗口加缓冲区的窗口化渲染，不能为完整视频创建无限宽 DOM。
4. 三条分析索引轨道的显隐、点击定位、悬停摘要和范围选择。
5. 入点/出点拖动、手动毫秒时间输入、范围预览、异步剪切和导入。

以下属于发布后交互增强，不作为产品首版发布门禁：

1. 键盘方向键及修饰键时间步进。
2. 刷新页面或从剪切任务返回后的播放时间、缩放、滚动、轨道显隐和选区恢复。
3. 同一轨道重叠片段的自动子行布局。
4. 同一像素范围内密集片段的 `12 段` 聚合块及点击展开。

#### 6.2.2 时间轴交互参考

用户提供的 Google AI Studio App 示例作为首版时间轴的视觉与操作参考：

```text
https://aistudio.google.com/apps/7d524e04-4526-4506-9fca-778ff249f74e?showPreview=true&showAssistant=true
```

参考范围只包括时间轴的通用交互形态，不表示接入、复制或依赖该 App。依据用户在 2026-07-20 提供的参考截图，首版应采用以下结构：

1. 时间轴顶部显示秒级主刻度和更细的次刻度。
2. 使用贯穿全部轨道的高对比度播放游标，游标可拖动并与播放器当前时间双向同步。
3. 右上角提供时间轴缩放控制，包括缩小、滑杆、放大和适应视图。
4. 底部提供水平滚动条；轨道较多时，轨道区提供独立垂直滚动。
5. 左侧固定轨道头，滚动时间轴时轨道名称和控制项保持可见。
6. 时间片段使用横向色块表示，色块上直接显示序号和简短标题。
7. 当前选中片段使用高亮描边和填充区分，并显示可拖动的入点、出点手柄以及片段时长。
8. 相邻片段的边界必须清晰，过短片段仍应保留最小可点击宽度，但时间计算使用真实 `start_ms/end_ms`。
9. 索引片段支持点击定位、悬停摘要和双击预览该范围。
10. 时间轴缩放和滚动不得改变选区的真实时间值。

首版轨道语义不是参考图中的通用 `V1/V2/A1/A2` 多媒体编排，而是：

```text
C  Composite     综合分析索引轨道
D  Dialogue      对白分析索引轨道
V  Visual        画面分析索引轨道
S  Source Video  原始视频基准轨道
```

轨道按“终极索引 → 原始证据 → 源文件”从上到下排列。`Source Video` 只用于展示视频总时长、当前选择范围和可选缩略图/音频波形，不允许在轨道上移动源视频。三个分析轨道均为只读证据轨道，可以隐藏或锁定，但不能通过拖动改变分析结果本身。用户拖动的是剪切选区，不是索引片段。

#### 6.2.3 时间轴行为与阶段范围

1. 点击任一分析片段后，播放器跳到片段起点，并把剪切选区设为该片段的 `start_ms/end_ms`。
2. 拖动播放游标只改变预览时间，不自动改变剪切选区。
3. 拖动选区入点或出点后设置 `manual_override = true`，同时保留最初来源的 `scheme/fragment_id`。
4. 播放器播放时，播放游标应连续前进；到达选区出点时，范围预览模式应暂停或回到入点。
5. 多个分析片段时间范围重叠时分别显示在各自轨道，不做视觉合并。
6. 隐藏某条分析轨道只影响显示，不影响已经选中的片段或后端结果。
7. 缩放后刻度密度自适应，时间标签不能互相覆盖。
8. `[发布后增强]` 键盘左右方向键执行小步进，配合修饰键执行更细或更大的时间步进；具体步长由产品配置确定。
9. `[发布后增强]` 页面刷新或从剪切任务返回时，恢复播放时间、缩放比例、滚动位置、轨道显隐和当前选区。
10. 不同屏幕宽度下，播放器可以压缩，但时间轴轨道头和剪切操作区不得被挤出可用范围。

#### 6.2.4 用户提供的参考原型评审

本轮同时检查了用户提供的本地参考原型：

```text
docs/剪辑软件.zip
SHA-256: be041a7fb9512ad0d863e5478ddf794e92a7bd38fadf122145bae472417bbc70
```

该压缩包是 React 19、Tailwind CSS 和 Vite 编写的静态界面原型，核心实现位于压缩包内的 `src/App.tsx`。可以复用其交互概念，但不应直接把代码复制到 OpenCrew：

| 可以借鉴 | 不能直接复用 |
| --- | --- |
| 固定轨道头与可横向滚动的时间内容区 | React 组件；OpenCrew 当前前端使用 SolidJS |
| 秒级主刻度、十分之一秒次刻度和随缩放变化的位置计算 | 固定为 10 秒、50–300 px/s 的演示数据 |
| 播放游标、当前时间码和播放控制布局 | 使用 `requestAnimationFrame` 模拟播放，未绑定真实 `<video>` |
| 片段标题、持续时间、高亮描边和关键点菱形标记 | 片段、关键点和选中状态都是硬编码，不能剪切或持久化 |
| 缩放滑杆、水平/垂直滚动和深色高对比度样式 | Inspector、刀片、撤销、轨道锁定等按钮目前只是视觉占位 |

原型还通过 Vite `define` 把 `GEMINI_API_KEY` 注入浏览器构建。OpenCrew 禁止采用这一做法；所有 LLM 密钥、模型配置和调用必须留在后端。

真实实现还必须补足长视频性能：时间轴只渲染当前可见时间窗口及适量缓冲区，不得按完整视频时长创建无限宽、包含全部片段的 DOM。

### 6.3 三种分析索引的时间轴表达

#### 6.3.1 统一时间坐标

三种分析结果必须共享同一个以源视频为基准的毫秒时间坐标。每个索引至少包含：

```text
scheme
fragment_id
start_ms
end_ms
label
summary
confidence
run_id
source_version
stale
evidence_refs
```

区间统一使用左闭右开语义 `[start_ms, end_ms)`，避免相邻片段在同一个时间点重复命中。渲染位置由时间计算，不能把屏幕像素反写为新的分析时间：

```text
x = (time_ms - visible_start_ms) * pixels_per_ms
```

#### 6.3.2 三条分析轨道的视觉语义

| 轨道 | 色彩与标识 | 色块内容 | 额外标记 | 点击后的默认范围 |
| --- | --- | --- | --- | --- |
| `C Composite` | 紫色、`C` 图标 | 综合描述或推荐用途 | `D2/V1` 等证据数量徽标 | 综合片段的 `start_ms/end_ms` |
| `D Dialogue` | 蓝色、对话气泡图标 | 对白首句；空间不足时只显示序号 | ASR 置信度、说话人或字幕标记 | 对白句段的 `start_ms/end_ms` |
| `V Visual` | 琥珀色、画面图标 | 场景、动作或主体短标签 | Keyframe 使用菱形锚点 | 画面场景的 `start_ms/end_ms` |

不能只依赖颜色区分轨道；轨道头、片段前缀和悬停卡片都必须显示 `C/D/V` 类型。色块空间不足时隐藏文字，但保留最小可点击宽度、类型图标和悬停信息。

各轨道的展开内容为：

1. `Composite`：综合摘要、可用场景、关键词、对白/画面证据引用和综合分析版本。
2. `Dialogue`：完整对白、说话人、原始或识别字幕来源、置信度和对白分析版本。
3. `Visual`：人物通用称谓、动作、场景、物体、镜头描述、代表 Keyframe 和画面分析版本。

低置信度片段使用虚线边框；`stale` 片段使用斜纹遮罩和警告标识；这些状态不能仅靠降低透明度表达。

#### 6.3.3 重叠和密集片段

1. 不同类型的片段分别留在各自轨道，不跨轨合并。
2. 同一轨道内不重叠的片段按时间顺序平铺。
3. `[发布后增强]` 同一轨道内发生重叠时，在该轨道内部自动分配临时子行，不能改变数据时间。
4. `[发布后增强]` 同一屏幕像素范围内片段过密时，显示聚合块和数量，例如 `12 段`；放大或点击后展开。
5. 极短片段的可点击宽度可以大于其真实像素宽度，但剪切范围和时间提示必须使用真实时间。
6. 综合片段允许覆盖多个对白和画面片段，其 `start_ms/end_ms` 应由 LLM 输出经后端校验，不能在前端临时推断。

首版遇到同轨重叠时仍必须保留全部真实片段，按开始时间稳定渲染并把焦点片段置顶；用户也可以从与时间轴联动的索引列表精确选择。片段密集时依靠缩放、水平滚动和可见窗口渲染保持可用，不要求首版实现自动子行或聚合块。

#### 6.3.4 综合索引与原始证据联动

综合索引是对白和画面证据的上层索引。用户悬停或选中一个综合片段时：

1. 高亮该综合片段引用的 `dialogue_fragment_id` 和 `visual_fragment_id`。
2. 在所有轨道显示同一时间范围的纵向焦点带。
3. 未被引用的片段降低视觉强调，但仍保持可读。
4. 详情卡按“综合结论、对白证据、画面证据”展示可追溯关系。
5. 默认不绘制跨轨连接线，避免密集时间轴产生视觉噪音；只在悬停详情中按需显示。

选中对白或画面片段时，可以反向标出引用它的综合片段。如果综合分析尚未完成，该轨道显示“尚未生成”，不能伪造聚合片段。

#### 6.3.5 播放游标、焦点索引、搜索上下文和剪切选区

页面必须区分四种状态：

| 状态 | 数量 | 用途 |
| --- | --- | --- |
| 播放游标 | 1 | 表示播放器当前时间 |
| 焦点索引 | 0 或 1 | 展示当前索引详情和证据关系 |
| 搜索上下文 | 0 到多条 | 组成“素材搜索”的语义输入 |
| 剪切选区 | 0 或 1 | 表示将要生成文件的唯一时间范围 |

普通点击索引时，把它设为焦点索引，并默认用它初始化剪切选区。用户可以通过明确的“加入搜索条件”操作把一个或多个索引加入搜索上下文；不能因为用户拖动了播放游标就改变搜索条件或剪切范围。

从多个轨道加入搜索上下文时，查询构建规则为：

1. 对白索引提供原文语义和说话意图。
2. 画面索引提供人物通用称谓、动作、场景和物体。
3. 综合索引提供归纳后的主题、推荐用途和关键词。
4. 如果综合索引已引用相同的对白/画面证据，查询构建器进行去重，避免重复加权。

#### 6.3.6 索引选择和状态生命周期

用户可以：

1. 切换显示对白、画面、综合三条轨道。
2. 点击一条索引，把该索引的 `start_ms/end_ms` 设为当前选择。
3. 拖动入点和出点微调。
4. 手动输入开始和结束时间。
5. 在播放器中预览当前选择范围。

首版在当前页面生命周期内维护以下状态，并在创建搜索或剪切请求时提交必要字段：

```text
asset_id
source_version
scheme
fragment_id
start_ms
end_ms
manual_override
search_fragment_refs
visible_tracks
focused_fragment_ref
```

产品首版不要求刷新页面后恢复上述编辑器状态。发布后状态恢复功能再持久化播放时间、缩放比例、滚动位置、轨道显隐和未提交选区；已经创建的异步剪切任务及其输入范围不受页面刷新影响。

如果分析版本变化，旧焦点索引、搜索上下文和由该索引初始化的剪切选区应标记为 `stale`。页面必须清除其“当前推荐”资格，不能继续用它发起素材搜索；用户如仍要使用同一时间范围，必须把它显式转换为不依赖旧 fragment 的手动选区。原始视频不可原地替换，因此 `source_version` 不匹配属于数据完整性错误，剪切请求直接拒绝。

### 6.4 基于索引执行素材搜索（外部 + 全局素材库）

用户已经确认，首版剪辑页的“素材搜索”必须同时支持外部 provider 和 OpenCrew 全局素材库。用户选择一个索引，或把多条索引加入搜索上下文后点击“素材搜索”，系统先把结构化语义去重、合并成共享 query plan，再分别调用两个来源。焦点索引为空但搜索上下文非空时仍可搜索；两者都为空时按钮禁用。

查询上下文可以包含：

1. 标题、摘要和关键词。
2. 对白文本。
3. 场景、人物通用称谓、动作、物体。
4. 视频方向和期望时长。
5. 用户补充的搜索要求。

来源合同：

| source | 实现与资格 |
| --- | --- |
| `external` | 走现有 Pexels、Pixabay、Wikimedia、Unsplash 等 provider 和合规链路 |
| `media_library` | 调用共享 `MediaLibrarySearchService`，召回未归档且具 active dialogue 或合格四帧 visual semantic 的原视频，以及显式开放、父素材未归档的派生片段 |

首版默认同时搜索两个来源，并允许用户单独关闭任一来源。当前正在剪辑的源 `asset_id` 默认从全局素材库候选中排除，避免返回自身。所有候选使用统一外壳，但必须显示明确来源：

```text
source
source_asset_id
source_version
title
preview
duration_ms
orientation
score
score_reasons
matched_fragments
license
```

`source_version` 只适用于 `media_library` 候选；外部候选为 `null`。前端不得用统一外壳掩盖两类来源在身份和后续操作上的差异。

外部候选必须展示来源、作者、授权、时长、比例和匹配原因，并继续执行 host allowlist、MIME、文件头、大小和 license 校验。外部 provider 通常返回完整视频，不保证返回精确可下载的“片段”，因此结果称为“视频候选”。产品首版只允许把外部候选整条导入指定 StoryBoard Task，不提供“直接剪辑”或“导入后自动进入剪辑”的入口。用户若确实需要剪辑该视频，必须在本需求之外重新把合法文件上传为新的全局素材库原始视频，获得新的 `asset_id` 后再使用剪辑页。

全局素材库候选展示原始素材身份、命中对白和建议 `start_ms/end_ms`。用户可以把整条原始视频通过 `media_library_original` 分支加入目标 StoryBoard Asset Pool，或打开该候选自己的剪辑页进一步剪切；不能在当前源视频的剪切任务里直接把另一个原始视频当作本地轨道。

首版全局素材库召回仍以对白索引为主。综合索引可以用于构造更好的 query plan，但不能因此宣称已经支持“按画面检索全局 B-roll”；画面检索属于后续搜索质量范围。

### 6.5 剪切动作

建议交互顺序为：

1. 用户确认 `start_ms/end_ms`。
2. 点击“剪切”。
3. 在确认弹窗中先输入文件名。
4. 后端创建异步剪切任务。
5. 页面展示排队、运行、成功或失败状态。
6. 成功后展示派生视频预览、下载和“加入故事板素材库”操作。

文件名应在执行前确认，避免先生成无名文件再等待用户输入而产生孤儿文件。

首版剪切任务采用进程内异步执行，不建设持久化任务队列：

1. `POST` 成功后返回当前服务进程内唯一的 `clip_job_id`，页面据此轮询状态；ID 内含不可由前端伪造的服务启动代次标识。
2. 只要后端服务进程仍在，页面刷新后可以通过 `asset_id + clip_job_id` 继续查询；不承诺服务重启后的任务恢复。
3. 服务重启后，后端可以通过启动代次识别旧 `clip_job_id`，返回结构化 `clip_job_lost`，提示用户重新创建剪切任务；从未存在或格式非法的 ID 仍返回普通 `clip_job_not_found`。
4. 首版不实现断点续剪；中断后从头重新执行。
5. 当前进程内重复提交相同幂等键不得创建两个并行 FFmpeg 进程；成功结果还受数据库唯一幂等键保护。
6. 后端启动时扫描受控临时目录并清理没有成功派生记录、且超过安全时间阈值的 `.part` 文件。
7. 进程内任务可以取消；取消或失败必须终止 FFmpeg 子进程并清理临时文件。
8. 只有成功完成并登记到数据库和 `session_files` 的派生片段属于持久化业务结果，不受后端重启影响。

后端必须使用参数数组调用 FFmpeg，禁止拼接 shell 字符串。需要明确：

1. 精确剪切默认允许重编码，以保证起止时间准确。
2. 如果提供快速无损模式，应提示切点可能吸附到关键帧。
3. 输出不得覆盖源视频。
4. `start_ms >= 0`、`end_ms <= duration_ms`、`end_ms > start_ms`。
5. 最短和最长允许时长由配置限制。
6. 失败、取消或进程退出后清理临时文件。

### 6.6 派生片段模型

剪切结果必须存入新增数据库表 `media_library_clip_derivatives`，并通过正式 schema migration 创建；不得复用或写入 `media_library_assets`。对应逻辑实体为：

```text
MediaClipDerivative
  clip_id
  idempotency_key
  source_asset_id
  source_session_id
  source_version
  source_start_ms
  source_end_ms
  source_scheme
  source_fragment_id
  output_path
  display_name
  duration_ms
  content_sha256
  size_bytes
  operation
  created_at
```

该表至少具有：

```text
clip_id primary key
source_asset_id foreign key -> media_library_assets.asset_id
idempotency_key unique
search_eligible boolean not null default false
check (search_eligible = false)
```

成功文件统一存储在来源素材 Session 的受控目录：

```text
SessionOutput/clips/{clip_id}/{safe_filename}
```

写入顺序必须是临时文件重编码、媒体校验、原子改名、数据库登记和 `session_files` 登记。任一步失败都不能留下可见的半成品记录。`media_library_clip_derivatives` 只保存成功结果；排队、运行、失败和中断状态属于当前服务进程内的 `clip_job`，服务重启后不恢复。

`source_asset_id` 首版使用删除保护，存在派生片段时不能绕过文件清理直接删除原始素材。删除服务必须先清理受控输出文件和派生记录，再处理原始素材，不能只依赖数据库级联。

全局素材库列表可以在原始素材详情中展示“派生片段”，但跨 Task 搜索服务只查询 `media_library_assets` 原始素材及其 fragment index，不得把 `media_library_clip_derivatives` 加入召回集合。

### 6.7 导入指定 StoryBoard Task

用户可以选择目标 Task。后端提供受控的 StoryBoard 目标列表，只返回当前部署中仍有效、可写入且能解析唯一 Session 的 StoryBoard Task。后端应从 Task 解析唯一 Session，不允许前端任意提交不匹配的 `task_id + session_id` 组合。

从 StoryBoard 检索结果打开剪辑页时，导航上下文中的 `target_task_id` 成为默认值，但用户可以更换目标。导入成功后页面提供“返回原 Dialogue”，使用原始 `task_id + dialogue_asset_key` 定位；如果来源 Dialogue 已删除或身份失效，则退回该 Task 的 StoryBoard 首页，不按旧列表序号猜测。

剪切片段导入目标：

```text
<target workspace>/SessionOutput/storyboard/assets/videos/
```

同时更新当前 StoryBoard 的资产 manifest，并记录：

```text
source = "media_library_clip"
source_asset_id
source_clip_id
source_start_ms
source_end_ms
source_search_id
source_dialogue_asset_key
imported_at
```

外部搜索候选必须先走现有下载、MIME、文件头、大小和授权校验，再进入目标 Task。禁止把第三方 URL 或另一个 Session 的绝对路径直接写入 StoryBoard manifest。

### 6.8 推荐接口

```text
GET  /api/media-library/{asset_id}/editor
GET  /api/media-library/import-targets/storyboards
POST /api/media-library/{asset_id}/clip-jobs
GET  /api/media-library/{asset_id}/clip-jobs/{clip_job_id}
POST /api/media-library/{asset_id}/clip-jobs/{clip_job_id}/cancel
GET  /api/media-library/{asset_id}/clips
GET  /api/media-library/{asset_id}/clips/{clip_id}
DELETE /api/media-library/{asset_id}/clips/{clip_id}
POST /api/media-library/{asset_id}/clips/{clip_id}/import-to-storyboard
POST /api/media-library/{asset_id}/search/plan
POST /api/media-library/{asset_id}/search/runs
GET  /api/media-library/{asset_id}/search/runs/{search_id}
POST /api/media-library/{asset_id}/search/runs/{search_id}/import-to-storyboard
```

创建剪切任务的首版请求至少包含：

```json
{
  "source_version": "0123456789abcdef...",
  "start_ms": 12300,
  "end_ms": 18800,
  "display_name": "产品核心卖点",
  "source_scheme": "composite",
  "source_fragment_id": "composite_0001",
  "manual_override": false,
  "idempotency_key": "client-generated-opaque-id"
}
```

进程内任务查询至少返回：

```json
{
  "clip_job_id": "clipjob_...",
  "status": "queued",
  "progress": 0,
  "clip_id": null,
  "error": null
}
```

`status` 只允许 `queued / running / completed / failed / cancelled`。成功时填充持久化 `clip_id`；服务启动代次不匹配时不伪造状态对象，而是返回 `clip_job_lost`。

搜索 run 请求显式携带 `sources: ["external", "media_library"]`。路由层只做剪辑页上下文适配，外部候选继续复用既有 provider service，并且首版只提供整条导入目标 StoryBoard；全局候选继续复用共享 `MediaLibrarySearchService` 和 `media_library_original` 导入分支。

### 6.9 验收标准

1. 三种分析索引可以独立显示、隐藏和选择。
2. 点击索引后播放器跳到正确范围并在出点暂停。
3. 用户可以手动输入合法起止时间。
4. 剪切任务异步执行，不阻塞 API event loop。
5. 成功输出新文件，源视频字节和路径不变。
6. 输出文件名经过安全清洗且不会路径穿越。
7. 剪切结果不会进入全局原始视频搜索集合。
8. 用户可以把剪切结果导入一个有权限的 StoryBoard Task。
9. 导入后结果出现在目标 Task 的“上传素材”视频区域，并保留来源追踪。
10. 外部搜索结果在导入前展示授权和来源确认。
11. 首版可以同时搜索外部 provider 与全局素材库，也可以单独关闭任一来源。
12. 两类候选的来源标识、资格、授权和导入路径不会混淆。
13. 全局素材库候选只包含符合资格的原始视频，并默认排除当前正在剪辑的源素材。
14. 10 分钟代表视频可以通过缩放、适应视图、水平滚动和窗口化渲染完成精确范围选择，并能在靠近尾部的非关键帧时间准确起切；30 分钟 synthetic 只作非阻塞压力测试。
15. 键盘步进、刷新状态恢复、同轨重叠子行和密集聚合未实现时，不影响首版“选择范围 → 剪切 → 导入”闭环验收。
16. 外部候选首版只有“整条导入 StoryBoard”，不出现“直接剪辑”或“导入后自动剪辑”入口。
17. 后端服务重启后，未完成剪切任务返回 `clip_job_lost` 并允许用户重新创建；遗留临时文件会被安全清理，成功片段仍可查询。
18. 从 StoryBoard 打开的剪辑页能默认选中原 Task、返回原 Dialogue，并通过同一 `search_id` 关联搜索、剪切和导入。
19. 输出实际播放时长与请求时长的允许偏差不能固定为 ±250 ms；容差取一个视频帧、一个音频编码帧和请求时长 5% 三者的最大值，并以 250 ms 封顶。帧率不可用时按 50 ms 回退。

## 7. 功能三：故事板“检索素材”

### 7.1 入口规则

当前故事板 Asset Pool 的“上传素材”区域已有：

```text
Upload | Folder | Agent
```

建议增加：

```text
Upload | Folder | Agent | 检索素材
```

显示条件：

1. 用户已经选中一个 Dialogue。
2. Dialogue 有非空文本。
3. 当前 Task 和 Session 有效。

未选择 Dialogue 时可以隐藏按钮，或禁用并提示“请先选择一个 Dialogue”；建议首版使用禁用态，便于用户发现能力。

### 7.2 查询构建

按钮点击后打开当前 Dialogue 的检索抽屉。发送给查询规划 LLM 的内容至少包括：

```text
task_id
dialogue_asset_key
dialogue_text
scene_text
shot_text
当前画幅
用户补充要求
```

`dialogue_asset_key` 是稳定身份；不得只用列表序号或可能变化的展示文本绑定结果。

LLM 只负责把创作上下文转换成结构化搜索意图，例如：

```json
{
  "query_text": "中医讲解者在茶桌前演示茶叶",
  "must_have": ["讲解者", "茶桌"],
  "nice_to_have": ["室内", "竖屏"],
  "negative_terms": [],
  "target_orientation": "portrait"
}
```

真正的召回、过滤和排序由素材库搜索服务执行，不能让 LLM 根据一份素材标题列表直接“猜”结果。

### 7.3 可检索资格

原始视频只有满足以下条件才进入召回集合：

1. `upload_status = "ready"`。
2. `archived = false`。
3. 业务层 `dialogue_status = "ready"`。
4. 当前激活的对白索引存在并通过 schema 校验。
5. 素材不是派生剪切片段。

综合分析可以在已完成时提高排序质量，但不能成为本功能的硬依赖，因为原始需求明确以完成对白分析为准。

### 7.4 搜索和排序

检索分为查询规划、召回、重排三个职责不同的阶段：

1. 查询规划：LLM 把 Dialogue 规范化为原文查询、关键词、短语、同义词和过滤条件；规划结果可以缓存。
2. 召回：只能由确定性代码和数据库执行，不能让 LLM 扫描标题列表或生成 `asset_id`。
3. 重排：在有限且可校验的候选集合上调整相关性，生成命中理由。

首版对白检索使用“LLM 查询规划 + 对白原句/短语精确匹配 + 确定性 token 重排”：

1. 发布当前激活的对白 fragment 到中心索引表。
2. 保留中文原始查询，不得为了复用外部素材搜索而只保留英文翻译。
3. 对对白原句、规划短语、素材标题和人工标签进行 Unicode/大小写/空白规范化后的完整短语或子串匹配。
4. 使用现有 Search Agent 的运行、候选和导入合同，但不直接复用其仅支持 ASCII token 的本地召回实现。
5. 首版的“token 重排”只统计规划器明确输出的关键词/短语在候选文本中的命中数量和覆盖比例，不引入隐藏的中文分词依赖。
6. LLM 查询规划失败、超时、被关闭或配额耗尽时，搜索不能整体失败；后端必须退回 Dialogue 原文、用户原始输入和显式过滤条件执行确定性精确/子串召回，并在响应中标记 `planner_degraded = true`。
7. 按 fragment 召回并按 `asset_id` 聚合，叠加方向、时长、质量和置信度等确定性信号。

M0–M4 阶段的产品文案曾限定为“对白/关键词检索”。专项 v0.3 完成后，当前页面必须诚实说明还支持合格四帧中文视觉描述与显式开放的派生片段名称/标签召回，但仍不得称为向量语义、连续动作理解或 clip 独立视觉分析。中文召回继续优先精确率；后续 FTS、分词和 LLM 重排仍是独立增强。

零结果状态至少提示用户尝试缩短关键词、移除可选限制、改用对白原句或重新运行对白分析。提示只帮助修改查询，不得自动放宽“对白分析完成、原始视频、未归档”等强制资格条件。

发布后的搜索质量增强在不改变接口合同的前提下，把召回层升级为 PostgreSQL `tsvector + GIN`，并增加可关闭的 top-N LLM 重排：

```text
查询规划
  -> 精确/全文确定性召回 top-N fragments
  -> 候选去重，并限制单个 asset 的 fragment 数
  -> LLM 对候选相关性打分并生成 score_reasons
  -> 后端校验
  -> 按 asset_id 聚合
```

top-N LLM 重排必须满足：

1. `N`、单素材 fragment 上限、超时和模型成本上限可配置。
2. 输入只包含稳定候选 ID、对白片段、时间范围和允许参与排序的元数据。
3. 输出只能引用输入中已有的候选 ID，分数范围和 `score_reasons` 必须通过 schema 校验；禁止生成新素材或新时间范围。
4. 缓存键至少包含 `query_plan_hash + candidate_set_hash + rerank_prompt_version + model_config_id`。
5. 可以按部署、入口或单次请求关闭；关闭、超时、配额耗尽或校验失败时，自动退回确定性排序。
6. 综合分析已完成时可以把综合描述和标签作为候选证据及加分信号；未完成时不降低对白检索资格。

这项搜索质量增强来自 LLM 查询扩展和有限候选重排，仍不得宣称已实现向量语义召回。

结果先按 fragment 召回，再按 `asset_id` 聚合成原始视频：

```text
一个原始视频结果
  -> top matched fragments
  -> overall score
  -> score reasons
```

### 7.5 结果交互

每个结果至少显示：

1. 原始视频名称和缩略图。
2. 命中范围和时长。
3. 命中对白。
4. 命中原因。
5. 代表 Keyframe。
6. 素材方向、总时长和分析状态。
7. 产品首版提供“预览原视频”“打开剪辑”“加入当前 Task”。

“加入当前 Task”默认导入原始视频，不自动生成剪切片段。用户需要精确范围时可以点击“打开剪辑”，携带命中 `start_ms/end_ms`、当前 `target_task_id`、`dialogue_asset_key`、`search_id` 和 `matched_fragment_id` 进入视频剪辑页面。剪辑页默认导入原 Task、允许更换有效目标，并在完成后提供返回原 Dialogue。阶段 4 完成前只允许内部里程碑验证，不发布缺失真实剪切能力的产品首版或可点击生产占位入口。

结果卡必须明确说明“按原视频归组”，不能让用户误以为只命中了整条视频。每个 `matched_fragment` 独立显示时间范围、命中文本和“剪切这个片段”动作；整条导入按钮必须显式标明会加入整条原视频。主结果仍是原视频，片段动作只把权威命中范围带入剪辑页，不在检索请求中隐式生成文件。

### 7.6 推荐接口

```text
POST /api/koubo-storyboard/tasks/{task_id}/dialogues/{dialogue_asset_key}/media-library-search/plan
POST /api/koubo-storyboard/tasks/{task_id}/dialogues/{dialogue_asset_key}/media-library-search/runs
GET  /api/koubo-storyboard/tasks/{task_id}/media-library-search/runs/{search_id}
POST /api/koubo-storyboard/tasks/{task_id}/media-library-search/import
```

### 7.7 验收标准

1. 选择不同 Dialogue 后，检索上下文同步变化。
2. 未选择 Dialogue 时不能发起检索。
3. 不返回未完成对白分析的素材。
4. 不返回归档、上传未完成或派生剪切片段。
5. 结果按原始视频聚合，不把虚拟 fragment 显示为新的全局素材。
6. 每条结果有可解释的命中范围和命中理由。
7. `[首版-剪辑]` 打开剪辑页面时正确传入来源素材和建议起止时间。
8. 导入当前 Task 后，视频出现在 Asset Pool 的上传素材区域。
9. 查询规划不可用时降级到原文检索，零结果时提供安全的查询修改建议。
10. 搜索、预览和最终导入通过 `search_id` 形成可查询关联。

## 8. 功能四：Agent - Asset Library 检索全局素材库

### 8.1 与现有素材搜索 Agent 的关系

现有 Agent 已支持：

```text
local
pexels
pixabay
wikimedia
unsplash
```

其中 `local` 的真实含义是“当前 Task 的 Asset Library”，不是全局素材库。为避免破坏现有行为，不应修改 `local` 的语义，建议新增独立来源：

```text
media_library
```

来源定义：

| source | 检索范围 |
| --- | --- |
| `local` | 当前 Task 已有图片、音频和视频 |
| `media_library` | 全局素材库中完成对白分析的原始视频 |
| 外部 provider | 联网素材网站 |

复用范围限于现有 Search Agent 的查询规划入口、运行状态、候选外壳、事件流、设置和导入交互。全局素材库仍需新增 `media_library` source adapter、对白 fragment 发布器、资格过滤和中心召回实现；不得把当前只扫描 Task manifest 的 `local` provider 或词袋余弦占位实现描述为可直接复用的全局中文检索能力。

### 8.2 共享服务要求

`media_library` 来源必须调用与故事板“检索素材”相同的共享服务，不允许在 Agent service 中复制一份索引扫描和排序逻辑。

共享请求至少支持：

```json
{
  "query_text": "……",
  "media_types": ["video"],
  "orientation": "portrait",
  "limit": 12,
  "eligible_analysis": "dialogue_ready",
  "include_derived_clips": false
}
```

### 8.3 候选合同

为了兼容现有 Search Agent，`media_library` 候选可以继续使用统一 candidate 外壳，但必须补充：

```json
{
  "provider": "media_library",
  "provider_asset_id": "mla_...",
  "source_version": "0123456789abcdef...",
  "media_type": "video",
  "local_reuse": false,
  "global_media_library": true,
  "matched_fragments": [],
  "search_eligible_source": "original_video"
}
```

候选导入必须通过后端按 `asset_id` 重新读取权威记录，不能信任前端回传的源路径、Session ID 或下载 URL。

### 8.4 验收标准

1. Agent 设置中可以单独启用或关闭“全局素材库”来源。
2. `local` 与 `media_library` 结果有清晰来源标签。
3. 全局素材库结果只包含符合资格的原始视频。
4. 同一原始视频多 fragment 命中时只展示一张主候选卡，并可展开命中范围。
5. 用户确认后可以把原始视频导入当前 Task。
6. Agent 不自动剪切、不自动导入，也不自动修改 StoryBoard。

## 9. 共享检索索引设计

### 9.1 不应在请求时扫描所有 Session JSON

“素材库中已经有很多上传视频”意味着请求时逐个打开：

```text
SessionOutput/json/*.json
```

不可接受。分析完成后应把可检索字段发布到中心检索存储。

推荐新增逻辑实体：

```text
media_library_fragment_index
```

至少包含：

```text
asset_id
source_session_id
source_version
analysis_scheme
analysis_run_id
result_hash
fragment_id
start_ms
end_ms
dialogue_text
title
summary
keywords_json
visual_labels_json
search_text
search_lexemes_text
search_tsv
tokenizer_name
tokenizer_version
dictionary_hash
normalization_version
quality_status
confidence
is_active
created_at
updated_at
```

首版中全文检索和分词版本字段可以为空，但中心表、稳定 fragment 身份、源版本、原始对白、时间范围、激活状态和结果 hash 必须已经存在，确保发布后的搜索质量增强只替换召回实现而不改动上层合同。

正式运行数据库已经是 PostgreSQL。首版可以先在同一中心表上使用规范化精确/短语/子串匹配和确定性 token 排序。产品已明确接受这一简单召回只用于不超过 500 条原始视频的小规模试运行；分页和候选上限只能限制返回量，不能被描述为消除了子串查询的全表扫描成本。

首版发布门禁为：

1. 使用接近真实对白长度和 fragment 分布的 500 条视频数据集执行 PostgreSQL 集成压测。
2. 正常搜索端到端 P95 不超过 3 秒，并同时记录数据库召回耗时、规划耗时和总耗时。
3. 运行时持续记录原始视频数量、active fragment 数量和 P95；达到 500 条视频前必须发出容量告警并安排全文检索增强。
4. 超过 500 条视频后不再承诺简单召回的 3 秒目标；不能继续把它描述为无规模限制的正式检索方案。
5. 如果未达到 500 条视频但真实 active fragment 数量已经使 P95 超过 3 秒，同样必须提前进入全文检索增强，不能以视频条数未超限为由忽略性能退化。

发布后的全文检索增强机制写死为：

```text
search_text          TEXT，用于审计、展示和精确匹配
search_lexemes_text  TEXT，后端生成的空格分隔规范化检索词
search_tsv           TSVECTOR，由 to_tsvector('simple', search_lexemes_text) 生成
GIN(search_tsv)      PostgreSQL GIN 索引
```

查询规划器输出原始查询和规范化 `query_terms`，后端使用同一套词项规范化规则构造 `websearch_to_tsquery('simple', ...)`，并使用 `ts_rank_cd` 排序。中文对白不能直接依赖 PostgreSQL 默认分词；发布器必须把对白关键词、同义词、标题和标签预先转换成空格分隔的规范化词项。

中文/CJK 分词器是发布后搜索质量增强的显式新增依赖，不得隐藏在“规范化”一词中。编码前先做针对真实素材语料的短期 spike，再锁定具体的分词库、版本、词典和自定义业务词表。要求：

1. 发布端和查询端使用同一分词实现、版本、归一化规则和词典。
2. 索引记录 `tokenizer_name / tokenizer_version / dictionary_hash / normalization_version`。
3. 模型生成的关键词只能作为分词输入或查询扩展，不能替代确定性分词。
4. 分词库、主词典或归一化规则升级视为索引重建事件，必须支持并行重建、校验和回滚。
5. spike 至少覆盖专有名词、数字单位、中英文混排、同义表达和无空格长句，并以召回率、索引耗时、查询延迟、依赖体积和维护成本作为选型依据。
6. 在分词器尚未选定和验证前，不得把中文 PostgreSQL FTS 标记为完成。

这些列和 GIN 索引必须通过 PostgreSQL migration 创建。SQLite 合同测试可以使用兼容字段或 repository fake，但不能替代 PostgreSQL 全文检索集成测试。

仓库目前没有 embedding 模型、向量存储、pgvector 或外部向量服务。向量召回不属于产品首版或发布后的第一轮搜索质量增强，也不是“接入现有服务”的工作。它作为第二迭代单独立项，开始前必须完成：

1. embedding 模型与数据外发方式选型。
2. pgvector 或外部向量数据库选型。
3. 向量维度、模型版本、重建和回滚策略。
4. 存储、模型调用、批量回填和持续增量成本评估。
5. 延迟、召回质量、监控、备份和运维责任评估。

第二迭代若获批，向量记录仍必须通过稳定的 `asset_id + analysis_scheme + analysis_run_id + fragment_id` 关联当前索引，不能改变原始视频作为主结果的业务边界。

### 9.2 top-N LLM 重排服务边界

发布后搜索质量增强的重排器位于确定性召回之后，不直接访问全库。调用输入包含规范化查询意图、有限候选及其证据快照；调用输出只接受：

```json
{
  "items": [
    {
      "candidate_id": "fragment_...",
      "relevance_score": 0.91,
      "score_reasons": ["对白明确提到目标主题"]
    }
  ]
}
```

后端必须拒绝候选集外 ID、重复 ID、非有限数值、越界分数、缺失证据的理由和任何由模型新增的时间范围。重排分数不能覆盖强制资格过滤；素材在模型调用期间失效时，返回前必须重新过滤。重排记录需区分 `deterministic_score / llm_rerank_score / final_score`，以便审计、离线评测和关闭重排后的结果对比。

### 9.3 发布与失效

首版对白索引只实现防止重复和脏索引所必需的最小激活版本：

1. 每次发布携带稳定 `source_version / analysis_run_id / result_hash`。
2. 对白分析完成后，在同一事务内写入新 dialogue fragments、将新记录设为 `is_active = true`，并把该素材旧 dialogue fragments 设为 `is_active = false`。
3. 相同 `asset_id + source_version + analysis_run_id + result_hash` 的重复发布保持幂等，不产生重复 fragment。
4. 新索引写入或校验失败时，旧的 active 索引继续可用，不能出现新旧各激活一部分。
5. 素材归档后不参与默认召回；素材删除前先删除或失效索引。
6. 派生剪切片段从不发布到此表。
7. 旧运行和旧 fragment 保留为审计历史，但普通用户不能重新激活；新的成功运行通过发布事务自动成为唯一 current active 结果。

通用多 Attempt 比较 UI 不属于首版。后端仍保留运行身份、Tool Session、Attempt 和结果 hash 供审计；普通用户只查看 current 结果，current 变为 `stale` 且尚未被新成功结果替代时保持只读，不提供任意历史列表或恢复旧版本。首版阶段 2/3 在视觉语义和综合分析出现时，必须补齐对白/Keyframe/视觉语义到 composite 的上游版本引用和 stale 级联，但不需要先建设一个通用工作流依赖图平台。

首版阶段 2/3 及后续质量增强继续满足：

1. 综合分析完成后发布 composite fragment index。
2. 上游结果失效时，对应 composite 索引立即失效。
3. 分词器、词典或归一化版本变化时，旧全文索引标记为待重建；重建成功切换前继续使用已验证的旧索引，不能在请求中混用不同版本词项。

### 9.4 统一搜索响应

```json
{
  "search_id": "mls_...",
  "retrieval_version": "dialogue_literal_v1",
  "planner_degraded": false,
  "result_count": 1,
  "items": [
    {
      "source": "media_library",
      "candidate_id": "mla_...",
      "asset_id": "mla_...",
      "source_version": "0123456789abcdef...",
      "display_name": "原始采访视频",
      "preview_url": "/api/...",
      "duration_ms": 180000,
      "orientation": "portrait",
      "score": 0.88,
      "score_reasons": ["对白短语命中", "竖屏匹配"],
      "matched_fragments": [
        {
          "scheme": "dialogue",
          "fragment_id": "dialogue_0012",
          "start_ms": 42100,
          "end_ms": 49800,
          "dialogue_text": "……",
          "summary": "……",
          "keyframe_url": "/api/..."
        }
      ]
    }
  ]
}
```

### 9.5 全局可见性

当前产品没有用户或租户归属字段，`media_library_assets.session_id` 只表示素材来源和文件所在 Session，不是权限边界。本需求采用部署级全局素材库：

1. 所有符合召回资格的原始视频都可以被任意 Task/Session 的故事板和 Agent 检索。
2. 搜索查询不增加用户、租户或 source Session 过滤条件。
3. 导入目标仍必须是有效 Task，且后端从 Task 解析目标 Session。
4. 归档、上传状态、对白分析状态和“非派生片段”仍是强制资格过滤。
5. 未来如果引入多租户，需要新增明确的 owner/tenant 数据模型、迁移和授权设计；不能把 `session_id` 临时解释成租户 ID。

### 9.6 首版检索遥测与迭代闭环

为了让发布后的分词器选型、查询扩展和重排调优基于真实数据，首版检索基础必须从第一天记录轻量、可查询的检索遥测。优先复用现有搜索 run 和事件基础设施，不要求接入新的外部分析平台。

每次搜索至少记录：

```text
search_id
entry_point                  storyboard | agent | editor
target_task_id
dialogue_asset_key           可空
query_source                 dialogue | manual | planner
query_hash
planner_version
retrieval_version
planner_degraded
result_count
zero_result
latency_ms
top_candidates[]             source、candidate_id、source_asset_id、rank、score、matched_fragment_ids
created_at
```

用户操作至少通过同一 `search_id` 关联：

```text
previewed_source / previewed_candidate_id / previewed_rank
imported_source / imported_candidate_id / imported_rank
action_at
```

使用规则：

1. 导入可以作为较强的隐式正相关信号；预览是较弱信号；“没有导入”不能直接视为负相关。
2. 发布后的搜索质量增强至少比较零结果率、返回条数分布、P50/P95 延迟、规划降级率、导入率和被导入结果的原始排名。
3. 每条遥测必须带检索、规划、分词和重排版本；首版未启用的分词/重排明确记录为 `none`，缺少版本的数据不得混在同一质量结论中。
4. Dialogue 查询优先保存稳定 `dialogue_asset_key + query_hash`，避免无必要地复制完整对白。确需保留原始查询用于质量抽样时，必须有可配置保留期、访问限制、脱敏和随源 Task/素材删除的清理规则。
5. 不记录绝对文件路径、模型密钥、完整模型提示词或与检索质量无关的素材内容。
6. 遥测写入失败不能阻断搜索和导入主流程，但必须产生可监控的计数或结构化告警。

## 10. 跨 Session 导入合同

长期目标是让“加入故事板素材库”操作收敛到一个共享导入合同，但实现按阶段收缩：

| 阶段 | `source_kind` | 实现范围 |
| --- | --- | --- |
| 首版阶段 1 | `media_library_original` | 检索基础里程碑必须实现 |
| 首版阶段 4 | `media_library_clip` | 随视频剪辑和派生片段交付，属于首版门禁 |
| 现有能力/首版阶段 4 适配 | `external_candidate` | 保持既有外部 provider 导入路径；首版不要求重构进新服务 |

目标输入合同：

```text
source_kind = media_library_original | media_library_clip | external_candidate
source_id
target_task_id
requested_name
```

首版阶段 1 的 `media_library_original` 分支必须：

1. 重新校验源素材仍符合部署级全局可见和导入资格。
2. 校验目标 Task 有效，并根据 `target_task_id` 解析目标 Session。
3. 只信任前端提交的 `asset_id` 和目标身份；根据 `asset_id` 重新读取 `media_library_assets` 权威记录和真实源文件，不信任前端路径、来源 Session 或下载 URL。
4. 复制文件到目标 Task 的 `SessionOutput/storyboard/assets/videos/`。
5. 计算 sha256、文件大小和媒体元数据。
6. 写入 StoryBoard 资产 manifest。
7. 写入 provenance 和导入事件。
8. 记录 `search_id / source_asset_id / source_session_id / target_task_id / content_sha256`，使遥测可以关联最终导入结果。
9. 返回目标 Task 内可直接展示的 asset。

可以复用现有 `import_asset_search_candidates` 的搜索运行读取、候选去重、目标 manifest、hash 去重、事件和导入结果外壳，但必须新增 `media_library_original` 专用分支。该分支直接从受控的来源 Session 文件复制，不调用外部 `provider_for`、provider refresh、远程 URL 校验或下载逻辑。

禁止：

1. 在目标 manifest 中写入另一个 Session 的绝对路径。
2. 只复制数据库记录而不复制文件。
3. 用文件名作为唯一身份。
4. 自动绑定到 Dialogue 的视频槽位；首版只加入 Asset Pool，由用户选择和绑定。
5. 为了复用外部 provider 导入代码，把素材库本地文件包装成可由前端控制的 `download_url`。

## 11. 状态、并发和一致性

### 11.1 分析状态

对白、画面和综合分析的主状态必须独立，画面分析内部再区分结构与语义：

```text
dialogue_status
visual_structure_status
visual_semantic_status
visual_status
composite_status
```

`visual_status` 由两个画面子状态派生，素材总状态再由 `dialogue_status / visual_status / composite_status` 派生。任何派生状态都不能由最后完成的一个分析直接覆盖。例如对白失败、画面结构成功但语义失败时，总状态应是 `partial`，不能因为最后一次更新成功而丢失失败信息。

业务分析状态与 Tool Session 状态是两个不同域。业务层允许：

```text
not_analyzed
queued
running
blocked
partial
ready
stale
failed
```

其中 `completed` 不是业务分析状态；它只用于 Tool Session 终态。业务 `blocked` 表示缺少本次授权、配额或其他可恢复前置条件，不得写成普通 `failed`。业务 `stale` 结果可以只读查看，但其 current index 必须失效，并排除在搜索、默认剪切建议和自动导入之外。页面可以把 `queued/running` 统一显示为“处理中”，但 API 和存储仍保留二者区别。

每个对白/画面分析 run 还必须在退出前完成 Tool Session 收尾：业务 run 已结束时，对应 Tool Session 汇总不能继续停留在 `running`。映射规则是业务 `ready -> completed`、业务 `blocked -> blocked`、业务 `failed -> failed`；`partial/stale` 是业务聚合或版本状态，不直接作为 Tool Session 终态。`blocked` 是一种预期工具终态，不应被错误归类成历史回归；例如用户本次未明确授权云 ASR 时，`02_01` 返回 blocked 符合设计。收尾必须生成结果索引并同步产物登记；收尾失败需要形成可观察的同步错误状态，不能静默把业务结果标成可信。

素材库 Session 是原始素材与 Tool Session 的轻量容器，不运行普通 StoryBoard 工作流，因此其 workspace 只有 `inbox / meta / tool_use_sessions` 是正常结构，不应为了看起来像普通 Task/Session 而创建 `S1...S10 / SessionInput / SessionOutput / SessionReport` 等空目录。真正需要修复的是每个 `tool_use_sessions/{id}` 内的终态、结果索引和产物登记。

### 11.2 并发

1. 同一 scheme 同时只能有一个 `queued/running` 的非终态 run；历史成功结果和当前运行可以同时存在，但只能有一套 current active fragment。
2. 对白和画面可以并行，但更新索引摘要时必须避免 read-modify-write 丢失另一方结果。
3. 综合分析运行时若上游被重跑，本次综合结果不得激活。
4. 素材存在 active 分析、当前进程内剪切或导入任务时，删除必须阻止或先完成安全取消。
5. 剪切和导入请求应使用幂等键，防止重复点击生成重复文件；剪切运行中的幂等只保证当前服务进程，成功派生记录的幂等由数据库唯一约束保证。

### 11.3 错误表达

页面必须展示结构化错误：

```text
code
user_message
suggested_action
run_id
failed_step
```

不能只显示：

```text
工具 02_01 返回 blocked
```

后端内部日志可保留详细堆栈，但普通用户响应需要脱敏。

## 12. 隐私、安全与合规

1. 运行云 ASR 前必须有明确的数据外发授权，或让用户选择本地 ASR。
2. 使用云端视觉模型发送 Keyframe 前，必须单独获得图像数据外发授权；未授权时使用已配置的本地视觉模型或把视觉语义标记为 blocked。
3. 视觉分析只发送本次 Scene 对应的受控 Keyframe，不发送完整源视频、无关帧或其他素材。
4. 综合分析发送到 LLM 的只有结构化分析文本和标签，不发送源视频或 Keyframe 图像。
5. 提示词、模型调用、数据外发授权、结果 hash 和 usage 需要审计。
6. 全局素材库按部署级共享，不执行用户、租户或 source Session 隔离；只执行素材资格、状态和原始/派生类型过滤。
7. 外部素材搜索继续执行 provider host allowlist、MIME、文件头、大小和 license 校验。
8. 所有跨 Session 文件操作必须做路径归一化和 workspace 边界检查。
9. FFmpeg 使用参数数组，输出到受控目录，文件名只作为展示和安全 basename。
10. 删除源素材前检查 StoryBoard 导入引用和运行中任务；已经复制到 StoryBoard 的独立文件不随源素材静默级联删除，派生片段则按受控删除流程先清理文件和记录。
11. 原始视频被删除后，虚拟索引、派生片段和引用关系必须按保留策略处理，不能留下指向不存在源文件的“可用”结果。
12. 检索遥测优先记录稳定引用、hash 和统计值；原始查询抽样必须受访问控制、脱敏、保留期和删除联动约束，不得把遥测变成无限期保存对白内容的旁路数据仓库。
13. `SessionOutput/**` 分析产物及结果索引必须登记到 `session_files`，供索引发布、审计、删除和清理统一枚举；`meta/thumbnails/**` 属于可由源文件重新生成的展示缓存，不作为分析产物登记。
14. 每个分析 run 在 prepare 阶段只保留一份隔离输入快照：`inbox/视频 -> 0_SessionContext/Video_Source.*`；兼容层的旧式 `SessionContext/` 媒体文件应引用或硬链接该快照，而不是再次物理复制。可变 JSON 配置仍需独立复制；不支持硬链接时允许安全降级为复制。不得把 `inbox` 与 run 快照直接硬链接，因为这会让工具原地写入同时破坏原始素材；未来只有在真实部署卷验证 CoW clone/reflink 具备独立 inode、写时隔离和安全复制回退后才可替换 `copy2`。
15. 原始上传文件在 ready 前计算并保存 `content_sha256`；源文件不可在同一 `asset_id` 下原地替换，文件路径、mtime 和 Session ID 不得冒充内容版本。
16. 成功派生片段必须登记到来源 Session 的 `session_files`；服务启动清理只能删除没有成功派生记录、且超过安全阈值的受控临时文件。

### 12.1 LLM/VLM 成本、缓存和配额

以下调用都可能产生模型成本：

1. 每个 Scene 的 Keyframe 视觉语义描述。
2. 每个素材及每次有效上游版本的综合分析。
3. 每次新的素材搜索查询规划。
4. 发布后搜索质量增强中每个未命中缓存的 top-N 候选重排。
5. 故事板 Dialogue 触发的搜索规划。

必须采用：

1. 视觉语义按 `keyframe_hash + prompt_version + model_config_id + schema_version` 缓存。
2. 综合分析按对白结果 hash、画面结构结果 hash、画面语义结果 hash、提示词版本、模型配置和 schema 版本缓存。
3. 搜索规划按规范化 Dialogue/query、过滤条件、提示词版本和模型配置缓存；故事板入口和 Agent 入口共享缓存。
4. top-N 重排按 `query_plan_hash + candidate_set_hash + rerank_prompt_version + model_config_id` 缓存；缓存结果仍需重新校验候选资格。
5. Dialogue 切换不自动发起 LLM 请求；如实现后台预规划，至少防抖 500 ms，并取消已失效请求。
6. 对单素材、单 Task、单用户操作窗口和部署总量设置可配置并发、token、调用次数和成本上限。
7. 批量画面分析必须限流、可暂停并显示预计与实际调用量。
8. 达到预算或配额时返回结构化 `quota_exceeded`；搜索重排允许显式降级到确定性排序，其他模型任务不得静默切换到未批准模型。
9. 重试只针对可重试错误，并复用幂等键，不能重复计费或发布重复结果。

向量召回第二迭代的 embedding 回填与增量成本不包含在本轮预算中，必须在单独立项中评估。

## 13. 推荐实施顺序

### 13.1 干系人可见性提醒

用户已经确认，综合分析和视频剪辑都必须进入产品首版。因此“快速交付”不能再解释为先公开一个只有对白检索的产品版本，而应解释为：用最小正确实现完成全部首版主入口，把质量增强和通用化能力延后。必须书面对齐：

1. 阶段 1 的对白检索闭环是内部可验证基础里程碑，不是产品首版发布点。
2. 产品首版必须继续完成阶段 2 最小 VLM、阶段 3 综合分析和阶段 4 视频剪辑。
3. 首版全局素材库检索主要匹配“视频里说了什么”，不承诺按画面召回无对白 B-roll。
4. 首版接受 LLM 查询扩展加关键词/短语命中，召回有意优先精确率；中文 FTS、LLM 重排和向量召回不阻塞发布。
5. 首版剪辑页必须同时搜索外部 provider 和全局素材库。
6. 综合分析提示词首版由系统维护，普通用户不可编辑。
7. M0–M4 VLM 复用现有单 Keyframe 并诚实限制动作描述；面向无声素材客户的 R1 必须升级为每片段四帧后才允许发布视觉检索，自动全库模型重跑仍不属于默认行为。
8. 首版外部 provider 候选只允许整条导入 StoryBoard，不支持直接剪辑；全局素材库原始视频才有剪辑入口。
9. 全局素材库原始视频不可替换；上传完成时计算 `content_sha256` 并形成稳定 `source_version`。
10. 普通用户只使用最新成功分析结果，不能恢复旧版本；`stale` 历史只读可见但不参与搜索或默认剪切。
11. 首版检索只承诺不超过 500 条视频的小规模试运行，并以 P95 不超过 3 秒作为发布门禁。
12. 首版剪切任务不跨后端服务重启恢复；成功派生片段持久化，中断任务由用户重新创建。
13. StoryBoard 打开剪辑页时保留来源 Task、Dialogue、search 和建议范围，默认回到原业务上下文。

阶段 0–3 可以通过 feature flag 或内部入口持续验证，但生产环境不得把缺失真实剪切能力的阶段 1/2/3 称为产品首版，也不得提前放置可点击的“视频剪辑”生产占位入口。

### 阶段 0（几天）：解锁对白分析并收紧分析流水线基线

1. 完成并验证素材详情页单次云 ASR 数据外发授权流程。
2. 透传 `blocked_reasons` 到素材详情页。
3. 为真实素材补充对白分析端到端测试。
4. 确认未授权时不会发送音频，重复运行和失败重试不会复用过期授权。
5. 对白和画面分析退出时统一调用 Tool Session finalize/result-sync，按 `ready -> completed`、依赖阻塞 `-> blocked`、其他失败 `-> failed` 映射 Tool Session 终态；同步失败必须显式记录，不能留下伪 `running` 或静默成功。
6. finalize 生成 `SessionOutput/manifests/result_index.json`，并把 `SessionOutput/**` 分析产物登记到 `session_files`，使索引发布、审计和清理不依赖硬编码嵌套路径。
7. 保留 prepare 阶段从 `inbox` 到 `0_SessionContext` 的单份 run 隔离快照；旧式 `SessionContext` 的媒体文件改用引用/同卷硬链接，避免兼容层产生第二份物理副本，可变配置仍独立复制。以同一素材执行 dialogue 和 visual structure 为例，验收基线是 5 个逻辑路径、3 个物理 inode；不得把已经消除的两个 legacy 物理副本继续描述成“5 倍复制”。CoW clone/reflink 仅作为不阻塞首版的存储 spike。
8. 提供默认 dry-run、显式写入且可重复执行的历史 Tool Session 修复工具，用于补齐终态、结果索引和产物登记，并在内容一致时把存量兼容媒体副本安全重链接。
9. 新上传素材在合并文件时计算 `content_sha256`，以此写入 `source_version`；现有 ready 素材提供可限流、可重复的内容哈希回填，且不提供原地替换源文件的产品入口。
10. 业务分析状态使用 `blocked/failed/ready/stale` 等业务枚举，Tool Session 单独使用 `blocked/failed/completed`，不再把业务 `completed` 当作合法状态。

本地 ASR 选择不是首版强制前置；如果产品要求无云模式，则作为独立可选分支排期，不能用“云授权或本地选择”的模糊措辞同时承诺两条路径。阶段 0 的交付物是“对白分析真正可用，分析 run 的终态、产物登记和输入存储可信”，不是画面分析升级。

### 阶段 1（首版基础里程碑）：对白检索与原始视频复用闭环

1. 固化 dialogue fragment schema，建立中心 `media_library_fragment_index` 表和 migration。
2. 对白分析成功后事务式发布当前激活的 dialogue fragments，实现 `source_version / analysis_run_id / result_hash / is_active`、幂等发布和新旧索引原子切换。
3. 实现共享 `MediaLibrarySearchService` 的精确/短语召回、简单确定性 token 重排、资格过滤、分页和按 `asset_id` 聚合。
4. 查询规划 LLM 不可用时降级到 Dialogue 原文和用户输入的确定性检索，搜索主流程仍可用。
5. 复用现有 Search Agent 的规划、运行、候选和导入框架，新增 `media_library` source adapter。
6. 在 StoryBoard Asset Pool 增加“检索素材”，使用当前 Dialogue 构建查询。
7. 在 Agent - Asset Library 中增加 `media_library` 来源。
8. 只实现 `media_library_original` 导入分支，把用户确认的全局原始视频安全复制到当前 StoryBoard Task Asset Pool。
9. 从第一天记录可查询的搜索、零结果、延迟、候选排名、预览和最终导入遥测。
10. 本阶段历史页面明确标注“对白/关键词检索”；专项 v0.3 当前页面已增量标注四帧视觉描述与派生片段名称/标签召回，并继续明确不含向量召回。
11. 使用 500 条真实分布视频的 PostgreSQL 数据集完成性能门禁，正常搜索端到端 P95 不超过 3 秒；持续监控视频数、active fragment 数和 P95。

阶段 1 交付物是：用户可以根据 Dialogue 中“说了什么”检索全局素材库中已完成对白分析的原始视频，并把整条原始视频加入当前 Task。该里程碑用于尽早验证真实价值和收集遥测，但不能单独作为产品首版对外验收。

### 阶段 2（M0–M4 已完成基线）：最小单帧 VLM 视觉语义

1. 固化 Scene、Keyframe 和视觉语义 fragment schema。
2. M0–M4 复用 `03_02` 每个 Scene 的现有中点 Keyframe，并写入 `sampling_strategy = "scene_midpoint_v1"`；该项是历史完成基线，不是 R1 四帧完成证据。
3. 实现 `03_03 Keyframe Visual Semantic Description`、结果 schema 校验、证据引用和质量报告。
4. 增加 `visual_structure_status / visual_semantic_status`，历史结构结果迁移为语义未分析的 `partial`。
5. 实现云端视觉数据外发授权或已配置的本地视觉模型分支。
6. 支持用户对当前素材显式运行视觉语义；状态迁移本身不自动触发全库存量调用。
7. 单帧不能证明的连续动作必须输出 `null`；R1 升级四帧后仍保持该动作限制，直到另有连续视频动作合同。
8. 为新素材、历史 `partial`、授权阻塞和部分失败补充真实素材端到端测试。

### 阶段 3（首版必需）：综合分析

1. 固化综合 fragment schema。
2. 实现综合分析 prompt、LLM 调用、校验、状态和结果展示。
3. 只消费已发布的对白文本和 `03_03` 视觉语义结果，不读取视频或 Keyframe 字节。
4. 发布 composite fragment index，并实现上游版本引用和 stale 级联。
5. 把综合描述和标签作为全文召回及重排的可选增强信号。
6. 提示词由系统维护和版本化；普通用户只读，不提供编辑全局默认的入口。
7. 后端保留历史运行用于审计，但普通用户只使用自动激活的最新成功结果，不提供旧运行 `activate`。

### 阶段 4（首版发布门禁）：视频剪辑页面

1. 实现最小可用的单素材时间轴和三条索引轨道。
2. 保留长视频必需的缩放、适应视图、水平滚动、自适应刻度和可见窗口渲染。
3. 实现虚拟范围选择、手动时间输入和预览。
4. 实现当前服务进程内的异步 FFmpeg 精确剪切、轮询、取消、`clip_job_lost` 和启动临时文件清理；不建设跨服务重启恢复。
5. 实现 `media_library_clip_derivatives`、受控 `SessionOutput/clips`、`session_files` 登记、派生片段 manifest、删除和导入 StoryBoard。
6. 接入基于当前索引的双来源素材搜索：现有外部 provider + 全局 `MediaLibrarySearchService`。
7. 外部候选先走既有下载和授权链路并且只整条导入 StoryBoard；全局候选通过 `media_library_original` 权威复制分支导入，并可打开自己的剪辑页。
8. 键盘步进、刷新状态恢复、同轨重叠子行和密集片段聚合明确延后，不进入首版发布门禁。
9. 实现 StoryBoard 到剪辑页的受控上下文传递、有效目标 Task 列表、默认目标、目标更换和返回原 Dialogue。
10. 完成阶段 0–4 的集成、生产构建和真实浏览器端到端验收后，才标记产品首版可发布。

### 发布后质量增强 A：中文全文检索与 top-N LLM 重排

1. 用真实中文对白语料完成 CJK 分词器 spike，锁定库、版本、词典和索引重建策略。
2. 通过 PostgreSQL migration 实现 `search_lexemes_text + search_tsv + GIN`。
3. 发布端与查询端接入相同的中文分词和归一化版本。
4. 实现可配置、可缓存、可关闭、失败可降级的 top-N LLM 重排。
5. 基于首版遥测和补充的中文召回评测集，建立真实 PostgreSQL 集成测试、质量基线和查询性能基线。
6. 在达到 500 条视频前，或任何规模下首版召回 P95 超过 3 秒时，完成从简单召回到 FTS 的切换。

### R1/P0 前置质量门禁：四帧 VLM 采样与受控重分析

1. 每个不超过 15 秒的 fragment 固定在 12.5%/37.5%/62.5%/87.5% 生成四个稳定采样槽，版本为 `scene_uniform_4_v1`。
2. 每 fragment 使用一次含四图的多模态请求；模型不支持四图、缺帧、负载超限或授权不足时结构化阻断，禁止单帧降级。
3. 四帧逐项记录 ID、实际时间、相对路径和 SHA-256；模型输出使用四帧 refs 和字段证据，`action` 继续为 `null`。
4. 历史单帧结果保持只读并列入 `reanalysis_required_count`；只有用户或运维明确触发、授权和成本可见的真实 structure + semantic 重跑才能升级，index backfill 本身不调用模型。
5. 新四帧 structure/semantic 激活后，旧 semantic 和 composite 按上游版本引用变为 stale；R1 搜索只发布合格四帧结果。

### 第二迭代：向量召回

单独完成 embedding 模型、数据外发、向量存储、成本、回填、运维和质量评估后立项。向量召回替换或补充确定性召回层，查询规划、top-N 重排、原始视频聚合和导入合同继续复用。

### 13.2 风险登记

| 风险 | 等级 | 缓解 |
| --- | --- | --- |
| M0–M4 同时包含综合分析和剪辑页，范围较大 | 高 | 历史阶段已按阶段 1–3 验收；R1 不重做剪辑器和搜索平台，只升级四帧视觉输入并打通视觉召回 |
| 四帧图片输入增加负载和成本 | 中 | 每 fragment 一次四图请求；限制四图总字节、图片数、调用数和四图估算成本，记录真实 image/model-call 指标 |
| 稀疏四帧仍无法可靠证明连续动作 | 中 | R1 继续强制 `action=null`，只描述各帧直接可见事实；连续动作另立视频理解合同 |
| 中文 FTS 召回质量取决于尚未选定的分词器 | 中 | 发布后增强编码前先做真实语料 spike；首版用精确/短语召回兜底 |
| 存量视觉语义回填触发 VLM 成本洪峰 | 中 | 首版只允许显式按素材运行；发布后回填作业默认不自动全库启动，可暂停、限流并显示成本 |
| 首版保守召回被误判为最终检索质量 | 中 | 提前声明精确率优先；结合零结果、排名、预览、导入遥测和固定评测集评估后续增强 |
| 首版简单召回随素材库增长而变慢 | 中 | 明确只承诺不超过 500 条视频且 P95 不超过 3 秒；持续监控 active fragment 和 P95，在越界前切换 FTS |
| 剪辑页双来源搜索混淆授权和导入路径 | 中 | 统一候选外壳但保留来源标签；外部走 provider 下载且只能整条导入 StoryBoard，全局走权威素材复制并可打开剪辑；合同测试覆盖禁止交叉调用 |
| 后端重启导致运行中的剪切任务丢失 | 中 | 产品明确不承诺恢复；旧 job 返回 `clip_job_lost`，用户重新创建；启动时清理孤立临时文件，成功派生片段保持持久化 |
| 旧素材缺少稳定内容哈希 | 中 | 上传路径立即计算 SHA-256；存量 ready 素材使用限流、幂等回填，完成前不发布依赖 `source_version` 的新索引 |
| 查询遥测复制敏感对白或无限期保留 | 中 | 优先记录 Dialogue 引用和 hash；原文抽样需脱敏、访问控制、保留期和删除联动 |
| 查询规划 LLM 不可用导致首版搜索中断 | 低-中 | 使用 Dialogue 原文、用户输入和显式过滤条件执行确定性降级召回 |
| LLM 重排增加延迟、成本或返回非法候选 | 低-中 | 限制 top-N、缓存、schema 与候选 ID 校验、成本上限和确定性降级 |

## 14. 测试要求

### 14.1 后端合同测试

1. `[阶段 0]` 云 ASR 未授权时不发送音频，明确授权后可运行；授权作用域、重复运行和过期处理正确。
2. `[阶段 0]` `blocked_reasons` 用户消息透传；如果后续交付本地 ASR，再单独覆盖 provider 选择分支。
3. `[阶段 0]` 对白和画面分析按 `ready -> completed`、依赖阻塞 `-> blocked`、其他失败 `-> failed` 写入 Tool Session 汇总；result-sync 失败时使用显式同步错误状态，不能遗留 `running`。
4. `[阶段 0]` finalize 生成结果索引并把 `SessionOutput/**` 登记到 `session_files`；重复同步幂等，存量修复默认 dry-run 且重复执行安全。
5. `[阶段 0]` prepare 只创建一份 run 隔离媒体快照；同卷兼容媒体与该快照共享物理文件，可变 JSON 独立复制，硬链接不可用时安全降级；原始 inbox 与 run 快照不得是同一 inode。
6. `[阶段 0]` `thumbnail_url / preview_url` 数据库字段为空时，API 仍派生受控预览路由，缩略图作为可再生缓存按需生成。
7. `[首版-检索]` dialogue fragment schema、整数毫秒、稳定 `analysis_run_id` 和 `result_hash` 校验。
8. `[首版-检索]` 新旧 dialogue fragments 在同一事务内切换 active；重复发布幂等；新发布失败时旧 active 集合保持完整。
9. `[首版-检索]` 查询规划 LLM 失败、超时、关闭和配额耗尽时，使用 Dialogue 原文和用户输入完成确定性降级检索并返回 `planner_degraded = true`。
10. `[首版-检索]` 搜索只返回对白分析完成的原始视频，并排除归档、上传中、失败和派生片段。
11. `[首版-检索]` 搜索跨 Task/Session 全局可见，不使用 `session_id` 作为权限过滤。
12. `[首版-检索]` 完整短语、子串和规划关键词覆盖排序结果可解释，多 fragment 按原始视频聚合。
13. `[首版-检索]` 搜索 run 记录版本、结果数、零结果、耗时和候选排名；预览及导入通过 `search_id` 正确关联原始排名。
14. `[首版-检索]` 遥测不写入绝对路径和密钥，原始查询遵守保留/删除规则；遥测写入失败不阻断搜索和导入。
15. `[首版-检索]` `media_library_original` 导入重新读取权威素材记录、执行跨 Session 安全复制并记录 provenance，不调用外部 provider refresh 或下载逻辑。
16. `[首版-检索]` Agent 的 `local` 与 `media_library` 来源、候选身份和导入行为不混淆。
17. `[发布后-搜索增强]` 发布端和查询端使用相同的分词器、词典、归一化版本；版本变化触发可回滚的索引重建。
18. `[发布后-搜索增强]` PostgreSQL migration 创建 `search_tsv` 和 GIN 索引，并通过真实 PostgreSQL 查询计划、中文结果和性能测试。
19. `[发布后-搜索增强]` top-N LLM 重排不能返回候选集外 ID，非法分数或时间范围被拒绝，超时、关闭和配额耗尽时退回确定性排序。
20. `[发布后-搜索增强]` 查询规划与重排缓存键、候选资格复核、并发和成本上限正确。
21. `[首版/发布后]` 搜索运行时不依赖 embedding、pgvector 或外部向量服务。
22. `[M0–M4-VLM]` 复用现有 Scene 中点 Keyframe，并稳定写入 `sampling_strategy = "scene_midpoint_v1"`；只作为历史回归。
23. `[M0–M4/R1-VLM]` 视觉语义输出 schema、证据引用、敏感字段和动作限制正确；单帧或稀疏四帧无法证明的连续动作必须为 `null`。
24. `[首版-VLM]` 云端视觉数据外发授权与已配置的本地视觉模型分支。
25. `[首版-VLM]` 历史 `visual_status = ready` 不会被迁移为视觉语义已完成，状态迁移不会自动触发模型调用。
26. `[R1-P0-VLM]` `scene_uniform_4_v1` 四个稳定采样位置、单次四图请求、逐帧 hash/证据、总负载、缓存、计量和成本上限；不支持多图时明确 blocked。
27. `[发布后-VLM]` 存量回填作业支持暂停、恢复、限流、幂等、失败重试和成本统计。
28. `[首版-综合]` 综合分析依赖对白、画面结构和画面语义三项检查。
29. `[首版-综合]` 综合分析不读取视频或 Keyframe 图像文件，也不能生成上游画面语义中不存在的视觉事实。
30. `[首版-综合]` 综合 LLM 输出 schema、整数毫秒时间和引用校验。
31. `[首版-综合]` Keyframe、画面语义或对白变化导致综合结果 stale。
32. `[首版-剪辑]` migration 创建 `media_library_clip_derivatives`；剪切结果不会写入 `media_library_assets`。
33. `[首版-剪辑]` 剪切路径、起止毫秒、进程内幂等、失败清理、取消和服务重启后的 `clip_job_lost`。
34. `[首版-剪辑]` 剪辑页搜索同时返回外部和全局素材库候选，来源标签和当前源素材排除正确。
35. `[首版-剪辑]` 外部候选只走 provider 下载/授权链路并整条导入 StoryBoard，不创建全局 `asset_id` 或剪辑入口；全局候选只走权威素材复制链路，二者不能交叉调用。
36. `[全阶段]` active 运行期间删除保护。
37. `[全阶段]` LLM/VLM 缓存、并发限制、预算上限和结构化 `quota_exceeded`。
38. `[阶段 0]` 新上传素材流式计算 `content_sha256`，`source_version` 稳定；现有 ready 素材哈希回填限流、幂等，且源视频不能在同一 `asset_id` 下替换。
39. `[全阶段]` 业务分析状态不写入 `completed`，Tool Session 与业务 `ready/blocked/failed` 映射正确；`stale` 结果只读但不参与检索和默认剪切。
40. `[首版-检索]` 使用 500 条真实分布视频的 PostgreSQL 数据集压测，正常搜索端到端 P95 不超过 3 秒，并分别记录召回和规划耗时。
41. `[首版-分析]` 历史运行保留审计引用，但普通用户接口不提供历史列表发现或旧运行激活；最新成功结果自动成为唯一 current active，替代前的 current stale 结果只读可见。
42. `[首版-剪辑]` 成功片段原子写入受控目录、数据库和 `session_files`；服务重启后成功片段仍存在，孤立 `.part` 文件被安全清理。
43. `[首版-剪辑]` StoryBoard 导航上下文中的建议范围、默认 Task、Dialogue 和 `search_id` 被服务端重校验；任意 URL、Session ID 和路径注入被拒绝。

### 14.2 前端测试

1. `[阶段 0]` 云 ASR 授权勾选、blocked 原因和再次运行交互。
2. `[首版-检索]` Asset Pool 仅在有选中 Dialogue 时允许检索。
3. `[首版-检索]` 切换 Dialogue 后旧搜索结果不会错误绑定。
4. `[首版-检索]` Agent 中 `local` 与 `media_library` 来源不混淆。
5. `[首版-检索]` 查询规划降级时搜索仍可使用，并显示非阻断的降级状态。
6. `[首版-检索]` 零结果页提示缩短关键词、移除可选条件、使用对白原句或重跑对白分析，不自动放宽强制资格过滤。
7. `[首版-检索]` 能力边界文案明确检索主要基于对白、不含按画面召回和向量语义，并说明当前召回优先精确率。
8. `[首版-检索]` 原始视频导入后 Asset Pool 保持当前 Tab 和编辑状态。
9. `[发布后-搜索增强]` LLM 重排启用、关闭和降级状态不影响结果可用性。
10. `[M0–M4/R1-VLM]` 视觉结构、视觉语义、partial、授权阻塞、单帧历史兼容和四帧 R1 资格显示正确。
11. `[首版-综合]` 综合分析按钮依赖状态，以及运行、失败、重跑和 stale UI。
12. `[首版-综合]` 提示词版本和模型配置只读，不显示普通用户编辑入口。
13. `[首版-剪辑]` 视频剪辑路由可以打开并正确返回素材详情。
14. `[首版-剪辑]` 播放器、播放游标与时间码双向同步。
15. `[首版-剪辑]` 时间轴缩放和滚动不改变选区真实时间。
16. `[首版-剪辑]` 分析轨道、轨道显隐、片段选择和手动时间输入。
17. `[首版-剪辑]` 播放游标、焦点索引、搜索上下文和剪切选区四种状态互不混淆。
18. `[首版-剪辑]` 10 分钟代表视频使用可见窗口渲染，缩放、适应视图和水平滚动后仍可在尾部精确选择范围；30 分钟 synthetic 只记录非阻塞压力测试结果。
19. `[首版-剪辑]` 同轨片段重叠或密集时仍可通过焦点置顶和联动索引列表选择全部真实片段。
20. `[首版-剪辑]` 剪切任务进度、失败和成功结果。
21. `[首版-剪辑]` 素材搜索可同时或分别启用外部与全局素材库，两种候选来源、授权提示和导入动作清晰。
22. `[发布后-剪辑增强]` 键盘时间步进及修饰键步长正确。
23. `[发布后-剪辑增强]` 页面刷新或任务返回后恢复播放时间、缩放、滚动、轨道显隐和未提交选区。
24. `[发布后-剪辑增强]` 同轨重叠自动子行、密集片段聚合和展开正确，且不改变真实时间。
25. `[R1-P0-VLM]` 四帧重新分析、`reanalysis_required_count` 和纯 index backfill 状态显示正确，不把单帧 ready 误报为可检索。
26. `[首版-状态]` `blocked/partial/ready/stale/failed` 文案和操作正确；`stale` 可查看但搜索按钮禁用，不能初始化默认剪切选区。
27. `[首版-剪辑]` 外部候选卡只有整条导入 StoryBoard，不显示剪辑入口；全局素材候选才显示“打开剪辑”。
28. `[首版-剪辑]` 从 StoryBoard 打开时默认选中原 Task，允许更换有效目标；导入后可返回原 Dialogue，原 Dialogue 失效时安全退回 StoryBoard 首页。
29. `[首版-剪辑]` 后端服务重启后旧 job 显示“任务已中断，请重新创建”，不会把它误报为仍在运行或成功。

### 14.3 端到端验收

至少准备：

1. 有硬字幕的竖屏口播。
2. 无字幕的竖屏口播。
3. 横屏采访。
4. 多 Scene、对白跨镜头的视频。
5. 无对白 B-roll：对白工具链成功时允许发布 current ready、0 fragments；该素材不进入首版对白检索资格，画面分析与后续素材使用不被错误阻断。
6. 同义表达检索样本。
7. 跨 Task/Session 检索同一全局原始素材。
8. 秒/毫秒边界和不足一秒的短片段。
9. 包含相似对白但画面不同的排序样本。
10. 大文件和长视频样本。
11. 查询规划 LLM 超时、关闭或配额耗尽的降级检索样本。
12. 无字面命中的零结果与查询修改建议样本。

首版阶段 1 基础链路：

```text
上传原始视频
  -> 明确授权并完成对白分析
  -> 发布对白 fragment
  -> StoryBoard 选 Dialogue 或 Agent 选择 media_library 来源
  -> 按对白/关键词检索原始视频
  -> 记录结果数、零结果、耗时和候选排名
  -> 导入当前 Task Asset Pool
  -> 通过 search_id 记录被导入 asset 及原始排名
```

阶段 1 端到端还必须在查询规划 LLM 不可用时重复上述检索与导入链路，确认确定性降级可用，且遥测故障不会阻断主流程。

产品首版完整链路：

```text
上传原始视频
  -> 明确授权并完成对白分析
  -> 最小 Keyframe 视觉语义
  -> 综合分析
  -> 发布检索索引
  -> StoryBoard 选 Dialogue
  -> 按对白检索并导入全局原始视频
  -> 携带原 Task、Dialogue、search_id 和建议范围打开素材视频剪辑页
  -> 加载三套索引并默认选择原 Task，允许更换目标
  -> 选择索引，同时搜索外部 provider 与全局素材库
  -> 验证外部候选只能整条下载导入 StoryBoard、没有剪辑入口
  -> 验证全局候选通过权威复制导入，并可打开其自己的剪辑页
  -> 指定时间范围并剪切
  -> 把派生片段导入当前 Task Asset Pool
  -> 返回原 StoryBoard Dialogue
```

剪切端到端还必须覆盖一次后端服务重启：未完成 job 返回 `clip_job_lost`，用户可以重新创建；启动清理不会删除已经成功登记的派生片段。

## 15. 已确认的产品决定

本轮需求评审采用以下决定：

| 事项 | 已确认决定 |
| --- | --- |
| “剪映”是否指第三方剪映集成 | 否；只描述工具性质和参考形态，正式按钮使用“视频剪辑” |
| 全局素材库检索主要匹配什么 | 主要按视频对白，即“视频里说了什么”匹配；首版不以按画面召回 B-roll 为验收目标 |
| 是否接受首版关键词/短语方案 | 接受；首版使用 LLM 查询扩展加确定性关键词/短语命中，质量增强后续迭代 |
| 综合分析和视频剪辑是否属于首版 | 是；二者均为产品首版发布门禁，不能作为发布后的 fast-follow |
| 剪辑页素材搜索有哪些来源 | 外部 provider + 全局素材库，首版两者都要 |
| 综合分析提示词是否允许普通用户编辑 | 否；首版由系统维护并只展示版本，不允许修改全局默认 |
| 故事板检索结果是否自动剪切 | 否；返回原始视频和建议范围 |
| 导入原始视频后是否自动绑定 Dialogue | 否；只加入 Asset Pool |
| 派生片段是否进入全局素材库搜索 | 否 |
| 综合分析是否是故事板检索硬依赖 | 否；对白分析完成即可检索，但综合分析本身仍属于产品首版 |
| 综合分析的视觉语义从哪里来 | 由画面分析新增的 `03_03` 从 Keyframe 生成；综合分析不读取图像 |
| M0–M4 VLM 采样范围 | 已交付版本复用每 Scene/分析窗口单个中点 Keyframe，`scene_midpoint_v1` 保持历史兼容 |
| R1 无声视觉检索采样范围 | 每 fragment 固定四帧 12.5%/37.5%/62.5%/87.5%，一次请求携带四图；单帧结果重新分析前不能进入视觉检索 |
| 首版检索承诺什么 | 对白原句、关键词和短语检索以及确定性 token 排序；不承诺按画面召回或向量语义 |
| 首版中文召回策略 | 有意优先精确率，召回可能偏保守；通过真实遥测和固定评测集在发布后提升 |
| 首版简单召回的容量和延迟边界 | 小规模试运行，不超过 500 条原始视频；500 条真实分布数据集上正常搜索端到端 P95 不超过 3 秒 |
| PostgreSQL FTS 何时交付 | 产品首版发布后；必须同时完成中文分词器选型、版本锁定和真实 PostgreSQL 集成测试 |
| 检索是否使用 LLM | 查询规划使用 LLM，但失败时降级到原文确定性检索；发布后对确定性召回的 top-N 候选增加可关闭、可降级的 LLM 重排；LLM 不负责全库召回 |
| 首版是否记录检索遥测 | 是；记录搜索版本、零结果、耗时、候选排名、预览和最终导入关联，并执行查询隐私与保留规则 |
| 首版索引版本化做到什么程度 | 素材保存稳定 `source_version`；对白索引保留 run/hash/active、幂等发布和新旧集合原子切换；综合分析阶段补上游版本引用和 stale 级联；历史保留审计但普通用户不能激活旧运行 |
| 是否包含向量召回 | 否；embedding 与向量存储另立第二迭代 |
| 检索是否按用户/租户隔离 | 否；当前采用部署级全局可见，`session_id` 不是权限边界 |
| 新增合同的时间单位 | 存储和传输一律使用整数毫秒，UI 才格式化 |
| 原始视频是否可以在同一素材下替换 | 否；替换内容必须重新上传并产生新的 `asset_id` |
| `stale` 分析结果如何处理 | 只读可见，但不参与检索、默认剪切建议或自动导入 |
| 剪切片段存在哪里 | 新表 `media_library_clip_derivatives`，不写入 `media_library_assets` |
| 首版导入实现哪些来源 | 阶段 1 新增 `media_library_original`；阶段 4 增加 `media_library_clip`；external 保持既有 provider 路径并接入剪辑页 |
| 外部搜索候选是否能直接按远程时间范围剪切 | 否；首版只能整条导入 StoryBoard，不提供直接剪辑或导入后自动剪辑 |
| 哪些素材可以打开本需求的剪辑器 | 只有全局素材库中的原始视频；外部候选和 StoryBoard Task 内普通素材不能直接打开 |
| 首版剪切是否支持多轨、拼接和转场 | 否；只做单素材范围剪切 |
| 剪切任务是否跨后端服务重启恢复 | 否；旧 job 返回 `clip_job_lost`，用户重新创建；成功派生片段继续持久化 |
| StoryBoard 打开剪辑页是否保留来源上下文 | 是；保留 Task、Dialogue、search 和建议时间，默认原 Task、允许更换，并可返回原 Dialogue |
| 首版时间轴保留和延后什么 | 保留缩放、适应视图、水平滚动、自适应刻度和可见窗口渲染；键盘步进、刷新状态恢复、重叠子行和密集聚合发布后增强 |
| 单帧或 R1 四帧能否推断连续动作 | 否；四帧仍是稀疏静态证据，只描述直接可见事实或客观状态差异，连续动作输出 `null` |
| 本地 ASR 是否阻塞首版 | 否；首版必须完成云 ASR 明确授权路径，本地 ASR 如需支持则单独排期 |
| 视频剪辑按钮何时交付 | 首版阶段 4；它是首版最后完成的主入口，也是发布门禁 |

## 16. Definition of Done

### 16.1 首版阶段 1 基础里程碑 Done

同时满足以下条件，阶段 1 才完成内部基础里程碑。它可用于真实用户验证和遥测，但不能单独标记为产品首版：

1. 当前对白分析授权入口和 blocked 原因展示已修复；未授权时保持预期 blocked，明确授权后的真实素材端到端通过。
2. 对白和画面分析 run 的业务终态、Tool Session 汇总、结果索引和 `session_files` 产物登记一致；存量修复可 dry-run、可重复执行。
3. 每个分析 run 只保留一份隔离媒体快照，兼容 `SessionContext` 不再形成第二份物理媒体副本；可变配置仍保持隔离。
4. 新上传及存量 ready 素材具有内容 SHA-256，原始视频不可在同一 `asset_id` 下替换；`source_version` 在分析、检索和剪切合同中一致。
5. dialogue fragments 以整数毫秒、稳定 ID、`source_version / analysis_run_id / result_hash / is_active` 发布；重复发布幂等，新旧集合原子切换，发布失败不破坏旧 active 集合。
6. 共享搜索服务提供对白原句、关键词和短语召回、确定性排序、分页、资格过滤和按原始视频聚合。
7. 查询规划 LLM 失败、超时、关闭或配额耗尽时，原文确定性检索与导入链路仍然可用。
8. 故事板“检索素材”和 Agent - Asset Library 复用同一共享服务，Agent 以独立 `media_library` 来源接入。
9. 两个入口只检索完成对白分析的原始视频，不检索派生剪切片段。
10. 搜索结果展示命中对白、整数毫秒时间范围、可验证的命中理由、检索版本和规划降级状态。
11. 用户可以通过 `media_library_original` 分支把确认的原始视频安全导入指定 StoryBoard Task 的 Asset Pool；该分支不经过外部 provider 下载逻辑。
12. 搜索、零结果、耗时、候选排名、预览和最终导入通过 `search_id` 形成可查询的遥测闭环；遥测故障不阻断主流程。
13. 页面明确提示检索主要基于对白、首版优先精确率且暂不包含按画面召回和向量语义；零结果时提供安全的查询修改建议。
14. 500 条真实分布视频的 PostgreSQL 性能验收达到正常搜索端到端 P95 不超过 3 秒，并已建立视频数、active fragment 数和 P95 容量告警。
15. 阶段 0/1 后端合同测试、前端生产构建和真实浏览器端到端验收全部通过。

### 16.2 产品首版 Done

在阶段 1 基础里程碑 Done 的基础上，同时满足以下条件，产品首版才可以发布：

1. M0–M4 `03_03` 复用现有 Scene 中点 Keyframe 生成可校验的视觉语义，并记录 `sampling_strategy = "scene_midpoint_v1"`；该完成条件不等于 R1 四帧已交付。
2. 单帧不能证明的连续动作使用 `null`，不能用低置信度包装推测；历史画面结果不会被误迁移为视觉语义已完成，状态迁移不自动触发全库 VLM 调用。R1 四帧同样保持动作空值合同。
3. 综合分析只消费文本化上游结果，不读取视频或 Keyframe 字节；结果能生成、校验、版本化、展示和发布 composite 索引。
4. 综合分析保存上游 run/hash 引用，对白、Keyframe 或视觉语义变化后 composite 正确变为 stale；stale 只读可见但不参与检索和默认剪切。
5. 综合分析提示词由系统维护和版本化，普通用户只能查看版本和模型配置。
6. 视频剪辑页面能加载对白、画面和综合三套索引，支持范围选择、手动输入、预览和异步 FFmpeg 剪切；缩放、适应视图、水平滚动、自适应刻度和可见窗口渲染可支撑 10 分钟代表视频，并能在靠近尾部的非关键帧时间准确起切。
7. 剪辑页“素材搜索”同时支持外部 provider 和全局素材库，可分别开关并显示明确来源；当前源素材默认不作为全局候选返回。
8. 外部候选执行既有 provider 下载与授权链路，并且只能整条导入 StoryBoard、不显示剪辑入口；全局候选执行权威素材复制并可打开其剪辑页，两条路径不会混用。
9. 剪切任务在当前服务进程内异步执行；服务重启后旧 job 返回 `clip_job_lost` 并可重新创建，孤立临时文件被清理，成功派生片段仍然可用。
10. 剪切结果只写入 `media_library_clip_derivatives`，登记到受控目录和 `session_files`，始终排除在全局召回之外，并能安全导入指定 StoryBoard Task Asset Pool。
11. StoryBoard 打开剪辑页时保留并重校验来源 Task、Dialogue、search 和建议范围；默认原 Task、允许更换，导入后可返回原 Dialogue。
12. 普通用户始终使用自动激活的最新成功分析结果，不能激活旧运行；业务 `completed` 与 Tool Session `completed` 不混用。
13. 所有结果可追溯到原始素材、`source_version`、分析 run、整数毫秒时间范围、提示词/模型/采样版本、搜索来源和导入目标。
14. 部署级全局可见性、数据外发授权、模型成本配额、路径安全、外部素材授权和删除引用规则通过测试。
15. 阶段 0–4 的后端合同测试、前端生产构建和真实浏览器端到端验收全部通过。

键盘步进、刷新状态恢复、同轨重叠子行和密集片段聚合不属于产品首版 Done，不得因这些发布后增强尚未实现而阻塞首版。

### 16.3 R1/P0 与发布后质量增强 Done

M0–M4 产品首版不因本节未完成而失效；但面向无声素材客户发布 R1 必须先满足第 3、4、6 项及专项 v0.3 第 18.2 节。其余发布后增强只有达到各自条件才可标记完成：

1. PostgreSQL `tsvector + GIN`、锁定版本的中文分词和 top-N LLM 重排完成；真实 PostgreSQL 查询、中文质量和降级测试通过。
2. 质量比较同时使用固定中文评测集和首版遥测，并按检索/规划/分词/重排版本隔离数据。
3. R1/P0 的固定四帧采样、单次四图请求、逐帧证据、总负载、缓存、计量和成本上限按专项 v0.3 完成；黑帧/模糊评分和高级去重仍可作为其后质量增强。
4. 存量视觉语义回填支持暂停、恢复、限流、幂等、失败重试、优先级和预计/实际成本显示。
5. 视频剪辑的键盘步进、刷新状态恢复、同轨重叠子行和密集片段聚合完成，并通过长视频和高密度索引交互测试。
6. 新视觉语义激活后 composite stale 级联和重跑行为通过测试。

向量召回属于第二迭代，不纳入产品首版或上述质量增强 Done。
