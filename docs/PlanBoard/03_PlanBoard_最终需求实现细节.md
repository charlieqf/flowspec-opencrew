# Plan Board 最终需求实现细节

版本：v0.1

状态：第一版需求细节稿。

## 1. 最终命名边界

本设计采用用户确认后的 Control-M 对齐命名：

| Control-M | OpenCrew 最终命名 | 说明 |
| --- | --- | --- |
| Workspace | Plan Board | Plan 的编排、确认、状态查看和 Job 关系展示界面 |
| Folder | Plan | 一个具体业务计划 |
| Regular Folder | Regular Plan | 普通计划 |
| SMART Folder | SMART Plan | 可继承统一调度和条件规则的计划 |
| Sub-folder | Sub Plan | Plan 下的子计划或子流程 |
| Job | Job | 最小可执行单元 |
| Job Status | Job Status | Job 当前状态 |
| Wait for Events | Wait for Events | Job 执行前等待的事件或产物 |
| Actions | Actions | Job 执行后发布产物、调整事件或后置动作 |

OpenCrew 自有概念：

| 概念 | 说明 |
| --- | --- |
| Session | 创建 Plan 时生成的运行状态容器，保存上下文、状态、输出、报告、执行记录 |
| Workspace | Session 对应的文件系统产出目录，不等同于 Control-M Workspace |
| Plan Row | Plan Board 中按行重复执行同一组 Job 的业务分组，适用于 Segment、Dialogue、文件、场景等重复单元 |

硬性规则：

```text
一个 Job 只能承担一个最小业务产物或一个明确动作。
```

禁止让一个 Job 同时承担多个业务内容，例如：

```text
Job = Video
同时表示 New Video / Video_Raw 和 Final Video / Video_Final
```

必须拆成：

```text
Job = Video_Raw
Job = Video_Final_Copy
Job = Video_Final_LipSync
Job = Video_Final_AudioMix
Job = TailFrame
```

## 2. 范围定义

Plan Board 是一个 Session 内的计划、状态和执行可视化标准。它服务于工具链，而不是替代工具本身。

第一版范围：

1. 一个 Plan 创建一个 Session。
2. Session 内有 `SessionContext`、`SessionOutput`、`SessionReport`。
3. 每个工具有自己的工具目录。
4. 工具可以自动创建或刷新 Plan Board。
5. Plan Board 由多个 Plan Row 组成，每个 Plan Row 是一条串行 Job 链。
6. 每个 Plan Row 由多个 Job 组成。
7. Job Status 由 Workspace 一级文件和状态标记派生。
8. 不处理跨 Plan Row 的 Wait for Events 交叉。
9. 不处理多 Plan Row 并行自动调度。
10. 数据结构预留资源池、互斥锁、优先级和多输入条件。

## 3. 目录结构

标准 Session / Workspace 结构：

```text
<workspace>/
  SessionContext/
    Variables.json
    Video_Source.mp4
    Image_Reference_001.png
    Prompt_FinalPrompt.txt

  SessionOutput/
    <domain>/
      Working/
        PlanBoard.json
        PlanBoardAnchors.json
        ExecutionArchive/
        <row_anchor>_<stage>.<ext>
        <row_anchor>_<stage>_Running_<signature12>_<marker_uid>.json
        <row_anchor>_<stage>_Failed_<signature12>_<marker_uid>.json
      assets/
        history/

  SessionReport/
    <domain>/
      PlanBoard_Report.md

  S01_<ToolName>/
    Working/
    Output/
    Report/
```

约束：

1. `SessionContext` 保存全局变量和全局文件。
2. `SessionOutput/<domain>/Working` 是业务状态事实源，也是 Plan Session Workspace 下的标准产物区。
3. 工具目录只保存执行快照和审计产物。
4. 工具成功后必须把业务产物发布到 `SessionOutput/<domain>/Working`。
5. Plan Board 的 Job Status 不从工具目录直接染色。

## 4. 核心对象

### 4.1 Plan Board

```json
{
  "schema_version": "planboard_0.1",
  "plan_board_id": "storyboard_video_plan",
  "plan_id": "plan_storyboard_video",
  "session_id": "6",
  "domain": "storyboard",
  "created_by_tool": "05_01_VideoPlanGenerator",
  "working_dir": "SessionOutput/storyboard/Working",
  "plan_rows": []
}
```

字段规则：

1. `plan_board_id` 在同一 Session 内唯一。
2. `domain` 决定 Working 目录。
3. `plan_rows` 只表达结构，不制造完成态。
4. 刷新 Plan Board 时，必须重新调用 Job Status 派生服务。

### 4.2 Plan Row

```json
{
  "plan_row_id": "row_srt_0001_01",
  "row_anchor": "srt_0001_01",
  "row_label": "S1",
  "row_kind": "segment",
  "segment": {
    "segment_id": "shot_001_scene_001_segment_001",
    "dialogue_asset_keys": ["srt_0001_01"],
    "representative_dialogue_asset_key": "srt_0001_01"
  },
  "jobs": []
}
```

Plan Row 是 Plan Board 中按行重复执行同一组 Job 的业务分组。`row_anchor` 是该 Plan Row 下所有业务文件和状态标记的稳定前缀。不得使用临时数组下标、前端行号、生成时间。

### 4.3 Job

```json
{
  "job_id": "job_srt_0001_01_video_raw",
  "stage": "Video_Raw",
  "label": "新视频",
  "job_type": "tool_stage",
  "depends_on": ["job_srt_0001_01_video_prompt"],
  "wait_for_events": [],
  "actions": [],
  "resources": [],
  "locks": [],
  "manual_action": null,
  "job_status": {
    "tone": "white",
    "reason": "ready",
    "message": ""
  }
}
```

约束：

1. `stage` 是稳定机器名。
2. `label` 是 UI 文案。
3. `wait_for_events` 判断能否运行。
4. `actions` 声明成功后发布什么产物、调整什么事件或执行什么后置动作。
5. `job_status` 是派生结果，不能被当作持久事实。
6. 一个 Job 只能有一个最小完成标准；如果有多个完成标准，必须拆成多个 Job。

## 5. Wait for Events 模型

```json
{
  "event_id": "event_srt_0001_01_image_new_exists",
  "type": "file_exists",
  "path": "SessionOutput/storyboard/Working/srt_0001_01_Image_New.png",
  "stage": "Image_New",
  "operator": "required",
  "on_missing": "blocked_waiting_input"
}
```

支持类型：

| 类型 | 含义 | 第一版 |
| --- | --- | --- |
| `file_exists` | 文件存在且大小有效 | 必须 |
| `file_bound` | 业务 JSON 绑定指向标准文件 | 按 Job 需要 |
| `manual_confirmed` | 人工确认 | 必须 |
| `marker_absent` | 当前 stage 没有运行冲突 | 必须 |
| `resource_available` | 资源池数量可用 | 预留 |
| `lock_available` | 互斥锁可用 | 预留 |
| `custom_predicate` | 业务函数判断 | 预留 |

多个 Wait for Events 默认 AND。未来可支持：

```json
{
  "logic": "or",
  "events": []
}
```

## 6. 文件命名规则

### 6.1 总规则

所有业务产物使用：

```text
SessionOutput/<domain>/Working/{row_anchor}_{stage}.{ext}
```

所有运行和失败标记使用：

```text
SessionOutput/<domain>/Working/{row_anchor}_{stage}_Running_{signature12}_{marker_uid}.json
SessionOutput/<domain>/Working/{row_anchor}_{stage}_Failed_{signature12}_{marker_uid}.json
```

归档标记使用：

```text
SessionOutput/<domain>/Working/ExecutionArchive/{final_state}_{original_marker_file_name}
```

### 6.2 StoryBoard 归一化命名

StoryBoard 样例归一化如下：

| Stage | 标准业务文件 | 说明 |
| --- | --- | --- |
| `AudioPrompt` | `{anchor}_AudioPrompt.json` | 音频提示词 |
| `Audio_Final` | `{anchor}_Audio_Final.wav` | Dialogue 级最终音频 |
| `SegmentAudio_Final` | `{anchor}_SegmentAudio_Final.wav` | Segment 级合成音频 |
| `Image_Source` | `{anchor}_Image_Source.*` | 原图 / 参考图 |
| `ImagePrompt` | `{anchor}_ImagePrompt.json` | 图像提示词 |
| `Image_New` | `{anchor}_Image_New.*` | 新图 / 视频首帧 |
| `VideoPrompt` | `{anchor}_VideoPrompt.json` | 视频提示词 |
| `Video_Raw` | `{anchor}_Video_Raw.*` | 新视频 / Raw Video |
| `Video_Raw_TailFrame` | `{anchor}_Video_Raw_TailFrame.*` | Raw 诊断尾帧 |
| `Video_Final_Copy` | `{anchor}_Video_Final.*` | Raw 拷贝成终视频 |
| `Video_Final_LipSync` | `{anchor}_Video_Final.*` | 对嘴型产出终视频 |
| `Video_Final_AudioMix` | `{anchor}_Video_Final.*` | 音频替换或混音产出终视频 |
| `Video_Final` | `{anchor}_Video_Final.*` | 当前终视频业务产物 |
| `TailFrame` | `{anchor}_TailFrame.*` | 终视频尾帧，下游继承凭证 |

旧命名迁移规则：

1. `Image_01` 不再作为新实现业务 Job 名。
2. 原图统一为 `Image_Source`。
3. 新图统一为 `Image_New`。
4. 新视频统一为 `Video_Raw`。
5. 终视频统一为 `Video_Final`。
6. 尾帧统一为 `TailFrame`。
7. Prompt 文件独立为 `ImagePrompt.json` / `VideoPrompt.json`。

扩展名规则：

1. 图片允许 `.png`、`.jpg`、`.jpeg`、`.webp`。
2. 音频允许 `.wav`、`.m4a`、`.mp3`。
3. 视频允许 `.mp4`、`.mov`。
4. JSON 固定 `.json`。
5. 工具和 UI 不得假设固定媒体扩展名，必须读取状态服务返回的实际路径。

### 6.3 命名反例

禁止：

```text
shot_001_scene_001_Image_01.png
segment_0_video.mp4
latest_final.mp4
S9_05_02_VideoPlanExecutor/Working/srt_0001_01_Video_Raw.mp4
```

原因：

1. Shot / Scene 编号可能因重排变化。
2. 数组下标不稳定。
3. `latest` 不能表达业务身份。
4. 工具目录不是业务 Working。

## 7. Job Status 标准

Job Status 与 UI 颜色映射：

| Control-M 状态 | UI 颜色 | tone | 含义 |
| --- | --- | --- |
| `Waiting` | 白 | `white` | Wait for Events 已满足，等待运行 |
| `Ended OK` | 绿 | `green` | 标准产物已经完成 |
| `Ended Not OK` | 红 | `red` | 当前签名 Job 失败 |
| `Executing` | 黄 | `yellow` | 当前签名 Job 正在运行 |
| `Waiting` / skipped | 灰 | `gray` | Wait for Events 不满足、等待资源、等待人工确认，或下游已完成导致无需运行 |

固定优先级：

```text
Ended OK > Executing > Ended Not OK > Waiting white > Waiting gray
```

派生顺序：

1. 读取 `PlanBoardAnchors.json` 确定 `row_anchor`。
2. 扫描 Working 一级业务文件。
3. 校验必要业务 JSON 绑定。
4. 计算每个 stage 的 `step_signature`。
5. 匹配同层 Running / Failed 标记。
6. 如果标准产物完成，Job Status 为 `Ended OK`。
7. 如果未完成但 Running 匹配，Job Status 为 `Executing`。
8. 如果未完成但 Failed 匹配，Job Status 为 `Ended Not OK`。
9. 如果 Wait for Events 满足且没有下游产物消费，Job Status 为 `Waiting`，UI 显示白色。
10. 否则 Job Status 仍为 `Waiting`，UI 显示灰色并输出 reason。

灰色必须输出 reason：

| reason | 含义 |
| --- | --- |
| `blocked_waiting_input` | 缺少必要输入，上游完成后可变白 |
| `skipped_consumed_by_downstream` | 下游已完成，本 Job 无需运行 |
| `disabled_by_plan_scope` | 当前 Plan 视图不包含该任务 |
| `manual_action_not_available` | 人工动作条件不足 |

## 8. Step Signature

`step_signature` 用于区分当前输入下的运行 / 失败标记是否仍然有效。

推荐输入：

```text
sha256(
  plan_board_id,
  row_anchor,
  stage,
  normalized_in_condition_paths,
  input_file_fingerprints,
  normalized_out_condition_paths,
  prompt_file_fingerprint,
  model_config_ref,
  binding_fingerprint
)
```

规则：

1. 输入文件变更，签名必须变化。
2. Prompt 文件变更，签名必须变化。
3. 输出目标路径变更，签名必须变化。
4. 只是重新生成 Plan Board，但输入、输出、模型配置不变，签名应保持不变。
5. 旧签名的 Running / Failed 不能染当前 Job。

## 9. 执行生命周期

### 8.1 开始执行

1. 解析 row 和 stage。
2. 校验 `wait_for_events`。
3. 计算 `step_signature` 和 `signature12`。
4. 生成 `marker_uid`。
5. 归档同 row + stage 的旧 Running / Failed。
6. 写入临时 Running 文件。
7. 原子改名为正式 Running 文件。
8. 返回 UI 黄色状态。

### 8.2 外部 API 任务创建

外部 API 返回任务 ID 后必须立即更新 Running 标记：

```json
{
  "external_api_tasks": [
    {
      "provider": "wan",
      "api_name": "video_generation",
      "external_task_id": "task_xxx",
      "last_known_status": "submitted"
    }
  ]
}
```

这样后端重启或页面刷新后，可以恢复查询上游任务。

### 8.3 成功

成功流程：

```text
工具执行成功
-> 写临时业务文件
-> 校验文件
-> 原子发布到 Working 标准路径
-> 同步业务 JSON 绑定
-> 必要时生成 TailFrame
-> 归档 Running 标记
-> 重新派生状态
-> UI 显示绿色
```

成功不创建 Completed 热路径标记。绿色只由业务文件和绑定派生。

### 8.4 失败

失败流程：

```text
执行失败
-> Running 改名为 Failed 或新建 Failed
-> 写入错误摘要
-> 保留 input / output / external_api_tasks
-> 不删除已有业务产物
-> UI 显示红色
```

如果业务文件已经完成，失败不能压过绿色。

### 8.5 重新执行

重新执行前必须归档旧 Running / Failed，不能让旧红黄继续污染当前尝试。

归档命名：

```text
ExecutionArchive/Completed_<old_marker>.json
ExecutionArchive/Failed_<old_marker>.json
ExecutionArchive/ClearedByRetry_<old_marker>.json
ExecutionArchive/ClearedByAssetChange_<old_marker>.json
```

### 8.6 删除或替换产物

用户删除或替换普通 Job 产物时：

1. 清空业务 JSON 中对应绑定。
2. 将标准 Working 文件移入 Asset History。
3. 清理受影响 stage 的 Running / Failed 标记。
4. 不级联删除上游或下游文件。
5. 重新派生 Plan Board 的 Job Status。

Prompt 是例外，不通过普通 Job 产物删除流程删除。

## 10. StoryBoard 第一版 Plan Row 内 Job 链

推荐串行链：

```text
AudioPrompt
-> Audio_Final
-> SegmentAudio_Final
-> Image_Source
-> ImagePrompt
-> Image_New
-> VideoPrompt
-> Video_Raw
-> Video_Raw_TailFrame
-> Video_Final_Copy / Video_Final_LipSync / Video_Final_AudioMix
-> Video_Final
-> TailFrame
```

不同 Plan 视图只是过滤：

| 视图 | 展示 Job |
| --- | --- |
| Image Plan | `Image_Source`、`ImagePrompt`、`Image_New` |
| Video Only Plan | `Audio_Final`、`Image_New`、`VideoPrompt`、`Video_Raw`、`Video_Final_Copy`、`TailFrame` |
| Video Plan | `Audio_Final`、`SegmentAudio_Final`、`Image_New`、`VideoPrompt`、`Video_Raw`、`Video_Final_LipSync` / `Video_Final_AudioMix`、`Video_Final`、`TailFrame` |

注意：

1. 视图可以隐藏不相关 stage，但状态服务仍基于统一链路。
2. 同一 Job 在不同视图中必须显示同一 Job Status。
3. UI 按钮可以执行多个 Job，但返回结果必须拆开。

## 11. 后端服务

建议新增统一模块：

```text
openclip_backend/planboard/
  planboard_schema.py
  anchor_services.py
  condition_services.py
  file_contract_services.py
  status_derivation_services.py
  marker_services.py
  execution_lifecycle_services.py
```

核心函数：

```text
load_plan_board(session_workspace, domain, plan_board_id)
resolve_row_anchor(row, anchors_file)
business_artifact_path(working_dir, row_anchor, stage)
marker_paths(working_dir, row_anchor, stage, signature12)
compute_step_signature(slot, inputs, outputs, bindings, settings)
derive_job_status(job, working_files, markers, bindings)
write_running_marker(job, attempt)
write_failed_marker(job, error)
archive_marker(marker_path, final_state)
publish_business_artifact(tmp_path, target_path)
sync_business_binding(stage, target_path)
clear_slot_artifact(stage, target_path)
```

所有 StoryBoard Plan 入口都应消费同一状态服务：

1. StoryBoard 主界面。
2. Image Plan Modal。
3. Video Only Plan Modal。
4. Video Plan Modal。
5. Confirm Final API。
6. 删除 / 替换 / 绑定素材 API。

## 12. 前端合同

前端只消费后端返回：

```json
{
  "job_id": "job_srt_0001_01_video_raw",
  "stage": "Video_Raw",
  "label": "新视频",
  "tone": "white",
  "reason": "ready",
  "artifact_path": "",
  "running_marker": "",
  "failed_marker": "",
  "actions": [
    {
      "action_id": "run_video_raw",
      "enabled": true
    }
  ]
}
```

前端不得：

1. 自行读取 execution JSON 染色。
2. 因没有 plan item 隐藏固定 Job。
3. 用旧缓存覆盖后端派生状态。
4. 把 Raw 当 Final。
5. 把 SegmentAudio 当 Dialogue Audio。
6. 把多个业务产物合并成一个 Job Status。

前端必须：

1. 固定渲染当前视图的 Job 集合。
2. 按 `tone` 显示颜色。
3. 按 `reason` 显示可读说明。
4. 对人工动作使用明确按钮，例如 Confirm Final、Materialize TailFrame。
5. 对文件存在但绑定缺失显示修复入口。

## 13. 测试范围

最小测试：

1. 空 Working 时 Job 显示白 / 灰。
2. 输入文件补齐后下游灰变白。
3. Running 标记使当前 stage 变黄。
4. Failed 标记使当前 stage 变红。
5. 标准业务文件存在时覆盖旧红黄变绿。
6. 旧签名 Running / Failed 不染当前 Job。
7. 删除 Job 产物只影响目标文件。
8. Raw 存在不等于 Final 完成。
9. Final 存在时上游缺失显示灰色无需运行。
10. Prompt 存在即绿，普通删除不移除 Prompt。
11. TailFrame 缺失时下游继承不可用。
12. 双 JSON 绑定失败时不显示正常绿色。
13. 工具目录产物未发布到业务 Working 时不显示绿色。
14. UI 三个 Plan 视图同一 stage 状态一致。

## 14. 迁移策略

1. 保留现有 StoryBoard JSON 和 execution JSON。
2. 新增 Plan Board JSON 和 Job Status 派生服务。
3. 先只读接入：从现有 Working 文件派生状态，不改变执行器。
4. 再接入 Running / Failed 标记。
5. 再把执行器发布路径收敛到标准命名。
6. 最后清理旧 fallback 和旧 `Image_01` 解释逻辑。

旧文件兼容：

1. 读取旧 `Image_01` 时只能作为迁移提示，不能直接当 `Image_New` 完成。
2. 如果旧 Final 文件已存在但未绑定，进入修复态。
3. 如果旧 execution JSON 显示成功但文件不存在，不显示绿色。
4. 如果工具目录有结果但业务 Working 没有，提示发布缺失，不显示完成。

## 15. 第一版完成定义

Plan Board 第一版完成的标准：

1. 能从 Session 自动生成 Plan Board。
2. 能解释每个 Plan Row 为什么存在。
3. 能解释每个 Job 的 Wait for Events 和 Actions。
4. 能从 Working 一级文件派生五色状态。
5. 能刷新后恢复黄 / 红。
6. 能成功后以业务文件自然变绿。
7. 能避免一个 Job 代表多个任务的状态混乱。
8. 能把 StoryBoard 的命名规则归一化为稳定文件合同。
9. 能为未来资源池、互斥锁、跨行依赖留下 schema。
