# Analysis_V1 04_03_StoryBoardQuick 工具需求整理

版本：v0.1

状态：需求草案。本文用于指导 `04_03_StoryBoardQuick.py`、`00_PrepareSessionVariables.py`、Analysis_V1 运行弹窗和后端 run-to-storyboard 编排的实现。

## 1. 背景

当前 `04_02_StoryBoard.py` 通过文本大模型，把 `SessionOutput/subtitle/rewritten_srt_items.json` 组织成 `Shot -> Scene -> Dialogue` 的 StoryBoard。

现在需要新增一个不调用大模型的快速 StoryBoard 工具：

```text
04_03_StoryBoardQuick.py
```

该工具使用确定性算法，根据默认节奏把改写后的 SRT 分组：

```text
Scene 目标时长：8 秒
Shot 目标时长：16 秒
默认容忍度：2 秒
```

但分组不能机械按秒切断，必须尽量保持语言完整。也就是说，工具应优先在自然句边界、语义停顿边界、段落边界拆分；只有在超出容忍度且没有更好边界时，才使用降级边界。

## 2. 当前截图参数存储位置

截图里的 `VideoPlan Params` 当前不属于 Analysis_V1 04_02/04_03 StoryBoard 参数。

当前实现位置是口播 StoryBoard 页面的视频生成计划参数：

```text
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardVideoPlan.js
OpenCrew/OpenClip/frontend/src/KouboStoryBoard/components/KouboTimeline.jsx
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/video_plan_signature_services.py
OpenCrew/OpenClip/backend/openclip_backend/koubo_storyboard/tool_runner_services.py
```

其数据流是：

1. 前端默认值在 `DEFAULT_VIDEO_PLAN_SETTINGS` 中定义。
2. 用户在 `KouboTimeline.jsx` 的 popover 中修改。
3. 点击 VideoPlan 时，前端把 `settings` 放进 `/api/koubo-storyboard/tasks/{task_id}/video-plan` 请求体。
4. 后端归一化后，把参数作为 CLI 参数传给 `05_01_VideoPlanGenerator.py`：

```text
--max-video-seconds
--min-video-seconds
--split-tolerance-seconds
```

结论：

1. 这些值当前主要是前端临时状态 + 单次请求参数。
2. 它们不会被 `00_PrepareSessionVariables.py` 读取。
3. 它们不会进入 `SessionContext/Variables.json`。
4. 它们是 05_01 视频生成计划参数，不是 04_02/04_03 StoryBoard 分组参数。

## 3. 04_03 参数命名与 UI 所属位置

`04_03_StoryBoardQuick.py` 应使用独立参数名，避免和 05_01 VideoPlan 参数混淆。

这些参数应放在 Prompt Builder 的 `StoryBoard` Tab 下，而不是放在 StoryBoard 运行弹窗或口播 StoryBoard 页面底部的 VideoPlan 参数里。

推荐 UI 位置：

```text
Prompt Builder
  SRT Rewrite
  StoryBoard
    StoryBoard 简单提示词
    StoryBoard Quick Params
      Scene: 8s
      Shot: 16s
      Tolerance: 2s
      Boundary: balanced
    StoryBoard 最终提示词
```

`StoryBoard Quick Params` 和 `storyboard_simple_prompt` 属于同一个 StoryBoard 配置块：

1. 用户修改 StoryBoard 参数后，应同步刷新或重建 `storyboard_simple_prompt`。
2. 保存 Prompt Builder 时，应同时保存 `storyboard_simple_prompt` 和 Quick 参数。
3. 生成 `storyboard_final_prompt` 时，最终提示词中应明确包含这些 Quick 参数，用于记录 StoryBoard 结构目标。
4. 即使后续选择 `04_03_StoryBoardQuick.py` 不调用大模型，这些参数仍是本次 StoryBoard 任务设计的一部分，应被 00 快照进 `SessionContext/Variables.json`。

推荐字段：

```json
{
  "storyboard_quick_config": {
    "enabled": true,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced",
    "source": "openclip_tasks.storyboard_quick_config_json"
  }
}
```

默认值：

```text
target_scene_seconds = 8.0
target_shot_seconds = 16.0
split_tolerance_seconds = 2.0
language_boundary_mode = balanced
```

参数含义：

1. `target_scene_seconds`：Scene 目标时长，默认 8 秒。
2. `target_shot_seconds`：Shot 目标时长，默认 16 秒。
3. `split_tolerance_seconds`：Scene 和 Shot 分组的默认容忍度，默认 2 秒。
4. `language_boundary_mode`：语言完整度策略。第一版支持 `strict`、`balanced`、`loose`，默认 `balanced`。

## 4. 由 00 读入 Session Variable 的设计

### 4.1 需要先有持久化来源

如果希望 00 把 Quick StoryBoard 参数写入 `SessionContext/Variables.json`，参数必须先存在于 00 能读取的持久化来源中。

当前 00 的主要数据来源是：

```text
openclip_tasks
sessions
```

因此推荐把 Quick StoryBoard 参数作为 Prompt Builder 的 StoryBoard 配置保存到 `openclip_tasks`，和 `storyboard_simple_prompt` / `storyboard_final_prompt` 同级。

这比放在运行弹窗更好：

1. 参数会随 Prompt Builder 保存，刷新页面不丢。
2. 参数能参与简单提示词生成，用户看到的 StoryBoard 意图和实际 04_03 算法一致。
3. 00 可以一次性读取完整 StoryBoard 配置并写入 Session Variable。
4. run-to-storyboard 只负责执行，不负责偷偷改变 StoryBoard 设计参数。

### 4.2 推荐 DB 字段

推荐新增一个 JSON 字段：

```text
openclip_tasks.storyboard_quick_config_json TEXT
```

不推荐一开始就拆成多个列：

```text
storyboard_quick_target_scene_seconds
storyboard_quick_target_shot_seconds
storyboard_quick_split_tolerance_seconds
storyboard_quick_language_boundary_mode
```

原因是 Quick StoryBoard 后续可能继续增加：

1. 标点权重。
2. 中文/英文边界策略。
3. 短句合并策略。
4. 超长句降级策略。
5. Shot 起止命名策略。

JSON 字段能减少后续迁移成本。

### 4.3 00 写入 Variables 的结构

`00_PrepareSessionVariables.py` 应在 `fetch_task_context()` 中读取：

```sql
t.storyboard_quick_config_json
```

然后在 `build_variables()` 中写入：

```json
{
  "storyboard_quick_config": {
    "enabled": true,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced",
    "source": "openclip_tasks.storyboard_quick_config_json",
    "normalized_at": "2026-06-02T00:00:00Z"
  }
}
```

如果 DB 字段为空或非法，00 写入默认值，并在 `S1_00_PrepareSessionVariables/Report/Result.json` 中记录 warning：

```json
{
  "code": "storyboard_quick_config_defaulted",
  "message": "storyboard_quick_config_json was missing or invalid; defaults were written to SessionContext/Variables.json."
}
```

### 4.4 与 StoryBoard 简单提示词同步

Prompt Builder 中的 StoryBoard 简单提示词应由 StoryBoard 基础字段和 Quick 参数共同生成。

推荐简单提示词中包含类似内容：

```text
请基于以下业务参数，生成一份用于 SRT StoryBoard 结构化任务的最终提示词。
StoryBoard 结构参数：
- Scene 目标时长：8 秒
- Shot 目标时长：16 秒
- 分割容忍度：2 秒
- 语言边界策略：balanced，优先保持自然句和语义完整
```

同步规则：

1. 用户修改 `target_scene_seconds`、`target_shot_seconds`、`split_tolerance_seconds`、`language_boundary_mode` 后，前端应重新构建 `storyboard_simple_prompt`。
2. 用户手动编辑 `storyboard_simple_prompt` 后，参数控件不应被反向猜测覆盖；参数控件仍以结构化字段为准。
3. 保存时，后端同时保存 `storyboard_simple_prompt`、`storyboard_final_prompt`、`storyboard_quick_config_json`。
4. 00 只从 DB 的结构化字段读取参数，不从自然语言 prompt 里解析参数。

### 4.5 运行时覆盖

原则上不推荐 run payload 临时覆盖 Quick 参数。Quick 参数应先在 Prompt Builder 的 StoryBoard Tab 保存，再点击 Run。

推荐优先级：

```text
openclip_tasks.storyboard_quick_config_json
> 00 内置默认值
```

如果产品上必须支持运行时临时覆盖，后端启动 run 前也必须先把 override 写回 `openclip_tasks.storyboard_quick_config_json`，然后再启动 00。

否则 00 已经运行完以后，后续再把参数塞给 04_03，会导致：

1. `Variables.json` 不是真实运行快照。
2. 04_03 的 `Result.json` 和 SessionContext 不一致。
3. resume/cache 判断更容易误复用旧结果。

## 5. 工具定位

`04_03_StoryBoardQuick.py` 是 `04_02_StoryBoard.py` 的确定性替代工具，不是 `05_01_VideoPlanGenerator.py` 的替代工具。

主链路有两种 StoryBoard 模式：

```text
model 模式：
00 -> 01 -> 02_01 -> 02_02 -> 03_02/03_01 -> 04_01 -> 04_02

quick 模式：
00 -> 01 -> 02_01 -> 02_02 -> 03_02/03_01 -> 04_01 -> 04_03
```

两种模式都写同一个业务输出：

```text
SessionOutput/storyboard/srt_storyboard.json
```

所以后续口播 StoryBoard 页面、`05_01_VideoPlanGenerator.py`、`05_02_VideoPlanExecutor.py` 不需要根据 04_02/04_03 分叉读取不同文件。

## 6. 输入

必需输入：

```text
SessionContext/Variables.json
SessionOutput/subtitle/rewritten_srt_items.json
```

其中 `rewritten_srt_items.json` 必须包含：

```text
items[]
items[].srt_id
items[].dialogue
items[].start
items[].end
items[].duration
items[].image_path
```

可选输入：

```text
SessionContext/Variables.json -> storyboard_quick_config
CLI 参数 override
```

## 7. 输出

工具目录：

```text
S7_04_03_StoryBoardQuick/
```

工作副本：

```text
S7_04_03_StoryBoardQuick/Working/InputFrom_0_Variables.json
S7_04_03_StoryBoardQuick/Working/InputFrom_6_rewritten_srt_items.json
S7_04_03_StoryBoardQuick/Working/InputParams_storyboard_quick_config.json
S7_04_03_StoryBoardQuick/Working/State_grouping_audit.json
```

工具输出：

```text
S7_04_03_StoryBoardQuick/Output/srt_storyboard.json
S7_04_03_StoryBoardQuick/Output/grouping_audit.json
S7_04_03_StoryBoardQuick/Report/Result.json
```

Session 输出：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/assets/images/
SessionOutput/storyboard/assets/videos/
SessionOutput/storyboard/Working/
```

04_03 不创建 `Prompt/` 目录，不写 `model_response.json`，不调用 OpenCode run model。

## 8. 输出结构合同

`SessionOutput/storyboard/srt_storyboard.json` 必须兼容 04_02 当前结构：

```text
schema_version = analysis_v1_srt_storyboard_0.2
shots[]
shots[].shot_id
shots[].title
shots[].start
shots[].end
shots[].duration
shots[].srt_ids
shots[].key_frame_paths
shots[].scenes[]
shots[].scenes[].scene_id
shots[].scenes[].title
shots[].scenes[].start
shots[].scenes[].end
shots[].scenes[].duration
shots[].scenes[].srt_ids
shots[].scenes[].dialogue_items
shots[].scenes[].key_frame_paths
shots[].scenes[].asset_key
shots[].scenes[].working_assets
```

差异字段：

```json
{
  "tool": "04_03_StoryBoardQuick",
  "model": {
    "provider": "none",
    "model": "deterministic_storyboard_quick",
    "source": "algorithm"
  },
  "storyboard_mode": "quick",
  "algorithm_config": {
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced"
  },
  "source_signature": {
    "rewritten_srt_items_sha256": "...",
    "algorithm_config_sha256": "..."
  }
}
```

## 9. 语言完整分组算法

### 9.1 基本原则

1. 不改变 `srt_id`。
2. 不改变对白文本。
3. 不改变 `start`、`end`、`duration`。
4. 不改变 `image_path` 绑定。
5. 每个输入 item 必须被覆盖且只被覆盖一次。
6. Scene 和 Shot 的 `srt_ids` 必须连续且保持输入顺序。
7. Scene 优先在语言完整边界结束。
8. Shot 优先由完整 Scene 组成，不拆 Scene。

### 9.2 Scene 分组

Scene 目标窗口：

```text
min_scene = target_scene_seconds - split_tolerance_seconds = 6 秒
max_scene = target_scene_seconds + split_tolerance_seconds = 10 秒
```

从当前未分组的第一条 SRT 开始累积，枚举每个可切分边界，给边界打分。

候选边界包括：

1. 当前 item 后面是硬句末。
2. 当前 item 后面是软停顿。
3. 当前 item 和下一 item 之间存在明显话题转折。
4. 当前 item 已达到或接近目标时长。

硬句末：

```text
。 ！ ？ ! ? …… …
```

软停顿：

```text
， , ； ; ： :
```

不推荐切分的边界：

1. 当前文本以未闭合引号、括号结尾。
2. 当前文本明显是上半句，例如以“但是 / 因为 / 所以 / 然后 / 接下来 / 如果 / 只要 / 而且 / 并且”等连接词结尾。
3. 下一句明显承接上一句，例如以“所以 / 因此 / 但是 / 那么 / 然后 / 也就是说 / 换句话说”开头。
4. 当前 Scene 时长还明显低于 `min_scene`，且后续还有 item。

推荐评分：

```text
score = duration_score + boundary_score + transition_score - penalty_score
```

其中：

1. `duration_score`：越接近 `target_scene_seconds` 越高。
2. `boundary_score`：硬句末 > 软停顿 > 普通边界。
3. `transition_score`：话题转折、段落转折、强钩子句后可加分。
4. `penalty_score`：低于最小时长、未闭合标点、连接词断裂、超长惩罚。

选择规则：

1. 如果存在 `[min_scene, max_scene]` 内的候选，选择最高分。
2. 如果已超过 `max_scene`，选择从 `min_scene` 之后到当前位置之间最高分的边界。
3. 如果没有任何语言边界且已超过 `max_scene`，选择当前位置作为降级边界，并在 audit 中记录 `forced_boundary`。
4. 如果最后剩余时长低于 `min_scene`，优先合并到前一个 Scene；如果没有前一个 Scene，则允许最后一个短 Scene。

### 9.3 Shot 分组

Shot 目标窗口：

```text
min_shot = target_shot_seconds - split_tolerance_seconds = 14 秒
max_shot = target_shot_seconds + split_tolerance_seconds = 18 秒
```

Shot 只在 Scene 边界上分组。

推荐规则：

1. 先完成 Scene 分组。
2. 从第一个 Scene 开始累积 Shot。
3. 优先选择接近 `target_shot_seconds` 的 Scene 边界。
4. 如果两个 Scene 合计约 16 秒，默认形成一个 Shot。
5. 如果一个 Scene 已经超过 `max_shot`，允许单 Scene Shot，并记录 warning。
6. 如果最后剩余 Shot 低于 `min_shot`，优先合并到前一个 Shot；如果合并后明显超过 `max_shot`，保留为短 Shot 并记录 warning。

## 10. CLI 参数

推荐 CLI：

```text
python3 OpenCrew/ToolLibrary/Analysis_V1/04_03_StoryBoardQuick.py \
  --workspace <workspace> \
  --target-scene-seconds 8 \
  --target-shot-seconds 16 \
  --split-tolerance-seconds 2 \
  --language-boundary-mode balanced \
  --force \
  --print-json
```

参数：

```text
--workspace
--target-scene-seconds
--target-shot-seconds
--split-tolerance-seconds
--language-boundary-mode strict|balanced|loose
--force
--resume
--print-json
```

如果未传 CLI 参数，读取：

```text
SessionContext/Variables.json -> storyboard_quick_config
```

如果 Variables 中也没有，则使用内置默认值。

## 11. Result.json 合同

成功：

```json
{
  "tool": "04_03_StoryBoardQuick",
  "tool_version": "0.1.0",
  "status": "completed",
  "requires_database": false,
  "requires_model_calls": false,
  "model_call_policy": {
    "text_model": "not_used",
    "visual_model": "not_used",
    "prompt_dir": "not_created"
  },
  "inputs": {
    "variables": "SessionContext/Variables.json",
    "rewritten_srt_items": "SessionOutput/subtitle/rewritten_srt_items.json"
  },
  "outputs": {
    "srt_storyboard": "SessionOutput/storyboard/srt_storyboard.json",
    "grouping_audit": "S7_04_03_StoryBoardQuick/Output/grouping_audit.json"
  },
  "counts": {
    "input_items": 0,
    "shots": 0,
    "scenes": 0,
    "forced_boundaries": 0
  },
  "algorithm_config": {
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced"
  },
  "warnings": []
}
```

阻塞条件：

1. `SessionContext/Variables.json` 缺失。
2. `SessionOutput/subtitle/rewritten_srt_items.json` 缺失。
3. `items[]` 为空。
4. `srt_id` 缺失或重复。
5. `start/end/duration` 非法，无法计算时间。
6. 输出覆盖校验失败。

## 12. Run 按钮接入需求

### 12.1 前端运行弹窗

当前 Analysis_V1 运行弹窗是“运行至 04_02”。需要改成 StoryBoard 模式可选：

```text
StoryBoard
[ model 04_02 大模型分组 ] [ quick 04_03 快速分组 ]
```

运行弹窗不是 Quick 参数的主编辑入口。Quick 参数主编辑入口在 Prompt Builder 的 `StoryBoard` Tab 下，和 `storyboard_simple_prompt` 同步保存。

当选择 `model`：

1. 保持现有 Provider/Model 必填。
2. 按原链路运行到 04_02。
3. 仍要求 `storyboard_final_prompt` 存在。

当选择 `quick`：

1. Provider/Model 对 04_03 不再必填；但 04_01 仍需要 run model，所以如果链路包含 04_01，运行模型仍要保留。
2. 运行弹窗只显示已保存的 Quick 参数摘要，不在这里编辑参数：

```text
Scene 8s
Shot 16s
Tolerance 2s
Boundary balanced
```

3. 如果用户需要修改参数，应回到 Prompt Builder -> StoryBoard Tab 修改并保存。
4. 按 quick 链路运行到 04_03。
5. 文案建议改为：

```text
Run StoryBoard
运行至 StoryBoard
```

不要再固定写 `Run to 04_02`。

### 12.2 后端 payload

`OpenClipAnalysisV1RunPayload` 新增：

```python
storyboard_mode: str = "model"
```

允许值：

```text
storyboard_mode = model | quick
```

后端归一化：

```text
model, 04_02, llm -> model
quick, 04_03, deterministic -> quick
```

不推荐在 run payload 中携带 `storyboard_quick_config`。后端应从 `openclip_tasks.storyboard_quick_config_json` 读取已保存参数，并由 00 写入 `SessionContext/Variables.json`。如果短期实现必须携带参数，也必须在启动 00 前写回 task 配置。

### 12.3 后端编排

`analysis_v1_run_step_specs()` 接受：

```python
analysis_v1_run_step_specs(tts_builder_mode: str = "quick", storyboard_mode: str = "model")
```

当 `storyboard_mode == "model"`：

```text
最后一步 = 04_02_StoryBoard.py
```

当 `storyboard_mode == "quick"`：

```text
最后一步 = 04_03_StoryBoardQuick.py
```

`analysis_v1_step_command()`：

1. `04_02` 保持传 `--model-provider/--model-id`。
2. `04_03` 不传模型参数。
3. `04_03` 传：

```text
--target-scene-seconds
--target-shot-seconds
--split-tolerance-seconds
--language-boundary-mode
```

### 12.4 事件和状态命名

推荐把已有事件中的 `run_to_04_02` 升级为更中性的：

```text
analysis_v1.run_to_storyboard.*
```

短期兼容可以继续保留原事件名，但状态摘要必须包含：

```json
{
  "storyboard_mode": "quick",
  "storyboard_step_id": "04_03"
}
```

## 13. tool_registry 需求

新增：

```json
{
  "id": "04_03",
  "name": "04_03_StoryBoardQuick",
  "script": "ToolLibrary/Analysis_V1/04_03_StoryBoardQuick.py",
  "stage": "storyboard",
  "required_by_default": false,
  "cost_level": "low",
  "uses_llm": false,
  "uses_vlm": false,
  "supports_resume": true,
  "hard_dependencies": ["04_01"],
  "soft_dependencies": [],
  "estimated_runtime": {
    "basis": "deterministic language-aware duration grouping",
    "relative": "low"
  },
  "main_outputs": ["SessionOutput/storyboard/srt_storyboard.json"],
  "writes_session_context": []
}
```

`05_01` 的实际依赖应表达为：

```text
SessionOutput/storyboard/srt_storyboard.json exists
```

而不是只认某个固定 step id。 registry 如果暂时不支持 OR dependency，可保留 `hard_dependencies: ["04_02"]`，但运行编排和 UI 必须允许 quick 模式通过 04_03 产生同一文件后继续。

## 14. 验收标准

### 14.1 参数与 00

1. Prompt Builder 的 StoryBoard Tab 显示 Quick 参数。
2. 修改 Quick 参数后，`storyboard_simple_prompt` 同步更新。
3. 保存 Prompt Builder 后，刷新页面仍能看到保存值。
4. 保存 Prompt Builder 后，DB 中保留 `storyboard_quick_config_json`。
5. 点击 Run StoryBoard 后，00 写入 `SessionContext/Variables.json -> storyboard_quick_config`。
6. `S1_00_PrepareSessionVariables/Output/Variables.json` 与 `SessionContext/Variables.json` 中的 Quick 参数一致。
7. 参数为空或非法时，00 写入默认值并记录 warning。

### 14.2 04_03 工具

1. 不创建 Prompt 目录。
2. 不调用 OpenCode run model。
3. `Result.json.requires_model_calls = false`。
4. 输出 `SessionOutput/storyboard/srt_storyboard.json`。
5. 输出结构可被口播 StoryBoard 页面打开。
6. 输出结构可被 `05_01_VideoPlanGenerator.py` 消费。
7. 所有 `srt_id` 覆盖一次且只覆盖一次。
8. Scene / Shot 的 `srt_ids` 连续且有序。
9. 默认分组尽量接近 Scene 8 秒、Shot 16 秒。
10. 在 2 秒容忍度内优先选择语言完整边界。
11. 必要降级切分必须写入 `grouping_audit.json`。

### 14.3 Run 按钮

1. 选择 model 模式时，链路运行至 04_02。
2. 选择 quick 模式时，链路运行至 04_03。
3. Run 弹窗显示已保存的 Quick 参数摘要，但不作为主要编辑入口。
4. 进度弹窗显示真实步骤名，不应把 quick 模式显示为 04_02。
5. quick 模式完成后，“故事板”按钮可打开同一个口播 StoryBoard 页面。
6. run summary 和 session events 能区分 `storyboard_mode=model|quick`。

## 15. 第一版实现建议

第一版建议按以下顺序实现：

1. 新增 `04_03_StoryBoardQuick.py`，先支持 CLI + 默认参数 + Variables 参数。
2. 新增工具 registry 条目。
3. 新增 `openclip_tasks.storyboard_quick_config_json` 字段和 bootstrap 补列。
4. 扩展 `OpenClipTaskConfigPayload`、保存接口、task detail 返回。
5. 修改 Prompt Builder 的 StoryBoard Tab，加入 Quick 参数控件，并与 `storyboard_simple_prompt` 同步。
6. 修改 00 读取并写入 `storyboard_quick_config`。
7. 修改 run payload 和后端 step specs，支持 `storyboard_mode=quick`。
8. 修改 Analysis_V1 运行弹窗，支持选择 StoryBoard 模式，并显示已保存 Quick 参数摘要。
9. 增加 contract tests：
   - 04_03 no model call contract。
   - run-to-storyboard quick step chain contract。
   - frontend saves StoryBoard Quick config with simple prompt contract。
   - 00 writes storyboard_quick_config contract。
