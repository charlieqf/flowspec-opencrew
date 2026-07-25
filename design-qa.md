# 素材库列表页 Design QA

- source visual truth: `/var/folders/7s/w6p6m5nn4fbgn1vx0p68jkg40000gn/T/codex-clipboard-cf7dc14e-cf0b-41c5-995c-cf7f6dff6ea9.png`
- typography reference: `/var/folders/7s/w6p6m5nn4fbgn1vx0p68jkg40000gn/T/codex-clipboard-a459bbad-aaf2-4e8d-ad34-1dc4ca160dfc.png`
- implementation screenshot: `/private/tmp/opencrew-media-library-list-fixed.png`
- typography implementation screenshot: `/private/tmp/opencrew-media-library-font-fixed.png`
- subtitle removal implementation screenshot: `/private/tmp/opencrew-media-library-subtitle-removed.png`
- detail screenshot: `/private/tmp/opencrew-media-library-detail.png`
- viewport: `1616 × 760`
- state: 素材库列表包含 3 条临时验收数据；随后点击竖屏素材名称进入详情页

## Full-view comparison evidence

参考图与实现截图已在同一视觉比较输入中以原始分辨率检查。实现沿用了现有 OpenCrew 的左侧导航、浅灰页面背景、白色筛选控件、表头底色、表格分隔线、圆角和紧凑字号。素材库新增字段与筛选项较多，因此筛选区使用两行；这属于信息架构差异，不是样式漂移。

首轮检查发现右侧 `System Health` 占用 320px 后，素材表格在 1616px 视口出现横向滚动。修正后素材库使用完整中心区：页面 `bodyScrollWidth` 与 `bodyClientWidth` 均为 `1616px`，表格容器 `scrollWidth` 与 `clientWidth` 均为 `1350px`，没有页面或表格横向溢出。

## Focused region comparison evidence

表格区域在原始分辨率下逐项检查，列标题、行高、分隔线、状态标签、数量标签、文件名截断和更新时间均清晰可读，不需要额外放大裁切。缩略图的实际 DOM 尺寸为：横屏 `88 × 50px`，竖屏 `38 × 68px`，分别保持 16:9 与 9:16；素材名称为唯一详情链接。详情页验证结果为：URL `#/media-library/codex-ui-test-portrait`、标题正确、左侧素材库导航保持高亮、预览舞台带 `is-portrait` 状态。

2026-07-14 字号复验将任务列表参考图与素材库修正截图放在同一视觉比较输入中检查。素材库搜索框、筛选下拉框和清除按钮统一为 `13px`；任务列表实测搜索框为 `13px`、下拉框为浏览器原生 `13.3333px`，两者视觉密度一致。两类页面控件高度均为 `36px`，没有改变原有筛选布局。

2026-07-14 副标题移除复验将用户红框参考图与最新实现截图放在同一视觉比较输入中检查。页头中的“管理可跨任务复用的原始视频与 OpenCut 分析摘要”已从 DOM 完全移除；“素材库”标题与“共 0 条视频”数量标签保持同一行，搜索、筛选和空态区域未发生位移或样式退化。浏览器断言结果为禁用文案数量 `0`、页头段落数量 `0`。

## Required fidelity surfaces

- Fonts and typography: 素材库输入框、下拉框及按钮显式使用 `13px`，与任务列表筛选控件的 `13px` 基准一致；标题、表头、正文与次级文件名的字重和截断关系保持不变。
- Spacing and layout rhythm: 左导航宽度、22px 内容内边距、筛选间距、8px 左右圆角和紧凑表格行高与现有任务列表一致；修正后无横向溢出。
- Colors and visual tokens: 沿用现有浅灰背景、白色容器、蓝色选中态及绿/橙/灰状态语义；没有引入新的品牌色体系。
- Image quality and asset fidelity: 本期不包含上传和缩略图生成，验收数据故意使用视频占位态；占位态仍严格保留素材真实横竖比，不裁切未来真实缩略图。
- Copy and content: 页面名称、OpenCut 状态、对白/视觉/综合结构、质量、字幕、标签、空态和详情文案符合已确认需求。

## Findings

- 无剩余 P0/P1/P2 问题。
- P3：当前验收数据没有真实视频缩略图和可播放地址，因此真实图片锐度、视频 poster 和播放器加载态需要在上传能力实现后再验证。

## Comparison history

1. 首轮 P1：右侧系统栏压缩主内容，表格出现横向滚动，主要质量与标签列不可见。
2. 修正：素材库路由改为完整中心区，隐藏无关的右侧系统栏。
3. 复验：1616px 下页面与表格均无横向溢出；横屏和竖屏缩略图尺寸正确；素材名称成功进入对应详情页；浏览器控制台无 error 或 warning。
4. 字号 P2：素材库筛选控件继承页面默认 `16px`，明显大于任务列表。修正为模块内统一 `13px` 后，在 `1720 × 630` 视口复验通过，浏览器控制台无 error 或 warning。
5. 文案 P3：按用户标注移除素材库标题下方说明文字。修正后在 `1799 × 724` 视口复验通过，标题、数量标签、搜索筛选区和空态均正常，浏览器控制台无 error 或 warning。

## Primary interactions tested

- 打开 `#/media-library` 并恢复左侧素材库高亮。
- 加载列表与标签 facets。
- 点击竖屏素材名称进入独立详情页。
- 详情页保持素材库导航高亮并显示对应内容结构、质量、对白摘要和标签。

## 2026-07-14 筛选弹窗与模块拆分复验

- source visual truth: `/var/folders/7s/w6p6m5nn4fbgn1vx0p68jkg40000gn/T/codex-clipboard-050e088a-f7c8-4c0b-b892-49e25ea70098.png`
- closed-state implementation screenshot: `/private/tmp/opencrew-media-library-filter-closed.png`
- open-dialog implementation screenshot: `/private/tmp/opencrew-media-library-filter-dialog.png`
- viewport: `1717 × 558`
- state: 素材库为空；先检查收起状态，再打开筛选弹窗检查全部条件

### Full-view comparison evidence

用户参考图、筛选收起截图与弹窗展开截图已放入同一视觉比较输入。参考图右侧两行筛选控件已全部移除，收起状态只保留一行搜索框和右侧 `92px` 筛选按钮，主内容区随之上移且不再被大量筛选项挤占。页面标题、数量标签、左侧导航、空态卡片、字号、边框和背景色均保持现有 OpenCrew 视觉体系；参考图中的页头说明文字属于上一次已确认移除的旧状态，不作为本次回归问题。

### Focused region comparison evidence

弹窗在 `1717 × 558` 视口中的实测尺寸为 `620 × 484px`，上下均完整可见。分析状态、字幕类型、素材时长、素材标签、更新时间和画面方向使用两列网格，排序方式和归档开关使用整行；底部重置、取消和应用筛选操作没有裁切。弹窗控件继续使用列表页统一的 `13px` 字号和 `36px` 高度，焦点边框、遮罩、圆角与现有页面层级一致。

### Findings

- 无剩余 P0/P1/P2 视觉或交互问题。
- P3：开发环境加载时仍记录一条 Solid 生命周期 warning；没有浏览器 error，且筛选打开、关闭、应用和清除均正常。

### Comparison history

1. 原始问题：8 个筛选/排序控件、归档开关和清除操作占据页面右侧两行，搜索与列表内容空间被压缩。
2. 修正：列表页收敛为搜索框加单一筛选按钮；全部条件迁入居中弹窗，并增加已选数量、重置、取消、应用和 Esc/遮罩关闭行为。
3. 复验：选择“分析状态=已完成”“方向=竖屏”“显示归档”后，URL 正确写入 `analysis=ready&orientation=portrait&archived=1`，按钮显示 `3`；清除后恢复默认 URL 与空态。
4. 分页回归：在 `page=2` 状态打开弹窗并应用筛选后，页面正确回到第 1 页并移除分页参数。

### File decomposition evidence

- `MediaLibraryModule.jsx` 仅保留 10 行路由装配。
- 列表页、详情页、筛选、表格、分页、预览与展示原语均拆为独立文件，单个 JSX 文件最大 144 行。
- 样式按基础、筛选、表格、预览、详情拆为 5 个文件，单个 CSS 文件最大 189 行。

### Primary interactions tested

- 单击筛选按钮打开 `role=dialog` 弹窗并自动聚焦第一个条件。
- 组合选择条件并应用，URL、按钮计数和空态同步更新。
- 清除筛选恢复默认条件；应用新条件时分页重置为第 1 页。
- 关闭弹窗后恢复页面滚动，弹窗节点从 DOM 移除。
- 浏览器控制台无 error。

final result: passed

## 2026-07-14 对白分析工具抽屉运行信息复验

- source visual truth: `/var/folders/7s/w6p6m5nn4fbgn1vx0p68jkg40000gn/T/codex-clipboard-d24734a5-25a9-4033-b286-c5548633cdb8.png`
- removal reference: `/var/folders/7s/w6p6m5nn4fbgn1vx0p68jkg40000gn/T/codex-clipboard-63906d0b-5206-4df5-9ca0-3cb347b7fed8.png`
- implementation screenshot: `/tmp/opencrew-opencut-drawer-implementation.png`
- combined comparison: `/tmp/opencrew-opencut-drawer-comparison.png`
- viewport: `1920 × 907`
- state: 会话 #53 对白分析已完成，共 22 个片段

### Full-view comparison evidence

参考抽屉和实现抽屉已组合到同一张对比图中检查。实现保持原有 `470px` 右侧抽屉宽度、标题与说明层级、步骤列表密度和遮罩层级；右上角新增蓝色纯图标运行按钮，并位于关闭按钮左侧。原四个参数卡片和底部操作区均已删除，正文替换为运行状态、总运行时间和运行描述。

### Focused region comparison evidence

运行区域逐项检查：完成态显示绿色状态标签，计算样式为前景 `rgb(22, 128, 74)`、背景 `rgb(232, 248, 238)`；历史任务从工具 Session 状态文件回填总耗时并显示 `00:23`；运行描述为“对白分析已完成，共生成 22 个片段”。DOM 中“当前状态、任务 / 会话、源视频、输出”旧参数标签数量为 0，抽屉 `footer` 数量为 0。运行和关闭按钮的横坐标分别为 `1828px` 与 `1868px`，顺序正确。

### Required fidelity surfaces

- Fonts and typography: 沿用素材详情页现有小字号体系；标题、说明、状态、耗时、描述和步骤层级清晰，没有出现大字号或文本溢出。
- Spacing and layout rhythm: 运行摘要采用两列状态/耗时和一行描述，步骤区紧随其后；抽屉无多余底部留白控件或固定 footer。
- Colors and visual tokens: 状态继续复用 OpenCrew 的 info/success/danger 语义色；运行按钮复用页面主蓝色，关闭按钮保留中性描边。
- Image quality and asset fidelity: 本次区域不含位图内容；运行和关闭均使用模块现有矢量图标组件，在高分辨率视口下清晰。
- Copy and content: 仅保留运行状态、总运行时间、运行描述和工具步骤；移除用户指定的任务、会话、源视频及输出路径重复信息。

### Comparison history

1. 首轮复验发现历史完成任务缺少新版时间字段，虽然新任务可实时计时，但旧任务显示 `--:--`。
2. 修正：后端从独立 Tool Session 的启动 ID 与步骤 State 时间回填历史运行耗时，同时新任务继续持久化开始、更新、结束和耗时字段。
3. 复验：会话 #53 显示 `00:23`；状态颜色、描述、按钮顺序、旧卡片移除和 footer 移除均通过；浏览器控制台无 error。

### Primary interactions tested

- 点击详情页“对白分析”打开工具抽屉。
- 点击右上角关闭按钮后抽屉从 DOM 移除，再次打开正常。
- 完成态运行按钮可作为重新运行入口；本次未触发实际重跑，避免改变现有分析产物。

### Findings

- 无剩余 P0/P1/P2 问题。
- 无阻塞性交互或控制台错误。

final result: passed
