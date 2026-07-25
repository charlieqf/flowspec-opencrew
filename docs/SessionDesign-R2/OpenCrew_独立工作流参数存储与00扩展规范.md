# OpenCrew 独立工作流参数存储与 00 扩展规范

版本：v1.0
日期：2026-07-12
状态：规范草案，作为脚本生成、人物口播、动作模拟及后续同类工作流的扩展基线。

## 1. 文档目标

本文定义 OpenCrew 新增独立工作流时必须遵守的参数存储、目录隔离、运行准备和后续工具取参原则，解决以下问题：

1. 新增工作流时不持续扩充同一张业务表，避免数据库出现大量互不相关的空字段。
2. 用户点击保存后，所有可恢复的编辑态参数以数据库为唯一主状态。
3. 每个工作流拥有自己的 ToolLibrary 目录和自己的 `00_PrepareSessionVariables.py`。
4. 工作流运行 00 后，后续任务只从该 Session 的 `Variables.json` 获取运行参数。
5. 避免数据库、`task_meta.json`、`Variables.json` 三份参数互相回退、覆盖或产生歧义。
6. 保证新增人物口播、动作模拟等流程时，不影响现有视频分析和普通脚本生成。

适用范围包括但不限于：

- 视频分析 `Analysis_V1`
- 普通脚本生成
- 人物口播 `TalkingHead_V1`
- 动作模拟 `DanceMimic_V1`
- 视频复刻 `Rebuild_V1`
- 后续新增的任何独立业务工作流

## 2. 核心架构原则

### 2.1 一个工作流对应一套独立运行域

每个独立工作流必须满足：

```text
一个 workflow_id
= 一个独立 ToolLibrary 目录
= 一个独立 00_PrepareSessionVariables
= 一套独立 Variables schema
= 一条独立后续任务链
```

示例：

```text
ToolLibrary/Analysis_V1/
  00_PrepareSessionVariables.py

ToolLibrary/TalkingHead_V1/
  00_PrepareSessionVariables.py

ToolLibrary/DanceMimic_V1/
  00_PrepareSessionVariables.py

ToolLibrary/Rebuild_V1/
  本工作流自己的准备步骤
```

不得建设一个根据大量条件拼装所有工作流参数的“万能 00”。不同工作流可以共享底层库函数，但不能共享工作流参数组装入口。

### 2.2 保存态与运行态严格分离

标准数据流：

```text
编辑/保存阶段
前端面板 <-> 数据库

运行准备阶段
当前工作流的 00：数据库 + SessionContext 素材 -> Variables.json

运行阶段
01 及后续任务：Variables.json + 明确的上游输出文件
```

约束：

- 运行 00 前，用户可编辑参数的唯一权威源是数据库。
- 点击保存不得提前生成运行态 `Variables.json`。
- 00 是数据库编辑态转换为 Session 运行态的唯一入口。
- 00 成功后，当前工作流的后续任务不得逐项查询任务数据库补参数。
- 参数缺失时必须明确失败并要求重新运行 00，不得从历史 JSON、旧输出或数据库静默回退。

### 2.3 Workspace 不是任务主状态数据库

Session workspace 用于保存：

- 用户上传的媒体和文档文件。
- 00 物化的运行输入文件。
- `SessionContext/Variables.json`。
- 各工具的 Working、Output、Report 和最终交付物。

Workspace 中的文件路径、哈希、大小和素材关联可以在数据库中建立索引，但 workspace 文件不得替代数据库中的任务编辑态主状态。

## 3. 数据层划分

### 3.1 Session 层

`sessions` 只负责通用会话能力：

- `session_id`
- Session 名称
- `workspace_dir`
- OpenCode Session 引用
- Session 状态
- 创建、开始、结束和更新时间

Session 表不承载人物口播、动作模拟或视频分析的专属业务参数。

### 3.2 通用 Task 层

通用 Task 记录负责所有工作流都需要的任务信封信息：

- `task_id`
- `session_id`
- `workflow_mode`
- `status`
- 标题和来源展示类型
- 当前版本或 Attempt 引用
- 通用模型选择引用
- 创建和更新时间

现阶段 `openclip_tasks` 仍承载视频分析和脚本生成的历史字段。为了兼容现有流程，不应在一次迁移中删除或重命名这些字段；但后续也不应继续把所有新工作流的专属字段加入该表。

### 3.3 工作流专属配置层

每个复杂独立工作流应使用自己的专属配置表，推荐结构：

```text
<workflow>_task_configs
  task_id             PK/FK
  schema_version
  mode/discriminator  可检索的核心模式字段
  config_json         工作流完整编辑态配置
  created_at
  updated_at
```

示例：

```text
talking_head_task_configs
dance_mimic_task_configs
script_generation_task_configs   # 仅在普通脚本生成独立复杂化后需要
```

使用原则：

1. 经常筛选、分支和校验的核心判别字段使用独立数据库列。
2. 只属于当前工作流、结构可能持续扩展的参数放入数据库 `config_json`。
3. `config_json` 是数据库字段，不是在 Session 中额外创建一份面板参数 JSON。
4. 每个 `config_json` 必须带 `schema_version`，并提供明确的默认值、校验器和迁移器。
5. 不允许把 API Key、Access Token、数据库密码等密钥写入 `config_json`。

### 3.4 为什么不把所有参数都加入共享任务表

如果把人物声音、人物图片、参考脚本、动作参考视频、动作参数等全部加入 `openclip_tasks`，会造成：

- 视频分析任务存在大量永远为空的列。
- 新工作流字段命名互相冲突。
- 通用 Repository 和序列化接口不断膨胀。
- 数据迁移频繁修改共享表，回归范围扩大。
- 不同工作流开始依赖彼此不应理解的字段。

因此采用“通用 Task 信封 + 工作流专属配置”的混合模型。

## 4. 工作流身份与内部模式

### 4.1 workflow_mode 必须唯一且稳定

推荐标识：

| 工作流 | workflow_mode |
| --- | --- |
| 视频分析 | `analysis_v1` |
| 普通脚本生成 | `script_generation_v1` |
| 人物口播 | `person_talking_head_v1` |
| 动作模拟 | `dance_mimic_v1` |
| 视频复刻 | `rebuild_v1` |

不得让人物口播和普通脚本生成共同使用含义模糊的 `workflow_mode=script`，再依赖 workspace 文件或 `task_meta.json.create_mode` 猜测真实工作流。

### 4.2 工作流内部模式必须与 workflow_mode 分离

`workflow_mode` 决定使用哪个目录、哪个 00 和哪条任务链；工作流内部 mode 决定当前工作流内的运行分支。

人物口播示例：

```text
workflow_mode = person_talking_head_v1

script_creation_mode =
  user_provided
  ai_create
  ai_rewrite
```

动作模拟可以定义自己的内部模式，但不得复用人物口播的 `script_creation_mode` 表达动作逻辑。

## 5. 点击保存的标准语义

### 5.1 保存必须写入数据库

保存按钮应在一个数据库事务中完成：

```text
BEGIN
1. 创建或更新 Session
2. 创建或更新通用 Task
3. 创建或更新当前 workflow 专属配置
4. 创建或更新素材索引和素材关联
5. 更新状态与 updated_at
COMMIT
```

任何一步失败必须整体回滚。

### 5.2 保存可以写入 SessionContext 的内容

保存阶段允许写入 SessionContext 或统一素材目录的内容仅限真实文件：

- 上传的视频、图片、音频、脚本附件。
- 其他无法合理作为数据库文本字段保存的大文件。

数据库必须保存这些文件的：

- 素材 ID。
- Session 相对路径或素材库引用。
- 文件哈希。
- 文件大小与类型。
- 创建来源。

面板中的选择、文本、Prompt、模式、模型和结构化配置仍然只存数据库。

### 5.3 保存不得执行的行为

点击保存不得：

- 调用脚本创作、改写或故事版模型。
- 启动 00 或任何后续工具。
- 创建运行态 `Variables.json`。
- 创建一份重复面板参数的 `task_meta.json`。
- 把同一组配置同时写入数据库、task_meta 和 Variables。

## 6. 00_PrepareSessionVariables 标准

### 6.1 每个工作流必须拥有自己的 00

当前工作流的 00 是该工作流唯一的运行准备入口，必须：

1. 接收明确的 `task_id`、`session_id` 或 workspace 定位参数。
2. 查询通用 Task 记录。
3. 验证 `workflow_mode` 与当前目录一致。
4. 查询当前工作流专属配置。
5. 按 `schema_version` 校验并升级配置。
6. 校验当前内部模式的必填参数。
7. 解析素材索引并检查输入文件存在性及哈希。
8. 物化后续工具需要的文本或结构化输入文件。
9. 解析非敏感的模型和公共运行配置。
10. 原子写入完整 `SessionContext/Variables.json`。
11. 输出独立的准备报告和清晰错误信息。

### 6.2 00 的数据库访问边界

00 可以集中访问：

- Session 和 Task 数据库记录。
- 当前工作流专属配置表。
- 素材索引。
- 非敏感的模型公共配置。
- API Key 引用信息。

00 不得把 API Key 本体写入 Variables。Variables 中最多保存 `api_key_ref`；具体密钥由后续执行器在运行时从环境变量或密钥服务解析。

### 6.3 Variables 是运行态完整快照

所有工作流的 Variables 建议使用统一外壳：

```json
{
  "schema_version": "session_variables_1.0",
  "task": {
    "task_id": 42,
    "session_id": 43,
    "workflow_mode": "person_talking_head_v1"
  },
  "workflow": {},
  "runtime": {
    "prepared_at": "",
    "config_revision": 1
  }
}
```

`workflow` 内部结构由各工作流独立定义。公共工具只能依赖统一外壳；专属工具只能依赖自己的 workflow schema。

### 6.4 00 的输出必须原子化

00 不得边准备边覆盖正式 Variables。推荐：

```text
1. 在内存中构造完整对象
2. 完成 schema 和必填项校验
3. 写入 Variables.json.tmp
4. fsync/关闭文件
5. 原子替换 Variables.json
6. 写 Prepare Report
```

00 失败时，应保留上一份成功 Variables 或明确标记本次准备失败，不得留下半份可被后续任务误读的 JSON。

## 7. 00 之后的取参纪律

01 及后续任务只能读取：

1. `SessionContext/Variables.json`。
2. Variables 明确声明的 SessionContext 输入文件。
3. tool registry 中声明的上游任务产物。
4. API Key 等密钥的安全运行时来源。

禁止：

- 后续任务直接查询业务参数数据库。
- 从 `task_meta.json` 补充 Variables 缺少的参数。
- 根据某个文件是否存在猜测工作流或内部模式。
- 在 Variables、task_meta、数据库之间使用 `A or B or C` 的静默回退。
- 因 Variables 缺字段而读取旧 SessionOutput 作为参数默认值。

如果用户在 00 后修改并保存了面板参数，当前 Variables 不自动代表最新配置。系统应将任务标记为“需要重新准备”，重新运行当前工作流的 00 后才能继续执行。

## 8. 人物口播工作流示例

### 8.1 数据库存储

`talking_head_task_configs` 至少包含：

```text
task_id
schema_version
script_creation_mode
config_json
created_at
updated_at
```

`config_json` 建议包含：

- 用户完整脚本或参考脚本。
- 行业、人设、目标受众、视频公式、产品信息、约束条件。
- 脚本简单提示词、最终提示词和 Prompt Model。
- StoryBoard 简单提示词、最终提示词和 Quick Config。
- 人物形象素材引用。
- Voice、Tempo、Segment Planning。
- Video Model 和 Resource Strategy。

### 8.2 TalkingHead_V1/00

`TalkingHead_V1/00_PrepareSessionVariables.py` 必须根据 `script_creation_mode` 准备：

| 模式 | 必填输入 | 运行策略 |
| --- | --- | --- |
| `user_provided` | 用户完整脚本 | 不调用脚本模型，直接进入标准化 |
| `ai_create` | 完整脚本创作最终提示词 | 一次模型调用，从零创作完整脚本 |
| `ai_rewrite` | 参考脚本、改写最终提示词 | 一次模型调用，改写参考脚本 |

00 可将数据库脚本文本物化为：

```text
SessionContext/Script/user_script.txt
SessionContext/Script/reference_script.txt
```

Variables 保存输入类型、路径和哈希。后续工具不得再通过统一的 `source_script.txt` 是否存在判断这是用户脚本还是改写参考脚本。

## 9. 视频分析、脚本生成和动作模拟的隔离

### 9.1 视频分析

```text
视频分析数据库配置
-> Analysis_V1/00
-> Analysis Variables
-> Analysis_V1 后续任务
```

人物口播改造不得删除或重命名视频分析当前依赖的共享字段。视频分析 00 不读取 `talking_head_task_configs`。

### 9.2 普通脚本生成

普通脚本生成必须使用自己的 `workflow_mode`。当其参数规模较小时可以暂时使用已有 Task 字段；当出现独立模型、版本、输入模式或执行链后，应建立自己的配置表和 ToolLibrary 目录，而不是继续借用 Analysis 或 TalkingHead 的 00。

### 9.3 动作模拟

```text
动作模拟数据库配置
-> DanceMimic_V1/00
-> DanceMimic Variables
-> DanceMimic_V1 后续任务
```

动作模拟 00 只读取动作模拟配置、参考动作素材和目标人物素材，不读取人物口播脚本模式，也不得默认执行 `Analysis_V1/00`。

## 10. Prompt 生成按钮与全局运行按钮

单字段 Magic/生成按钮属于编辑阶段：

1. 读取当前工作流数据库配置或本次表单请求。
2. 只执行该按钮声明的一次模型调用。
3. 只回写对应的最终 Prompt 字段。
4. 不运行 00。
5. 不生成 Variables。
6. 不顺带生成其他模块 Prompt。

全局运行按钮属于执行阶段：

1. 保存当前面板。
2. 创建 Attempt。
3. 按 `workflow_mode` 精确分发到当前工作流目录。
4. 首先执行该目录自己的 00。
5. 00 成功后才按 tool registry 执行后续任务。

## 11. task_meta.json 收敛原则

`task_meta.json` 不应作为跨工作流通用参数层，也不应保存数据库和 Variables 已有的重复配置。

迁移步骤：

1. 先把真实 `workflow_mode` 和内部 mode 写入数据库。
2. 让当前工作流的 00 改为只从数据库读取编辑态配置。
3. 让当前工作流 01 及后续任务只从 Variables 读取运行态参数。
4. 清理列表、详情页、运行分发和素材服务对 task_meta 的身份判断。
5. 完成回归测试后，停止为该工作流创建 task_meta。

该迁移可以按工作流逐个完成，不要求所有历史工作流一次性删除 task_meta；但任何新工作流不得以 task_meta 作为正式参数主状态。

## 12. 新工作流扩展检查清单

### 12.1 数据库

- [ ] 是否定义唯一稳定的 `workflow_mode`？
- [ ] 是否区分 workflow_mode 与工作流内部 mode？
- [ ] 是否只在通用 Task 中保存公共字段？
- [ ] 是否建立工作流专属配置表或明确说明无需建立？
- [ ] 专属配置是否带 schema_version？
- [ ] 保存是否使用事务？
- [ ] 媒体是否只保存索引而非 blob/base64？
- [ ] 是否避免保存 API Key 本体？

### 12.2 ToolLibrary

- [ ] 是否拥有独立目录？
- [ ] 是否拥有独立 00？
- [ ] 00 是否验证 workflow_mode？
- [ ] 是否定义独立 Variables schema？
- [ ] 是否定义 tool registry 和明确依赖？
- [ ] 后续工具是否完全停止查询业务参数数据库？

### 12.3 保存与运行

- [ ] 保存是否只落数据库和真实素材文件？
- [ ] 保存是否不会生成 Variables？
- [ ] Magic 按钮是否只调用一次模型并只回填目标字段？
- [ ] Run 是否先运行当前工作流自己的 00？
- [ ] 参数保存后是否标记需要重新运行 00？
- [ ] 00 失败时是否阻止后续任务？

### 12.4 兼容与回归

- [ ] 是否没有删除其他工作流正在使用的共享字段？
- [ ] 是否没有让其他工作流读取本工作流专属配置？
- [ ] 是否覆盖创建、保存、重开、运行、重跑和版本切换？
- [ ] 是否覆盖旧任务迁移和旧 task_meta 兼容期？
- [ ] 是否验证后续工具断开数据库后仍可仅凭 Variables 执行？

## 13. 验收标准

新增工作流只有同时满足以下条件才算完成基础设施接入：

1. 保存后关闭页面并重新打开，全部编辑参数可以从数据库恢复。
2. 保存后、运行 00 前，不依赖 Session JSON 恢复面板。
3. 运行分发能通过数据库 `workflow_mode` 精确选择工作流目录。
4. 当前工作流运行的是自己的 00，不会错误运行 `Analysis_V1/00`。
5. 00 能从数据库和素材索引生成完整 Variables。
6. 断开后续工具的业务数据库访问后，01 及后续任务仍可根据 Variables 和上游产物执行。
7. 修改数据库配置但未重新运行 00 时，系统能提示运行快照已过期。
8. 重新运行 00 后，Variables 与最新保存配置一致。
9. API Key 本体未出现在数据库业务 JSON、Variables、Report 或日志中。
10. 新工作流字段和运行分支不会改变视频分析、脚本生成及其他已有工作流的行为。

## 14. 规范优先级

如果历史文档仍要求保存时同步写入数据库、`task_meta.json` 和 `Variables.json`，以本文为准：

```text
保存：数据库为唯一编辑态主状态
00：生成 Variables 运行态快照
后续任务：只读 Variables 和明确上游产物
```

历史实现可以保留兼容读取，但必须按工作流逐步迁移，不得继续作为新工作流的设计模板。
