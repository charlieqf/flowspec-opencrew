# Plan Board Control-M 实现调研分析报告

版本：v0.1

状态：设计调研稿。

参考资料：

- BMC Control-M 9.0.22 文档首页：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/home.htm`
- Events：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/Events.htm`
- Prerequisites：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/Job_prerequisites.htm`
- Lock Resources：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/Lock_Resources.htm`
- Resource Pools：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/Resource_Pools.htm`
- Creating a Job：`https://documents.bmc.com/supportu/9.0.22/en-US/Documentation/Creating_a_Job.htm`

## 1. 调研结论

Plan Board 不需要复刻 Control-M 的全部企业调度能力，但应该继承它的三个核心思想：

1. 任务不是靠 UI 顺序运行，而是靠 `Prerequisites` 判断是否可以运行。
2. 一个任务完成后会释放可被后续任务等待的事件或产物。
3. 调度不仅受依赖控制，也受资源池、锁资源、优先级和人工确认控制。

映射到 OpenCrew，`Wait for Events` 不应该只是抽象字符串，而应该优先绑定到 Session Workspace 中 `SessionOutput/<domain>/Working/` 下的标准文件。`Actions` 也不应该只是执行结果 JSON，而应该是 Job 成功后必须发布的标准产物文件、事件调整或后置动作。

第一版 Plan Board 的实现重点：

1. 保持 StoryBoard 当前的一行串行任务体验。
2. 保留 `Plan Row`，用于按 Segment、Dialogue、文件或其它业务实体逐行重复一组 Job。
3. 每个 Job 明确 Wait for Events、Actions、运行标记和失败标记。
4. Job Status 从 Workspace 文件派生，避免 Plan JSON、Execution JSON、UI cache 三套状态互相打架。
5. 数据模型预留未来 Control-M 能力：多输入条件、资源数量、互斥锁、优先级、人工确认、跨行依赖。

## 2. Control-M 关键概念

### 2.1 Job / Folder / Workspace

Control-M 的 Job 是可执行单元，Folder / SMART Folder 用来组织 Job，Workspace 是规划和编辑区。一个 Job 会定义通用属性、调度属性、前置条件和后置动作。

OpenCrew 最终命名映射：

| Control-M | OpenCrew 命名 | 含义 |
| --- | --- |
| Workspace | Plan Board | Plan 的编排、确认、状态查看和 Job 关系展示界面 |
| Folder | Plan | 一个具体业务计划 |
| Regular Folder | Regular Plan | 普通计划 |
| SMART Folder | SMART Plan | 可继承统一调度和条件规则的计划 |
| Sub-folder | Sub Plan | Plan 下的子计划或子流程 |
| Job | Job | 最小可执行单元 |
| Job Type | Tool Stage / Action Type |
| Prerequisites | Prerequisites |
| Wait for Events | Wait for Events |
| Actions | Actions |
| Run ID | Attempt ID / Marker UID |

在 OpenCrew 中，一个 Plan 的创建就是一个 Session。Session 是该 Plan 的运行状态容器，Workspace 是该 Session 的文件系统产出目录。Control-M 的 Workspace 不对应这个文件系统 Workspace，而是对应 `Plan Board`。

`Plan Row` 是 OpenCrew 在 Plan Board 中保留的额外视图概念。它用于表达“同一组 Job 按行重复执行”的结构，例如 StoryBoard 按 Segment 逐行执行音频、图像、视频和终视频 Job。Plan Row 不是 Sub Plan，也不是 Job；它是 Plan Board 的横向重复执行单元。

### 2.2 Events

Control-M 的 Events 是任务之间的依赖。前序任务完成后添加 event，后续任务等待 event 满足后才能运行。一个后续任务可以等待多个 event，并支持 AND / OR 关系。

Plan Board 映射：

1. `Event` 映射为文件或业务状态事件。
2. `Wait for Events` 映射为 Job 的输入依赖。
3. `Add/Delete Event` 映射为 Job 成功后的 Actions。
4. 事件名称不使用人工随意命名，优先由标准 Working 文件路径自动生成。

推荐命名：

```text
pb://working/{domain}/{row_anchor}/{stage}/{artifact_name}
```

示例：

```text
pb://working/storyboard/srt_0001_01/Image_New/file
pb://working/storyboard/srt_0001_01/Video_Raw/file
pb://working/storyboard/srt_0001_01/Video_Final/file
```

### 2.3 Prerequisites

Control-M 的前置条件包括用户确认、等待事件、锁资源、资源池等。调度判断不是单一的“前一个任务完成”，而是多类条件同时满足。

Plan Board 第一版应定义统一的 Wait for Events 类型：

| 类型 | 用途 | 第一版是否执行 |
| --- | --- | --- |
| `file_exists` | 标准 Working 文件存在且有效 | 是 |
| `file_bound` | 文件已写回业务 JSON 绑定 | 是，按工具需要 |
| `marker_absent` | 同 stage 没有当前 Running 标记 | 是 |
| `manual_confirmed` | 人工确认，例如 Confirm Final | 是 |
| `resource_available` | 资源池数量可用 | 预留 |
| `lock_available` | 互斥锁可用 | 预留 |
| `custom_predicate` | 复杂业务函数判断 | 预留 |

第一版虽然只做 Plan Row 内串行 Job，也必须把每个 Job 的 `wait_for_events` / `actions` 写清楚，不能只靠数组顺序。

### 2.4 Resource Pools

Control-M 的 Resource Pools 表达可量化资源，例如数据库连接数、CPU、并发登录数。任务需要一定数量资源，资源不足时等待。

Plan Board 映射：

```json
{
  "resources": [
    {
      "name": "video_model.wan",
      "amount": 1
    }
  ]
}
```

第一版不做全局并发调度，但字段必须存在，原因是视频生成、TTS、VLM、字幕分析都天然受成本和并发限制。未来做自动执行时，可以从这些字段升级到真实资源池。

### 2.5 Lock Resources

Control-M 的 Lock Resources 分为 Shared 和 Exclusive。Exclusive 用于避免多个任务同时改同一个文件、同一数据库表或同一逻辑对象。

Plan Board 映射：

```json
{
  "locks": [
    {
      "name": "storyboard.srt_0001_01.final_video",
      "mode": "exclusive"
    }
  ]
}
```

StoryBoard 中特别需要锁的操作：

1. 写同一个 `{anchor}_Video_Final.*`。
2. 写同一个 `{anchor}_TailFrame.*`。
3. 同步写 `srt_storyboard.json` 和 `koubo_storyboard_edit.json`。
4. 删除或替换 Job 产物时归档 Asset History。

第一版可以不开放 UI 配置互斥，但后端执行同一 Job 时应默认串行，避免并发覆盖。

### 2.6 Priority / Critical

Control-M Job 有 Priority 和 Critical 概念，用于资源竞争时决定先运行谁。

Plan Board 映射：

```json
{
  "priority": "normal",
  "critical": false
}
```

第一版可以固定为 `normal`，但未来自动执行同一行或多行并行时，必须允许关键路径任务优先拿资源。

## 3. Plan Board 与 Control-M 的边界

Plan Board 不做以下事情：

1. 不引入独立企业调度服务器。
2. 不把文件条件抽象成难以追踪的全局事件表。
3. 不在第一版实现跨行复杂 AND / OR 拓扑。
4. 不让 Execution JSON 成为状态真相。
5. 不让 UI cache 或历史运行状态压过当前 Working 文件。

Plan Board 必须做以下事情：

1. 以 Working 文件作为最小状态事实源。
2. 将每个 Job 的 Wait for Events、Actions、Executing、Ended Not OK、跳过原因显式化。
3. 将 Plan Row 和 Job 作为 Plan Board 可视化结构，而不是状态来源。
4. 每次刷新 Plan Board 都从 Working 文件重新派生 Job Status。
5. 让产物文件命名足够稳定，支持重建 Plan 后恢复状态。
6. 保证一个 Job 只承担一个最小业务产物或一个明确动作。

禁止设计：

```text
一个 Job = Video，内部同时代表 New Video 和 Final Video
```

正确设计：

```text
Job = Video_Raw
Job = Video_Final_Copy
Job = Video_Final_LipSync
Job = Video_Final_AudioMix
Job = TailFrame
```

UI 可以提供一个复合按钮，但底层必须拆成多个 Job。每个 Job 都有自己的 Wait for Events、Actions、Job Status 和标准文件。

## 4. 第一版建议模型

### 4.1 Plan Board

```json
{
  "schema_version": "planboard_0.1",
  "plan_board_id": "storyboard_video_plan",
  "session_id": "6",
  "domain": "storyboard",
  "working_dir": "SessionOutput/storyboard/Working",
  "plan_rows": []
}
```

### 4.2 Plan Row

```json
{
  "plan_row_id": "row_srt_0001_01",
  "row_anchor": "srt_0001_01",
  "row_label": "S1",
  "segment": {
    "segment_id": "shot_001_scene_001_segment_001",
    "dialogue_asset_keys": ["srt_0001_01"]
  },
  "jobs": []
}
```

### 4.3 Job

```json
{
  "job_id": "job_srt_0001_01_video_raw",
  "stage": "Video_Raw",
  "label": "新视频",
  "wait_for_events": [],
  "actions": [],
  "resources": [],
  "locks": [],
  "job_status": {
    "tone": "white",
    "reason": "ready"
  }
}
```

## 5. Job Status 映射

| 颜色 | Control-M 类比 | Plan Board 判定 |
| --- | --- | --- |
| 白 | Waiting | Wait for Events 已满足，尚未运行 |
| 绿 | Ended OK | 标准输出文件存在并有效，必要时已绑定 |
| 红 | Ended Not OK | 当前签名的 Failed 标记存在，且输出未完成 |
| 黄 | Executing / Waiting external task | 当前签名的 Running 标记存在，且输出未完成 |
| 灰 | Waiting prerequisites / Skipped | Wait for Events 不满足，或下游已完成导致无需运行 |

固定优先级：

```text
Ended OK > Executing > Ended Not OK > Waiting white > Waiting gray
```

这个优先级来自 StoryBoard 实现经验：真实业务文件已经落盘时，旧失败、旧运行、旧 skipped 都不能继续染色。

## 6. 对 OpenCrew 的实现启发

1. `Wait for Events` 应默认指向 Working 文件，而不是指向某个工具目录的 Output。
2. `Actions` 必须由工具发布到业务 Working 区，工具自己的 `Sxx_ToolName/Output` 只是审计快照。
3. Job 执行成功必须形成闭环：工具成功、产物发布、业务 JSON 绑定、Job Status 重新派生、UI 变绿。
4. 资源和锁即使第一版不执行，也要进入 schema，避免未来改数据结构。
5. 同一 UI 显示格或按钮可能包含多个底层执行步骤时，必须拆成多个 Job，不允许一个 Job 吞掉多个状态。
6. 状态标记文件应与业务文件同层，便于刷新、恢复、跨浏览器一致显示。
7. 事件命名要机器可读、可由文件路径反推，避免人工命名漂移。

## 7. 建议落地顺序

1. 先实现 Plan Board JSON schema 和 Job Status 派生函数。
2. 再实现 Working 标准命名与 Running / Failed 标记。
3. 再接入 StoryBoard 现有 Image Plan / Video Only Plan / Video Plan。
4. 最后再考虑资源池、互斥锁、跨行依赖和自动并行调度。

第一版验收标准不是“像 Control-M 一样强”，而是“每个 Job 为什么是 Ended OK、Executing、Ended Not OK 或 Waiting，都能从 Workspace 文件、Wait for Events 和 Actions 合同解释清楚”。
