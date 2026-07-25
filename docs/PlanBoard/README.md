# Plan Board 需求设计文档索引

版本：v0.1

状态：设计标准初稿。本文档组从 `SessionDesign-R2` 的 StoryBoard 实现经验中抽象出 Plan Board 的通用设计标准、实施流程和第一版实现合同。

## 文档列表

1. `01_ControlM_实现调研分析报告.md`
   - 说明 Control-M 中事件、前置条件、资源池、锁资源、优先级等思想如何映射到 OpenCrew Plan Board。
   - 结论：Plan Board 第一版只实现单行串行任务链，但数据模型必须预留多输入条件、资源数、互斥锁和并行调度字段。

2. `02_PlanBoard_项目工作规划.md`
   - 定义做一个 Plan Board 前必须完成的分析步骤。
   - 重点是先定 Plan Row、再定 Job、再定 Wait for Events / Actions、再定 Workspace 文件命名和 Job Status 派生。

3. `03_PlanBoard_最终需求实现细节.md`
   - 定义 Plan Board 的核心对象、目录结构、文件命名、Job Status、执行生命周期、UI 合同、后端服务和测试范围。
   - 已将 StoryBoard Workspace 文件命名规则归一化为 Plan Board 可复用规则。

## 设计总原则

一个 Plan 的创建就是一个 Session。Session 是该 Plan 创建后的运行状态容器，保存 `SessionContext`、`SessionOutput`、`SessionReport`、工具目录、Job Status 和执行记录。Workspace 是这个 Session 的文件系统产出目录，不等同于 Control-M Workspace。

Control-M 的 `Workspace` 在本设计中命名为 `Plan Board`，它是 Plan 的编排、确认、状态查看和 Job 关系展示界面。Control-M 的 `Folder` 命名为 `Plan`；`Regular Folder` 命名为 `Regular Plan`；`SMART Folder` 命名为 `SMART Plan`；`Sub-folder` 命名为 `Sub Plan`。

Plan Board 必须保留 `Plan Row` 概念。Plan Row 是 Plan Board 里的横向重复执行单元，适用于 StoryBoard 这类按 Segment、Dialogue、文件、场景或其它业务实体逐行重复同一组 Job 的流程。Plan Row 不是 Control-M 的替代概念，也不等同于 Sub Plan；它是 Plan Board 的视图和执行分组。

Plan Board 的状态事实源必须落到 Session Workspace 内 `SessionOutput/<domain>/Working/` 的一级文件。Plan Board JSON 只定义结构、依赖和显示，不单独制造完成态。`Ended OK` 必须来自标准业务产物文件；`Executing` 和 `Ended Not OK` 来自同层 Running / Failed 状态标记；`Waiting` 由 Wait for Events、Wait Resources、Wait User 和下游产物实时派生。

最重要的设计规则：一个 Job 只能承担一个最小业务产物或一个明确动作，不能让一个 Job 在 UI 显示格里同时承担多个内容。例如 Video 显示格不能同时代表 `New Video / Video_Raw` 和 `Final Video / Video_Final`。如果一个 UI 按钮会连续执行多个动作，底层也必须拆成多个 Job，并分别拥有自己的 Wait for Events、Actions、Job Status 和标准文件。

第一版范围与 StoryBoard 对齐：每个 Plan Row 是一条串行 Job 链；不处理跨 Plan Row 条件交叉、不处理复杂 AND/OR 拓扑调度、不做全局并行队列。但 Job 模型、命名和状态服务必须从一开始支持未来扩展。
