# Analysis_V1 Tool Use Session 框架适配通知与验收清单

适用对象：`ToolLibrary/Analysis_V1/` 业务脚本负责人。

背景：平台侧 M1-M5 已提供 Tool Use Session 目录、Runner、SubprocessToolAdapter、ResultIndex / session_files 同步、ModelBroker 基础入口和合同 schema。`Analysis_V1` 当前可以作为独立业务链路运行，但还没有适配新的框架合同。本文档用于明确业务脚本需要修改的范围，以及平台侧如何验收。

## 结论

`ToolLibrary/Analysis_V1` 的业务逻辑由 Analysis_V1 负责人修改。平台侧负责提供合同、示例、检查脚本和 review，不直接改写业务算法、prompt 策略、provider 选择逻辑或多媒体处理流程。

当前维护边界已调整为：先完成框架适配，不评价视频、图片、TTS、ASR 等业务输出质量。框架适配可以通过 `framework_bridge.py` 先承接 CLI / ToolResult / OutputManifest / blocked schema；后续如果要达到严格 provider 边界，再逐个把直接 provider 调用迁到 `ModelBroker`。

迁移目标不是让旧脚本“能被调用”即可，而是让每个工具满足以下框架合同：

- Runner 能以统一 CLI 启动工具。
- 工具只读 Tool Use Session 根目录内的受控输入。
- 工具输出标准 `ToolResult`、`OutputManifest`、必要时输出 `PromptManifest`。
- 依赖缺失使用标准 blocked schema。
- provider key 不进入工具输入、环境变量、日志、产物文件。
- 模型/provider 调用走平台 `ModelBroker` / resolver。
- 平台可以同步 `SessionOutput/manifests/result_index.json` 和 `session_files`。

## 必须修改

### 1. CLI 入口

所有 `ToolLibrary/Analysis_V1/*.py` 主链路脚本必须接受以下参数：

```text
--tool-session-root <path>
--step-id <id>
--tool-id <id>
--print-json
--force-rerun
```

`--workspace` 可以保留为本地调试兼容参数，但框架运行时必须以 `--tool-session-root` 为准。工具不得要求 Runner 额外传 `--task-id`、`--database-url`、个人机器路径或 provider key。

迁移期允许脚本通过 `ToolLibrary/Analysis_V1/framework_bridge.py` 接收上述参数，再桥接到旧 `--workspace` 入口。桥接模式视为框架调度合同已适配，但不代表 provider/key 边界已经完全收口。

当前平台 adapter 的调用形式是：

```text
python <script> --tool-session-root <root> --step-id <step_id> --tool-id <tool_id> --print-json
```

### 2. 目录布局

工具必须使用 Tool Use Session 目录布局：

```text
<tool_session_root>/
  0_SessionContext/
    Variables.json
    InputManifest.json
  <step_dir>/
    Working/
    Output/
    Report/
    Prompt/
  SessionOutput/
    manifests/
    schemes/
    reports/
    media/
    subtitles/
    json/
    packages/
```

旧布局 `SessionContext/`、`S1_...`、`S2_...` 可以作为迁移期读取 fallback，但命中 fallback 时必须写 warning 和 debug event；最终产物必须写入新布局。

### 3. 会话 ID 和上下文

工具不得自己生成 `tool_use_session_id`。必须从 `0_SessionContext/Variables.json` 或 `--tool-session-root` 推导当前会话。

工具不得直接修改 `0_SessionContext/Variables.json`。如果确实需要写回上下文字段，必须通过 stdout 的 `ToolResult.context_patch` 返回，由 Runner 校验 registry ownership 后合并。

### 4. blocked 输出

依赖不满足时，工具必须输出标准 schema：

```json
{
  "schema_version": "1.0",
  "status": "blocked",
  "missing_dependencies": [
    {
      "kind": "tool_output",
      "required_from": "01",
      "required_path": "SessionOutput/json/video_metadata.json",
      "suggested_action": "Run tool 01 before this step."
    }
  ]
}
```

`--print-json` 时 stdout 必须输出 `ToolResult(status="blocked", ...)`，不能只输出自定义 `blocked_reasons`。

### 5. 产物输出

每个工具必须把下游消费的产物写到当前 step 的 `Output/`，并写：

```text
<step_dir>/Output/OutputManifest.json
```

Manifest 必须使用平台 schema，至少包含：

- `schema_version: "1.0"`
- `tool_use_session_id`
- `step_id`
- `tool_id`
- `status`
- `files[]`

每个文件条目必须尽量包含：

- `path`
- `kind`
- `size`
- `sha256`
- `visibility`
- `downloadable`
- `sensitivity`
- `schema_name`，如果是结构化 JSON

`Report/Result.json` 可以保留为人类调试报告，但不能替代 `ToolResult` 和 `OutputManifest`。

### 6. stdout 结构化结果

`--print-json` 时 stdout 必须输出平台 `ToolResult`，例如：

```json
{
  "schema_version": "1.0",
  "tool_id": "01",
  "tool_name": "VideoProbeMetadata",
  "step_id": "S1_01_VideoProbeMetadata",
  "status": "completed",
  "outputs": {},
  "warnings": [],
  "errors": [],
  "result_paths": ["S1_01_VideoProbeMetadata/Output/video_metadata.json"],
  "metrics": {},
  "context_patch": {}
}
```

### 7. 模型和 provider 调用

工具脚本不得直接：

- 读取 `api_key_ciphertext`
- 解析 provider key
- 从环境变量读取 provider key
- 拼接 `Authorization: Bearer ...`
- 拼接 `?key=<api_key>`
- 直接调用 OpenAI / xAI / Gemini / DashScope / Sync.so 等 provider API

所有 LLM、VLM、TTS、图片、视频、唇形、ASR 云调用必须通过平台 `ModelBroker` 或后续指定的 broker/resolver 入口。工具只传：

- `model_provider`
- `model_id`
- `payload`
- `input_files`
- `idempotency_key`
- `visibility`
- `sensitivity`

provider key 由本机 resolver 注入，工具进程不持 key。

### 8. Prompt 和审计

使用模型的工具必须写：

```text
<step_dir>/Prompt/PromptManifest.json
<step_dir>/Prompt/Ref_*.md
<step_dir>/Prompt/ModelCall_*.json
```

参考 prompt 文档必须在 prepare 阶段复制到 `Prompt/Ref_*`，并在 `PromptManifest.json` 中索引。模型调用必须有 `ModelCall` audit，包含 request id、local usage id、idempotency key、provider mode、billing mode、visibility、sensitivity 等字段。

### 9. registry

`Analysis_V1` 需要提供自己的 registry：

```text
ToolLibrary/Analysis_V1/tool_registry.json
```

每个工具至少声明：

- `id`
- `name`
- `script`
- `stage`
- `hard_dependencies`
- `soft_dependencies`
- `main_outputs`
- `uses_llm`
- `uses_vlm`
- `supports_resume`
- `cost_level`
- `estimated_runtime.relative`
- `writes_session_context`，如果有 context patch

自由文本依赖 token 必须人工归一化，不要依赖 adapter 机械推导。

### 10. 路径和 IO 边界

工具不得写死个人机器路径，例如 `/Users/duheng/.opencrew`。

所有输入文件必须通过 Tool Use Session 根目录和 manifest 定位。涉及复制、读取、删除、软链、硬链、reflink 时，必须做 realpath 边界校验，不能越过当前 workspace / tool session 根目录。

`--force-rerun` 只能清理当前 step 拥有的目录，不能删除 `0_SessionContext`、其他 step、历史 attempt 或全局 workspace 数据。

## 平台侧验收

平台侧会使用以下方式验收：

1. 静态检查：运行 `scripts/check_analysis_v1_contract.py`。
2. schema 校验：`ToolResult`、`OutputManifest`、`DependencyCheckResult`、`PromptManifest` 必须能被 `opcrew_backend.tool_sessions.schemas` 反序列化。
3. dry run：使用 fake/no-op broker，不真正生成图片、视频、音频，验证入口、目录、manifest、result index。
4. secret 检查：stdout、stderr、Result、Manifest、Prompt audit 中不得出现 key、cookie、Authorization header、DB URL。
5. resync 检查：Runner 完成后能够生成/同步 `SessionOutput/manifests/result_index.json` 和 `session_files`。

默认检查只判断框架适配合同。若要同时检查 provider/key 严格边界，运行：

```bash
scripts/check_analysis_v1_contract.py --strict-provider-boundary
```

## 提交要求

每个迁移 PR 至少包含：

- 修改后的业务脚本。
- 对应 `tool_registry.json` 条目。
- 最小 dry-run 样例输出。
- 说明是否仍有 legacy fallback。
- 说明是否调用模型/provider，以及对应 broker 调用点。
- 运行 `scripts/check_analysis_v1_contract.py` 的结果。

## 当前已知不合规点

截至本通知创建时，`ToolLibrary/Analysis_V1` 当前版本存在以下合同差异：

- CLI 未接受 `--tool-session-root --step-id --tool-id --force-rerun`。
- 使用 `SessionContext/` 和固定 `S1_... S9_...` 目录。
- `00_PrepareSessionVariables.py` 自己生成 `tool_use_session_id`。
- 输出自定义 `Report/Result.json`，缺少标准 `OutputManifest.json`。
- blocked 使用 `blocked_reasons`，不是标准 `missing_dependencies`。
- 多个工具直接读取 DB/provider key 并直接调用 provider API。
- 存在个人机器路径和默认 DB URL。
- 缺少 `ToolLibrary/Analysis_V1/tool_registry.json`。
