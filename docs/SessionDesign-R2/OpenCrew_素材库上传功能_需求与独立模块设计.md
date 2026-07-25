# OpenCrew 素材库上传功能需求与独立模块设计

版本：v0.1

状态：历史归档（上传阶段设计）。分片上传、上传身份和路径安全背景仍可参考；分析 run 的输入快照、文件登记和生命周期以[开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)为准。

归档说明：本文第 8.2 节的共享 `SessionContext` 复制示例已经过时。当前合同要求每个分析 run 保留独立 `0_SessionContext` 输入快照；inbox 不与 run 快照直接硬链接，legacy 媒体只与同一 run 快照硬链接。

关联文档：

1. `OpenCrew_素材库列表页_需求设计.md`
2. `OpenCrew_素材库视频语义拆分工具规划.md`
3. `Analysis_V1_00_PrepareSessionVariables_工具实现Workbook.md`

本文新增素材库上传能力。对于 `OpenCrew_素材库列表页_需求设计.md` 中“首版不包含上传”的范围说明，以本文为准；本文只放开素材上传、Session 创建和列表新增，不同时放开 OpenCut V1 分析工具、OCR、ASR、Keyframe、语义拆分或剪辑能力。

## 1. 结论先行

素材库页面新增一个极简的 `上传素材` 入口。

核心规则：

```text
一个视频 = 一个素材 = 一个 OpenCrew Session = 素材库列表中的一行
```

上传流程必须满足：

1. 用户一次只上传一个视频。
2. 不上传 SRT；后续字幕来自视频画面的 OCR 识别，并可结合 ASR 校准。
3. 不填写标签、分析模式、模型或其他参数。
4. Session 名称和素材名称默认取视频原始文件名去除扩展名。
5. 上传成功后关闭弹窗，停留在素材库列表页，并立即新增一行。
6. 上传阶段只创建素材和 Session，不自动运行 OpenCut V1 工具。
7. 大文件使用分片上传，并显示真实上传进度。
8. Session 创建、OpenCode Session 绑定、workspace 创建以及视频从上传区复制到 Session Context 的行为，与“视频分析（口播）/ Analysis V1”保持一致。
9. OpenCut V1 必须以独立源码模块实现，不得在运行时依赖 Analysis、Analysis V1、人物口播或动作模拟的业务代码。

## 2. 产品目标

用户应能在素材库列表页完成以下最短路径：

```text
点击上传素材
  -> 选择或拖入一个视频
  -> 点击上传
  -> 查看上传进度
  -> 上传完成
  -> 弹窗关闭
  -> 列表新增一条未分析素材
```

本功能解决的是“把一个原始视频可靠地放入素材库并建立独立 Session”，不解决视频分析和剪辑问题。

## 3. 本期范围

### 3.1 本期包含

1. 素材库标题区的 `上传素材` 按钮。
2. 单视频上传弹窗。
3. 拖拽选择和文件选择。
4. 大文件分片上传。
5. 上传百分比、已上传大小和上传阶段展示。
6. 分片失败自动重试。
7. 取消上传和失败重试。
8. OpenCrew Session 创建。
9. Session workspace 创建。
10. OpenCode Session 创建和绑定。
11. 素材记录创建并绑定 `session_id`。
12. 视频写入 Session workspace 的 `inbox/`。
13. 上传成功后素材库列表新增一行。
14. 未完成上传的清理和超时回收。

### 3.2 本期不包含

1. SRT 文件上传。
2. 标签填写或维护。
3. 多视频批量上传。
4. 文件路径输入。
5. 从其他素材库选择视频。
6. OCR、ASR、字幕检测和字幕校准。
7. Keyframe 抽取。
8. 视频片段语义拆分。
9. OpenCut V1 工具配置和运行。
10. 自动进入素材详情页。
11. 上传后自动开始分析。
12. 视频剪辑、裁切、转码和导出。

## 4. 页面入口

### 4.1 按钮位置

在素材库列表页标题区右侧增加主按钮：

```text
上传素材
```

要求：

1. 使用与现有 OpenCrew 主操作一致的蓝色按钮。
2. 不放入筛选弹窗。
3. 不与搜索框、筛选按钮混排成复杂工具栏。
4. 页面进入上传状态后，按钮仍保留，但同一页面只允许一个活动上传任务。

### 4.2 点击行为

点击后打开居中的 `素材上传` 弹窗。

弹窗不跳转路由，不预先在列表中插入占位行。

## 5. 上传弹窗

### 5.1 界面内容

弹窗只包含：

1. 标题：`素材上传`。
2. 关闭按钮。
3. 一个视频拖拽/选择区域。
4. `取消`按钮。
5. `上传`主按钮。

明确不显示：

1. Session 名称输入框。
2. 素材名称输入框。
3. SRT 上传区。
4. 标签输入框。
5. 分析模式。
6. 模型选择。
7. 本地路径输入。
8. “上传并分析”按钮。

### 5.2 选择文件前

上传区文案：

```text
拖拽视频到这里，或点击选择视频
```

一次只能选择一个文件。再次选择时替换当前文件。

### 5.3 选择文件后

上传区显示：

1. 文件名。
2. 文件大小。
3. 文件类型。
4. `重新选择`或移除操作。

Session 标题和素材名称自动使用：

```text
原始文件名去除最后一个扩展名
```

例如：

```text
老板采访_第三版.mp4
-> 老板采访_第三版
```

## 6. 上传进度与大文件行为

### 6.1 进度条

上传开始后，原上传区域切换为进度状态，显示：

1. 上传进度条。
2. 百分比，例如 `63%`。
3. 已上传大小 / 文件总大小，例如 `1.26 GB / 2.00 GB`。
4. 当前阶段。

当前阶段只能使用以下文案：

```text
正在准备
正在上传
正在保存
上传完成
上传失败
已取消
```

上传进度必须来自客户端实际发送字节数或已被后端确认的分片大小，不能使用定时器模拟。

### 6.2 分片上传

第一版默认：

```text
chunk_size = 16 MiB
```

要求：

1. 文件按固定大小切分。
2. 每个分片携带 `upload_id`、`chunk_index`、`total_chunks`、文件总大小和原始文件名。
3. 后端按 `upload_id` 隔离临时分片。
4. 后端只接受合法范围内的分片编号。
5. 重复上传同一分片必须幂等，不能重复累加已上传字节数。
6. 完成请求必须验证所有分片存在且合并后大小与原文件一致。
7. 合并先写临时文件，验证成功后再原子替换为最终文件。

### 6.3 自动重试

单个分片失败时自动重试，默认最多三次：

```text
第 1 次失败 -> 立即重试
第 2 次失败 -> 短暂退避后重试
第 3 次失败 -> 整体进入上传失败状态
```

重试单个分片时不得重新上传已经被后端确认的分片。

### 6.4 上传到 100% 后

客户端字节发送完成不等于素材已经可用。

当分片已全部上传，但后端仍在合并、校验和写入最终文件时：

```text
进度显示 100%
状态显示“正在保存”
```

只有后端返回完成结果后才能显示“上传完成”并向列表插入素材行。

### 6.5 取消与关闭

1. 上传前关闭弹窗不需要确认。
2. 上传过程中关闭弹窗或点击取消，需要二次确认。
3. 确认取消后，前端终止正在进行的请求，并调用取消接口。
4. 后端清理临时分片、未完成素材记录、对应 Session 和 workspace。
5. 取消完成后不得在素材库列表留下占位行。

### 6.6 页面刷新和短暂断线

后端保存 `upload_id` 的已完成分片状态。

第一版要求支持：

1. 同一上传弹窗内的网络失败重试。
2. 通过上传状态接口查询已完成分片。
3. 已完成分片不重复上传。

浏览器完全关闭后的自动恢复不作为首版强制 UI 能力，但后端上传事务必须保留足够状态，并由超时清理任务回收废弃上传。

## 7. Session 创建行为

### 7.1 创建顺序

点击 `上传` 后执行：

```text
创建 upload transaction
  -> 创建 OpenCrew Session
  -> 创建 sessions/<session_id>/workspace
  -> 创建 OpenCode Session
  -> 将 opencode_session_id 写回 OpenCrew Session
  -> 创建未完成素材记录
  -> 上传视频分片
  -> 合并到 workspace/inbox/<原文件名>
  -> 更新素材记录为可用
  -> 返回完整素材列表行数据
```

Session 创建方式必须与视频分析（口播）一致：

1. 先写入 OpenCrew `sessions` 表。
2. 使用正式 `session_id` 创建 workspace。
3. 更新 `sessions.workspace_dir`。
4. 创建并绑定 OpenCode Session。
5. Session 进入可被后续工具使用的状态。

OpenCut V1 使用独立标识，例如：

```text
source = open-cut-v1
group_id = open-cut-v1
sender_name = OpenCut V1
```

不得复用：

```text
source = openclip-analysis
group_id = openclip-analysis
```

### 7.2 素材记录

素材记录至少需要保存：

```text
asset_id
session_id
display_name
original_filename
source_video_path
media_type
size_bytes
upload_status
analysis_status
subtitle_mode
created_at
updated_at
```

默认状态：

```text
upload_status = uploading -> ready
analysis_status = not_analyzed
subtitle_mode = ocr_pending
```

`session_id` 与 `asset_id` 在首版为一一对应关系，并应由数据库约束或服务层唯一性检查保证。

## 8. 文件目录与复制规则

### 8.1 上传完成后的目录

上传完成后，视频保存在与视频分析（口播）一致的位置：

```text
<OPENCREW_DATA_DIR>/sessions/<session_id>/workspace/
└── inbox/
    └── <原始文件名>
```

规则：

1. 不建立 `uploads/original/videos/` 等额外目录。
2. 数据库中的 `source_video_path` 使用 workspace 相对路径。
3. 原始文件名需要经过路径安全处理，但显示名称仍保留用户原始文件名。
4. 临时分片不得写入最终文件路径。
5. 分片临时目录必须可以按 `upload_id` 整体清理。

### 8.2 后续 OpenCut V1 第 00 步

上传阶段不提前运行第 00 步。

当用户后续启动 OpenCut V1 分析时，OpenCut V1 自己的 `00_PrepareSessionVariables` 按照 Analysis V1 的行为，把真实视频复制到标准 Session Context：

```text
<workspace>/
├── inbox/
│   └── <原始文件名>
├── SessionContext/
│   ├── Variables.json
│   └── Video_Source.mp4
├── SessionReport/
├── SessionOutput/
└── S1_00_PrepareSessionVariables/
    ├── Output/
    │   └── Variables.json
    └── Report/
        └── Result.json
```

要求：

1. 数据库中的 `source_video_path` 是唯一源路径，不扫描 `inbox/` 猜测输入文件。
2. 必须复制真实文件，不能创建 symlink。
3. OpenCut V1 后续工具只读取 `SessionContext/Video_Source.mp4`。
4. OCR、ASR 和 Keyframe 工具不能直接依赖 `inbox/`。
5. 本文不要求上传完成后立即生成 `Variables.json`；该文件由 OpenCut V1 第 00 步生成。

## 9. 上传接口需求

上传接口归属素材库独立模块，建议使用：

```text
POST   /api/media-library/uploads
GET    /api/media-library/uploads/{upload_id}
POST   /api/media-library/uploads/{upload_id}/chunks
POST   /api/media-library/uploads/{upload_id}/complete
DELETE /api/media-library/uploads/{upload_id}
```

### 9.1 创建上传事务

请求至少包含：

```json
{
  "filename": "example.mp4",
  "size_bytes": 2147483648,
  "content_type": "video/mp4"
}
```

响应至少包含：

```json
{
  "upload_id": "mlu_...",
  "session_id": 123,
  "chunk_size": 16777216,
  "total_chunks": 128,
  "status": "uploading"
}
```

### 9.2 上传分片

分片接口返回：

```json
{
  "upload_id": "mlu_...",
  "chunk_index": 7,
  "received_bytes": 134217728,
  "total_size": 2147483648,
  "complete": false
}
```

### 9.3 完成上传

完成接口必须在事务内或等价的一致性边界内完成：

1. 验证全部分片。
2. 合并临时文件。
3. 验证最终大小。
4. 原子写入 `workspace/inbox/<原始文件名>`。
5. 更新素材记录为 `ready`。
6. 记录 `file.uploaded` Session 事件。
7. 返回素材库列表所需的完整行数据。

完成响应不能只返回 `ok: true`；必须返回前端新增列表行所需的规范素材对象。

## 10. 上传成功后的列表行为

上传完成后：

1. 弹窗关闭。
2. 当前路由继续保持 `#/media-library`。
3. 不进入素材详情页。
4. 不进入 OpenCut V1 分析页。
5. 新素材插入当前列表顶部。
6. 当前筛选条件如果会排除新素材，则重新请求列表，并按真实筛选结果展示，不能强行插入不符合条件的行。
7. 页面素材总数增加一条。

新行默认信息：

```text
素材名称：原始文件名去除扩展名
上传状态：已完成
分析状态：未分析
字幕状态：待 OCR 识别
更新时间：当前时间
```

## 11. 错误处理与清理

### 11.1 前端错误

前端必须区分：

1. 文件不支持。
2. 文件为空。
3. 上传请求失败。
4. 分片重试耗尽。
5. 后端合并失败。
6. Session 创建失败。
7. OpenCode Session 创建失败。
8. 用户主动取消。

错误后保留当前文件选择，允许用户直接重试；用户明确移除文件或关闭弹窗后再清空。

### 11.2 后端清理

以下情况不得留下可见素材行：

1. Session 创建中断。
2. OpenCode Session 创建失败。
3. 分片上传取消。
4. 分片合并失败。
5. 最终文件校验失败。

清理顺序：

```text
素材记录/上传事务标记失败
  -> 删除临时分片
  -> 删除未完成最终文件
  -> 删除未完成 Session 关联数据
  -> 删除对应 workspace
```

如果进程异常退出导致同步清理未执行，后台超时清理任务必须回收超过有效期的上传事务。

## 12. 独立代码模块硬约束

### 12.1 原则

“与 Analysis V1 完全一致”指行为和合同一致，不表示运行时调用 Analysis V1。

必须遵守：

1. OpenCut V1 不导入 `ToolLibrary/Analysis_V1`。
2. 素材上传模块不调用 `/api/openclip/tasks`。
3. 素材上传模块不调用 Analysis V1 的前端 API 封装。
4. 素材上传模块不复用 `AnalysisV1PromptBuilder` 等页面组件。
5. 素材上传模块不向 `openclip_tasks` 写入任务记录。
6. 素材上传模块不使用 `openclip-analysis` 的 source/group 标识。
7. Analysis V1 只作为实现复制和行为比对的来源。
8. 后续删除 Analysis V1 业务模块时，素材库上传和 OpenCut V1 仍必须可以运行。

允许共享的仅是 OpenCrew 平台基础设施：

1. Session repository。
2. workspace store。
3. OpenCode adapter/client。
4. 数据库 engine 和事务设施。
5. 通用鉴权、日志和 Session event 基础设施。
6. 通用安全路径校验。

### 12.2 前端模块建议

```text
frontend/src/modules/mediaLibrary/
├── MediaLibraryModule.jsx
├── pages/
│   └── MediaLibraryListPage.jsx
└── upload/
    ├── MediaLibraryUploadDialog.jsx
    ├── MediaLibraryUploadDropzone.jsx
    ├── MediaLibraryUploadProgress.jsx
    ├── mediaLibraryUploadApi.js
    ├── mediaLibraryUploadModel.js
    ├── useMediaLibraryUpload.js
    └── mediaLibraryUpload.css
```

职责限制：

| 文件 | 职责 |
| --- | --- |
| `MediaLibraryUploadDialog.jsx` | 弹窗布局和交互编排 |
| `MediaLibraryUploadDropzone.jsx` | 文件选择、拖拽和文件摘要 |
| `MediaLibraryUploadProgress.jsx` | 进度条和上传阶段展示 |
| `mediaLibraryUploadApi.js` | 创建、分片、查询、完成和取消接口 |
| `mediaLibraryUploadModel.js` | 状态、进度和错误归一化 |
| `useMediaLibraryUpload.js` | 分片调度、重试、取消和恢复 |
| `mediaLibraryUpload.css` | 上传弹窗专属样式 |

单个 UI 文件不得同时承载弹窗、分片算法、API 请求和列表更新逻辑。

### 12.3 后端模块建议

```text
backend/opcrew_backend/media_library_upload/
├── __init__.py
├── router.py
├── schemas.py
├── service.py
├── repository.py
├── storage.py
└── cleanup.py
```

职责限制：

| 文件 | 职责 |
| --- | --- |
| `router.py` | HTTP 参数和响应，不实现文件合并算法 |
| `schemas.py` | 上传请求、响应和状态合同 |
| `service.py` | 上传事务、Session 创建和完成编排 |
| `repository.py` | 上传事务与素材记录数据库访问 |
| `storage.py` | 分片写入、校验、合并和原子落盘 |
| `cleanup.py` | 取消、失败和超时回收 |

禁止把全部上传实现继续堆入：

```text
backend/opcrew_backend/routes/media_library.py
backend/opcrew_backend/koubo/router.py
frontend/src/modules/mediaLibrary/pages/MediaLibraryListPage.jsx
```

这些文件只能负责注册、调用或接收上传结果。

### 12.4 OpenCut V1 工具边界

后续独立工具目录：

```text
ToolLibrary/OpenCut_V1/
└── 00_PrepareSessionVariables.py
```

该脚本可以从 Analysis V1 复制所需逻辑，但复制完成后必须：

1. 使用自己的 workflow id。
2. 使用自己的变量 schema/version。
3. 查询素材库资产和 OpenCut V1 自己的数据表。
4. 不 import Analysis V1。
5. 拥有自己的合同测试。

## 13. 状态模型

前端上传状态：

```text
idle
selected
preparing
uploading
saving
completed
failed
cancelled
```

允许的主要转换：

```text
idle -> selected
selected -> preparing
preparing -> uploading
uploading -> saving
saving -> completed

preparing/uploading/saving -> failed
preparing/uploading/saving -> cancelled
failed -> preparing
selected -> idle
```

素材记录只在 `ready` 后作为正常素材返回给默认列表。`uploading` 和 `failed` 记录默认不进入素材库列表。

## 14. 安全与数据完整性

1. 不信任客户端文件名、content type、分片编号和文件大小。
2. 禁止绝对路径和 `..` 路径逃逸。
3. 临时目录只能位于 OpenCrew 数据目录的受控上传区域。
4. 完成上传前验证所有分片和最终大小。
5. 文件合并不得把完整视频一次性读入内存。
6. 上传完成使用原子 rename/replace。
7. 同一 `upload_id` 只能操作自己的 Session 和素材记录。
8. 完成、取消和超时清理接口必须幂等。
9. 日志不得记录视频内容或二进制分片。
10. Session 事件记录文件名、字节数、素材 ID 和上传状态，但不记录文件内容。

## 15. 验收标准

### 15.1 界面验收

1. 素材库标题右侧存在 `上传素材` 按钮。
2. 弹窗中没有 SRT、标签、Session 名称或分析设置。
3. 只能选择一个视频。
4. 选择后显示文件名和大小。
5. 上传时显示真实进度条、百分比和字节数。
6. 100% 后合并期间显示“正在保存”。
7. 上传成功后弹窗关闭，停留在列表页。
8. 列表新增且仅新增一行。

### 15.2 Session 验收

1. 一条素材绑定一个唯一 OpenCrew Session。
2. Session 具有独立 workspace。
3. Session 绑定有效 OpenCode Session。
4. 上传视频位于 `workspace/inbox/<原始文件名>`。
5. 素材记录保存 workspace 相对路径。
6. 上传阶段不运行 OpenCut V1。
7. 后续第 00 步能复制为 `SessionContext/Video_Source.mp4`。

### 15.3 大文件验收

1. 大文件按 16 MiB 分片发送。
2. 上传进度单调增加，不出现模拟跳动。
3. 单个分片失败可以自动重试。
4. 已确认分片不重复计数。
5. 合并后的文件大小与原文件一致。
6. 上传取消后临时分片和未完成 Session 被清理。
7. 列表不会出现失败或半完成素材。

### 15.4 独立性验收

1. 删除或禁止导入 `ToolLibrary/Analysis_V1` 后，素材上传模块合同测试仍能运行。
2. 前端上传模块没有从 `modules/koubo/AnalysisV1` import。
3. 后端上传模块没有从 `koubo/router.py` import 业务函数。
4. 上传接口路径使用 `/api/media-library/uploads`。
5. Session 的 source/group 使用 OpenCut V1 独立标识。
6. 新功能没有继续扩大现有大文件。

## 16. 必须建立的测试

### 16.1 前端测试

1. 单文件选择与替换。
2. 非视频文件拦截。
3. 分片进度计算。
4. 分片自动重试。
5. 取消上传。
6. 100% 后进入 saving 状态。
7. 成功后关闭弹窗并更新列表。
8. 失败后不新增列表行。

### 16.2 后端合同测试

建议新增：

```text
backend/tests/contracts/test_media_library_upload_contract.py
backend/tests/contracts/test_media_library_upload_module_boundary_contract.py
```

至少覆盖：

1. 创建上传事务时创建独立 Session。
2. Session source/group 正确。
3. OpenCode Session 绑定成功。
4. 分片乱序、重复和缺失处理。
5. 非法分片编号拒绝。
6. 文件大小不一致拒绝。
7. 最终视频原子落盘。
8. 素材记录只在完成后进入默认列表。
9. 取消与超时清理。
10. 路径逃逸拦截。
11. OpenCut V1 模块不依赖 Analysis V1。
12. 一个视频只产生一个 Session 和一个素材行。

## 17. Definition of Done

只有同时满足以下条件，素材库上传功能才算完成：

1. 用户能从素材库上传一个大视频。
2. 上传过程有真实、连续、可理解的进度反馈。
3. 上传成功后停留列表并新增一行。
4. 一条素材只对应一个 Session。
5. Session 和文件落盘行为与视频分析（口播）一致。
6. 不要求用户提供 SRT、标签或其他配置。
7. 失败和取消不会留下半成品素材。
8. 前后端上传逻辑均被拆分为独立模块。
9. OpenCut V1 与 Analysis V1 没有运行时业务代码依赖。
10. 合同测试覆盖大文件、Session、清理和模块边界。
