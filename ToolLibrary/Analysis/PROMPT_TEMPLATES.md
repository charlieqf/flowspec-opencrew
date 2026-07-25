# OpenClip Prompt Templates

This file documents prompt templates for OpenClip business prompt construction. These prompts are for business understanding and segmentation intent only. They must not include tool names, commands, code, file paths, or execution steps.

## Current OpenClip UI Fields

The current Prompt Builder UI provides these fields:

- `reference_video_path`: reference video path.
- `industry`: industry.
- `persona`: persona or main speaking role.
- `target_audience`: target audience.
- `product_info`: product/service information, selling points, use cases.
- `analysis_goal`: analysis goal.
- `video_formula`: business structure formula.
- `constraints`: special requirements and advanced business controls.
- `simple_prompt`: generated or edited simple prompt.
- `final_prompt`: model-generated complex business prompt.

Use `constraints` for advanced controls until the UI exposes dedicated fields for scene list, transition rules, balanced logic, summary logic, and retake focus.

## Simple Prompt Template

Use this template to generate the OpenClip Simple Prompt from UI parameters.

```text
<!-- OPENCLIP_SIMPLE_PROMPT_TEMPLATE_START -->
请根据下面的简单提示词，生成一段更详细的复杂业务提示词，用于指导视频内容理解和拆解逻辑。
复杂业务提示词必须只描述业务信息、结构意图、场景规则、三套方案业务规则和复拍关注点。
不要包含工具名称、代码、命令、文件路径或执行步骤。

按“{video_formula}”理解并拆解这条视频。

行业：{industry}
人设：{persona}
目标观众：{target_audience}
产品/服务：{product_info}
分析目标：{analysis_goal}

公式槽位：
{formula_slots}

业务拆解重点：
请识别视频中的关键观点、角色关系、冲突/证据/方案/转化节点，并按“{video_formula}”归纳结构。

主拍摄场景：
{shooting_scene_list}

场景判断规则：
只有真实物理拍摄空间变化才算场景转场。同一空间内的机位、景别、人物、动作、话题或字幕变化，不单独算真实场景转场。标题卡、黑屏、截图、信息插页、平台导流页作为特殊视觉类型单独识别。

三套方案业务规则：
detail：保留最细业务表达单元。
balanced：合并同一表达功能的连续片段，形成可复拍、可交付的业务单元。
summary：按视频公式槽位和业务阶段聚合。

复拍描述重点：
人物关系、主场景、关键动作、道具/产品露出、情绪触发、口播落点、画面必须保留信息。

特殊要求：
{constraints}

请输出仅面向业务理解和拆解逻辑的提示词，不要包含工具名称、代码、命令、文件路径或执行步骤。
<!-- OPENCLIP_SIMPLE_PROMPT_TEMPLATE_END -->
```

## Current Backend Behavior

The current backend uses the same template, with one practical adjustment because `shooting_scene_list` is not yet a standalone UI field:

```text
主拍摄场景：
如特殊要求中已明确列出主拍摄场景，请严格使用该列表；如未提供，请根据视频实际画面归纳主拍摄场景。
```

If the UI later adds `shooting_scene_list`, replace that sentence with the actual scene list.

## Constraints Examples

### Complex Multi-Scene Video

Paste this into `constraints` when accurate physical scene transition judgement matters:

```text
本视频为复杂多场景视频，需要严格区分真实物理场景转场。

主拍摄场景包括：
1. 前厅走道
2. 宴会厅
3. 后厨
4. 门口/大门区域

转场判断规则：
只有从一个连续物理空间切换到另一个连续物理空间，才算真实场景转场。
同一空间内的机位变化、人物变化、动作变化、景别变化、话题变化、字幕变化，不算真实场景转场。
标题卡、黑屏、截图、图文页、信息插页、平台导流页作为特殊视觉类型，不与真实拍摄空间混淆。
```

### Balanced And Summary Business Logic

Paste this into `constraints` when the user wants explicit balanced/summary aggregation logic:

```text
balanced 逻辑：
请按完整复拍业务单元聚合，而不是按固定时长聚合。
优先聚合为：发现问题 -> 现场确认 -> 核查过程 -> 解决方案 -> 总结/导流。

summary 逻辑：
请按视频公式和业务阶段聚合。
每个 summary 片段应能概括一个完整的大阶段。
```

### Retake Description Focus

Paste this into `constraints` when retake output must emphasize specific production details:

```text
复拍描述需要重点关注：
1. 人物关系和角色身份。
2. 主场景和空间位置。
3. 关键动作和动作触发原因。
4. 道具、产品或服务露出。
5. 情绪变化和情绪触发点。
6. 口播落点和画面必须保留的信息。
7. 不适合复拍或需要人工确认的风险点。
```

## Simple To Complex Business Prompt Generator

This is the model instruction used conceptually when generating `final_prompt` from the Simple Prompt.

```text
你将收到 OpenClip 的界面参数和简单提示词。

你的任务是生成一段“纯业务复杂提示词”，用于指导视频内容理解和拆解逻辑。

禁止：
- 不要出现任何工具名称。
- 不要出现 OpenClip、OpenCrew、OpenCode、ToolLibrary。
- 不要出现步骤编号、代码、命令或文件路径。
- 不要描述技术执行流程。

必须保留和强化：
- 行业。
- 人设。
- 目标观众。
- 产品/服务。
- 分析目标。
- 视频公式。
- 公式槽位。
- 主拍摄场景列表或主场景归纳要求。
- 场景转场判断规则。
- detail 细分业务原则。
- balanced 均衡业务原则。
- summary 汇总业务原则。
- 复拍描述关注点。
- 特殊要求/限制。

请把输入中的零散参数整理成一段清晰、完整、可执行的业务提示词。
提示词应帮助模型理解视频业务结构，而不是告诉系统怎么运行工具。

输出：
只输出最终复杂业务提示词。
```

## Formula Slot Templates

The backend currently expands these formulas:

```text
Hook/Trust/CTA
- Hook：前段强抓钩
- Trust：中段证据与可信信息
- CTA：末段动作引导或转化收束

老板巡店冲突型
- 巡店开场：建立老板进入现场与强判断开场
- 问题暴露：暴露管理或服务问题
- 老板判断：输出判断、标准或方法
- 价值收束：形成价值落点与动作引导

问题-过程-方案型
- 问题：提出问题或痛点
- 过程：展示过程、证据或推演
- 方案：提出方案与价值结果

反常识抓钩型
- 反常识抓钩：用反常识钩子打断预期
- 证据展开：给出证据、过程或案例
- 认知翻转：完成观点转向
- 动作引导：给出动作落点
```

Custom formulas are split by `/`, `->`, or `-` and converted into slot labels.
