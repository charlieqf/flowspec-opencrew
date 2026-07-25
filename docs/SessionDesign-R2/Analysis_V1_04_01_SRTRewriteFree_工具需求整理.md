# Analysis_V1 04_01_SRTRewriteFree 工具需求整理

版本：v0.1

状态：最终确认需求。本文用于指导 `04_01_SRTRewriteFree.py`、Analysis_V1 Prompt Builder、`00_PrepareSessionVariables.py`、run-to-storyboard 编排和前端一键重跑按钮的实现。

## 1. 最终确认点

本需求已经确认以下边界：

1. `04_01_SRTRewriteFree` 是新增工具，不替换现有 `04_01_SRTRewrite`。
2. 新工具的输出链路必须对齐旧 `04_01_SRTRewrite` 和通用工具输出标准：本工具 `Output/` 保存交给下游消费的局部最终产物，`SessionOutput/` 同步页面和后续 workflow 消费的全局最终产物。业务最终输出仍覆盖现有下游路径：

```text
SessionOutput/subtitle/rewritten_srt_items.json
```

3. 不新增 Prompt 存储、不新增配置表、不新增新的业务中间产物。
4. 新工具完全遵从 `rewrite_prompt.final_prompt`，允许句数变多、变少、合并、拆分、重写。
5. 下游 StoryBoard 以新 `rewritten_srt_items.json` 的顺序为准。
6. 新 SRT 的时间不按原 SRT 总时长补齐；应按 TTS Builder 参考片段推算单字速度，再乘以每句字数计算每句时长。
7. 红框按钮的标准链路为：

```text
保存当前 Prompt Builder 配置
-> 运行 00_PrepareSessionVariables
-> 运行 04_01_SRTRewriteFree
-> 运行 04_02_StoryBoard
```

8. 新工具不得读取任何数据库。所有业务输入、模型配置、OpenCode/ModelBroker 调用所需信息必须来自 `SessionContext/Variables.json`、Runner/CLI 注入或平台 broker/resolver；如果缺少必要 runtime 信息，返回 `blocked`，不得自行查询 DB。

## 1.1 通用工具确认项对齐

本工具同时遵守通用 Tool Use Session / Analysis_V1 工具撰写规则：

1. CLI：支持现有 `--workspace` 兼容入口，并预留/兼容框架入口 `--tool-session-root`、`--step-id`、`--tool-id`、`--print-json`、`--force-rerun`。
2. 输入边界：只读取当前 session workspace / tool session root 内的受控文件，不读取数据库，不读取个人机器路径，不读取全局状态。
3. SessionContext：只读 `SessionContext/Variables.json`，不写回 `Variables.json`，不新增全局 SessionContext 文件。
4. Working：只保存输入快照、TTS 参考片段解析快照、语速计算快照和断点状态。
5. Prompt：所有模型调用前必须先把完整 prompt 写入 `Prompt/`，模型调用只能从落盘 prompt 读取；模型请求/响应审计必须留在 `Prompt/` 或受控审计文件中。
6. Output：保存本工具交给下游消费的局部最终产物，例如 `Output/rewritten_srt_items.json`、`Output/rewritten_dialogue.srt`、`Output/model_response.json` 和 `Output/OutputManifest.json`。
7. SessionOutput：只同步页面和后续工具需要读取的全局最终产物，例如 `SessionOutput/subtitle/rewritten_srt_items.json` 和 `SessionOutput/subtitle/rewritten_dialogue.srt`。
8. Report：`Report/Result.json` 只保存运行结果、错误、计数、输入输出路径、warning 和人工排查信息，不替代 `OutputManifest.json`。
9. blocked：缺少必要输入、prompt、runtime 或模型输出不可解析时，返回标准 blocked/failed 结果；句数变化、srt_id 变化、合并拆分不属于失败。
10. force：只清理本工具拥有的 `S6_04_01_SRTRewriteFree/Working`、`Prompt`、`Output`、`Report` 以及本工具明确拥有的 rewritten subtitle SessionOutput 文件，不清理其他 step、`SessionContext`、StoryBoard 输出或历史 attempt。
11. registry：需要在 Analysis_V1 工具注册/运行计划中声明新工具、依赖、主输出、是否使用 LLM、是否支持 resume、成本等级和下游输出。
12. 安全：Prompt、Report、Output、stdout/stderr 不得写入 API key、数据库连接串、cookie、Authorization header、access token 等敏感信息。

## 2. 背景

现有 `04_01_SRTRewrite.py` 是严格逐句改写工具，核心约束是：

```text
句数不能变
srt_id 不能变
顺序不能变
不能合并
不能拆分
不能新增
不能删除
```

这个约束适合“保留原 SRT 结构，只替换表达内容”的场景。

现在新增需求是“自由改写 SRT 并重新生成 StoryBoard”。用户可以在 Prompt Builder 里要求模型按新的表达节奏、销售逻辑、口播结构生成新的 SRT。新 SRT 可以比原 SRT 更短或更长，也可以合并、拆分或重新组织句子。

因此需要新增：

```text
04_01_SRTRewriteFree.py
```

它和旧工具并存：

1. `04_01_SRTRewrite.py`：严格逐句改写。
2. `04_01_SRTRewriteFree.py`：自由改写，完全遵从提示词。

## 3. 工具定位

`04_01_SRTRewriteFree.py` 的职责是：

1. 读取 `SessionContext/Variables.json`。
2. 读取原始可参考的 SRT/对白材料。
3. 读取 `rewrite_prompt.final_prompt`。
4. 调用文本大模型生成新的 rewritten SRT。
5. 将模型输出标准化为下游 `04_02_StoryBoard.py` 可消费的 `rewritten_srt_items.json`。
6. 按参考语速为每句新 SRT 计算 `start/end/duration`。

该工具不负责：

1. 生成 StoryBoard。
2. 改写 StoryBoard prompt。
3. 新建 Prompt 存储。
4. 新建新的业务中间 JSON 作为下游入口。
5. 复用旧 `rewritten_srt_items.json`。
6. 查询数据库获取 Task、Session、Attempt、Prompt Version、OpenCode runtime 或业务状态。

## 4. Prompt Builder 与存储要求

### 4.1 继续使用现有 Prompt Builder

新工具不引入新的 Prompt Builder，也不引入新字段。

继续使用现有字段：

```text
rewrite_simple_prompt
rewrite_final_prompt
storyboard_simple_prompt
storyboard_final_prompt
storyboard_quick_config_json
```

前端保存时继续走现有链路：

```text
normalizePromptBundle
-> saveConfig
-> openclip_tasks
```

Prompt Builder 生成最终提示词时继续走现有链路：

```text
saveConfig
-> generatePrompt(prompt_kind = rewrite 或 storyboard)
-> 回写对应 final prompt
```

### 4.2 00 仍是 Variables 唯一装载入口

`00_PrepareSessionVariables.py` 仍然是唯一的 Session Variables 装载入口。

运行 `04_01_SRTRewriteFree` 前必须先运行 `00`，确保最新配置被写入：

```json
{
  "rewrite_prompt": {
    "simple_prompt": "...",
    "final_prompt": "...",
    "source": "openclip_tasks.rewrite_final_prompt"
  },
  "storyboard_prompt": {
    "simple_prompt": "...",
    "final_prompt": "...",
    "source": "openclip_tasks.storyboard_final_prompt"
  },
  "storyboard_quick_config": {
    "enabled": true,
    "target_scene_seconds": 8.0,
    "target_shot_seconds": 16.0,
    "split_tolerance_seconds": 2.0,
    "language_boundary_mode": "balanced"
  }
}
```

## 5. 输入要求

### 5.1 必需输入

```text
SessionContext/Variables.json
SessionOutput/subtitle/final_srt_frame_items.json
```

`final_srt_frame_items.json` 作为源对白参考材料，不再作为输出结构约束。

### 5.2 推荐读取的参考信息

工具可以从以下数据读取参考信息：

```text
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/subtitle/calibrated_srt_items.json
SessionContext/ASR_Segments.json
SessionContext/ASR_Raw.srt
SessionOutput/tts/tts_builder_candidates.json
SessionContext/Variables.json
```

读取优先级应保持稳定，但不得因为参考数据缺少某个可选字段而阻断工具运行。

## 6. 模型调用要求

### 6.1 模型来源

模型配置沿用现有 Analysis_V1 run model：

```text
CLI override
-> rewrite_model_config
-> run_model_provider / run_model_id
```

### 6.2 Prompt 结构

Prompt 必须包含：

1. `rewrite_prompt.final_prompt`。
2. 原 SRT/对白参考材料。
3. 明确说明本工具为自由改写模式。
4. 明确说明模型可以按最终提示词要求调整句数、合并、拆分、删减、扩写。
5. 要求模型只输出严格 JSON。

Prompt 不应再写入以下旧规则：

```text
必须保持 srt_id 完全一致
必须保持句数完全一致
不得合并
不得拆分
不得新增
不得删除
```

### 6.3 推荐模型输出 JSON

模型输出推荐格式：

```json
{
  "items": [
    {
      "dialogue": "新的第一句口播",
      "note": "可选，说明这一句的表达功能"
    },
    {
      "dialogue": "新的第二句口播"
    }
  ]
}
```

模型也可以输出：

```json
{
  "items": [
    {
      "srt_id": "custom_001",
      "dialogue": "新的第一句口播",
      "start": 0,
      "end": 2.4,
      "duration": 2.4
    }
  ]
}
```

如果模型输出了 `srt_id/start/end/duration`，工具可以读取，但必须重新归一化和校验。

## 7. 输出要求

### 7.1 业务输出路径

新工具必须先写本工具局部最终产物：

```text
S6_04_01_SRTRewriteFree/Output/rewritten_srt_items.json
S6_04_01_SRTRewriteFree/Output/rewritten_dialogue.srt
S6_04_01_SRTRewriteFree/Output/model_response.json
S6_04_01_SRTRewriteFree/Output/OutputManifest.json
```

然后同步页面和后续工具消费的全局最终产物：

```text
SessionOutput/subtitle/rewritten_srt_items.json
SessionOutput/subtitle/rewritten_dialogue.srt
```

其中：

1. `Output/rewritten_srt_items.json` 是本工具交给下游的局部最终产物。
2. `SessionOutput/subtitle/rewritten_srt_items.json` 是 UI、`04_02_StoryBoard.py` 和后续 workflow 消费的全局最终产物。
3. 两者内容必须一致。
4. 不新增 `SessionOutput/subtitle/free/`、`SessionOutput/subtitle/rewrite_free/` 等新业务路径。

这和旧 `04_01_SRTRewrite.py` 的链路保持一致：旧工具同时写 `S6_04_01_SRTRewrite/Output/rewritten_srt_items.json` 和 `SessionOutput/subtitle/rewritten_srt_items.json`；Free 工具只是更换 step 目录和 schema/策略，不改变下游消费位置。

### 7.2 输出 schema

推荐输出：

```json
{
  "schema_version": "analysis_v1_rewritten_srt_items_free_0.1",
  "rewrite_mode": "free",
  "prompt_source": "SessionContext/Variables.json:rewrite_prompt.final_prompt",
  "timing_policy": "tts_reference_seconds_per_character",
  "items": [
    {
      "srt_id": "free_srt_0001",
      "order": 1,
      "dialogue": "新的第一句口播",
      "start": 0.0,
      "end": 2.24,
      "duration": 2.24,
      "char_count": 14,
      "timing_source": "tts_reference_seconds_per_character"
    }
  ]
}
```

### 7.3 srt_id 生成规则

如果模型输出的 `srt_id` 为空、重复或不稳定，工具必须生成稳定顺序 ID：

```text
free_srt_0001
free_srt_0002
free_srt_0003
```

最终输出以工具归一化后的 `srt_id` 为准。

`srt_id` 必须满足 `04_02_StoryBoard.py` 当前消费方式：

1. 全局唯一。
2. 字符串稳定。
3. 可作为 `srt_ids`、Dialogue key、asset key 的来源。
4. 不包含空格、斜杠、冒号或路径字符。
5. 不要求匹配原 SRT 的 `srt_id`。

## 8. 时间计算规则

### 8.1 总原则

新 SRT 不需要补齐原 SRT 总时长，也不需要和原视频时长对齐。

每句时长按以下公式计算：

```text
每句 duration = 单字耗时 * 当前句有效字数
```

每句 `start/end` 按输出顺序连续累加：

```text
item_1.start = 0
item_1.end = item_1.duration
item_2.start = item_1.end
item_2.end = item_2.start + item_2.duration
```

### 8.2 单字耗时来源

优先使用 TTS Builder 中用户选定的参考声音片段。

图中示例：

```text
选择片段：15.58s - 31.58s
片段时长：16s
```

工具应根据该参考片段覆盖的参考对白计算：

```text
单字耗时 = 参考片段时长 / 参考片段有效字数
```

参考片段有效字数来自参考片段时间范围内的原 SRT/ASR 文本。

### 8.3 有效字数规则

第一版按以下规则计算有效字数：

1. 去掉空格。
2. 去掉常见中文和英文标点。
3. 中文按单字计数。
4. 英文和数字第一版按字符计数。
5. 如果一句为空，按 1 个有效字兜底，避免 0 秒时长。

### 8.4 缺少参考片段时的兜底

如果找不到 TTS Builder 参考片段，工具不得阻断运行。

兜底策略：

1. 优先用 `Variables.json` 中可配置的默认单字耗时。
2. 如果没有配置，使用默认值：

```text
0.18 秒 / 字
```

兜底信息必须写入输出 payload：

```json
{
  "timing_warning": "tts_reference_missing_default_seconds_per_character_used"
}
```

## 9. 校验要求

新工具只校验自由改写结果是否可用于下游，不校验是否和原 SRT 一致。

必须校验：

1. JSON 可解析。
2. `items` 是非空数组。
3. 每条 item 有非空 `dialogue`。
4. 归一化后 `srt_id` 唯一。
5. `order` 连续递增。
6. `duration > 0`。
7. `start/end` 连续且非负。

不得校验：

1. 输出句数是否等于输入句数。
2. 输出 `srt_id` 是否等于输入 `srt_id`。
3. 输出顺序是否等于输入顺序。
4. 是否合并了原句。
5. 是否拆分了原句。
6. 是否新增或删除句子。

## 10. 与 04_02_StoryBoard 的关系

`04_02_StoryBoard.py` 的输入仍是：

```text
SessionOutput/subtitle/rewritten_srt_items.json
```

它必须以 rewritten SRT 的顺序为准。

当输入来自 `04_01_SRTRewriteFree` 时，`04_02` 不应假设：

1. `srt_id` 来自原 SRT。
2. `image_path` 一定存在。
3. `key_frame_paths` 一定能从原帧继承。
4. rewritten SRT 和 `final_srt_frame_items.json` 一一对应。

如果 free rewritten item 没有 `image_path`，`04_02` 应允许空图片路径，并继续生成文本 StoryBoard。

## 11. 前端按钮需求

在 Analysis_V1 对话/SRT 区域红框位置新增标准按钮：

```text
Free Rewrite + StoryBoard
```

中文可显示为：

```text
自由改写并生成 StoryBoard
```

按钮行为：

1. 读取当前 Prompt Builder draft。
2. 调用现有 `saveConfig` 保存配置。
3. 调用 run-to-storyboard。
4. 固定选择 free rewrite 链路。
5. 打开运行进度弹窗。
6. 运行成功后刷新任务详情、改写 SRT 和 StoryBoard。

推荐请求参数：

```json
{
  "mode": "run_selected_steps",
  "selected_step_ids": ["00", "04_01", "04_02"],
  "rewrite_mode": "free",
  "storyboard_mode": "model",
  "include_tts_builder": false,
  "tts_builder_mode": "skip",
  "force": true
}
```

如果后端暂时不支持 `rewrite_mode`，也可以先用专用 endpoint 或专用 run option，但最终语义必须清楚指向：

```text
04_01_SRTRewriteFree
```

## 12. 后端编排需求

run-to-storyboard 需要支持选择 rewrite 工具：

```text
rewrite_mode = strict -> 04_01_SRTRewrite.py
rewrite_mode = free   -> 04_01_SRTRewriteFree.py
```

当 `rewrite_mode=free` 时，计划中的 04_01 步骤应显示：

```text
04_01 SRT 自由改写
```

或：

```text
04_01_SRTRewriteFree
```

执行命令必须带 `--force`，确保不复用旧的 `rewritten_srt_items.json`。

后端编排不得要求 `04_01_SRTRewriteFree.py` 自行读取数据库。运行前如果需要 DB 中的 task/prompt/model 信息，必须由后端在保存配置、运行计划编译或 `00_PrepareSessionVariables` 阶段完成，并写入 `SessionContext/Variables.json` 或通过 Runner 安全注入。

## 13. 测试与验收

### 13.1 Prompt 保存验收

1. 修改 SRT Rewrite 简单提示词并保存，task 中 `rewrite_simple_prompt` 更新。
2. 通过 Prompt Builder 生成最终提示词，task 中 `rewrite_final_prompt` 更新。
3. 修改 StoryBoard Quick Params，task 中 `storyboard_quick_config_json` 更新。
4. 运行 `00` 后，`SessionContext/Variables.json` 中以上字段同步更新。

### 13.2 Free Rewrite 验收

准备 3 条原 SRT，模型返回 5 条新 SRT：

1. 工具运行成功。
2. 输出 5 条 items。
3. `srt_id` 为 `free_srt_0001` 到 `free_srt_0005`。
4. 不触发句数不一致错误。

准备 5 条原 SRT，模型返回 2 条新 SRT：

1. 工具运行成功。
2. 输出 2 条 items。
3. 不触发缺失原 `srt_id` 错误。

### 13.3 时间计算验收

参考片段：

```text
duration = 16s
有效字数 = 80
单字耗时 = 0.2s/字
```

新句：

```text
dialogue = "所有的身心疲惫"
有效字数 = 7
```

则：

```text
duration = 1.4s
start/end 按顺序累加
```

### 13.4 StoryBoard 验收

1. `04_02_StoryBoard.py` 能读取 free rewritten SRT。
2. StoryBoard 输出顺序和 free rewritten SRT 顺序一致。
3. 不要求原始 `image_path` 必须存在。
4. 不因为句数变化阻断。

### 13.5 按钮验收

点击“自由改写并生成 StoryBoard”后：

1. 先保存当前 Prompt Builder 配置。
2. 运行 `00`。
3. 运行 `04_01_SRTRewriteFree`。
4. 运行 `04_02_StoryBoard`。
5. `SessionOutput/subtitle/rewritten_srt_items.json` 更新时间更新。
6. `SessionOutput/storyboard/srt_storyboard.json` 更新时间更新。
7. 前端“改写 SRT”和 StoryBoard 状态刷新。

## 14. 非目标

本需求不包含：

1. 修改旧 `04_01_SRTRewrite.py` 的严格模式。
2. 新增 Prompt Builder 存储。
3. 新增数据库表。
4. 新增新的下游 rewritten SRT 业务路径。
5. 要求 free rewritten SRT 补齐原视频时长。
6. 要求 free rewritten SRT 继承原图片帧。
