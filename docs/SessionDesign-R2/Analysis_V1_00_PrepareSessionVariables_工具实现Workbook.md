# Analysis_V1 00_PrepareSessionVariables 工具实现 Workbook

版本：v0.1

状态：已确认的实现约束，用于后续 `OpenCrew/ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py` 编码和后续 Analysis_V1 工具迁移。

## 1. 工具定位

`00_PrepareSessionVariables` 是 Analysis_V1 工具链的第 0 步。

它只支持：

```text
workflow_id = openclip_analysis
```

它的职责是集中完成后续工具链需要的上下文准备：

1. 读取 OpenClip Task 主状态。
2. 读取 OpenCrew Session 主状态。
3. 解析绑定的 OpenCode Session。
4. 确认 Workspace 可读写。
5. 读取当前 Prompt Version、Skill Version、Attempt、Prompt Model、Run Model。
6. 复制全局源视频到 Session Context。
7. 写入后续工具唯一可信的全局变量文件。

它不做视频分析，不生成分镜，不调用 LLM/VLM，不创建新的数据库进程，不重启服务。

## 2. 核心设计目标

第 0 步必须把容易受沙箱、数据库、OpenCode、外部路径影响的事情一次性处理完。

成功后，后续 Analysis_V1 工具应满足：

1. 默认不再访问数据库；但明确需要运行时密钥或 provider 实时配置的工具除外，例如 `02_01_AudioASR.py`。
2. 不再自行解析 OpenCode Session。
3. 不再读取 workspace 外部原始媒体路径。
4. 不再通过目录扫描猜测全局状态。
5. 只从 `SessionContext/Variables.json` 读取全局变量。
6. 只读取 `Variables.json` 声明的 workspace 相对文件。
7. 需要运行时访问数据库的工具，只能读取必要配置和密钥，密钥不得写入 `Variables.json`、`Output/`、`Report/` 或 stdout。

如果第 0 步无法完成，应直接 `blocked`，不进入后续工具链。

## 3. 目录与最小文件产出

第一版必须尽量少产出文件。

最终只允许产生：

```text
<workspace>/
  SessionContext/
    Variables.json
    Video_Source.mp4

  SessionReport/
  SessionOutput/

  S1_00_PrepareSessionVariables/
    Report/
      Result.json
```

规则：

1. `SessionContext/Variables.json` 是后续工具唯一全局变量入口。
2. `SessionContext/Video_Source.mp4` 是后续工具唯一全局源视频入口。
3. `S1_00_PrepareSessionVariables/Report/Result.json` 是本工具自己的执行报告。
4. 第一版不产生 `.md`。
5. 第一版不同时产生 `Report.json`、`DependencyCheck.json`、`VariablesSnapshot.json` 等重复文件。
6. 第一版不创建 `Working/`、`Output/`、`Prompt/`，除非后续实现确实需要。
7. `Output/` 只应放后续工具要消费的局部产物；00 的后续消费物已经进入 `SessionContext`，所以第一版不需要 `Output/`。
8. 00 开始运行时删除 legacy workspace 目录：`inbox/`、`meta/`、`outbox/`。
9. 00 开始运行时创建完整 Session 级目录：`SessionContext/`、`SessionReport/`、`SessionOutput/`、`S1_00_PrepareSessionVariables/Report/`。

## 4. Variables.json 合同

`Variables.json` 建议结构：

```json
{
  "schema_version": "analysis_v1_session_context_0.1",
  "tool_use_session_id": "",
  "workflow_id": "openclip_analysis",
  "task_id": null,
  "opencrew_session_id": null,
  "opencode_session_id": "",
  "workspace_dir": "",
  "current_attempt_id": null,
  "current_prompt_version_id": null,
  "current_skill_version_id": null,
  "latest_attempt_id": null,
  "run_model_provider": "",
  "run_model_id": "",
  "clip_mode": "virtual",
  "selected_scheme": "detail",
  "source_video_path": "SessionContext/Video_Source.mp4",
  "reference_video_original_path": "",
  "default_asr_config": {
    "config_name": "aliyun_fun_asr_default",
    "provider": "aliyun_bailian_fun_asr",
    "model": "fun-asr",
    "language": "zh",
    "api_url": "dashscope://audio/asr/transcription",
    "api_key_ref": "aliyun_bailian_fun_asr_key",
    "has_api_key": false,
    "source": "postgres:tool_asr_provider_configs"
  },
  "default_image_config": {
    "provider": "",
    "model": "",
    "api_key_ref": "",
    "has_api_key": false,
    "source": "postgres:tool_media_provider_configs",
    "extra": {}
  },
  "default_video_config": {
    "provider": "",
    "model": "",
    "api_key_ref": "",
    "has_api_key": false,
    "source": "postgres:tool_media_provider_configs",
    "extra": {}
  },
  "default_lipsync_config": {
    "provider": "",
    "model": "",
    "api_key_ref": "",
    "has_api_key": false,
    "source": "postgres:tool_media_provider_configs",
    "extra": {}
  },
  "asr_mode": "default",
  "cloud_asr_data_transfer_allowed": false,
  "cloud_asr_data_transfer_scope": "",
  "cloud_asr_data_transfer_authorized_at": "",
  "simple_prompt": "",
  "final_prompt": "",
  "created_at": "",
  "updated_at": ""
}
```

规则：

1. 第一版允许 `simple_prompt` 和 `final_prompt` 直接写入 `Variables.json`，减少文件数量。
2. 如果未来 prompt 内容过长、需要审计，才升级为独立 JSON 文件。
3. `source_video_path` 必须是 workspace 相对路径。
4. `reference_video_original_path` 可记录原始来源路径，但后续工具不得依赖它作为输入。
5. 密钥类信息不得写入 `Variables.json`。
6. `default_asr_config` 只写入非密钥公共配置，用于记录默认 ASR provider/model/language/api_url/api_key_ref/has_api_key。
7. `default_image_config`、`default_video_config`、`default_lipsync_config` 只写入非密钥公共配置，用于记录 05_02 默认图片、视频和对嘴型 provider/model/api_key_ref/has_api_key。
8. `02_01_AudioASR.py` 和 `05_02_VideoPlanExecutor.py` 真正调用模型时必须重新访问数据库获取 API Key；`00` 不把 API Key 写入 Session Context。
9. `cloud_asr_data_transfer_allowed` 是 00 阶段的一次性流程授权记录；只有显式传 `--allow-cloud-asr-data-transfer` 时才为 true。
10. 如果该授权为 false，`02_01_AudioASR.py` 在 default/cloud 且 provider 为云端时必须立刻 blocked，不得提取或上传音频。
11. 自动化运行默认云端 `02_01` 前，必须在 `00_PrepareSessionVariables.py` 阶段显式传 `--allow-cloud-asr-data-transfer`。`02_01_AudioASR.py` 不接受命令级补授权，只认可 00 写入的授权记录。

不得写入：

```text
password
API key
cookie
auth header
database URL
access token
refresh token
```

## 5. Result.json 合同

`Result.json` 是本工具执行报告，必须放在：

```text
S1_00_PrepareSessionVariables/Report/Result.json
```

建议结构：

```json
{
  "tool": "00_PrepareSessionVariables",
  "tool_version": "0.1.0",
  "status": "completed",
  "workflow_id": "openclip_analysis",
  "task_id": null,
  "opencrew_session_id": null,
  "workspace_dir": "",
  "created_files": [
    "SessionContext/Variables.json",
    "SessionContext/Video_Source.mp4",
    "S1_00_PrepareSessionVariables/Report/Result.json"
  ],
  "prepared_directories": [
    "SessionContext",
    "SessionReport",
    "SessionOutput",
    "S1_00_PrepareSessionVariables/Report"
  ],
  "cleanup_actions": [],
  "blocked_reasons": [],
  "warnings": [],
  "updated_at": ""
}
```

状态建议：

```text
completed
blocked
failed
```

规则：

1. 依赖、权限、DB、源文件缺失导致无法进入后续工具链时，使用 `blocked`。
2. 代码异常或不可预期错误使用 `failed`。
3. `Result.json` 不写密钥类信息。
4. `--print-json` 输出内容应和 `Result.json` 同结构。

## 6. 命令参数

建议第一版命令：

```bash
python3 OpenCrew/ToolLibrary/Analysis_V1/00_PrepareSessionVariables.py \
  --task-id 25 \
  --attempt-mode latest \
  --clip-mode virtual \
  --print-json
```

参数：

```text
--workflow-id openclip_analysis
--task-id <int>
--session-id <int>
--attempt-id <int>
--attempt-mode latest|none
--clip-mode virtual|copy|encode
--selected-scheme detail|balanced|summary
--source-video <path>
--database-url <url>
--allow-cloud-asr-data-transfer
--force
--print-json
```

参数规则：

1. `--workflow-id` 默认 `openclip_analysis`，且第一版只允许该值。
2. `--task-id` 必需。
3. `--session-id` 只用于交叉校验，不允许覆盖 task 绑定的 session。
4. `--source-video` 只用于补救，正常应读取 `openclip_tasks.reference_video_path`。
5. `--database-url` 只用于显式传入既有数据库连接，不得写入任何输出文件。
6. `--allow-cloud-asr-data-transfer` 是后续默认云端 ASR 的数据外发授权；自动化若计划跑默认 `02`，必须在 00 阶段显式传入。
7. `--force` 允许覆盖重建 `Variables.json` 和 `Video_Source.mp4`。

## 7. attempt-mode 语义

`attempt-mode` 只决定 00 是否把当前运行记录绑定进 `Variables.json`。

### latest

读取现有 attempt，不创建新 attempt。

优先级：

1. 使用 `openclip_tasks.latest_attempt_id`。
2. 如果为空，查询当前 task 最新一条 `openclip_attempts`。
3. 如果仍为空，写 `current_attempt_id = null`，并给 warning。

### none

不绑定 attempt。

写入：

```json
{
  "current_attempt_id": null
}
```

适合还没进入正式 run，只是准备上下文。

### 暂不支持 create

第一版不支持 `attempt-mode=create`。

原因：

1. create 会写数据库。
2. create 会更新 task 的 `latest_attempt_id`。
3. create 可能影响 session/task 状态。
4. 这属于 Run API / Plan Runner 的职责，不属于 00。

## 7.1 强制 Rerun 恢复状态

`--force` 是 00 工具自己的强制 Rerun 入口。

执行 `--force` 时，00 只清理自己拥有的状态：

```text
SessionContext/
0_SessionContext/  # 历史目录名，仅用于迁移清理
S1_00_PrepareSessionVariables/
```

然后重新创建：

```text
SessionContext/
SessionReport/
SessionOutput/
S1_00_PrepareSessionVariables/Report/
```

并重新写入：

```text
SessionContext/Variables.json
SessionContext/Video_Source.mp4
S1_00_PrepareSessionVariables/Report/Result.json
```

强制 Rerun 不删除：

```text
S1_*
S2_*
其它后续工具目录
SessionReport/ 下其它工具汇总结果
SessionOutput/ 下最终交付结果
```

说明：

1. `--force` 让 00 回到“从未运行过”的干净状态；如果 workspace 里存在历史目录名 `0_SessionContext/`，同步清理，避免新旧 SessionContext 并存。
2. 它不负责清理后续工具目录；如果完整工具链要从头 rerun，应由 Plan Runner 或上层编排器决定是否清理 S1/S2 等目录。
3. 如果 00 重新生成的上下文改变，后续工具旧结果可能变 stale；这应由后续工具的依赖检查或上层 rerun 策略处理。

## 8. 沙箱授权策略

运行 Analysis_V1 前，必须一次性准备好沙箱权限。

需要：

```text
文件读写：/Users/duheng/.opencrew
网络访问：连接既有 OpenCrew PostgreSQL
```

第 0 步 preflight 必须检查：

1. `/Users/duheng/.opencrew` 是否可读写。
2. `sessions.workspace_dir` 是否存在。
3. workspace 是否可创建 `SessionContext`。
4. workspace 是否可创建 `S1_00_PrepareSessionVariables/Report`。
5. 源视频是否可读。
6. `SessionContext/Video_Source.mp4` 是否可写。
7. 数据库是否可按既有连接读取。

如果沙箱权限不足，必须直接 `blocked`。

不得绕过，不得猜路径，不得改到仓库目录临时运行。

## 9. 数据库访问规则

00 是 Analysis_V1 第一版唯一 DB-aware 工具。

后续 Analysis_V1 工具默认不得访问 DB。

数据库连接来源：

1. 优先 `--database-url`。
2. 其次环境变量 `OPENCREW_DATABASE_URL`。
3. 最后使用 `OpenCrew/ToolLibrary/Analysis_V1/__init__.py` 中的 `DEFAULT_OPENCREW_DATABASE_URL`。
4. 不允许每次让大模型临时搜索 DB URL。
5. 不允许把 DB URL 写入 `Variables.json` 或 `Result.json`。

`__init__.py` 必须保存：

```python
DEFAULT_WORKFLOW_ID = "openclip_analysis"
DEFAULT_DATABASE_URL_ENV = "OPENCREW_DATABASE_URL"
DEFAULT_OPENCREW_DATABASE_URL = "postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew"
```

说明：

1. 默认 DB URL 是工具库运行默认值，不是输出产物。
2. `Variables.json` 和 `Result.json` 仍不得写入 DB URL。
3. 如果默认 DB URL 无法连接，工具只返回 `blocked`，不启动、不重启、不发现数据库进程。

驱动：

1. 优先 `psycopg`。
2. fallback `psycopg2`。

只读查询表：

```text
openclip_tasks
sessions
openclip_prompt_versions
openclip_skill_versions
openclip_attempts
```

第一版禁止写数据库：

1. 不创建 attempt。
2. 不更新 task。
3. 不更新 session。
4. 不写 session_events。
5. 不修改 prompt/skill version。

核心查询：

```sql
SELECT
  t.id AS task_id,
  t.session_id,
  t.status AS task_status,
  t.reference_video_path,
  t.simple_prompt,
  t.final_prompt,
  t.current_prompt_version_id,
  t.current_skill_version_id,
  t.latest_attempt_id,
  t.run_model_provider,
  t.run_model_id,
  s.id AS opencrew_session_id,
  s.opencode_session_id,
  s.workspace_dir,
  s.status AS session_status
FROM openclip_tasks t
JOIN sessions s ON s.id = t.session_id
WHERE t.id = %s
LIMIT 1
```

`attempt-mode=latest` 且 `latest_attempt_id` 为空时：

```sql
SELECT id
FROM openclip_attempts
WHERE task_id = %s
ORDER BY id DESC
LIMIT 1
```

数据库错误必须分类输出：

```text
missing_database_url
database_driver_missing
database_auth_failed
database_connection_refused
database_network_blocked
database_query_failed
```

## 10. 数据库失败的禁止动作

如果数据库访问不了，坚决不能在代码或会话中做以下动作：

```text
不运行 opencrew_local_stack.sh
不启动 PostgreSQL
不重启 backend
不扫描端口
不自动发现 DATABASE_URL
不创建新的数据库进程
不创建新的数据库
不写死数据库密码
不尝试修复端口占用
```

正确行为：

1. 只尝试连接既有配置。
2. 失败后写 `Result.json`。
3. 返回 `status=blocked`。
4. 清晰说明失败类别和下一步需要的外部修复。

示例：

```json
{
  "status": "blocked",
  "blocked_reasons": [
    {
      "code": "database_unreachable",
      "message": "Cannot connect to configured OPENCREW_DATABASE_URL. This tool will not restart, create, or discover database processes."
    }
  ]
}
```

## 11. 源视频处理规则

源视频来源：

1. 默认读取 `openclip_tasks.reference_video_path`。
2. 如果传入 `--source-video`，仅作为补救覆盖。

解析规则：

1. 绝对路径：检查存在后复制。
2. workspace 相对路径：相对 `sessions.workspace_dir` 解析。
3. 不扫描目录猜测源视频。
4. 不从 `inbox/`、`source_video.mp4`、`reference_video*` 中自动挑选。
5. 不创建 symlink。
6. 必须复制真实文件到 `SessionContext/Video_Source.mp4`。

第一版建议：

1. 如果源文件不是 `.mp4`，直接 `blocked`。
2. 不把 `.mov`、`.m4v`、`.avi` 硬复制成 `.mp4`。
3. 后续如需转码，另设独立工具或明确增加 00 的转码职责。

## 12. OpenCode Session 规则

00 只读取并验证绑定：

```text
sessions.opencode_session_id
```

第一版不调用 OpenCode API。

规则：

1. `opencode_session_id` 为空时直接 `blocked`。
2. 不创建新的 OpenCode Session。
3. 不自动修复 OpenCode Session。
4. 不从前端 state 或最近 message 推断 session。
5. 后续工具使用 `Variables.json` 中的 `opencode_session_id`。

## 13. 阻断规则

以下情况必须直接 `blocked`：

1. `workflow_id` 不是 `openclip_analysis`。
2. `task_id` 不存在。
3. task 绑定 session 不存在。
4. 传入 `--session-id` 与 task 绑定 session 不一致。
5. `sessions.workspace_dir` 为空。
6. workspace 不可读写。
7. `/Users/duheng/.opencrew` 沙箱读写权限不足。
8. `opencode_session_id` 为空。
9. `final_prompt` 为空。
10. 源视频路径为空。
11. 源视频不存在或不可读。
12. 源视频不是 `.mp4`。
13. 无法复制源视频到 `SessionContext/Video_Source.mp4`。
14. 数据库不可访问。
15. 数据库访问需要启动、重启、端口扫描或新建进程才能继续。

## 14. 后续工具强约束

Analysis_V1 后续工具必须遵守：

1. 默认不访问 DB。
2. 默认不读取 `/Users/duheng/.opencrew` 以外的原始路径。
3. 默认不读取 `reference_video_original_path`。
4. 默认不解析 OpenCode Session。
5. 默认不创建新的 OpenCode Session。
6. 默认不把密钥类信息写入任何工具产物。
7. 全局变量只读 `SessionContext/Variables.json`。
8. 全局源视频只读 `SessionContext/Video_Source.mp4`。

如果后续工具确实需要新增全局变量：

1. 必须声明自己写入哪些字段。
2. 必须保留 `Variables.json` 已有字段。
3. 不得覆盖其他工具拥有的字段。
4. 只有会被多个后续工具复用的值才允许写回 `Variables.json`。

## 15. 实现验收标准

运行成功后必须满足：

1. `SessionContext/Variables.json` 存在。
2. `SessionContext/Video_Source.mp4` 存在。
3. `S1_00_PrepareSessionVariables/Report/Result.json` 存在。
4. `Variables.json.workflow_id = openclip_analysis`。
5. `Variables.json.task_id` 与输入一致。
6. `Variables.json.opencrew_session_id` 与 DB 绑定 session 一致。
7. `Variables.json.workspace_dir` 与 `sessions.workspace_dir` 一致。
8. `Variables.json.source_video_path = SessionContext/Video_Source.mp4`。
9. 输出文件不包含 DB URL、password、API key、cookie、auth header。
10. `--print-json` 返回结构与 `Report/Result.json` 一致。

失败或阻断时必须满足：

1. 不进入后续工具链。
2. 不启动任何数据库或服务进程。
3. 不重启任何服务。
4. 不扫描端口。
5. `Result.json` 清楚说明 `blocked_reasons`。

## 16. 01_VideoProbeMetadata 实现理解与要求

本章用于持续记录后续 Analysis_V1 工具实现时对 00 SessionContext 合同的补充理解。`01_VideoProbeMetadata.py` 是 00 之后的第一个只读源视频工具。

### 16.1 工具定位

`01_VideoProbeMetadata.py` 的职责是读取 00 已准备好的源视频，并生成后续工具共同复用的视频基础参数：

```text
duration_seconds
fps
frame_count
width
height
aspect_ratio
has_audio
audio_stream_count
codec_name
pixel_format
```

它不生成页面报告，不生成视觉报告，不生成交付物，不调用 LLM / VLM，不访问数据库。

### 16.2 产出位置原则

`Video_Metadata.json` 属于源视频的结构化全局属性，不属于页面绑定产物。

因此它的产出规则是：

1. 先写入本工具自己的 `Output/`：

```text
S2_01_VideoProbeMetadata/Output/Video_Metadata.json
```

2. `finalize` 阶段再同步到 SessionContext：

```text
SessionContext/Video_Metadata.json
```

3. 同步更新 `Variables.json` 指针：

```json
{
  "video_metadata_path": "SessionContext/Video_Metadata.json"
}
```

4. 不写入 `SessionOutput/`，除非未来有独立页面或视觉报告需要展示视频基础信息。

### 16.3 最小目录与文件

`01` 成功运行后只允许新增或更新：

```text
S2_01_VideoProbeMetadata/
  Working/
    InputFrom_0_Variables.json
    State_progress.json
  Output/
    Video_Metadata.json
  Report/
    Result.json

SessionContext/
  Variables.json
  Video_Metadata.json
```

规则：

1. `InputFrom_0_Variables.json` 是 prepare 阶段的输入快照。
2. `State_progress.json` 保存 source video fingerprint、阶段状态和 resume 判断依据。
3. `Output/Video_Metadata.json` 是本工具的本地最终产物。
4. `SessionContext/Video_Metadata.json` 是后续多工具共享产物。
5. `Variables.json` 只允许新增或更新 `video_metadata_path` 和 `updated_at`。
6. 本工具没有 Prompt，因此不创建 `Prompt/` 目录。
7. 本工具不创建 `SessionOutput/metadata/`。
8. 本工具不创建 `.md` 摘要、`run_result.json`、`DependencyCheck.json` 或重复 snapshot。

### 16.4 数据库与 SessionContext 规则

`01` 不连接数据库。

只读取：

```text
SessionContext/Variables.json
SessionContext/Video_Source.mp4
```

只写入：

```text
SessionContext/Video_Metadata.json
SessionContext/Variables.json:video_metadata_path
```

如果 `Variables.json` 缺少 `source_video_path`，或源视频不存在、不是 `.mp4`、不可读，工具必须返回 `blocked`，不得从数据库、原始绝对路径或目录扫描中补救。

### 16.5 Rerun 与断点续跑

原始状态定义为：

```text
S2_01_VideoProbeMetadata/ 不存在或为空
SessionContext/Video_Metadata.json 不存在
Variables.json 中没有 video_metadata_path
```

`--force` 行为：

1. 删除 `S2_01_VideoProbeMetadata/`。
2. 删除 `SessionContext/Video_Metadata.json`。
3. 从 `Variables.json` 移除旧 `video_metadata_path`，或在成功后重写为新指针。
4. 不删除 `SessionContext/Video_Source.mp4`。
5. 不删除 `Variables.json` 的其它字段。
6. 不删除 `S2_*`、`S3_*` 或其它后续工具目录。

`--resume` 行为：

1. 读取 `Working/State_progress.json`。
2. 比对当前 `Video_Source.mp4` 的 fingerprint。
3. 如果 fingerprint 一致且 `Output/Video_Metadata.json` 已可信完成，则复用 Output。
4. 即使复用 Output，也必须重新同步 `SessionContext/Video_Metadata.json` 和 `Variables.json.video_metadata_path`。

### 16.6 后续消费

后续工具必须优先通过：

```text
Variables.json.video_metadata_path
```

读取：

```text
SessionContext/Video_Metadata.json
```

第一阶段明确消费者：

1. `03_VisualSceneKeyframeExtract.py`：用于关键帧时间边界、fps、frame_count。
2. `09_DetailTimelineBuild.py`：用于 timeline coverage。
3. `12_SRTSegmentKeyframeQualityCheck.py`：用于最终时间边界和视频基础参数 QA。

如果未来需要页面展示视频基础信息，应由单独页面/报告工具从 `SessionContext/Video_Metadata.json` 读取后生成页面绑定产物，而不是让 `01` 直接进入 `SessionOutput`。

### 16.7 测试要求

`01` 必须覆盖以下测试：

1. `test_success_writes_output_then_session_context_metadata`：先有工具 Output，再有 `SessionContext/Video_Metadata.json`，两者内容一致。
2. `test_updates_only_video_metadata_pointer`：`Variables.json` 保留原字段，只新增或更新 `video_metadata_path`。
3. `test_no_database_access`：工具运行不读取 DB URL、不连接数据库。
4. `test_no_prompt_or_session_output_directory`：无 Prompt 时不生成 `Prompt/`，不生成 `SessionOutput/metadata/`。
5. `test_blocked_when_variables_missing`：缺 `Variables.json` 时 blocked。
6. `test_blocked_when_source_video_missing`：缺 `Video_Source.mp4` 时 blocked。
7. `test_force_rerun_scope`：force 只清理 S1 和 `Video_Metadata.json`，不清理 `Video_Source.mp4`、其它 Variables 字段或后续工具目录。
8. `test_resume_reuses_matching_output`：fingerprint 一致时复用 Output，并重新同步 SessionContext。

## 17. 02_01_AudioASR 实现理解与要求

### 17.1 本次确认点

`02_01_AudioASR.py` 的目标不是只跑本地 Whisper，而是建立 Analysis_V1 的第一版可信字幕时间轴。

已确认：

1. 需要同时支持云端 ASR 和本地 ASR。
2. 因为方言识别需要，默认优先使用云端 ASR。
3. 默认 ASR provider/model 由数据库 `tool_asr_provider_configs` 决定。
4. `00` 只把默认 ASR 的公共配置写入 `Variables.json.default_asr_config`，并把默认图片、视频、对嘴型公共配置写入 `default_image_config`、`default_video_config`、`default_lipsync_config`，不写 API Key。
5. `02` 和 `05_02` 真正调用时再访问数据库获取 API Key。
6. 需要参数控制本地或云端：`--asr-mode default|cloud|local`。
7. 默认 `default` 模式只使用数据库默认云端配置；不得自动 fallback 到本地 Whisper。
8. 只有显式传入 `--asr-mode local` 或 `--allow-local-fallback` 时，`02` 才允许使用本地 Whisper。

### 17.2 旧 Analysis 实现取舍

参考旧工具：

```text
OpenCrew/ToolLibrary/Analysis/02_audio_asr_pipeline.py
```

保留：

1. 从视频提取 ASR 用音频。
2. 本地 Whisper 路径。
3. Aliyun Bailian Fun-ASR 路径。
4. ASR segment 标准化。
5. ASR 质量报告。
6. 时间轴 gap / 覆盖率检查。

精简：

1. 不迁移旧版配置表初始化、upsert 等管理命令。
2. 不默认生成 `audio/original_audio.wav`、`audio/asr_enhanced_audio.wav`、`meta/asr_chunks.json`、`meta/asr_chunk_results.json`、`meta/asr_provider_raw_responses.json` 等多份调试产物。
3. 不默认做音频增强，避免不必要的时间轴风险。
4. 第一版不默认分片 gap recovery；先保证整条视频音频生成的 SRT 与视频时间轴一致。

### 17.3 最小产物

允许产出：

```text
S3_02_01_AudioASR/
  Working/
    InputFrom_0_Variables.json
    InputFrom_0_Video_Metadata.json
    Audio_Reference.wav
    State_progress.json
  Output/
    ASR_Segments.json
    ASR_Raw.srt
    ASR_Quality.json
  Report/
    Result.json

SessionContext/
  ASR_Segments.json
  ASR_Raw.srt
  ASR_Quality.json

SessionOutput/
  Audio_Reference.wav
```

同步规则：

1. 先写本工具 `Output/`。
2. 再同步到 `SessionContext/`。
3. 更新 `Variables.json`：

```json
{
  "asr_segments_path": "SessionContext/ASR_Segments.json",
  "asr_srt_path": "SessionContext/ASR_Raw.srt",
  "asr_quality_path": "SessionContext/ASR_Quality.json"
}
```

不生成：

1. `Prompt/`，因为本工具不调用 LLM Prompt。
2. `SessionOutput/transcript/`，因为原始 ASR 文本是后续参考输入，不是页面绑定产物。
3. 明文 API Key 文件。

特别规则：

1. `Working/Audio_Reference.wav` 是断点续跑用的本工具工作文件。
2. `SessionOutput/Audio_Reference.wav` 是页面可展示的重要产物。
3. `SessionOutput/Audio_Reference.wav` 必须直接放在 `SessionOutput/` 根目录，不得创建 `SessionOutput/audio/`、`SessionOutput/transcript/` 等子目录。

### 17.4 五个开工问题回答

| 问题 | 答案 |
|---|---|
| 是否最小程度生成中间文件和产出物 | 是。只保留一个 ASR 用 WAV、标准化 JSON、SRT、质量报告和 Result；其中 WAV 同步到 `SessionOutput/Audio_Reference.wav` 供页面展示。旧版多份 chunk/raw/debug meta 第一版不产出。 |
| 是否需要连接数据库 | 需要。`default/cloud` 模式运行时连接数据库读取默认 ASR provider/model/API Key；`local` 模式不读数据库。 |
| 是否需要产出 SessionContext | 需要。`ASR_Segments.json`、`ASR_Raw.srt`、`ASR_Quality.json` 是后续多个工具共享的全局参考输入，所以先写 Output，再同步到 `SessionContext`。 |
| 产出物是什么，给后面哪一步用 | `ASR_Segments.json` 和 `ASR_Raw.srt` 给 `05` 校准、`06` 语义结构、`08` fallback、`10` SRT 裁切、`12` QA；`ASR_Quality.json` 给 `04/05/12`；`SessionOutput/Audio_Reference.wav` 给页面播放/校对。 |
| 是否按照 Rerun 和断点继续实现 | 是。原始状态是 S2 目录、ASR SessionContext 文件、`SessionOutput/Audio_Reference.wav` 和 Variables ASR 指针不存在。`--force` 只清理 S2、本工具 ASR 文件/指针和 `SessionOutput/Audio_Reference.wav`；`--resume` 通过源视频 fingerprint 和 ASR config signature 复用可信 Output，并重新同步 Audio_Reference。 |

### 17.5 时间轴准确性要求

`02` 必须把 ASR 输出严格转换到原视频时间轴：

1. 从 `Variables.json.video_metadata_path` 读取视频 duration。
2. 从 `Variables.json.source_video_path` 读取 `SessionContext/Video_Source.mp4`。
3. 提取音频从 `0` 秒开始，不做裁剪偏移。
4. 每个 ASR item 必须满足：

```text
0 <= start <= end <= video_duration
```

5. ASR items 必须按 `start/end` 升序。
6. 重叠 item 需要去重或调整，并在 warning 中记录。
7. `ASR_Raw.srt` 的每条 cue 必须与 `ASR_Segments.json.segments` 同 index、同 start、同 end、同 text。
8. `ASR_Quality.json.timeline_alignment.status` 必须记录对齐是否通过。

### 17.6 数据库与密钥规则

`00`：

1. 查询默认 ASR 公共配置。
2. 写入 `Variables.json.default_asr_config`。
3. 只写 `has_api_key` 和 `api_key_ref`。
4. 不写 API Key。
5. 如果用户或自动化明确允许云端 ASR 数据外发，则写入 `cloud_asr_data_transfer_allowed=true` 和授权时间。

`02`：

1. `--asr-mode default`：读取 `Variables.default_asr_config.config_name`，运行时访问数据库获取完整配置和 API Key。
2. `--asr-mode cloud`：强制使用云端配置；如果数据库默认是 local，应 blocked。
3. `--asr-mode local`：强制使用本地 Whisper，不读数据库。
4. default/cloud 解析到云端 provider 时，必须先检查 `cloud_asr_data_transfer_allowed=true`；没有 00 阶段授权则 blocked，并提示重新运行 00 完成授权。02 不接受命令级补授权。
5. `Result.json` 和 stdout 只能写 provider/model/language/api_url/api_key_ref/has_api_key，不能写 API Key。

### 17.7 测试要求

`02` 必须覆盖：

1. `test_default_cloud_reads_db_and_writes_output_then_session_context`：默认模式读 DB，先写 Output，再同步 SessionContext，不泄漏 Key。
2. `test_default_cloud_blocks_before_upload_when_transfer_not_authorized`：00 未授权时，02 立刻 blocked，不提取、不上传音频。
3. `test_default_cloud_command_authorization_cannot_bypass_00_authorization`：02 不提供也不接受命令级云端外发补授权，不能绕过 00 授权记录。
4. `test_srt_and_json_are_clamped_sorted_and_aligned`：负时间、越界时间、重叠时间被修正，SRT 与 JSON 对齐。
5. `test_local_mode_does_not_read_database`：本地模式不访问数据库。
6. `test_default_cloud_failure_does_not_fallback_to_local_without_explicit_flag`：默认云端失败时不得自动使用 Whisper。
7. `test_cloud_failure_falls_back_to_local_when_explicitly_allowed`：只有显式允许 fallback 时才使用本地 Whisper。
8. `test_resume_reuses_completed_output_without_database_or_asr`：可信 resume 不重新读 DB、不重新跑 ASR。
9. `test_force_rerun_scope`：force 只清理 S2、本工具 ASR 文件/指针和 `SessionOutput/Audio_Reference.wav`，不影响 S1。
10. `test_blocked_when_metadata_missing`：缺少视频 metadata 时 blocked。
