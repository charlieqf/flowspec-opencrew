# OpenCrew 素材库标签、卡片视图与视频剪辑时间轴拖动实施方案

- 文档状态：已按团队复审修订，待确认
- 编写日期：2026-07-22
- 复审修订：2026-07-22
- 涉及范围：素材库、素材详情/编辑器
- 对应需求：素材标签管理、卡片式展示与自定义列数、时间轴拖动定位视频画面

## 1. 目标

本方案把三个需求收敛为一次素材工作流升级：

1. 用户可以在素材库中为单个素材添加、修改和移除标签，并可继续使用现有标签筛选。
2. 用户可以在列表视图与卡片视图之间切换，卡片视图支持设置列数并记住个人偏好。
3. 用户在视频剪辑页拖动时间轴游标时，预览画面和时间显示连续跟随，松开后准确停在目标位置。

本期不包含批量打标签、跨用户同步视图偏好和全功能非线性编辑器。素材上传流程已经按需生成 SessionOutput/media_library/previews/ 下的“流畅预览”代理视频，本期复用并校验该能力，不另建第二套代理生成链路。

## 2. 现状与差距

### 2.1 素材标签

当前系统已经具备标签的基础数据能力：

- 前端 MediaLibraryListPage.jsx 已能通过 PATCH 更新 tags。
- MediaLibraryTable.jsx 的操作菜单已有“编辑标签”入口。
- 后端 MediaLibraryPatch 接受标签数组，仓储层支持保存、搜索和标签聚合。
- 数据库存储在 media_library_assets.tags_json，不需要新增迁移。

当前入口使用浏览器原生 prompt，让用户输入逗号分隔文本。它缺少已有标签建议、逐项删除、重复校验、长度提示和一致的产品交互，因此本需求重点是把现有基础能力产品化，并补强服务端约束。

### 2.2 卡片视图

素材库主页面当前只渲染 MediaLibraryTable，没有视图切换、卡片组件和列数偏好。另一个上传素材库界面虽有网格列数逻辑，但数据模型和页面职责不同，不直接复用组件，只参考其交互模式。

### 2.3 时间轴与视频预览

当前视频剪辑页已经具备两种同步：

- 点击时间轴会调用 seek，更新 playheadMs 和 video.currentTime。
- 视频正常播放时，timeupdate 会反向更新游标。

当前缺口是连续拖动。时间轴只监听 pointerdown 进行一次定位，播放头本身没有拖动状态、指针捕获和 pointermove 处理。因此拖动过程中画面不会连续变化。

## 3. 总体设计

| 能力 | 前端变化 | 后端变化 | 数据迁移 |
| --- | --- | --- | --- |
| 标签管理 | 新增标签编辑弹层、标签输入和建议 | 收紧标签校验，沿用 PATCH | 无 |
| 卡片视图 | 新增视图切换、卡片网格、列数设置和本地偏好 | 无 | 无 |
| 时间轴拖动 | 新增 scrubbing 状态、指针捕获和连续 seek | 无 | 无 |

三个功能共用现有鉴权、素材查询、筛选、排序和分页能力，不新增平行数据源。

## 4. 需求一：素材标签管理

### 4.1 用户交互

在素材表格行操作和卡片操作菜单中统一保留“编辑标签”入口。点击后打开产品内弹层，不再使用浏览器 prompt。

弹层包含：

- 当前标签以可删除标签片展示。
- 一个输入框，按 Enter 或中文/英文逗号确认新标签。
- 输入时从当前素材库 facets.tags 中提供匹配建议。
- 保存、取消按钮，以及保存中、失败重试状态。
- 去除首尾空白、忽略空标签，并在提交前去重。

首期只支持单素材编辑。批量勾选后统一添加或删除标签不在本期范围内，避免把“编辑标签”与“批量覆盖标签”混为同一语义。

### 4.2 标签规则

建议采用以下产品约束：

- 每个素材最多 20 个标签。
- 单个标签最多 32 个 Unicode 字符。
- 标签不得只包含空白。
- 服务端先执行 strip，再保存去除首尾空白后的显示值；标签内部空白、大小写和原有文字形式保持不变。
- 去重以 strip 后的值判断，与 routes/media_library.py 当前行为一致。
- 删除全部标签是合法操作。

现有后端只通过 Pydantic Field(max_length=100) 约束数组长度，超过时会返回 Pydantic 通用 422；handler 已做 strip 和去重。实施时采用三层限制：

- 模型层把 tags 改为明显非业务含义的硬护栏 Field(max_length=1000)，阻止异常大列表进入 handler。
- handler 在归一化后执行 20 项、32 字符等业务规则，并返回稳定业务错误码。
- 给该 PATCH 路由或统一 API 层增加 64 KiB 请求体上限，超出返回 413。该限制必须在 JSON 反序列化前按实际接收字节执行，并覆盖 chunked body，不能只检查可伪造或缺失的 Content-Length。Pydantic 校验发生在 JSON 反序列化之后，仅靠 max_length 不能阻止超大请求体先被物化。

因此不能直接删掉模型层上限；但 1000 项结构护栏触发的框架 422/请求体 413 与正常业务的 422 code/message 明确分层，不把它们当作用户可达的产品校验。

历史超限数据采用“只许改善、不强制截断”的兼容规则：

- 读取和展示历史标签时不主动改写。
- 如果旧数组已经超过 20 项，只要归一化后的新数组数量不大于旧数组，就允许保存；界面同时提示用户逐步清理。
- 一旦数量降到 20 项或以下，后续严格执行 20 项上限。
- 单项 32 字符和空值规则只约束新增或被修改的标签。归一化后仍与旧数组中某项完全相同的历史超长/异常标签可以原样随整数组 PATCH 保存，直到用户主动修改或删除它；不能因编辑其他标签而卡死。
- 判断“未修改历史标签”使用 strip 后的规范值做多重集匹配，不能仅按数组位置比较；旧数组中的每个实例最多豁免新数组中的一个相同实例，防止复制一个历史异常值绕过新增校验。历史空值使用空字符串哨兵参与同一匹配，不能在比对前被过滤掉。
- 不允许因为打开编辑器而静默截断标签。

### 4.3 接口

沿用现有接口：

~~~text
PATCH /api/media-library/{asset_id}
Content-Type: application/json

{
  "tags": ["访谈", "横屏", "已授权"]
}
~~~

成功响应继续返回更新后的素材对象。标签错误统一使用本路由已有的 HTTPException detail 对象形状，返回可定位的 422：

- media_library_tags_too_many
- media_library_tag_too_long
- media_library_tag_empty

在 routes/media_library.py 增加 PUBLIC_MEDIA_LIBRARY_PATCH_ERRORS 集中映射，结构与 media_asset_name_empty 以及现有 PUBLIC_ANALYSIS_ERRORS 的 code/message 约定一致。1000 项 Pydantic 护栏只处理异常输入，不承担 20 项业务错误；前端按 handler 返回的 detail.code 显示稳定文案。

### 4.4 前端拆分

建议新增：

- frontend/src/modules/mediaLibrary/components/MediaLibraryTagEditor.jsx
- frontend/src/modules/mediaLibrary/components/MediaLibraryTagInput.jsx

建议修改：

- MediaLibraryListPage.jsx：维护正在编辑的素材、保存状态和 facets 刷新。
- MediaLibraryTable.jsx：把原有回调接到新弹层。
- 新增的 MediaLibraryCardGrid.jsx：使用同一个 onEditTags 回调。

成功保存后用接口返回值替换当前行/卡片，并重新获取 facets，确保刚新增的标签能立即出现在筛选项和建议列表中。

## 5. 需求二：卡片式展示与自定义列数

### 5.1 视图切换

在素材库工具栏增加“列表/卡片”分段按钮。切换仅改变展示组件，不改变当前查询条件、排序、筛选、页码和已加载数据。

建议默认行为：

- 首次访问默认列表视图，维持现有用户习惯。
- 记住用户最后选择的视图。
- 卡片视图默认 4 列。
- 用户可选择 2、3、4、5、6 列。

“自定义列数”在首期定义为 2 至 6 的整数选择，避免任意输入产生不可用布局。响应式布局使用“偏好列数”和“实际列数”分离：

- 大屏：按用户设置显示。
- 中等宽度：最多 3 列。
- 移动端：1 列，空间允许时 2 列。
- 窗口恢复后重新使用用户原始偏好，不覆盖保存值。

偏好保存在 localStorage：

- opencrew.mediaLibrary.viewMode，值为 table 或 cards。
- opencrew.mediaLibrary.cardColumns，值为 2 至 6。

本期偏好不写入服务端，因此不会跨浏览器或跨设备同步。

### 5.2 卡片内容

每张卡片展示：

- 缩略图；没有缩略图时显示按媒体类型区分的占位图。
- 素材名称和媒体类型。
- 时长、分辨率、格式等已有摘要信息。
- 分析状态、结构状态和质量状态。
- 最多两行标签，超出部分以“+N”表示。
- 更新时间。
- 与现有表格完全一致的操作：单击/按钮快速预览、点击素材名进入详情、重命名、编辑标签、归档或恢复归档、删除。

卡片和表格必须使用同一个 asset 对象和同一组操作回调，避免两个视图产生功能差异。openMenuId 和 onToggleMenu 继续由 MediaLibraryListPage.jsx 持有并同时传给两个视图；同一时刻全页面只能打开一个素材菜单，不能在卡片网格内部再维护第二套开合状态。

### 5.3 组件结构

建议新增：

- frontend/src/modules/mediaLibrary/components/MediaLibraryViewControls.jsx
- frontend/src/modules/mediaLibrary/components/MediaLibraryCardGrid.jsx
- frontend/src/modules/mediaLibrary/components/MediaLibraryCard.jsx

MediaLibraryListPage.jsx 负责：

- 读取并校验本地偏好。
- 控制 viewMode 和 preferredColumns。
- 继续持有查询、分页、删除和标签编辑等业务状态。
- 继续统一持有 openMenuId；切换视图时主动关闭当前菜单。

MediaLibraryTable 与 MediaLibraryCardGrid 只负责呈现，不分别请求数据。

### 5.4 性能

- 图片使用 lazy loading，并固定缩略图容器比例，避免加载时布局跳动。
- 卡片视图沿用当前分页，不一次性渲染全部素材。
- 列数变化只触发布局变化，不重新请求接口。
- 对损坏或过期缩略图提供 onError 回退，避免卡片空白。

## 6. 需求三：时间轴游标拖动与画面同步

### 6.1 目标交互

用户可以按住播放头或时间轴空白区域拖动。拖动期间：

1. 播放头连续跟随指针。
2. 当前时间文本连续更新。
3. video.currentTime 连续定位，预览画面随之变化。
4. 指针移出时间轴后仍能继续拖动，靠近可视区边缘时自动水平滚动。
5. 松开后准确停在最终时间点，并保持暂停。

片段左右选择手柄的业务语义保持不变，但把它当前基于 window pointermove/pointerup 的实现一起迁移到公共 pointer-capture 拖动 helper，补齐 pointercancel 和组件卸载清理。拖动选择边界时不得误触发播放头拖动。

### 6.2 状态模型

在 EditorTimeline.jsx 中增加清晰的拖动状态：

~~~text
idle
  -> pointerdown playhead/track
scrubbing
  -> pointermove: preview seek
  -> pointerup: final-seek-pending
  -> pointercancel: idle after cleanup
final-seek-pending
  -> final seeked: idle
  -> pointercancel/timeout: idle after cleanup
~~~

建议给父页面提供三类回调：

- onScrubStart(timeMs)
- onScrubMove(timeMs)
- onScrubEnd(timeMs)

如果希望少改接口，也可以保留 onSeek(timeMs, phase)，但必须明确 phase 为 start、move 或 end，避免未来用布尔值表达越来越多状态。

### 6.3 指针、定位和 SolidJS 更新

- 在 EditorTimeline.jsx 抽取 createPointerDrag 或等价的公共 helper，播放头和 selection handle 共用。
- pointerdown 在实际抓手元素上调用 setPointerCapture；pointermove、pointerup 和 pointercancel 绑定到捕获元素，而不是向 window 遗留全局监听器。
- helper 的 onCleanup 必须释放指针捕获、取消 requestAnimationFrame、停止边缘滚动并移除兜底监听。
- pointermove 使用当前时间轴可视区域、缩放和 scrollLeft 换算毫秒，统一限制在 0 至 durationMs。
- 这是 SolidJS 的细粒度信号更新。requestAnimationFrame 的作用是节流 setPlayheadMs 和 video.currentTime 写入，避免指针事件频率高于浏览器解码能力；中间定位最多每动画帧一次，并可再设置最小毫秒变化阈值。
- 浏览器支持 fastSeek 时可用于中间预览，但 pointerup 必须使用 currentTime 做最终精确定位。

放大后的边缘自动滚动规则：

- 指针进入 viewport 左右各 32 像素边缘区后启动 rAF 滚动循环。
- 根据距边缘的距离调整 scrollLeft，并在每帧滚动后重新计算目标毫秒。
- scrollLeft 限制在 0 至 canvasWidth - viewportWidth。
- 播放头和 selection handle 使用同一套边缘滚动机制。

### 6.4 视频事件反馈回路

MediaLibraryEditorPage.jsx 增加 isScrubbing 和 pendingFinalSeekMs 信号，并把现有 onVideoTimeUpdate 拆为普通 timeupdate 与最终 seeked 两种处理：

1. scrub start 先执行 video.pause()、setRangePreview(null)，再设置 isScrubbing=true。
2. scrubbing 期间 onTimeUpdate 和普通 onSeeked 直接 early-return，不能用浏览器滞后的可解码位置反写 playheadMs。
3. scrub move 只由拖动路径更新 playheadMs，并按 rAF 节流写 video.currentTime。
4. pointerup 保持 isScrubbing=true，设置 pendingFinalSeekMs，并对最终值执行一次精确 currentTime 赋值。
5. 只有最终 seeked 到达且 currentTime 与 pendingFinalSeekMs 落在容差内，才固定最终 playheadMs、清空 pending 值并恢复 isScrubbing=false。
6. pointercancel、视频 error、切换素材或最终 seek 超时走统一清理，但不恢复拖动前的播放状态。

这样可以阻断当前 onSeeked={onVideoTimeUpdate} 形成的异步反馈回路，避免播放头被旧 seeked 事件“橡皮筋式”拉回。开始拖动即取消 rangePreview，防止现有区间终点逻辑 pause、回写 currentTime 和清空状态时与 scrub 竞争。

### 6.5 命中区与交互优先级

事件优先级从高到低：

1. 片段左右选择手柄。
2. 片段本体的选择或移动交互。
3. 播放头拖动。
4. 时间轴空白区域定位。

各交互入口通过 stopPropagation 或明确的命中区域隔离。当前 .ml-editor-playhead 是从 top:22px 到 bottom:0 的贯穿轨道竖线，必须继续保持 width:1px 和 pointer-events:none。至少 24 像素的抓取命中区只能放在顶部 ruler band 或独立的播放头抓手元素上，不得把贯穿全高的竖线加宽并开启 pointer-events，否则会吞掉片段 click/dblclick。

### 6.6 统一时基与代理预览

- playheadMs、选区和剪切请求统一以源素材从 0 开始的毫秒时基为准。
- SessionOutput/media_library/previews/ 下的代理流只负责浏览器预览，不改变剪切坐标语义。
- 现有代理生成会转为 30 fps，但不应裁切、变速或改变起始时间。上传完成时用 ffprobe 比较源流和代理流时长；差值超过 100 毫秒时不发布该代理，回退源流并记录诊断。
- 最终 seek 的界面状态以请求的源时间毫秒为准；画面验收针对当前实际播放的流。代理流允许因 30 fps 丢/补帧产生一个代理帧的视觉差异，源流按自身帧率判断。
- 后续精确剪切仍由源文件和源时间坐标执行，不能用代理文件的帧序号反推剪切位置。

### 6.7 键盘与无障碍

- 播放头使用 slider 语义，并提供 aria-valuemin、aria-valuemax 和 aria-valuenow。
- 左右方向键每次移动 100 毫秒。
- Shift 加左右方向键每次移动 1 秒。
- 键盘定位与鼠标拖动使用同一个 seek 入口。
- 拖动状态不能只依赖颜色表达。

## 7. 文件级改动清单

### 前端

- frontend/src/modules/mediaLibrary/pages/MediaLibraryListPage.jsx
  - 替换 prompt 标签编辑。
  - 加入视图与列数偏好状态。
  - 在表格和卡片间切换。
- frontend/src/modules/mediaLibrary/components/MediaLibraryTable.jsx
  - 复用统一标签编辑和素材操作回调。
- 新增 MediaLibraryTagEditor.jsx 和 MediaLibraryTagInput.jsx。
- 新增 MediaLibraryViewControls.jsx、MediaLibraryCardGrid.jsx 和 MediaLibraryCard.jsx。
- frontend/src/modules/mediaLibrary/editor/EditorTimeline.jsx
  - 增加连续 scrubbing、公共 pointer-capture helper、边缘自动滚动和键盘控制。
- frontend/src/modules/mediaLibrary/editor/mediaLibraryEditor.css
  - 新增仅位于 ruler band 的播放头抓手，竖线保持 pointer-events:none。
- frontend/src/modules/mediaLibrary/pages/MediaLibraryEditorPage.jsx
  - 处理拖动开始、移动、最终 seeked 和取消；拖动期间屏蔽 timeupdate/seeked 回写并取消 rangePreview。

### 后端

- backend/opcrew_backend/routes/media_library.py
  - 将 tags 的 Pydantic 列表上限从业务含义不清的 100 改成 1000 项结构护栏，并增加 64 KiB 请求体上限。
  - 在归一化后增加标签数量、单项长度、空值和历史超限兼容校验。
  - 增加 PUBLIC_MEDIA_LIBRARY_PATCH_ERRORS，返回稳定 code/message。
- backend/opcrew_backend/repositories/media_library.py
  - 标签筛选和全文标签搜索同时兼容数据库 JSON 文本中的原始 UTF-8 与 ensure_ascii `\\u` 转义形式，保证中文标签在 SQLite 合同环境和 PostgreSQL 生产环境均可命中。
- backend/opcrew_backend/media_library_upload/storage.py
  - 在独立开关 OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD 下，发布代理预览前验证源流与代理流时长差，超限时回退源流。

数据库 schema 和素材查询接口不需要变化。

## 8. 测试方案

### 8.1 后端契约测试

覆盖：

- 添加、覆盖、删除全部标签。
- 标签前后空白清理和重复项处理。
- 超过 20 项、单项超过 32 字符、空白标签的错误响应。
- 历史 25 项标签可在新数量不增加时保存；降到 20 项后恢复严格上限。
- 未改动的历史 40 字符标签可随数组保存；新增长标签或修改后仍超长时返回 media_library_tag_too_long。
- 未改动的一个历史空值可以随其他标签的修改原样保存；复制该空值或新增空值仍返回 media_library_tag_empty。
- 21 至 1000 项的业务请求由 handler 返回统一 detail.code；超过 1000 项触发结构护栏，超过 64 KiB 触发 413。
- 标签更新后可立即被列表搜索和 facets 查询命中。
- 一个任务不能修改另一个任务的素材。

建议扩展现有 media library contract 测试，而不是另建一套数据库夹具。

### 8.2 前端组件与契约测试

标签：

- 输入、建议选择、逐项删除、取消、保存失败重试。
- 保存过程中防重复提交。
- 表格和卡片打开的是同一个编辑器。

卡片：

- table/cards 切换不丢筛选、排序和页码。
- 2 至 6 列设置正确映射 CSS grid。
- 非法 localStorage 值回退默认值。
- 响应式限制不覆盖用户偏好。
- 卡片和表格提供相同操作。
- 卡片与表格共享 openMenuId，切换视图会关闭菜单。

时间轴：

- 指针坐标正确换算并限制在时长范围内。
- pointermove 连续发出时间变化。
- scrubbing 期间滞后的 timeupdate/seeked 不会反写播放头。
- scrub start 立即取消 rangePreview。
- pointerup 保持屏蔽回写，直到最终 seeked 落定后才清理状态。
- pointercancel、切换素材和组件卸载不残留监听器。
- 播放头和 selection handle 都使用 pointer capture，并覆盖卸载中的清理。
- 放大后拖到可视区边缘会自动滚动并继续更新时间。
- 播放头抓手只占 ruler band，轨道竖线不拦截片段点击和双击。
- 拖动播放头不会改变片段边界；拖动边界不会移动播放头。
- 代理流和源流时长偏差超过 100 毫秒时回退源流。
- 键盘步进符合 100 毫秒和 1 秒规则。

### 8.3 浏览器验收

用一段至少 30 秒、画面变化明显的视频执行：

1. 给素材添加“访谈”和“横屏”，刷新后仍存在，并能通过标签筛选找到。
2. 删除一个标签和全部标签，列表及筛选聚合立即更新。
3. 在列表和卡片之间切换，筛选条件、排序、页码不变。
4. 设置 2 列和 6 列并刷新页面，偏好仍然生效。
5. 缩窄窗口时卡片自动降列，恢复窗口后回到原偏好。
6. 从约 10 秒拖到约 25 秒，播放头、时间文本和视频画面连续跟随。
7. 在区间预览播放中开始拖动，区间预览立即取消，播放头没有回弹。
8. 放大时间轴后把指针拖到视口边缘，视口自动滚动并可到达不可见区间。
9. 松开后视频暂停在目标源时间；playheadMs 与目标整数毫秒一致，video.currentTime 在最终 seeked 后与目标相差不超过当前播放流一帧，无法取得帧率时使用 50 毫秒容差。
10. 分别用源流和“流畅预览”代理流验收；代理用例必须选择 width 或 height 大于 1920、帧率超过 DEFAULT_PREVIEW_MAX_FPS、非 H.264/非兼容像素格式，或码率超过阈值的素材，以确保 should_create_proxy_preview 返回 true，并断言 previewUrl 命中 /media_library/previews/。代理流视觉帧按 30 fps 判断，剪切坐标始终使用源素材毫秒。
11. 拖动片段左右手柄时，片段范围变化但播放头不被抢占；拖动中卸载组件不残留全局监听。

## 9. 验收标准

### 标签

- 用户无需输入逗号字符串即可添加和删除标签。
- 保存后刷新页面数据不丢失，筛选聚合一致。
- 前后端执行相同的数量和长度限制。
- 历史超限素材可以逐步减少标签，不会因为整数组 PATCH 被永久卡死。
- 保存失败时用户输入不丢失，并能重试。

### 卡片视图

- 同一批数据可以无刷新地在列表与卡片间切换。
- 可选择 2 至 6 列，刷新后保留偏好。
- 小屏不会产生水平溢出。
- 两种视图的操作、状态和权限保持一致。
- 两种视图共享同一个菜单开合状态，不会同时出现两套操作菜单。

### 时间轴拖动

- 桌面鼠标和支持 Pointer Events 的触控设备均可连续拖动。
- 视频画面、播放头和时间文本在拖动时同步。
- 松开后定位准确并保持暂停。
- timeupdate/seeked 不会造成回弹，rangePreview 不会与拖动竞争。
- 播放头抓手不会遮挡轨道片段，放大后可边缘自动滚动。
- 片段手柄、点击定位、代理预览和正常播放同步没有回归。

## 10. 实施顺序

1. 先提取统一的标签编辑器并补后端校验，完成接口和组件测试。
2. 再引入视图状态和卡片组件，复用第一步的标签入口。
3. 先抽取播放头和 selection handle 共用的 pointer-capture helper，并补卸载、取消和边缘滚动测试。
4. 实现父页面 scrubbing/final-seek-pending 状态，处理 seeked 反馈回路和 rangePreview。
5. 独立提交代理时基守卫，以 OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD 灰度；不与时间轴拖动强绑定发布。
6. 执行素材库列表、编辑器契约测试和明确触发代理生成的浏览器回归。
7. 在测试环境开启验收，通过后再进入生产发布流程。

这样拆分后，标签与卡片可以作为一组素材库改动评审，时间轴改动可以独立回滚，降低相互影响。

## 11. 风险与回滚

- 标签规则收紧可能影响历史上超过限制的素材。通过“新数组数量不大于旧数组”规则允许渐进清理，不主动改写或截断历史值。
- 历史超长或空值标签按“未修改即放行”兼容；匹配逻辑错误可能误把新增长标签当历史值，必须有精确的新旧数组契约测试。
- 高码率或关键帧间隔很长的视频，拖动预览仍可能短暂模糊或延迟。系统已有按需生成的低码率代理预览；时基守卫启用后，漂移超限素材会回退源流，可能增加浏览器带宽和解码压力。
- 代理时基校验是上传链路行为变更，使用独立开关 OPENCREW_MEDIA_LIBRARY_PROXY_TIMEBASE_GUARD 发布。发生误判或流畅预览覆盖率明显下降时，只关闭该开关并恢复原代理发布行为，不回滚标签、卡片或时间轴代码。
- 频繁设置 currentTime 可能导致解码压力。通过 rAF 节流 setter、最小时间变化阈值和拖动期间屏蔽媒体事件回写来控制。
- localStorage 可能被禁用，界面需退回默认值但功能仍可使用。

回滚时可分别撤下卡片入口和连续拖动监听；代理时基守卫通过独立开关关闭。原列表、点击定位以及现有标签 PATCH 接口仍可继续工作，不涉及数据库回滚。
