# DanceMimic_V1 03_StoryBoardStandardTaskBuild 工具实现需求

版本：v0.1  
状态：implementation-ready  
上游：01_ReferenceMediaDemux、02_ReferenceFaceMaskedVideoBuild  
下游：StoryBoard 页面、04/05 生成链路  

## 1. 目标

`03_StoryBoardStandardTaskBuild` 负责把 DanceMimic 的参考视频分析结果转换成现有 StoryBoard 页面可以打开、编辑、继续生成的标准任务。

该工具不做模型生成，不调用 Seedance/OpenRouter，也不把参考视频当成最终产物。它只完成三件事：

1. 读取 02 生成的分段与人脸遮罩参考视频 manifest。
2. 生成 StoryBoard 标准源文件、DanceMimic 后续生成 seed、参考视频资产副本。
3. 将任务推进到可在 StoryBoard 页面编辑的状态。

## 2. 输入合同

### 2.1 必需输入

根目录由 `--workspace` 指定，以下路径均相对 session workspace。

| 路径 | 来源 | 要求 |
| --- | --- | --- |
| `SessionContext/Variables.json` | 00 | 必须包含 `source_video_path`；不得要求 `reference_video_path` |
| `SessionOutput/reference/reference_media_manifest.json` | 01 | 必须存在，用于读取参考视频、音频、时长等基础信息 |
| `SessionOutput/reference/segments/reference_segments_manifest.json` | 02 | 必须存在，是 03 的主输入 |
| `reference_segments_manifest.segments[].face_masked_reference_video_path` | 02 | 每个 segment 必须存在且文件可读 |

`source_video_path` 是 00/01/02/03 的统一变量名。DB 层和任务 meta 可以继续保留 `reference_video_path`，但工具脚本内部不再读取 `Variables.json.reference_video_path`。

### 2.2 可选输入

| 路径 | 要求 |
| --- | --- |
| `SessionOutput/reference/Audio_Reference_Mixed.wav` | 存在时复制到 StoryBoard audio assets，作为后续默认音频候选 |
| `SessionOutput/reference/Audio_Reference_Vocal.wav` | 存在时复制到 StoryBoard audio assets，供后续口型/节奏使用 |
| `SessionOutput/reference/Audio_Reference_BGM.wav` | 存在时记录到 seed，MVP 不要求 StoryBoard 直接使用 |

音频缺失不阻断 03，除非产品明确要求 DanceMimic 必须带原视频音频。缺失音频必须写入 warning。

### 2.3 segment 最小字段

`reference_segments_manifest.json` 中每个 segment 至少需要：

```json
{
  "segment_id": "segment_0001",
  "index": 1,
  "start_seconds": 0.0,
  "end_seconds": 3.2,
  "duration_seconds": 3.2,
  "dialogue_asset_key": "dak_0001",
  "face_masked_reference_video_path": "SessionOutput/reference/segments/segment_0001/face_masked_reference.mp4"
}
```

`dialogue_asset_key` 优先由 02 写入。若历史 02 输出缺少该字段，03 可以按 segment 顺序生成 `dak_%04d`，但必须保证同一个 manifest 在重复运行时得到相同 key。

## 3. 输出合同

### 3.1 StoryBoard 源文件

必须写入：

```text
SessionOutput/storyboard/srt_storyboard.json
```

推荐 schema：

```json
{
  "schema_version": "analysis_v1_srt_storyboard_0.2",
  "workflow_id": "dance_mimic_v1",
  "source_type": "dance_mimic_v1_storyboard",
  "task_summary": "DanceMimic reference motion storyboard",
  "video_formula": "dance_mimic_motion_reference",
  "shots": [
    {
      "shot_id": "shot_001",
      "shot_name": "DanceMimic reference motion",
      "start": 0.0,
      "end": 6.4,
      "duration": 6.4,
      "scenes": [
        {
          "scene_id": "scene_001",
          "scene_name": "Reference dance motion",
          "start": 0.0,
          "end": 6.4,
          "duration": 6.4,
          "dialogue_items": [
            {
              "srt_id": "srt_0001",
              "dialogue_asset_key": "dak_0001",
              "dialogue": "Dance motion segment 0001",
              "start": 0.0,
              "end": 3.2,
              "duration": 3.2,
              "image_path": "",
              "dance_mimic": {
                "source_segment_id": "segment_0001",
                "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
                "reference_video_role": "dance_mimic_segment_motion_reference"
              },
              "working_assets": {
                "audio": {
                  "slot": "Audio_Final",
                  "source_type": "",
                  "path": ""
                },
                "images": [
                  {
                    "slot": "Image_New",
                    "source_type": "",
                    "path": ""
                  },
                  {
                    "slot": "Image_02",
                    "source_type": "",
                    "path": ""
                  }
                ],
                "video": {
                  "slot": "Video_Final",
                  "source_type": "",
                  "path": ""
                }
              }
            }
          ]
        }
      ]
    }
  ]
}
```

约束：

- 03 MVP 使用 `1 Shot + 1 Scene + N Dialogue`。
- `dialogue_items[].dialogue_asset_key` 必须显式写入，且全局唯一。
- `dance_mimic.reference_video_path` 指向参考视频资产副本，不指向最终生成视频。
- `working_assets.video.path` 初始为空，不能把参考视频写成 `Video_Final`。
- `dialogue` 可以使用占位文本；该字段只是为了兼容现有 StoryBoard 文案结构，不代表口播台词。

### 3.2 DanceMimic seed

必须写入：

```text
SessionOutput/storyboard/storyboard_seed.json
```

schema：

```json
{
  "schema_version": "dance_mimic_v1_storyboard_seed_0.1",
  "workflow_id": "dance_mimic_v1",
  "task_id": 123,
  "session_id": 207,
  "source_video_path": "SessionContext/Video_Reference_Source.mp4",
  "reference_media_manifest_path": "SessionOutput/reference/reference_media_manifest.json",
  "reference_segments_manifest_path": "SessionOutput/reference/segments/reference_segments_manifest.json",
  "mixed_audio_path": "SessionOutput/storyboard/assets/audios/Audio_Reference_Mixed.wav",
  "vocal_audio_path": "SessionOutput/storyboard/assets/audios/Audio_Reference_Vocal.wav",
  "segments": [
    {
      "segment_id": "segment_0001",
      "index": 1,
      "srt_id": "srt_0001",
      "dialogue_asset_key": "dak_0001",
      "start_seconds": 0.0,
      "end_seconds": 3.2,
      "duration_seconds": 3.2,
      "reference_video_path": "SessionOutput/storyboard/assets/videos/dak_0001_Reference_FaceMasked.mp4",
      "source_face_masked_reference_video_path": "SessionOutput/reference/segments/segment_0001/face_masked_reference.mp4",
      "video_generation_mode": "dance_mimic_reference_video",
      "provider": "openrouter",
      "model": "bytedance/seedance-2.0",
      "model_alias": "MaxSR2",
      "reference_mode": "input_references",
      "prompt_template": "Video_SDR2V_DanceMimic.md",
      "reference_video_role": "dance_mimic_segment_motion_reference"
    }
  ],
  "warnings": []
}
```

后续 05_01/05_05 必须优先从 `storyboard_seed.json` 读取每个 `dialogue_asset_key` 对应的 `reference_video_path`，并把 `provider/model/reference_mode/prompt_template` 写入视频计划。执行侧由 05_02/05_06 按主收敛文档 §8.3 强制命中 `video_openrouter.py` 的 `input_references`。

### 3.3 参考视频资产

必须按 segment 复制：

```text
SessionOutput/storyboard/assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4
```

复制要求：

- 目标文件名只使用 `dialogue_asset_key` 和固定后缀，避免依赖原始 segment 文件名。
- 同一次运行内目标路径不能冲突。
- 复制完成后必须校验目标文件存在且非空。
- 若源文件不存在或为空，03 失败，Result 状态为 `failed`。

### 3.4 音频资产

存在则复制：

```text
SessionOutput/storyboard/assets/audios/Audio_Reference_Mixed.wav
SessionOutput/storyboard/assets/audios/Audio_Reference_Vocal.wav
SessionOutput/storyboard/assets/audios/Audio_Reference_BGM.wav
```

不要求把音频绑定到每个 dialogue 的 `working_assets.audio.path`。MVP 只保证资产和 seed 可被下游读取。

### 3.5 Report

必须写入：

```text
S4_03_StoryBoardStandardTaskBuild/Report/Result.json
```

最小结构：

```json
{
  "schema_version": "dance_mimic_v1_tool_result_0.1",
  "tool_id": "03_StoryBoardStandardTaskBuild",
  "workflow_id": "dance_mimic_v1",
  "status": "succeeded",
  "task_id": 123,
  "session_id": 207,
  "outputs": {
    "srt_storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
    "storyboard_seed_path": "SessionOutput/storyboard/storyboard_seed.json",
    "video_asset_count": 2
  },
  "warnings": []
}
```

失败时 `status=failed`，并写入 `error.code`、`error.message`、`error.details`。

## 4. 不输出项

MVP 不要求 03 预生成：

```text
SessionOutput/storyboard/koubo_storyboard_edit.json
```

现有 StoryBoard 后端可以从 `srt_storyboard.json` lazy normalize 出 edit schema。实现前必须验证：

1. explicit `dialogue_asset_key` 会被 `normalize_source_plan()` 保留。
2. `recalculate()` 不会在 key 唯一时改写该 key。
3. workflow-aware 改造后，lazy normalize 生成的 meta/title/source_type 不再硬编码为口播。

若后续选择预生成 edit 文件，也必须使用同一批 `dialogue_asset_key`，不能引入第二套 key 生成逻辑。

## 5. 幂等与重跑

### 5.1 默认幂等

同一 workspace、同一 manifest、同一参数重复运行，输出内容应稳定：

- `dialogue_asset_key` 不变化。
- segment 顺序不变化。
- `srt_storyboard.json` 和 `storyboard_seed.json` 语义不变化。
- 已存在且内容一致的资产可以跳过复制。

### 5.2 已存在 StoryBoard 时

若以下任一文件已存在，且未传 `--force`：

```text
SessionOutput/storyboard/srt_storyboard.json
SessionOutput/storyboard/koubo_storyboard_edit.json
SessionOutput/storyboard/storyboard_seed.json
SessionOutput/storyboard/Working/
```

工具必须失败并返回：

```json
{
  "error": {
    "code": "storyboard_existing_requires_force"
  }
}
```

这样避免覆盖用户已经在 StoryBoard 页面中编辑或生成的内容。

### 5.3 force 重建

传入 `--force` 时，必须先归档旧产物，再写入新产物。归档目录：

```text
SessionOutput/storyboard/_archive/{YYYYMMDD_HHMMSS}/
```

至少归档：

- `srt_storyboard.json`
- `koubo_storyboard_edit.json`
- `storyboard_seed.json`
- `Working/`
- `assets/videos/*_Reference_FaceMasked.mp4`

不要删除与本次 03 无关的用户上传素材；无法明确归属时只归档 manifest 中可追踪的文件。

## 6. 任务状态与 DB/meta 更新

03 成功后，必须把任务推进到 StoryBoard 可编辑状态。

### 6.1 文件 meta

更新或写入：

```text
SessionOutput/task_list/task_meta.json
```

推荐字段：

```json
{
  "workflow_id": "dance_mimic_v1",
  "workflow_mode": "dance_mimic",
  "title": "DanceMimic",
  "source_type": "dance_mimic_v1_storyboard",
  "storyboard_status": "generated",
  "storyboard_path": "SessionOutput/storyboard/srt_storyboard.json",
  "storyboard_seed_path": "SessionOutput/storyboard/storyboard_seed.json",
  "reference_video_path": "SessionContext/Video_Reference_Source.mp4"
}
```

### 6.2 DB

若任务已注册到 `openclip_tasks`，03 成功后应更新：

| 字段 | 值 |
| --- | --- |
| `workflow_mode` | `dance_mimic` |
| `status` | `editable` |
| `reference_video_path` | 原始参考视频路径 |

`workflow_mode` 若当前 DB schema 尚不存在，必须先落地 migration/ensure 逻辑；否则前端无法区分口播与 DanceMimic。

## 7. CLI 合同

推荐命令：

```bash
python ToolLibrary/DanceMimic_V1/03_StoryBoardStandardTaskBuild.py \
  --workspace /path/to/session/workspace \
  --task-id 123 \
  --session-id 207 \
  --print-json
```

参数：

| 参数 | 必需 | 说明 |
| --- | --- | --- |
| `--workspace` | 是 | session workspace 根目录 |
| `--task-id` | 否 | 有 DB 任务时传入 |
| `--session-id` | 否 | 有 DB session 时传入 |
| `--force` | 否 | 允许归档并重建已有 StoryBoard |
| `--print-json` | 否 | stdout 输出 Result 摘要，便于 runner 收集 |

该工具不需要模型 provider、API key、prompt 参数。

## 8. 失败码

| code | 场景 |
| --- | --- |
| `missing_source_video_path` | `Variables.json.source_video_path` 缺失 |
| `missing_reference_media_manifest` | 01 manifest 缺失 |
| `missing_reference_segments_manifest` | 02 manifest 缺失 |
| `empty_reference_segments` | segment 数为 0 |
| `duplicate_dialogue_asset_key` | key 重复 |
| `missing_face_masked_reference_video` | segment 对应 face-masked 视频缺失 |
| `empty_face_masked_reference_video` | segment 对应 face-masked 视频为空 |
| `storyboard_existing_requires_force` | 已有 StoryBoard 且未 force |
| `archive_failed` | force 归档失败 |
| `write_output_failed` | 输出写入失败 |
| `db_update_failed` | 文件产物已生成但 DB 更新失败 |

若 `db_update_failed` 发生，Result 必须保留已生成文件路径，并把 `status` 写为 `partial_failed`，由任务层决定是否允许用户进入 StoryBoard。

## 9. 与 StoryBoard 页面适配点

03 自身只写标准文件，但 implementation-ready 依赖以下页面/后端适配同时完成：

1. StoryBoard 后端读取 `workflow_mode=dance_mimic` 后，title/source_type/空态文案不能继续硬编码为“口播”。
2. `/api/koubo-storyboard/tasks` 和 task detail 必须返回可区分的 `workflow_mode`。
3. StoryBoard 页面在 DanceMimic 模式下仍复用现有编辑、资产、Working、聊天/任务视图，但文案要避免“台词生成”“口播视频”等误导。
4. `Working/` 仍是最终生成产物目录；03 只准备参考资产，不在 `Working/` 中伪造最终视频。

## 10. 验收用例

### 10.1 单 segment 成功

输入 1 个 segment，运行 03 后必须生成：

- `srt_storyboard.json`
- `storyboard_seed.json`
- 1 个 `assets/videos/{dialogue_asset_key}_Reference_FaceMasked.mp4`
- `Result.json`

StoryBoard 页面能打开该任务，且 dialogue key 与 manifest 一致。

### 10.2 多 segment 成功

输入 N 个 segment：

- StoryBoard 中必须出现 N 个 dialogue item。
- 每个 item 的 `dialogue_asset_key` 唯一且稳定。
- `storyboard_seed.segments.length == N`。
- 每个 seed segment 的 `reference_video_path` 均存在。

### 10.3 缺少 face-masked 视频失败

任一 segment 的 `face_masked_reference_video_path` 不存在时：

- 03 失败。
- 不生成不完整的 `srt_storyboard.json`。
- `Result.json.error.code == "missing_face_masked_reference_video"`。

### 10.4 已有 StoryBoard 保护

已有 `srt_storyboard.json` 或 `Working/` 且未传 `--force` 时：

- 03 失败。
- 不覆盖原文件。
- `Result.json.error.code == "storyboard_existing_requires_force"`。

### 10.5 force 归档

传 `--force` 时：

- 旧 StoryBoard 产物进入 `_archive/{timestamp}/`。
- 新 `srt_storyboard.json` 和 seed 写入当前路径。
- 新 key 仍由 manifest 决定；若 manifest 未变，key 不变。

### 10.6 lazy normalize key 保持

通过 StoryBoard detail API 触发 lazy normalize 后：

- `koubo_storyboard_edit.json` 中的 `dialogue_asset_key` 与 `srt_storyboard.json` 一致。
- `dance_mimic.reference_video_path` 未丢失。
- `source_type` 不再返回 `analysis_v1_storyboard`。

### 10.7 05 seed 读取

05_01 或 05_05 读取 `storyboard_seed.json` 后：

- 能按 `dialogue_asset_key` 找到每段 `reference_video_path`。
- 传给 Seedance SDR2V DanceMimic 的 `reference_video_path` 是 `assets/videos/*_Reference_FaceMasked.mp4`。
- 不从 `Variables.json.reference_video_path` 读取参考视频。

## 11. 非目标

- 不做人脸检测、遮罩、裁剪；这些属于 02。
- 不做图像生成或视频生成；这些属于 04/05。
- 不执行 MaxSR2/OpenRouter 路由或模型调用；03 只按主收敛文档 §8.3 写 seed 字段，真正路由由 05_01/05_05/05_02/05_06 适配。
- 不改变 StoryBoard 页面主交互模型；DanceMimic 继续复用现有 StoryBoard 资产与任务视图。
