# OpenCut V1 画面分析：Scene Detect 需求与实现设计

状态：底层子工具历史参考。本文只描述 M0–M4 单中点画面结构阶段；R1 四帧 structure v2、`03_03` 视觉语义、发布原子性、stale 和综合分析编排以[素材库开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)为准。

## 1. 目标

本期将素材视频处理为“一个 Scene Detect 镜头片段、一个源视频时间范围、一张关键帧”的只读索引。画面分析只依据视频画面切换检测边界，不读取对白，不执行 ASR、OCR、LLM 或 VLM，也不产生人物、动作、物体和场景语义描述。

本期结果是视觉镜头边界索引，不是最终视觉语义拆分。后续纯图像理解与综合分析在该稳定基线上继续扩展。

## 2. 本期范围

本期包含：

- 独立画面分析 Tool Use Session；
- 视频元数据读取；
- PySceneDetect `ContentDetector + AdaptiveDetector` 双检测；
- 相近切点合并与过短 Scene 合并；
- 完整连续 Scene 时间轴；
- 每个 Scene 的中点关键帧提取；
- Scene、源视频虚拟片段和关键帧一一对应；
- 独立运行状态、进度、错误和总运行时间；
- 素材详情页画面分析 Tab 的只读结果展示。

本期不包含：

- 对白分析结果读取或边界对齐；
- ASR、字幕 OCR 或文本理解；
- 人物、动作、物体、场景、镜头类型识别；
- 视觉语义摘要；
- 废片、口误、磕巴或质量判断；
- 物理 MP4 切片；
- 综合分析。

## 3. 独立性边界

- 所有运行时代码位于 `ToolLibrary/OpenCut_V1/`。
- 可以复制 `ToolLibrary/Analysis/04_pyscenedetect_runner.py` 的算法实现，但不得 import `ToolLibrary/Analysis` 或 `ToolLibrary/Analysis_V1`。
- OpenCut V1 拥有独立工具脚本、registry、输出合同和后端服务。
- 可以复用 OpenCrew Tool Use Session 框架、数据库、Session 文件服务、FFmpeg 和 PySceneDetect Python 包。
- 画面分析使用独立 Tool Use Session，不读取或覆盖对白分析 Tool Use Session。

## 4. 执行链

```text
00_PrepareSessionVariables
  -> 01_VideoProbeMetadata
  -> 03_01_VideoSceneDetect
  -> 03_02_SceneKeyframeIndex
```

一次画面分析运行创建一个新的 Tool Use Session，`selected_scheme=visual`。上传源文件从素材 Session 的 `inbox/` 复制为 Tool Use Session 内部的 `0_SessionContext/Video_Source.mp4`，兼容上下文位于 `SessionContext/Video_Source.mp4`。

### 4.1 03_01_VideoSceneDetect

固定首版参数：

- detectors：`content`、`adaptive`；
- content threshold：`27.0`；
- adaptive threshold：`3.0`；
- min scene：`0.5s`；
- merge window：`0.35s`；
- profile：`balanced`；
- pass：`single`。

两个检测器独立检测切点。相距不超过 `0.35s` 的切点合并为一个边界，并保留来源检测器与置信度。时间轴必须从 `0` 开始并结束于视频总时长，相邻 Scene 不得重叠或留空。

### 4.2 03_02_SceneKeyframeIndex

每个 Scene 默认取时间中点：

```text
keyframe_time = start + (end - start) / 2
```

FFmpeg 仅用于提取图片，不作为第二种分析算法。如果中点帧提取失败，按固定顺序尝试 Scene 内部的 40%、60%、25%、75% 位置；全部失败时本次运行失败，禁止生成缺少关键帧的 SceneUnit。

## 5. SceneUnit 合同

最终单元示例：

```json
{
  "scene_id": "scene_0001",
  "title": "Scene 0001",
  "start": 0.0,
  "end": 2.48,
  "duration": 2.48,
  "start_frame": 0,
  "end_frame": 74,
  "keyframe_time": 1.24,
  "image_path": "SessionOutput/visual/scene_frames/scene_0001.jpg",
  "source_detectors": ["adaptive", "content"],
  "confidence": 0.82,
  "usability": "detected"
}
```

约束：

1. `scene_id` 按时间顺序稳定生成；
2. 第一个 Scene 从 `0` 开始；
3. 最后一个 Scene 结束于视频总时长；
4. 相邻 Scene 前一项 `end` 等于后一项 `start`；
5. 每个 Scene 有且只有一张主关键帧；
6. 没有检测到切点时，整个视频形成一个 Scene；
7. 不生成独立视频文件，页面使用源视频加 `start/end` 播放。

## 6. 产物合同

```text
SessionOutput/visual/scene_detect_cuts.json
SessionOutput/visual/scene_detect_scenes.json
SessionOutput/visual/final_scene_frame_items.json
SessionOutput/visual/scene_frames/scene_0001.jpg
```

工具自身同时保留 `Working/`、`Output/`、`Report/`、`State.json` 与 `OutputManifest.json`，支持 resume 和 force rerun。强制重跑只清理本次画面分析 Tool Use Session 产物，不删除上传源视频，也不覆盖对白或综合分析结果。

## 7. 状态与交互

状态合同：

```text
not_analyzed / queued / running / ready / failed
```

画面分析工具弹窗显示：

1. 准备 Session；
2. 读取视频元数据；
3. Scene Detect 镜头切分；
4. 提取关键帧并生成片段索引。

弹窗显示状态颜色、当前运行描述、每一步状态、总运行时间、失败原因和重新运行按钮。同一素材同一时刻只能存在一个 active 画面分析运行，但画面分析不要求对白分析先完成。

画面分析 Tab 每行显示 Scene 名称和时间范围。选择一行后，右侧播放器跳到 `start` 并在 `end` 暂停，同时展示唯一关键帧。本期不展示人物、动作、物体等语义字段，页面明确提示“仅完成 Scene Detect，尚未生成视觉语义描述”。

## 8. 异常处理

- 无切点：生成一个覆盖完整视频的 Scene；
- 视频时长、FPS 或帧数不可读取：运行失败并保留可读错误；
- PySceneDetect 不可用：运行失败并提示依赖缺失；
- 单张关键帧首次提取失败：尝试 Scene 内部其他固定时间点；
- 任意 Scene 最终无法获得关键帧：整次运行失败，避免破坏一一对应合同；
- 重复点击运行：active run 返回冲突；
- 已完成后普通运行：返回已存在；显式重新运行使用 force。

## 9. 验收标准

1. OpenCut V1 registry 新增 `03_01`、`03_02`；
2. OpenCut V1 源码不 import Analysis 或 Analysis V1；
3. 无音频、无字幕视频可以独立运行；
4. 画面分析不要求对白分析完成；
5. Scene 时间轴完整覆盖视频且无空隙、无重叠；
6. 每个最终 SceneUnit 有真实时间范围和一张真实关键帧；
7. 无切点视频生成一个全视频 SceneUnit；
8. 详情接口读取真实 JSON 并返回可播放、可选择的 Scene 列表；
9. 页面可选择 Scene、播放对应范围并查看关键帧；
10. 页面不产生虚假的视觉语义描述；
11. 强制重跑不删除上传源文件，不覆盖对白或综合分析产物。
