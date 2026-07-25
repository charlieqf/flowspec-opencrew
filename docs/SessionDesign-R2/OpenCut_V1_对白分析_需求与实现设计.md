# OpenCut V1 对白分析需求与实现设计

状态：底层子工具历史参考。对白分析的行为意图和独立性仍适用；analysis run、`source_version`、发布原子性、stale、Tool Session 收尾和跨页面检索合同以[素材库开发实施设计 v1.1.4](./OpenCrew_素材库综合能力_开发实施设计_v1.md)为准。

## 1. 目标

第一阶段以 Analysis V1 前半段能力为行为参照，将一个素材视频处理为“一个最终对白单元、一个源视频时间范围、一张关键帧”的只读索引。对白保持识别与校准后的原文，不进行 SRT 改写、润色、脚本重写或内容创作。

## 2. 本期范围

本期包含 Session 初始化、视频元数据解析、音频提取、ASR、画面烧录字幕 OCR、ASR/OCR/时间轴校准、关键帧选择、对白片段索引、运行状态和素材详情页结果读取。

本期不包含多句话语义合并、对白润色、SRT 改写、口误/磕巴判断、纯画面分析、综合分析、物理 MP4 切片导出。

## 3. 独立性边界

- 独立目录为 `ToolLibrary/OpenCut_V1/`。
- 可以从 Analysis V1 复制实现，但运行时不得 import Analysis V1 或 Analysis 业务代码。
- OpenCut V1 拥有独立 registry、脚本、framework bridge、中文规范化、媒体二进制定位和运行时依赖声明。
- Tool Use Session 框架、数据库、provider 配置和本地 secret store 属于 OpenCrew 平台能力，可以复用。
- 删除 Analysis V1 目录后，OpenCut V1 的代码导入和 registry 仍应成立。

## 4. Session 与执行链

一个素材对应一个 OpenCut Task 和一个 OpenCrew Session。一次对白分析运行对应一个新的 Tool Use Session：

```text
00_PrepareSessionVariables
  -> 01_VideoProbeMetadata
  -> 02_01_AudioASR
  -> 02_02_VideoSRTFrame
```

上传源文件位于 Session workspace 的 `inbox/`。运行开始时将它复制为 Tool Use Session 内部的 `0_SessionContext/Video_Source.mp4`，业务工具通过兼容上下文 `SessionContext/Video_Source.mp4` 读取。

每个工具保留 `Working/`、`Output/`、`Report/`、`State.json` 和 `OutputManifest.json`，支持 resume 与 force rerun。强制重跑仅创建或清理本次 OpenCut Tool Use Session 产物，不修改上传源视频和其他分析方案产物。

## 5. 最终对白单元

最终 `DialogueUnit` 必须包含：

- 稳定 `srt_id`；
- 原始识别/校准对白；
- `start`、`end`、`duration`；
- 一张主关键帧 `image_path`；
- 源视频虚拟片段，即原视频加 `start/end`，不额外生成 MP4。

有烧录字幕时，在每个 ASR 窗口内抽帧并 OCR。若一条 ASR 包含多个独立字幕页，按字幕页拆成多个最终单元；连续重复字幕页去重。没有烧录字幕时，最终边界严格采用 ASR 句子边界，关键帧只能从该范围内选择。

## 6. 产物合同

核心产物：

```text
SessionOutput/Audio_Reference.wav
SessionOutput/Video_Metadata.json
SessionOutput/subtitle/asr_segments.json
SessionOutput/subtitle/calibrated_srt_items.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/visual/srt_frames/
```

`final_srt_frame_items.json` 的核心字段为 `srt_id`、`dialogue`、`image_path`、`start`、`end`、`duration`。详情接口将其转换为页面统一片段模型，并补充源视频 URL 和关键帧 URL。

## 7. 状态与交互

对白分析状态为 `not_analyzed / queued / running / ready / failed`。运行中详情页轮询真实状态；完成后读取实际 JSON，不生成占位结果。点击对白行后，右侧播放器跳到 `start` 并在 `end` 暂停，同时展示对应关键帧。

若无音轨或无对白，应生成空结果并携带可解释告警；依赖、权限、ASR 或 OCR 无法执行时进入失败状态并保留可读错误。重复点击运行时，如果已有 active run 返回冲突；已完成任务可显式重新运行。

## 8. 验收标准

1. OpenCut registry 只包含 00、01、02_01、02_02，不包含任何 rewrite 工具。
2. OpenCut 源码没有对 Analysis V1 或 Analysis 业务模块的 import。
3. 无字幕视频按 ASR 边界形成一句一片段。
4. 有字幕视频使用 OCR 校准；单个 ASR 窗口内多字幕页可拆分。
5. 每个最终单元有真实时间范围和真实关键帧。
6. 后端可启动独立后台运行，页面可看到 queued/running/ready/failed。
7. 详情接口读取真实产物并返回可播放、可选中的对白列表。
8. 页面不出现原 SRT/改写 SRT 双栏和 SRT 改写操作。
9. 同一素材同一时刻只能运行一个对白分析。
10. 强制重跑不删除上传源文件，不覆盖视觉或综合分析产物。
