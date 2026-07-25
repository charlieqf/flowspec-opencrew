# DanceMimic_V1 02_ReferenceFaceMaskedVideoBuild 工具研究与设计

版本：v0.2

> v0.6 implementation note：02 的默认检测器以 `DanceMimic_V1_实施收敛设计.md` §14.4 为准：V1 默认 `insightface_scrfd`，`deface` 仅作 fallback/对比基线；默认遮盖样式为 `grid_black`，`red_grid_guide` 只作位置引导，不用于隐私遮挡。

状态：已确认实现方向。本文用于指导 `OpenCrew/ToolLibrary/DanceMimic_V1/02_ReferenceFaceMaskedVideoBuild.py` 的正式实现。

## 1. 结论摘要

`02_ReferenceFaceMaskedVideoBuild.py` 的目标是稳定、可审计地把参考视频中每一帧的人脸遮住，并产出后续 StoryBoard / MaxSR2 可直接消费的遮脸参考视频。

已确认第一版默认采用 OpenCV 逐帧直接遮盖：

```text
ffmpeg 切分视频
  -> 人脸检测
  -> 人脸跟踪 / 补帧 / 平滑
  -> bbox 扩张
  -> OpenCV 逐帧绘制遮盖
  -> ffmpeg 规范化输出无音轨遮脸视频
  -> QA 抽帧与 manifest 校验
```

关键判断：

1. 遮脸保障来自 `FaceTrack.json`、逐帧补帧、bbox 扩张、逐帧绘制和输出后 QA。
2. 第一版直接生成最终遮脸参考视频，不做二次叠加合成。
3. 遮盖样式可以是黑色实心块、黑底网格、马赛克、强模糊或红色透明网格引导；默认推荐 `grid_black`。
4. `grid_black` 必须是“黑色实心底 + 网格线”，不能只画线框。
5. 所有可复跑、可审计的信息放在 `FaceTrack.json`、manifest、QA 抽帧报告中。

## 2. 与总文档的关系

02 的职责收敛为：

```text
按分段参数切视频，做人脸检测/跟踪，直接生成遮脸参考视频
```

标准输出为：

```text
SessionOutput/reference/segments/reference_segments_manifest.json
SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4
SessionOutput/reference/segments/Segment_0001_FaceTrack.json
SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4
S3_02_ReferenceFaceMaskedVideoBuild/Report/Result.json
```

下游 03 只消费 `Segment_0001_Reference_FaceMasked.mp4` 和 `reference_segments_manifest.json`。

## 3. GitHub / 开源方案调研

调研日期：2026-06-27。

Star 数是 GitHub 页面当日显示的近似值，仅用于技术选型参考，后续实现前可重新确认。

| 项目 | 当前 Star | 类型 | 适合度 | 说明 |
| --- | ---: | --- | --- | --- |
| [`ORB-HD/deface`](https://github.com/ORB-HD/deface) | 约 1.4k | 现成视频/图片人脸匿名化 CLI | 高 | 最贴近本工具需求。支持视频、图片、黑框、模糊、马赛克、自定义图片替换、mask 扩张、ONNX Runtime 后端。适合 MVP 或 fallback。 |
| [`deepinsight/insightface`](https://github.com/deepinsight/insightface) | 约 29.1k | 人脸检测/识别/分析库 | 高 | 适合作为正式版检测真源。包含 RetinaFace、SCRFD 等高质量人脸检测能力。不是现成遮脸工具，需要本工具自己完成跟踪、遮盖和视频写出。 |
| [`google-ai-edge/mediapipe`](https://github.com/google-ai-edge/mediapipe) | 约 35.8k | 实时感知 pipeline | 中高 | 适合轻量、实时、跨平台场景，可作为检测或辅助跟踪 fallback。对复杂遮挡、小脸、背侧/侧脸场景需要实测。 |
| [`ageitgey/face_recognition`](https://github.com/ageitgey/face_recognition) | 约 56.5k | 人脸识别 API | 中 | Star 很高，但定位是识别/比对，不是视频匿名化。可用来检测，但工程依赖较老，不建议作为本工具默认。 |
| [`hukkelas/DeepPrivacy`](https://github.com/hukkelas/DeepPrivacy) | 约 1.3k | GAN 人脸匿名化 | 中 | 支持图像与视频匿名化，更偏“生成新脸”。适合需要保留自然人脸观感的场景，不适合本工具第一版的确定性遮挡。 |
| [`hukkelas/deep_privacy2`](https://github.com/hukkelas/deep_privacy2) | 约 377 | 真实感人脸/全身匿名化 | 中低 | 研究价值高，但重依赖生成模型，工程复杂，不适合第一版稳定遮盖。 |
| [`understand-ai/anonymizer`](https://github.com/understand-ai/anonymizer) | 约 275 | 车辆场景人脸/车牌匿名化 | 低 | 已归档，且偏自动驾驶传感器图像，不建议接入。 |
| [`ctu-vras/blanket-infant-face-anonym`](https://github.com/ctu-vras/blanket-infant-face-anonym) | 约 3 | 婴儿视频匿名化研究 | 低 | 实验项目，README 也提示仍存在 missing detections 问题，不适合作为生产依赖。 |

调研结论：

1. 如果要最快落地，可以先包装 `deface`，直接产出遮脸视频。
2. 如果要做成 DanceMimic_V1 的长期可维护工具，建议用 `InsightFace/SCRFD` 或同等级检测器作为检测真源，再由本工具自己完成逐帧遮盖。
3. 不建议用生成式匿名化作为第一版主路径，因为它更重、更慢、更难 QA，也不符合“黑框 / 网格线框遮盖”的明确需求。

## 4. 第一版推荐技术路线

默认路线：

```text
detector_engine = insightface_scrfd | deface | mediapipe
face_tracking_engine = iou_kalman
mask_render_engine = opencv
mask_style = grid_black | solid_black | mosaic | blur | red_grid_guide
output_writer = opencv_then_ffmpeg
```

推荐默认配置：

```json
{
  "face_detection_engine": "deface",
  "face_detector_model": "",
  "face_detection_threshold": 0.2,
  "face_detection_scale": "1280x720",
  "face_tracking_engine": "iou_kalman",
  "max_missing_frames_to_interpolate": 10,
  "smooth_window_frames": 5,
  "mask_style": "grid_black",
  "mask_expand_ratio": {
    "left": 0.35,
    "right": 0.35,
    "top": 0.6,
    "bottom": 0.35
  },
  "mask_min_width_pixels": 32,
  "mask_min_height_pixels": 32,
  "grid_line_color": "#2b2b2b",
  "grid_fill_color": "#000000",
  "grid_line_width": 2,
  "grid_cell_size": 18,
  "guide_grid_line_color": "#ff1f1f",
  "guide_grid_border_color": "#ff1f1f",
  "guide_grid_line_width": 1,
  "guide_grid_cell_size": 44,
  "mosaic_cell_size": 18,
  "blur_kernel_size": 81,
  "block_on_face_not_detected": false,
  "qa_sample_frames_per_segment": 8
}
```

## 5. 工具定位

`02_ReferenceFaceMaskedVideoBuild.py` 是 DanceMimic_V1 的核心预处理工具。

它负责：

1. 读取 01 输出的无声参考视频。
2. 按 `storyboard_split_config` 生成 Dialogue 分段。
3. 切出每个 segment 的无声视频。
4. 对每个 segment 做人脸检测、跟踪、补帧、平滑和扩张。
5. 为每个 segment 生成 `FaceTrack.json`。
6. 为每个 segment 直接生成遮脸参考视频。
7. 生成 `reference_segments_manifest.json`，供 03 构建标准 StoryBoard。

它不负责：

1. 不做人声分离。
2. 不生成 StoryBoard JSON。
3. 不调用 MaxSR2 或任何视频生成模型。
4. 不识别具体人物身份。
5. 不把真实 API key、模型 token 或 provider signed URL 写入任何产物。

## 6. 输入合同

prepare 阶段读取：

```text
SessionContext/Variables.json
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/reference_media_manifest.json      # 可选，但建议读取
```

run 阶段读取本工具 Working 快照：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_0_Variables.json
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_1_Video_Reference_Silent.mp4
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_1_reference_media_manifest.json
```

如果 00 暂未写入 `reference_face_masked_video_build`，02 使用第 4 章默认配置。

## 7. 输出合同

### 7.1 SessionOutput

必须输出：

```text
SessionOutput/reference/segments/reference_segments_manifest.json
SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4
SessionOutput/reference/segments/Segment_0001_FaceTrack.json
SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4
...
```

### 7.2 工具目录输出

必须输出：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_0_Variables.json
S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_1_Video_Reference_Silent.mp4
S3_02_ReferenceFaceMaskedVideoBuild/Working/State_progress.json
S3_02_ReferenceFaceMaskedVideoBuild/Output/reference_segments_manifest.json
S3_02_ReferenceFaceMaskedVideoBuild/Report/Result.json
```

建议输出：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Report/qa_samples/
S3_02_ReferenceFaceMaskedVideoBuild/Report/face_mask_qa_report.json
```

可选调试输出：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Output/debug_frames/
S3_02_ReferenceFaceMaskedVideoBuild/Output/detection_raw/
```

调试输出默认不保留，除非 `keep_debug_frames = true`。

## 8. 处理流程

### 8.1 Prepare 阶段

1. 校验 workspace 存在且为目录。
2. 读取 `SessionContext/Variables.json`。
3. 校验 `storyboard_split_config.target_video_seconds >= minimum_video_seconds`。
4. 校验 `SessionOutput/reference/Video_Reference_Silent.mp4` 存在、非空、无音轨。
5. 用 ffprobe 读取源视频时长、fps、frame_count、width、height。
6. 创建 `S3_02_ReferenceFaceMaskedVideoBuild/Working|Output|Report/`。
7. 复制或硬链接无声参考视频到 `Working/InputFrom_1_Video_Reference_Silent.mp4`。
8. 写入 `Working/InputFrom_0_Variables.json`。
9. 写入 `Working/State_progress.json`，phase 为 `prepare`。

### 8.2 分段边界生成

使用 `target_video_seconds` 和 `minimum_video_seconds` 生成 segment 边界。

规则：

1. 每段时长不得超过 `target_video_seconds`。
2. 每段时长不得低于 `minimum_video_seconds`。
3. 如果直接按 target 切分导致尾段过短，必须近似均分。
4. 分段数量就是 03 生成 StoryBoard 的 Dialogue 数量。

推荐算法：

```text
D = source video duration
target = target_video_seconds
minimum = minimum_video_seconds

if target < minimum: blocked
if D < minimum: blocked

n = ceil(D / target)
remainder = D - floor(D / target) * target

if D % target == 0:
  use target-sized segments
else if remainder >= minimum:
  use target-sized segments + tail
else:
  split D into n near-even segments
```

边界必须避免累计浮点误差：

1. 用 frame index 作为内部切分真源。
2. `start_frame` / `end_frame` 决定实际切分。
3. `start` / `end` / `duration` 由 frame index 和 fps 派生。
4. manifest 同时记录秒和帧。

### 8.3 切出 Segment Silent Video

每个 segment 输出：

```text
SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4
```

推荐 ffmpeg：

```text
ffmpeg -y -i <silent_video> -ss <start> -t <duration> -map 0:v:0 -an -c:v libx264 -preset veryfast -crf 18 -movflags +faststart <segment_silent.mp4>
```

说明：

1. 为了 frame-accurate 分段，优先重编码 segment，而不是依赖 copy 模式。
2. 如果未来需要无损，可增加 `segment_cut_mode = copy|reencode`。
3. 输出必须无音轨。

### 8.4 人脸检测

检测目标：

1. 检测所有人脸，不识别具体身份。
2. 原则上宁可 false positive 多遮一点，不可 false negative 漏脸。
3. 对小脸、侧脸、遮挡脸、快速运动脸要保守处理。

第一版可选检测引擎：

#### A. `deface` 引擎

优点：

1. 最贴近目标，已包含视频匿名化 CLI。
2. 支持黑框、马赛克、模糊。
3. 支持 `--mask-scale` 扩张。
4. ONNX Runtime 后端较容易接入。

缺点：

1. 原生命令主要产出 anonymized video，不一定直接产出本工具所需的逐帧 `FaceTrack.json`。
2. 如果检测结果难以提取，需要 fork 或调用内部模块。
3. 长期可维护性不如直接维护自己的检测/跟踪管线。

建议用途：

1. MVP 快速验证。
2. fallback engine。
3. 对比 QA 基线。

#### B. `insightface_scrfd` 引擎

优点：

1. 检测质量高，适合作为长期生产检测真源。
2. SCRFD / RetinaFace 对复杂场景更可靠。
3. 能直接拿到 bbox / confidence，方便写 `FaceTrack.json`。

缺点：

1. 需要新增 Python 依赖和模型缓存管理。
2. macOS / Linux / GPU 环境需要分别验证。

建议用途：

1. 正式版默认引擎。
2. 对漏脸风险要求更高的流程。

#### C. `mediapipe` 引擎

优点：

1. 实时和移动端生态成熟。
2. 适合轻量化部署。

缺点：

1. 对复杂视频场景要实测。
2. 与现有 OpenCrew Python 运行环境的安装兼容性需要确认。

建议用途：

1. fallback engine。
2. 轻量模式。

### 8.5 跟踪、补帧和平滑

人脸检测本身可能存在抖动和短暂漏检，所以必须做后处理。

推荐最小实现：

1. 按 frame 顺序读取 detections。
2. 用 IoU 进行 track 关联。
3. 每条 track 维护 `track_id`、最近 bbox、最近 confidence、missing_frames。
4. 对 missing_frames 小于阈值的空洞做线性插值。
5. 对 bbox 中心点和宽高做滑动窗口平滑。
6. 对平滑后的 bbox 做扩张和边界裁剪。

建议默认：

```text
max_missing_frames_to_interpolate = 10
smooth_window_frames = 5
min_track_length_frames = 2
mask_expand_ratio.top = 0.60
mask_expand_ratio.left/right/bottom = 0.35
```

注意：

1. 人脸隐私保护场景中，扩张后的 bbox 是最终遮盖依据。
2. `FaceTrack.json` 应同时保留原始 bbox、平滑 bbox、扩张 bbox。
3. 如果检测器持续漏检，但前后帧同一 track 明确存在，应插值补齐，避免一两帧露脸。

### 8.6 遮盖绘制

默认使用 OpenCV 逐帧绘制。

支持样式：

| 样式 | 参数 | 隐私强度 | 建议 |
| --- | --- | --- | --- |
| `solid_black` | fill color | 高 | 最稳，适合安全模式 |
| `grid_black` | black fill + grid lines | 高 | 符合“黑框 / 网格线框”视觉需求，推荐默认 |
| `mosaic` | cell size | 中高 | 视觉柔和，但低强度马赛克可能被还原 |
| `blur` | kernel size | 中低 | 不建议作为隐私保护默认 |
| `red_grid_guide` | red border + red grid, transparent fill | 低 | 用户提供示例效果。用于脸部位置引导、检测可视化或低遮挡参考，不作为隐私保护默认 |
| `none` | 无 | 无 | 仅调试 |

推荐默认：

```text
mask_style = grid_black
grid_fill_color = #000000
grid_line_color = #2b2b2b
grid_line_width = 2
grid_cell_size = 18
```

`grid_black` 的实现规则：

1. 先用黑色实心矩形完全覆盖扩张 bbox。
2. 再在黑色矩形内画网格线。
3. 禁止只画外框或只画网格线但中间透明。

`red_grid_guide` 的实现规则：

1. 不填充 bbox 内部区域，只绘制红色外框和红色等距网格线。
2. 网格覆盖 expanded bbox 的完整矩形区域。
3. 默认线宽 1px，颜色 `#ff1f1f`，网格大小可按视频分辨率自适应，1080p 竖屏参考值为 40-48px。
4. 该样式会保留人脸可见性，只适合“脸部位置引导 / 检测可视化 / 低遮挡参考”场景。
5. 如果用户目标是隐私保护或确保脸不可识别，不得单独使用该样式，应使用 `grid_black`、`solid_black` 或 `mosaic`。

### 8.7 输出视频写出

推荐实现：

1. 用 OpenCV `VideoCapture` 逐帧读取 segment。
2. 按 frame_index 查找扩张 bbox。
3. 绘制遮盖。
4. 写出临时 video-only 文件。
5. 用 ffmpeg 规范化编码为 mp4：

```text
ffmpeg -y -i <tmp_masked_video> -map 0:v:0 -an -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 18 -movflags +faststart <Segment_0001_Reference_FaceMasked.mp4>
```

要求：

1. 输出无音轨。
2. 输出时长、fps、画幅与 segment silent video 一致或在容差内。
3. 输出 frame_count 与 segment silent video 一致或在容差内。
4. 不改变非人脸区域，除编码损耗外应尽量保持原画面。

## 9. `FaceTrack.json` 结构

每个 segment 输出：

```text
SessionOutput/reference/segments/Segment_0001_FaceTrack.json
```

最低结构：

```json
{
  "schema_version": "dance_mimic_v1_face_track_0.1",
  "segment_id": "segment_0001",
  "dialogue_asset_key": "dak_0001",
  "source_segment_video": "SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4",
  "masked_segment_video": "SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4",
  "fps": 25.0,
  "width": 1920,
  "height": 1080,
  "frame_count": 200,
  "duration": 8.0,
  "detection_engine": {
    "name": "insightface_scrfd",
    "model": "",
    "threshold": 0.2,
    "scale": "1280x720"
  },
  "tracking_engine": {
    "name": "iou_kalman",
    "max_missing_frames_to_interpolate": 10,
    "smooth_window_frames": 5
  },
  "mask_config": {
    "render_engine": "opencv",
    "style": "grid_black",
    "expand_ratio": {
      "left": 0.35,
      "right": 0.35,
      "top": 0.6,
      "bottom": 0.35
    }
  },
  "frames": [
    {
      "frame_index": 0,
      "timestamp": 0.0,
      "faces": [
        {
          "track_id": "face_0001",
          "confidence": 0.94,
          "bbox": [800, 220, 1040, 520],
          "smoothed_bbox": [798, 218, 1042, 522],
          "expanded_bbox": [713, 38, 1127, 627],
          "source": "detected",
          "masked": true
        }
      ]
    }
  ],
  "summary": {
    "detected_face_tracks": 1,
    "frames_with_faces": 200,
    "frames_without_faces": 0,
    "interpolated_frames": 3,
    "masked_frames": 200,
    "warnings": []
  }
}
```

字段规则：

1. bbox 统一为 `[x1, y1, x2, y2]`，单位为像素。
2. `expanded_bbox` 必须已裁剪到画幅内。
3. `source` 可取 `detected`、`interpolated`、`carried_forward`、`none`。
4. `masked = true` 表示该 face entry 已用于最终遮盖。
5. 即使某帧无人脸，也应有 frame 记录，方便 QA 逐帧追溯。

## 10. `reference_segments_manifest.json` 结构

目标路径：

```text
SessionOutput/reference/segments/reference_segments_manifest.json
```

最低结构：

```json
{
  "schema_version": "dance_mimic_v1_reference_segments_0.2",
  "tool": "02_ReferenceFaceMaskedVideoBuild",
  "source_video": "SessionOutput/reference/Video_Reference_Silent.mp4",
  "source_fingerprint": {
    "size_bytes": 0,
    "mtime_ns": 0,
    "sha256": ""
  },
  "source_video_probe": {
    "duration": 0.0,
    "fps": 0.0,
    "frame_count": 0,
    "width": 0,
    "height": 0,
    "has_audio": false
  },
  "split_config": {
    "target_video_seconds": 8.0,
    "minimum_video_seconds": 4.0,
    "split_algorithm": "frame_accurate_near_even_tail_guard"
  },
  "face_mask_config": {
    "face_detection_engine": "insightface_scrfd",
    "face_tracking_engine": "iou_kalman",
    "mask_render_engine": "opencv",
    "mask_style": "grid_black",
    "block_on_face_not_detected": false
  },
  "segments": [
    {
      "segment_id": "segment_0001",
      "dialogue_asset_key": "dak_0001",
      "index": 1,
      "start": 0.0,
      "end": 8.0,
      "duration": 8.0,
      "start_frame": 0,
      "end_frame": 199,
      "frame_count": 200,
      "silent_video_path": "SessionOutput/reference/segments/Segment_0001_Reference_Silent.mp4",
      "face_track_path": "SessionOutput/reference/segments/Segment_0001_FaceTrack.json",
      "face_masked_reference_video_path": "SessionOutput/reference/segments/Segment_0001_Reference_FaceMasked.mp4",
      "face_summary": {
        "detected_face_tracks": 1,
        "frames_with_faces": 200,
        "frames_without_faces": 0,
        "interpolated_frames": 3,
        "masked_frames": 200
      },
      "qa": {
        "status": "passed",
        "sample_sheet_path": "S3_02_ReferenceFaceMaskedVideoBuild/Report/qa_samples/Segment_0001_QA.jpg",
        "warnings": []
      },
      "warnings": []
    }
  ],
  "warnings": [],
  "created_at": ""
}
```

建议同时复制一份到：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Output/reference_segments_manifest.json
```

`SessionOutput/reference/segments/reference_segments_manifest.json` 是 03 的跨工具读取入口；工具目录里的 manifest 是本工具审计副本。

## 11. Result.json 结构

目标路径：

```text
S3_02_ReferenceFaceMaskedVideoBuild/Report/Result.json
```

最低结构：

```json
{
  "tool": "02_ReferenceFaceMaskedVideoBuild",
  "tool_version": "0.1.0",
  "status": "completed",
  "workspace_dir": "",
  "reads_session_context": [
    "SessionContext/Variables.json"
  ],
  "reads_session_output": [
    "SessionOutput/reference/Video_Reference_Silent.mp4",
    "SessionOutput/reference/reference_media_manifest.json"
  ],
  "writes_session_output": [
    "SessionOutput/reference/segments/reference_segments_manifest.json"
  ],
  "created_files": [],
  "prepared_directories": [
    "S3_02_ReferenceFaceMaskedVideoBuild/Working",
    "S3_02_ReferenceFaceMaskedVideoBuild/Output",
    "S3_02_ReferenceFaceMaskedVideoBuild/Report",
    "SessionOutput/reference/segments"
  ],
  "inputs": {
    "variables": "S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_0_Variables.json",
    "silent_video": "S3_02_ReferenceFaceMaskedVideoBuild/Working/InputFrom_1_Video_Reference_Silent.mp4"
  },
  "outputs": {
    "manifest": "S3_02_ReferenceFaceMaskedVideoBuild/Output/reference_segments_manifest.json",
    "session_manifest": "SessionOutput/reference/segments/reference_segments_manifest.json"
  },
  "segment_count": 0,
  "face_mask_summary": {
    "segments_completed": 0,
    "segments_with_faces": 0,
    "segments_without_faces": 0,
    "total_detected_face_tracks": 0,
    "total_masked_frames": 0
  },
  "warnings": [],
  "blocked_reasons": [],
  "resume": false,
  "force": false,
  "updated_at": ""
}
```

状态：

```text
completed
completed_with_warnings
blocked
failed
```

规则：

1. 所有 segment 都产出遮脸视频且 QA 通过时，`completed`。
2. 部分 segment 无人脸但参数允许继续时，`completed_with_warnings`。
3. 缺依赖、缺输入、参数非法、检测器不可用时，`blocked`。
4. 代码异常或不可预期错误时，`failed`。

## 12. QA 与验收

### 12.1 自动 QA

每个 segment 至少执行：

1. ffprobe 检查 `Segment_0001_Reference_Silent.mp4`。
2. ffprobe 检查 `Segment_0001_Reference_FaceMasked.mp4`。
3. 对比 duration / fps / width / height / frame_count。
4. 检查输出视频无音轨。
5. 检查 `FaceTrack.json` frame_count 与视频 frame_count 一致。
6. 抽取首帧、中间帧、尾帧和若干随机帧生成 QA sheet。
7. QA sheet 上叠加 expanded bbox，方便人工复核。

### 12.2 遮盖 QA

最低遮盖 QA：

1. 如果某帧 `faces[]` 非空，则输出遮脸帧中对应 expanded bbox 区域必须发生明显像素变化。
2. 对 `solid_black` / `grid_black`，expanded bbox 区域的黑色像素占比必须高于阈值。
3. 对 `mosaic` / `blur`，expanded bbox 区域必须与原帧存在足够差异。
4. 对 `red_grid_guide`，只校验红色外框和网格线是否覆盖 expanded bbox，不执行隐私遮盖强度判断。
5. 如果检测器在遮脸后重新检测仍能检测到人脸，应写 warning；`red_grid_guide` 例外，因为该样式本身保留人脸可见性。

推荐阈值：

```text
grid_black_black_pixel_ratio_min = 0.60
solid_black_black_pixel_ratio_min = 0.85
masked_region_diff_mean_min = 15.0
post_mask_face_detection_warning = true
red_grid_guide_line_presence_min = 0.95
```

说明：

1. 自动 QA 不能完全证明没有漏脸，但能发现常见坐标错位、遮盖未写入、输出视频错误。
2. 第一版必须生成抽帧 QA sheet，便于人工快速检查。

### 12.3 人工验收

最小验收：

1. 输入一个 20-40 秒含人脸参考视频。
2. 02 自动切成多个 segment。
3. 每个 segment 有 silent video、FaceTrack、face masked video。
4. face masked video 无音轨。
5. 人脸区域被黑色实心或网格黑框覆盖。
6. 不出现 1-2 帧脸突然露出的明显闪烁。
7. `reference_segments_manifest.json` 中 segment 数量与分段规则一致。
8. 03 能读取 manifest 并把遮脸参考视频复制到 StoryBoard assets。

异常验收：

1. 输入视频缺失时 blocked。
2. `target_video_seconds < minimum_video_seconds` 时 blocked。
3. 输入视频总时长低于 minimum 时 blocked。
4. 检测器不可用时 blocked。
5. 某个 segment 检测不到脸且 `block_on_face_not_detected = false` 时 completed_with_warnings。
6. 某个 segment 检测不到脸且 `block_on_face_not_detected = true` 时 blocked。
7. 输出视频有音轨时 failed 或 blocked，不得 completed。

## 13. Resume / Force 规则

### 13.1 Resume

当以下条件全部满足时，可以复用已有结果：

1. `reference_segments_manifest.json` 存在。
2. 所有 segment 的 silent video、FaceTrack、face masked video 存在且非空。
3. manifest 中 `source_fingerprint` 与当前输入视频一致。
4. `split_config` 签名一致。
5. `face_mask_config` 签名一致。
6. QA 状态为 passed 或 warning 可接受。
7. `--force` 未开启。

复用时：

1. 不重复切分、不重复检测、不重复遮盖。
2. `Result.json` 中写 warning：`reused_completed_output`。
3. `State_progress.json` phase 写为 `finalize`。

### 13.2 Force

`--force` 时必须清理：

```text
SessionOutput/reference/segments/
S3_02_ReferenceFaceMaskedVideoBuild/
```

但不得清理：

```text
SessionContext/
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
SessionOutput/storyboard/
其它工具目录
```

清理动作必须记录到 `Result.json.cleanup_actions`。

## 14. 依赖要求

硬依赖：

```text
ffmpeg
ffprobe
opencv-python-headless
numpy
```

检测引擎依赖，至少启用一种：

```text
deface
onnxruntime
insightface
mediapipe
```

推荐第一版依赖组合：

```text
opencv-python-headless
numpy
onnxruntime
deface
```

正式版可升级为：

```text
opencv-python-headless
numpy
onnxruntime
insightface
```

注意：

1. ffmpeg / ffprobe 复用 `OpenCrew.ToolLibrary.Analysis.media_binaries.find_ffmpeg()` 和 `find_ffprobe()`。
2. 不在工具里写死 ffmpeg 绝对路径。
3. 模型文件缓存不得写入 `SessionOutput/`。
4. 如果检测引擎需要下载模型，应通过依赖安装或模型缓存管理完成，不在运行报告中记录 signed URL。

## 15. 错误码

推荐 blocked / failed code：

```text
variables_missing
variables_invalid
split_config_missing
split_config_invalid
target_less_than_minimum
source_duration_less_than_minimum
silent_video_missing
silent_video_empty
silent_video_has_audio
ffmpeg_missing
ffprobe_missing
opencv_missing
face_detection_engine_missing
face_detection_engine_invalid
face_detection_failed
face_track_build_failed
segment_cut_failed
segment_cut_empty
face_mask_write_failed
face_masked_video_empty
face_masked_video_has_audio
face_not_detected
qa_failed
output_probe_failed
manifest_write_failed
```

错误处理规则：

1. 可恢复的依赖/输入/参数问题用 `blocked`。
2. 检测不到脸默认 warning，除非 `block_on_face_not_detected = true`。
3. 输出视频生成失败、QA 严重失败、manifest 写失败用 `failed`。
4. 所有错误必须进入 `Report/Result.json`，不得只输出到 stdout。

## 16. 与 03 的关系

03 只读取：

```text
SessionContext/Variables.json
SessionOutput/reference/Audio_Reference_Mixed.wav
SessionOutput/reference/Audio_Reference_Vocal.wav
SessionOutput/reference/segments/reference_segments_manifest.json
```

03 需要使用 02 manifest 中的：

```text
segments[].dialogue_asset_key
segments[].start
segments[].end
segments[].duration
segments[].face_masked_reference_video_path
segments[].warnings
```

03 不应重新检测人脸、不应重新切分视频、不应扫描 segment 目录猜结构。

## 17. 实现前确认事项

### 17.1 最小文件与产出物审查

第一版必须生成：

```text
reference_segments_manifest.json
Segment_NNNN_Reference_Silent.mp4
Segment_NNNN_FaceTrack.json
Segment_NNNN_Reference_FaceMasked.mp4
Result.json
```

建议生成：

```text
face_mask_qa_report.json
qa_samples/*.jpg
```

不建议默认长期保留：

```text
debug_frames/
detection_raw/
临时逐帧图片序列
模型原始日志全文
```

### 17.2 数据库连接审查

02 默认不连接数据库。

需要的信息必须来自：

```text
SessionContext/Variables.json
SessionOutput/reference/Video_Reference_Silent.mp4
SessionOutput/reference/reference_media_manifest.json
```

如果缺少必要字段，02 返回 blocked，不自行查库补救。

### 17.3 SessionContext 写入审查

02 默认不写 `SessionContext/Variables.json`。

如果未来需要新增检测配置，应由 00 写入 `reference_face_masked_video_build`，02 只读。

### 17.4 产出物与下游消费审查

下游 03 的核心依赖是：

```text
reference_segments_manifest.json
segments[].face_masked_reference_video_path
segments[].dialogue_asset_key
segments[].start/end/duration
```

如果这些缺失，03 必须 blocked。

### 17.5 Rerun 与断点续跑审查

02 必须支持：

```text
prepare -> split -> detect_track -> render_mask -> qa -> finalize
```

`Working/State_progress.json` 记录：

```json
{
  "phase": "detect_track",
  "current_segment_id": "segment_0001",
  "completed_segments": ["segment_0001"],
  "updated_at": ""
}
```

断点续跑只复用已完成且 QA 通过的 segment。

## 18. 建议落地顺序

第一阶段：MVP

1. 实现分段算法和 segment silent video 输出。
2. 包装 `deface` 或内部检测模块跑通黑框/马赛克遮脸。
3. 写出最小 `FaceTrack.json` 和 `reference_segments_manifest.json`。
4. 接入 QA 抽帧。
5. 让 03 能读取遮脸视频。

第二阶段：生产化

1. 引入 `insightface_scrfd` 作为正式检测引擎。
2. 增加 IoU/Kalman track、插值、平滑。
3. 增加 post-mask re-detect QA。
4. 增加多脸、多小脸、侧脸、快速运动回归样例。

第三阶段：增强

1. 支持手工补框 / 修框数据导入。
2. 支持车牌或其它敏感区域遮盖。
3. 支持 per-segment 参数覆盖。

## 19. 参考链接

1. `deface`: https://github.com/ORB-HD/deface
2. `InsightFace`: https://github.com/deepinsight/insightface
3. `MediaPipe`: https://github.com/google-ai-edge/mediapipe
4. `face_recognition`: https://github.com/ageitgey/face_recognition
5. `DeepPrivacy`: https://github.com/hukkelas/DeepPrivacy
6. `DeepPrivacy2`: https://github.com/hukkelas/deep_privacy2
7. `understand-ai/anonymizer`: https://github.com/understand-ai/anonymizer
8. `BLANKET`: https://github.com/ctu-vras/blanket-infant-face-anonym
