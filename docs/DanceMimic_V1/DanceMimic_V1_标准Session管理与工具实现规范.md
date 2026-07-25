# DanceMimic_V1 标准 Session 管理与工具实现规范

版本：v0.1

状态：规范草案，用于后续设计 `OpenCrew/ToolLibrary/DanceMimic_V1`。本文只定义新工具集自己的 Session 管理规则，以及确保每个工具按同一约定实现的检查标准。

## 1. 命名与范围

工具集英文名：

```text
DanceMimic_V1
```

推荐工具目录：

```text
OpenCrew/ToolLibrary/DanceMimic_V1/
```

需求文档目录：

```text
OpenCrew/docs/DanceMimic_V1/
```

本文只定义 DanceMimic_V1 自己的 Session 规则、最小文件结构和每个工具实现前必须确认的问题。不修改 Analysis_V1，不创建工具代码。

## 2. 设计目标

DanceMimic_V1 参照 Analysis_V1 的方式拥有自己的 Session、自己的 `SessionContext`、自己的工具步骤目录和自己的 `SessionOutput`。

规范目标：

1. 每次运行绑定明确的 Task / Session / Attempt / OpenCode Session。
2. 第 0 步集中准备 Session Variables 和全局输入文件。
3. 后续工具默认不再查库，不再扫描 workspace 外部文件。
4. 后续工具只读取 `SessionContext/Variables.json` 和本工具 `Working/` 输入快照。
5. 每个工具只生成后续工具、页面绑定、QA 或断点续跑真正需要的文件。
6. 每个工具实现前都必须回答 Analysis_V1 约定的通用确认事项。
7. 每个工具必须能说明自己的输入、输出、状态、恢复方式和下游消费对象。

## 3. 标准 Session 最小结构

DanceMimic_V1 第一版采用 Analysis_V1 当前的扁平 Session 结构。

最低目录：

```text
<workspace>/
  SessionContext/
    Variables.json
    InputManifest.json
    File_Input_001.ext

  SessionReport/

  SessionOutput/
    reference/
    storyboard/

  S1_00_PrepareSessionVariables/
    Report/
      Result.json
```

可选扩展：

```text
<workspace>/
  S2_01_ExampleTool/
    Working/
    Output/
    Report/

  S3_02_ExampleModelTool/
    Working/
    Output/
    Prompt/
    Report/
```

目录规则：

1. `SessionContext/Variables.json` 是全局变量唯一入口。
2. `SessionContext/InputManifest.json` 是全局输入文件快照清单。
3. `SessionContext` 只保存全局变量和全局输入文件。
4. `SessionReport/` 保存 Session 级 QA、汇总报告和最终验收文件。
5. `SessionOutput/` 保存业务产物和对外可消费结果，但目录必须按 Session 语义命名，例如 `reference/`、`storyboard/`，不得按工具集名称或工具名命名。
6. 每个工具使用 `S{step_index}_{tool_name}/` 作为本次运行目录。
7. 每个工具正式 run 阶段只读取自己的 `Working/` 快照。
8. 不把临时文件、模型中间响应、调试文件散落在 workspace 根目录。

## 4. 第 0 步：PrepareSessionVariables

DanceMimic_V1 的第 0 步建议命名为：

```text
00_PrepareSessionVariables.py
```

推荐步骤目录：

```text
S1_00_PrepareSessionVariables/
```

职责：

1. 读取 DanceMimic_V1 对应 Task 主状态。
2. 读取 OpenCrew Session 主状态。
3. 解析绑定的 OpenCode Session。
4. 确认 workspace 可读写。
5. 读取当前 Prompt Version、Runtime / Skill Version、Attempt、Prompt Model、Run Model。
6. 准备本工具集后续步骤所需的全局输入文件。
7. 写入 `SessionContext/Variables.json`。
8. 写入 `SessionContext/InputManifest.json`。
9. 写入本工具自己的 `S1_00_PrepareSessionVariables/Report/Result.json`。
10. 明确记录运行授权、数据传输授权、默认 provider/model 的非密钥配置。

不负责：

1. 不执行后续业务工具逻辑。
2. 不调用高成本模型，除非未来明确把模型探测列入第 0 步合同。
3. 不生成业务最终产物。
4. 不把 API key、cookie、token、数据库连接串写入任何 Session 文件。

## 5. Task / Session / Attempt 绑定

`Variables.json` 必须明确以下实体：

```text
workflow_id = dance_mimic_v1
toolset_id = DanceMimic_V1
task_id
opencrew_session_id
opencode_session_id
workspace_dir
current_attempt_id
current_prompt_version_id
current_runtime_version_id
prompt_model_provider
prompt_model_id
run_model_provider
run_model_id
```

绑定规则：

1. Task / Session / Attempt / OpenCode Session 只在第 0 步集中解析。
2. 后续工具不得自行重新查找 Task、Session 或 OpenCode Session。
3. 后续工具需要的信息必须来自 `Variables.json` 或上游 Output。
4. 如果 `Variables.json` 缺少必要字段，后续工具应返回 `blocked`，不得自行查库补救。
5. 模型 API key 只允许运行时读取到内存，不得落盘到 Variables、Prompt、Result、Output 或 stdout。

## 6. Variables.json 合同

目标路径：

```text
SessionContext/Variables.json
```

建议结构：

```json
{
  "schema_version": "dance_mimic_v1_session_context_0.1",
  "toolset_id": "DanceMimic_V1",
  "workflow_id": "dance_mimic_v1",
  "task_id": null,
  "opencrew_session_id": null,
  "opencode_session_id": "",
  "workspace_dir": "",
  "current_attempt_id": null,
  "current_prompt_version_id": null,
  "current_runtime_version_id": null,
  "prompt_model_provider": "",
  "prompt_model_id": "",
  "run_model_provider": "",
  "run_model_id": "",
  "input_files": [
    {
      "path": "SessionContext/File_Input_001.ext",
      "role": "primary_input",
      "required": true
    }
  ],
  "session_run_config": {
    "data_transfer_allowed": false,
    "data_transfer_scope": "",
    "authorization_note": "",
    "force": false,
    "resume": false
  },
  "default_model_configs": {},
  "created_at": "",
  "updated_at": ""
}
```

字段规则：

1. 所有文件路径优先使用 workspace 相对路径。
2. `input_files` 记录后续工具共享的全局输入文件。
3. `session_run_config` 只记录跨工具复用的运行配置，不记录单个工具私有参数。
4. `data_transfer_allowed` 表示是否允许把输入或派生产物上传到云端服务。
5. `default_*_config` 只允许保存非密钥配置和 `api_key_ref`，不得保存真实密钥。
6. 大型 Prompt、模型请求、模型响应不得直接塞入 `Variables.json`。
7. 后续工具原则上只读 `Variables.json`；确需更新时输出 patch，由 Runner 或明确的 Session 管理步骤合并。

不得写入：

```text
password
API key
cookie
auth header
database URL
access token
refresh token
provider signed download URL
```

## 7. 第 0 步 Result.json 合同

目标路径：

```text
S1_00_PrepareSessionVariables/Report/Result.json
```

建议结构：

```json
{
  "schema_version": "1.0",
  "tool": "00_PrepareSessionVariables",
  "tool_version": "0.1.0",
  "toolset_id": "DanceMimic_V1",
  "workflow_id": "dance_mimic_v1",
  "status": "completed",
  "task_id": null,
  "opencrew_session_id": null,
  "opencode_session_id": "",
  "workspace_dir": "",
  "current_attempt_id": null,
  "created_files": [
    "SessionContext/Variables.json",
    "SessionContext/InputManifest.json",
    "S1_00_PrepareSessionVariables/Report/Result.json"
  ],
  "prepared_directories": [
    "SessionContext",
    "SessionReport",
    "SessionOutput",
    "SessionOutput/reference",
    "SessionOutput/storyboard",
    "S1_00_PrepareSessionVariables/Report"
  ],
  "warnings": [],
  "blocked_reasons": [],
  "updated_at": ""
}
```

状态：

```text
completed
blocked
failed
```

规则：

1. 依赖、权限、DB、源文件缺失导致无法进入后续工具链时，使用 `blocked`。
2. 代码异常或不可预期错误使用 `failed`。
3. 成功但有非阻断问题时，使用 `completed` + `warnings`。
4. `Result.json` 不写密钥类信息。

## 8. InputManifest.json 合同

目标路径：

```text
SessionContext/InputManifest.json
```

建议结构：

```json
{
  "schema_version": "1.0",
  "toolset_id": "DanceMimic_V1",
  "workflow_id": "dance_mimic_v1",
  "task_id": null,
  "opencrew_session_id": null,
  "current_attempt_id": null,
  "files": [
    {
      "path": "SessionContext/File_Input_001.ext",
      "role": "primary_input",
      "source_kind": "uploaded_file",
      "source_ref": "",
      "sha256": "",
      "size": 0,
      "mime_type": "",
      "visibility": "internal",
      "sensitivity": "normal"
    }
  ]
}
```

规则：

1. 每个进入 `SessionContext` 的全局输入文件都必须登记。
2. 必须记录 `path`、`role`、`sha256`、`size`、`visibility`、`sensitivity`。
3. `path` 必须是 workspace 相对路径。
4. `visibility` 用于区分 internal、debug、customer_visible。
5. `sensitivity` 用于后续文件展示、下载、分享和 Debug Console 过滤。
6. 如果输入来自 workspace 外部，第 0 步必须复制或标准化到 workspace 内。
7. 后续工具不得依赖原始 workspace 外部绝对路径。

## 9. 每个工具通用目录合同

DanceMimic_V1 后续每个工具建议使用：

```text
S{step_index}_{tool_name}/
  Working/
  Output/
  Prompt/
  Report/
```

第一版允许无模型、无 Prompt 的工具不创建 `Prompt/`。

规则：

1. `Working/` 保存从 `SessionContext` 或上游 Output 复制来的输入快照、断点状态和必要缓存。
2. `Output/` 保存下游可消费的本工具结果。
3. `Prompt/` 只在调用 LLM / VLM / 外部模型或需要 Prompt 审计时创建。
4. `Report/` 保存 `Result.json`、QA 报告、错误摘要和人工可读检查结果。
5. run 阶段不得跨工具目录直接读取上游文件，应读取 prepare 阶段复制到本工具 `Working/` 的快照。
6. 每个 Output 必须有明确下游消费者或页面绑定用途。

## 10. Analysis_V1 默认工具实现确认事项

以下标准来自 `Analysis_V1_SRT_Detail_工具迁移实现路径.md` 的“每个工具实现前必须回答的问题”。DanceMimic_V1 后续每个工具正式编码前，也必须逐条回答。

### 10.1 最小文件与产出物审查

必须回答：

```text
是否最小程度生成中间文件和产出物？
这些文件是否都是后续工具、页面绑定、QA 或断点续跑真正需要的？
是否存在可以删除的重复 Report、重复 Snapshot、重复 Manifest？
```

判断标准：

1. `SessionContext` 只保存全局变量和全局输入文件。
2. `Working/` 只保存本工具断点续跑、输入快照和缓存所需文件。
3. `Output/` 只保存交给下游消费的最终产物。
4. `Report/` 只保存本工具执行报告、QA 报告或人工可读校验结果。
5. `Prompt/` 只在工具调用 LLM / VLM 或需要 Prompt 审计时创建。
6. 无 Prompt 工具不创建 `Prompt/` 目录。
7. 不允许因为调试方便而长期保留重复 JSON。

### 10.2 数据库连接审查

必须回答：

```text
是否需要连接数据库？
如果需要，为什么不能通过 00 写入的 Variables.json 或已有 Output 解决？
```

判断标准：

1. 第 0 步允许访问数据库，用于集中准备 Task、Session、OpenCode Session、模型、Prompt、Attempt 和源文件路径。
2. 后续工具默认不得访问数据库。
3. 后续工具需要的业务运行信息必须来自 `SessionContext/Variables.json` 或上游 Output。
4. 如果工具发现 `Variables.json` 信息不足，应返回 `blocked` 并说明缺失字段，不得自行查库补救。
5. 如果执行器工具确需运行时读取 provider key，只能读取密钥到内存，且必须解释为什么不能由第 0 步写入；真实 key 不得落盘。

### 10.3 SessionContext 写入审查

必须回答：

```text
是否需要产出或更新 SessionContext？
如果需要写入 Variables.json，写入字段是什么，谁会消费，为什么不能只放在本工具 Output？
```

判断标准：

1. 第 0 步必须创建 `SessionContext/Variables.json` 和必要全局输入文件。
2. 后续工具默认只读 `SessionContext`。
3. 只有会被多个后续工具复用的全局状态，才允许写回 `Variables.json`。
4. 写回时必须保留已有字段，不得覆盖其他工具拥有字段。
5. 所有敏感信息禁止写入 `Variables.json` 和 `Result.json`。

### 10.4 产出物与下游消费审查

必须回答：

```text
本工具产出物是什么？
产出物给后面哪一步使用？
如果产出物缺失，下游应该 blocked、fallback 还是 warning？
```

判断标准：

1. 每个 Output 必须有明确下游消费者。
2. 没有下游消费者的文件只能放入 `Report/` 或不生成。
3. 下游依赖必须在工具自己的 preflight 中显式检查。
4. 下游读取依赖时，应先在 prepare 阶段复制到本工具 `Working/`，run 阶段不得跨工具目录直接读取。

### 10.5 Rerun 与断点续跑审查

必须回答：

```text
是否按照 Rerun 和断点继续的方式实现？
原始状态是什么？
强制 Rerun 如何恢复到原始状态？
断点续跑如何识别已完成且可信的子步骤？
```

判断标准：

1. 每个工具必须支持 `prepare -> run -> finalize`。
2. 每个工具必须有 `Working/State_progress.json` 或等价状态文件。
3. 断点续跑前必须重新执行依赖自检。
4. 断点续跑只能复用已完成且可信的子步骤。
5. 强制 Rerun 只清理本工具目录下的 `Working/`、`Output/`、`Report/`、`Prompt/`。
6. 强制 Rerun 不得删除上游 Output、其它工具目录、`SessionContext` 中非本工具声明写入的变量。
7. 强制 Rerun 的目标是让本工具回到“从未运行过”的干净状态，而不是清空整个 Session。

## 11. 第一版建议落地顺序

建议按以下顺序推进：

1. 确认 DanceMimic_V1 的最小 Session 文件结构。
2. 确认 `00_PrepareSessionVariables.py` 的 Variables 字段。
3. 确认第一批工具列表和每个工具的上下游关系。
4. 为每个工具按第 10 章填写实现前确认事项。
5. 编写 `OpenCrew/ToolLibrary/DanceMimic_V1/tool_registry.json` 初版。
6. 实现 `00_PrepareSessionVariables.py`。
7. 按工具依赖顺序逐个实现后续工具。

## 12. 参考基线

本需求对齐以下现有设计：

1. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_SRT_Detail_工具迁移实现路径.md`
2. `OpenCrew/docs/SessionDesign-R2/Analysis_V1_00_PrepareSessionVariables_工具实现Workbook.md`
3. `OpenCrew/docs/工具调用会话管理设计PRD.md`
4. `OpenCrew/docs/Analysis_V1框架适配通知与验收清单.md`
5. `OpenCrew/ToolLibrary/Analysis_V1/tool_registry.json`
