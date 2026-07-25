# DanceMimic_V1 01_ReferenceMediaDemux 工具实现需求

> v0.6 implementation note：工具级输入字段以 `DanceMimic_V1_实施收敛设计.md` §14.1 为准：`SessionContext/Variables.json` 唯一视频路径键为 `source_video_path`；DB/task meta 可继续保留 `reference_video_path`；旧 `source_reference_video_path` 不再作为工具输入。

## 1. 工具定位

`01_ReferenceMediaDemux.py` 是 DanceMimic_V1 的参考媒体拆分工具。

它只负责把用户输入的参考视频拆成三类可复用 Session 产物：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
```

其中：

1. `Video_Reference_Silent.mp4` 是保留原画面、去掉音轨的参考视频。
2. `Audio_Reference_Mixed.wav` 是从原视频直接提取的人声 + 音乐混合音频。
3. `Audio_Reference_Vocal.wav` 是经过人声分离后得到的纯人声音频。

本工具不生成 StoryBoard，不切 Dialogue，不做人脸检测，不调用视频生成模型。

## 2. 参考实现来源

本工具应复用 Analysis / Analysis_V1 中已经验证过的实现方式。

### 2.1 Analysis_V1 音频提取实现

参考脚本：

```text
OpenCrew/ToolLibrary/Analysis_V1/02_01_AudioASR.py
```

可复用点：

1. 使用 `OpenCrew.ToolLibrary.Analysis.media_binaries.find_ffmpeg()` 获取 ffmpeg。
2. 使用 `media_env()` 注入 ffmpeg / ffprobe 所在目录。
3. 用 `subprocess.run(..., capture_output=True, text=True, check=False, env=media_env())` 执行媒体命令。
4. 对 ffmpeg 返回码、空文件、缺音轨做 blocked 处理。
5. 采用 `Working/InputFrom_0_Variables.json`、`Working/State_progress.json`、`Output/*.json`、`Report/Result.json` 的标准 Session 工具结构。

Analysis_V1 中已有的音频提取核心命令形态：

```text
ffmpeg -y -i <source_video> -vn -ac 1 -ar <sample_rate> <output_audio.wav>
```

DanceMimic_V1 的 mixed audio 可继承该模式，但默认建议输出为双声道或按源音频声道保留；是否降为单声道应由参数控制，而不是硬编码。

### 2.2 Analysis 旧版人声分离实现

参考脚本：

```text
OpenCrew/ToolLibrary/Analysis/02_0_source_separation.py
```

可复用点：

1. 使用 Demucs 做两轨分离。
2. 默认模型为 `htdemucs`。
3. 命令使用 `python -m demucs --two-stems vocals -n htdemucs -o <tmp_output> <input_audio.wav>`。
4. 从 Demucs 输出树中读取 `vocals.wav` 和 `no_vocals.wav`。
5. `vocals.wav` 作为人声轨。
6. 分离失败时写结构化失败结果，错误码区分 `missing_demucs`、`audio_extraction_failed`、`source_separation_failed`。

DanceMimic_V1 只需要 `vocals.wav`，`no_vocals.wav` 不作为标准 SessionOutput 必需产物；但可以写入本工具 `Output/` 作为调试或审计产物。

### 2.3 媒体二进制解析

参考脚本：

```text
OpenCrew/ToolLibrary/Analysis/media_binaries.py
```

必须复用或等价实现以下解析顺序：

1. 优先读取 `OPENCREW_FFMPEG_PATH` / `OPENCREW_FFPROBE_PATH`。
2. 其次读取项目内 `OpenCrew/ToolLibrary/.bin/ffmpeg`、`OpenCrew/ToolLibrary/.bin/ffprobe`。
3. 再读取系统 PATH。
4. ffmpeg 可回退到 `imageio_ffmpeg`。
5. ffprobe 不应依赖 `imageio_ffmpeg` 回退。

## 3. 工具命名

推荐文件名：

```text
OpenCrew/ToolLibrary/DanceMimic_V1/01_ReferenceMediaDemux.py
```

推荐工具目录：

```text
S2_01_ReferenceMediaDemux/
```

推荐工具元数据：

```json
{
  "id": "01",
  "name": "01_ReferenceMediaDemux",
  "display_name_zh": "参考视频音画拆分",
  "display_name_en": "Reference media demux",
  "stage": "reference_media_prepare",
  "uses_llm": false,
  "uses_vlm": false,
  "uses_audio": true,
  "supports_resume": true,
  "cost_level": "high"
}
```

## 4. 输入合同

本工具只读取 00 准备好的 Session 上下文。

必需输入：

```text
SessionContext/Variables.json
SessionContext/Video_Reference_Source.mp4
```

推荐从 `Variables.json` 读取：

```json
{
  "reference_video_path": "SessionContext/Video_Reference_Source.mp4",
  "reference_media_demux": {
    "mixed_audio_sample_rate": 44100,
    "mixed_audio_channels": 2,
    "vocal_audio_sample_rate": 44100,
    "vocal_audio_channels": 2,
    "source_separation_engine": "demucs",
    "source_separation_model": "htdemucs",
    "source_separation_device": "",
    "source_separation_shifts": 0,
    "source_separation_segment_seconds": null,
    "source_separation_timeout_seconds": 1800,
    "keep_no_vocals_debug_output": false,
    "keep_demucs_raw_output": false
  }
}
```

如果 00 暂未写入 `reference_media_demux`，01 必须使用上述默认值。

## 5. 输出合同

### 5.1 SessionOutput

必须输出：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
```

可选输出：

```text
SessionOutput/reference/reference_media_manifest.json
```

### 5.2 工具目录输出

必须输出：

```text
S2_01_ReferenceMediaDemux/Working/InputFrom_0_Variables.json
S2_01_ReferenceMediaDemux/Working/Input_Video_Reference_Source.mp4
S2_01_ReferenceMediaDemux/Working/State_progress.json
S2_01_ReferenceMediaDemux/Output/reference_media_demux_manifest.json
S2_01_ReferenceMediaDemux/Report/Result.json
```

可选调试输出：

```text
S2_01_ReferenceMediaDemux/Output/Audio_Reference_NoVocals_Debug.wav
S2_01_ReferenceMediaDemux/Output/demucs_raw/
```

`SessionOutput/` 下不得出现 `dance_mimic_v1/` 或工具名目录。

## 6. 处理流程

### 6.1 Prepare 阶段

1. 校验 workspace 存在且为目录。
2. 读取 `SessionContext/Variables.json`。
3. 解析 `reference_video_path`，必须是 workspace 相对路径。
4. 校验参考视频存在且为文件。
5. 创建 `S2_01_ReferenceMediaDemux/Working|Output|Report/`。
6. 写入 `Working/InputFrom_0_Variables.json`。
7. 复制或硬链接输入视频到 `Working/Input_Video_Reference_Source.mp4`，作为本工具运行快照。
8. 写入 `Working/State_progress.json`，phase 为 `prepare`。

### 6.2 视频去音轨

输出：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
```

推荐命令：

```text
ffmpeg -y -i <input_video> -map 0:v:0 -an -c:v copy <silent_video.mp4>
```

如果 copy 模式失败，允许 fallback 到重编码：

```text
ffmpeg -y -i <input_video> -map 0:v:0 -an -c:v libx264 -preset veryfast -crf 18 -movflags +faststart <silent_video.mp4>
```

要求：

1. 输出视频不得包含音轨。
2. 优先保持源视频时长、fps、宽高、旋转信息。
3. copy 成功时不得无意义重编码。
4. fallback 重编码必须记录在 manifest 和 Result warnings 中。

### 6.3 提取混合音频

输出：

```text
SessionOutput/reference/Audio_Reference_Mixed.wav
```

推荐命令：

```text
ffmpeg -y -i <input_video> -vn -ac <mixed_audio_channels> -ar <mixed_audio_sample_rate> <mixed_audio.wav>
```

要求：

1. mixed audio 是原视频音轨直接提取，不做人声分离。
2. 默认 `sample_rate = 44100`，`channels = 2`。
3. 如果源视频没有音轨，本工具必须 blocked，不得静默生成空音频。
4. 输出 wav 文件必须非空。

### 6.4 人声分离

输入：

```text
SessionOutput/reference/Audio_Reference_Mixed.wav
```

输出：

```text
SessionOutput/reference/Audio_Reference_Vocal.wav
```

推荐实现：

1. 用 mixed audio 作为 Demucs 输入。
2. 执行 `python -m demucs --two-stems vocals -n <model> -o <tmp_demucs_dir> <mixed_audio.wav>`。
3. 从临时输出目录中查找 `vocals.wav`。
4. 将 `vocals.wav` 复制为 `SessionOutput/reference/Audio_Reference_Vocal.wav`。
5. 默认不保留 Demucs 原始目录。
6. 如开启 `keep_no_vocals_debug_output`，把 `no_vocals.wav` 复制到工具 `Output/`，不放入 SessionOutput 标准结果。

推荐命令形态：

```text
python -m demucs --two-stems vocals -n htdemucs -o <tmp_demucs_dir> <mixed_audio.wav>
```

可选参数：

```text
--device cpu|mps|cuda
--shifts <int>
--segment <seconds>
```

要求：

1. Demucs 缺失时返回 blocked，错误码 `missing_demucs`。
2. Demucs 运行失败时返回 blocked，错误码 `source_separation_failed`。
3. 找不到 `vocals.wav` 时返回 blocked，错误码 `vocal_audio_missing`。
4. 输出 `Audio_Reference_Vocal.wav` 必须非空。
5. Demucs 的 stdout / stderr 只能写入工具 Report 或 manifest 摘要，不得污染 SessionOutput。

## 7. Manifest 结构

`S2_01_ReferenceMediaDemux/Output/reference_media_demux_manifest.json` 最低结构：

```json
{
  "schema_version": "dance_mimic_v1_reference_media_demux_0.1",
  "tool": "01_ReferenceMediaDemux",
  "source_video": "SessionContext/Video_Reference_Source.mp4",
  "source_fingerprint": {
    "size_bytes": 0,
    "mtime_ns": 0,
    "fingerprint": ""
  },
  "media_dependencies": {
    "ffmpeg": {"available": true, "path": ""},
    "ffprobe": {"available": true, "path": ""},
    "demucs": {"available": true, "model": "htdemucs"}
  },
  "outputs": {
    "silent_video": "SessionOutput/reference/Video_Reference_Silent.mp4",
    "mixed_audio": "SessionOutput/reference/Audio_Reference_Mixed.wav",
    "vocal_audio": "SessionOutput/reference/Audio_Reference_Vocal.wav"
  },
  "audio_config": {
    "mixed_audio_sample_rate": 44100,
    "mixed_audio_channels": 2,
    "vocal_audio_sample_rate": 44100,
    "vocal_audio_channels": 2
  },
  "source_separation": {
    "engine": "demucs",
    "model": "htdemucs",
    "device": "",
    "shifts": 0,
    "segment_seconds": null,
    "timeout_seconds": 1800,
    "no_vocals_debug_path": ""
  },
  "probes": {
    "silent_video": {},
    "mixed_audio": {},
    "vocal_audio": {}
  },
  "warnings": [],
  "created_at": ""
}
```

建议同时复制一份到：

```text
SessionOutput/reference/reference_media_manifest.json
```

这份 SessionOutput manifest 是给 02 / 03 跨工具读取的语义入口；工具自己的完整审计仍以 `S2_01_ReferenceMediaDemux/Output/reference_media_demux_manifest.json` 为准。

## 8. Result.json 结构

`S2_01_ReferenceMediaDemux/Report/Result.json` 最低结构：

```json
{
  "tool": "01_ReferenceMediaDemux",
  "tool_version": "0.1.0",
  "status": "completed",
  "workspace_dir": "",
  "reads_session_context": [
    "SessionContext/Variables.json",
    "SessionContext/Video_Reference_Source.mp4"
  ],
  "writes_session_output": [
    "SessionOutput/reference/Video_Reference_Silent.mp4",
    "SessionOutput/reference/Audio_Reference_Mixed.wav",
    "SessionOutput/reference/Audio_Reference_Vocal.wav",
    "SessionOutput/reference/reference_media_manifest.json"
  ],
  "created_files": [],
  "prepared_directories": [
    "S2_01_ReferenceMediaDemux/Working",
    "S2_01_ReferenceMediaDemux/Output",
    "S2_01_ReferenceMediaDemux/Report",
    "SessionOutput/reference"
  ],
  "inputs": {
    "variables": "S2_01_ReferenceMediaDemux/Working/InputFrom_0_Variables.json",
    "source_video": "S2_01_ReferenceMediaDemux/Working/Input_Video_Reference_Source.mp4"
  },
  "outputs": {
    "silent_video": "SessionOutput/reference/Video_Reference_Silent.mp4",
    "mixed_audio": "SessionOutput/reference/Audio_Reference_Mixed.wav",
    "vocal_audio": "SessionOutput/reference/Audio_Reference_Vocal.wav",
    "manifest": "S2_01_ReferenceMediaDemux/Output/reference_media_demux_manifest.json",
    "session_manifest": "SessionOutput/reference/reference_media_manifest.json"
  },
  "warnings": [],
  "blocked_reasons": [],
  "resume": false,
  "force": false,
  "updated_at": ""
}
```

blocked 时：

1. `status = "blocked"`。
2. 必须写 `blocked_reasons`。
3. 已经创建的有效输出可以保留，但必须在 `created_files` 和 `outputs` 中明确标记。
4. 不得把半成品伪装为 completed 输出。

## 9. Resume / Force 规则

### 9.1 Resume

当以下条件全部满足时，可以复用已有结果：

1. 三个 SessionOutput 文件都存在且非空。
2. manifest 存在。
3. manifest 中 `source_fingerprint` 与当前输入视频一致。
4. `reference_media_demux` 配置签名一致。
5. `--force` 未开启。

复用时：

1. 不重复运行 ffmpeg / Demucs。
2. `Result.json` 中写入 warning：`reused_completed_output`。
3. `State_progress.json` phase 写为 `finalize`。

### 9.2 Force

`--force` 时必须清理：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
SessionOutput/reference/reference_media_manifest.json
S2_01_ReferenceMediaDemux/
```

清理动作必须记录到 `Result.json.cleanup_actions`。

## 10. 依赖要求

硬依赖：

```text
ffmpeg
ffprobe
python package demucs
```

可复用的现有依赖：

```text
OpenCrew/ToolLibrary/Analysis/media_binaries.py
OpenCrew/ToolLibrary/Analysis_V1/requirements-runtime.txt
OpenCrew/backend/requirements.txt
```

当前仓库中已出现的相关依赖：

```text
static-ffmpeg==3.0
imageio-ffmpeg==0.6.0
torchaudio>=2.11.0
librosa>=0.11.0
soundfile>=0.14.0
```

注意：

1. `demucs` 是本工具人声分离的硬依赖，但当前需要在实现时确认目标运行环境是否已安装。
2. ffmpeg / ffprobe 不要在工具内写死绝对路径。
3. 不要把 Demucs 模型缓存、临时输出树、stderr 全量日志放入 `SessionOutput/`。
4. 如果未来提供云端人声分离 provider，也必须通过 00 写入非密钥配置和 `api_key_ref`，01 不直接落盘真实 key。

## 11. 错误码

推荐 blocked / failed code：

```text
variables_missing
variables_invalid
reference_video_path_not_relative
reference_video_missing
reference_video_not_file
ffmpeg_missing
ffprobe_missing
video_silent_export_failed
video_silent_export_empty
audio_stream_missing
mixed_audio_extraction_failed
mixed_audio_empty
missing_demucs
source_separation_failed
vocal_audio_missing
vocal_audio_empty
output_probe_failed
```

所有错误必须进入 `Report/Result.json`，并尽量保留可恢复信息。

## 12. 与后续工具的关系

02 工具读取：

```text
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/reference_media_manifest.json
```

03 工具读取：

```text
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
SessionOutput/reference/reference_media_manifest.json
```

01 不负责把音频复制到 StoryBoard assets；该动作由 03 在生成标准 StoryBoard Task 时完成。

## 13. 验收标准

最小验收：

1. 输入一个带音轨 mp4，完成后存在三个标准输出文件。
2. `Video_Reference_Silent.mp4` 无音轨。
3. `Audio_Reference_Mixed.wav` 可播放且时长接近源视频。
4. `Audio_Reference_Vocal.wav` 可播放且来自 Demucs vocals stem。
5. `reference_media_demux_manifest.json` 中记录所有输出路径、依赖状态和参数。
6. `Result.json` 中 `status = completed`，且 `blocked_reasons = []`。
7. `SessionOutput/` 下没有工具集名称目录或工具名目录。
8. `--resume` 能复用相同输入和相同配置的完成结果。
9. `--force` 能清理并重跑。

异常验收：

1. 输入视频无音轨时 blocked，错误码 `audio_stream_missing`。
2. ffmpeg 不可用时 blocked，错误码 `ffmpeg_missing`。
3. Demucs 不可用时 blocked，错误码 `missing_demucs`。
4. Demucs 没有生成 `vocals.wav` 时 blocked，错误码 `vocal_audio_missing`。
5. 任何半成品不得被写成 completed。
