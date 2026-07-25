# DanceMimic_V1 隐私网格模式实施设计

> 状态：MVP Implementation Candidate v1
>
> 日期：2026-07-13
>
> 适用范围：动作模拟创建弹窗、DanceMimic_V1 00-03 参考素材处理、05_01/05_02 视频计划与生成、人工验收
>
> 修订依据：`DanceMimic_V1_隐私网格模式_审核意见_v1.md` 及 MVP 复杂度复审

## 1. 文档目标

本文定义 DanceMimic_V1 新增“隐私网格”模式的产品语义、输入处理、最小数据合同、Prompt 合同、错误处理和测试要求。

本模式可以分别处理两类输入：

1. 参考动作视频：检测视频中的一个或多个人脸，计算一个覆盖绝大多数人脸出现位置的固定矩形区域，在该区域绘制固定红色细线网格。
2. 目标人物图片：检测目标人物人脸，在其扩张人脸区域绘制固定红色细线网格。

选择该模式后，参考视频区和目标人物图区各显示一个默认勾选的“应用隐私网格”复选框。只有勾选的输入才执行人脸检测和网格处理；取消勾选表示用户明确允许该类未加网格的输入进入 provider 请求。

系统必须逐项记录是否应用网格，不得把未勾选的输入标记为已处理或已脱敏。两项均未勾选时允许继续，但 UI 必须明确提示“参考视频和目标人物图均不会添加隐私网格，身份内容将按原始视觉输入发送给模型”。

生成视频不得残留红色网格、红色边框、红色追踪线或其他输入遮罩痕迹。

### 1.1 MVP 范围边界

首版只实现形成完整用户闭环所需的能力：

- 两个独立网格开关及其持久化。
- 参考视频全局固定网格和目标图单图网格。
- 最小 manifest、StoryBoard/VideoPlan 路径传播和严格 provider preflight。
- 根据实际网格作用范围和引用角色生成去网格 Prompt。
- 四种开关组合的 contract 测试、视觉 fixture 和付费 smoke。

以下能力明确延期，不属于 MVP：

- 自动检测生成视频中的红色网格残留。
- 自动重试、重试 fingerprint、独立重试输出和 ExecutionTracker 新状态。
- 跨帧人脸身份跟踪或稳定 `face_track_id`。
- 细粒度 stale 依赖图和自动 resume。
- 复合 QA sheet 和运营仪表盘。
- 派生媒体预览作为紧随 MVP 的独立增量，复用 02 产物，不阻塞输入处理合同。

## 2. 已确认的产品前提

### 2.1 模式定位

“隐私网格”是保留身份可识别性的轻度视觉扰动模式，而不是不可识别式匿名化模式。

已确认的经验前提：

- 透明底、较细的红色网格不会导致模型无法识别目标人物身份。
- 该网格不应明显破坏脸部结构、表情和人物一致性。
- 模型应读取网格下的人脸身份和动作信息，但不得在输出中复制网格。

因此本模式不增加黑色填充、马赛克或强模糊，不以“让 provider 无法识别人脸”为目标。

### 2.2 隐私强度声明

内部元数据必须明确记录：

```json
{
  "mode": "red_grid_guide",
  "apply_to_reference_video": true,
  "apply_to_target_identity_image": true,
  "identity_visible": true,
  "privacy_strength": "low",
  "output_grid_allowed": false
}
```

产品界面可显示“隐私网格”，但帮助文本必须说明“身份仍可见；网格仅作为输入侧轻度扰动和位置标记”。不得将其描述为严格匿名化或身份不可识别方案。

两项开关都关闭时，内部还必须记录 `effective_grid_scope=none`。此时 `red_grid_guide` 只表示用户进入过该配置模式，不能声称任何输入实际获得了网格保护。

### 2.3 模式总览

现有三种模式保持不变，并新增第四种模式：

| UI 名称 | 内部值 | 处理方式 | 身份可见性 |
| --- | --- | --- | --- |
| 仅遮脸 | `face_mask_only` | 动态黑底网格遮挡检测脸部 | 低 |
| 强隐私轮廓 | `provider_safe_outline` | 全画面非识别性轮廓 + 脸部遮挡 | 低 |
| 强隐私骨架 | `provider_safe_pose` | 全画面骨架，失败时降级轮廓 | 低 |
| 隐私网格（新增） | `red_grid_guide` | 按两个独立开关，对参考视频和/或目标图绘制透明底固定红色细线网格 | 高 |

表中的“身份可见性”描述 provider 是否仍能读取身份，不代表隐私等级。

## 3. 用户工作流

### 3.1 创建入口

在“动作模拟创建/编辑”弹窗的“参考隐私模式”下拉框新增：

```text
隐私网格（身份可见）
```

提交值：

```text
reference_privacy_mode=red_grid_guide
```

第一版继续复用 `reference_privacy_mode` 表示模式，并新增两个布尔配置字段表示作用范围。两个布尔值可以存入现有任务配置 JSON，不要求新增独立数据库列，但必须经过 API schema 校验并持久化。

选择该模式后，在两个上传区域分别显示：

```text
参考视频
[x] 应用隐私网格

目标人物图
[x] 应用隐私网格
```

建议新增并持久化两个 API 布尔字段：

```json
{
  "reference_privacy_mode": "red_grid_guide",
  "apply_privacy_grid_to_reference_video": true,
  "apply_privacy_grid_to_target_identity_image": true
}
```

交互规则：

- 新建任务首次选择 `red_grid_guide` 时，两项默认均为 `true`。
- 用户可独立取消任意一项，也允许两项都取消。
- 从其他模式切换到该模式且任务从未保存过这两个字段时，初始化为 `true/true`。
- 在同一次弹窗会话中切换到其他模式再切回时，保留用户刚才的选择。
- 编辑已有任务时严格按持久化值回显；旧任务字段缺失时按 `true/true` 迁移。
- 其他隐私模式隐藏这两个复选框，后端忽略其值。
- 两项都取消时显示非阻断警告，不静默改回勾选状态。

### 3.2 用户输入

用户仍需提供：

- 参考动作视频。
- 目标人物图片。
- 目标分段秒数和最小分段秒数。
- “无人脸时阻断”设置。

选择隐私网格后，人脸检测规则只约束已勾选“应用隐私网格”的输入：

- 参考视频已勾选但没有检测到人脸：强制阻断 `privacy_grid_face_not_detected`。
- 目标人物图已勾选但没有检测到人脸：必须阻断。
- 目标人物图已勾选且检测到多个人脸：第一版必须阻断，要求用户改用单人目标图。
- 某项未勾选：跳过该项的人脸检测、网格区域计算、网格渲染和输入网格 QA，不因没有真人而报错。

复选框控制的是“是否绘制网格”，不是“素材是否参与生成”。取消勾选后，该输入仍作为动作或身份输入发送给模型。

`red_grid_guide` 模式不使用现有 `block_on_face_not_detected=false` 的无脸直通语义。只要参考视频网格开关为 `true`，无脸就必须阻断；非真人参考视频应由用户取消对应复选框。其他三种模式继续遵循现有 `block_on_face_not_detected` 配置。

### 3.3 预览

预览增量在 02 成功后展示实际派生素材，不在浏览器模拟网格，也不重复执行人脸检测。

后端在任务详情中返回只读 `privacy_grid_preview`：

```json
{
  "status": "ready",
  "effective_grid_scope": "both",
  "reference_video": {
    "grid_applied": true,
    "preview_timestamp_seconds": 10.0,
    "preview_url": "/api/dance-mimic-v1/tasks/199/privacy-grid-preview/reference"
  },
  "target_identity": {
    "grid_applied": true,
    "preview_url": "/api/dance-mimic-v1/tasks/199/privacy-grid-preview/target"
  }
}
```

实现规则：

- 预览 URL 由后端根据 `privacy_grid_manifest.json` 生成，前端不得提交任意文件路径。
- 预览接口必须校验登录、任务 workspace 边界、manifest 声明路径和文件存在性。
- 参考视频的“已添加网格”预览只显示一张代表帧，不播放派生视频，不提供分段切换。
- 02 从已有有效人脸检测样本中选择最接近整段视频中点的一帧，使用与 provider 参考视频相同的固定网格参数渲染为 JPEG 或 PNG，并在 manifest 中记录路径、SHA-256 和时间点。
- 前端使用 `<img>` 显示参考视频网格代表帧；不为该预览加载 `<video>`，也不在请求时转码或抽帧。
- 目标图直接显示 `target_identity.provider_path`。
- 某项开关关闭时显示原始素材并标记“未应用”；不得使用带网格文件冒充关闭状态。
- 02 尚未完成时状态为 `pending`；02 阻断时复用现有错误；配置被标记 stale 后立即隐藏旧派生预览。
- URL 加 manifest 更新时间或 provider SHA-256 作为缓存版本，避免修改配置后浏览器继续显示旧网格。
- 该预览由本机后端流式返回，不需要上传 R2；R2 限制只适用于发送给外部 provider 的输入引用。

创建弹窗在任务尚未保存或 02 尚未运行时只显示原始素材。若未来确实需要“运行前预览”，应增加一个明确的“生成网格预览”后台动作，复用 00-02，而不是实现不准确的 CSS 叠加预览。

## 4. 处理流程总览

```text
原始参考视频
  -> 判断 apply_privacy_grid_to_reference_video
  -> true: 分段/采样 -> 全候选人脸检测 -> 全视频固定区域计算 -> 网格渲染
  -> false: 跳过人脸检测和网格渲染
  -> Provider Reference Video

原始目标人物图
  -> 判断 apply_privacy_grid_to_target_identity_image
  -> true: 单图人脸检测 -> 主人脸扩张区域计算 -> 网格渲染
  -> false: 跳过人脸检测和网格渲染
  -> Provider Target Identity Image

两份按开关选择的 provider 输入
  -> StoryBoard / VideoPlan
  -> 至少一项应用网格时，注入“保持身份、忽略网格、输出不得残留”Prompt
  -> Provider 视频生成
  -> 测试阶段人工检查网格残留
  -> 通过 / 人工标记失败
```

## 5. 参考视频人脸检测与固定区域

本章仅在 `apply_privacy_grid_to_reference_video == true` 时执行。值为 `false` 时必须跳过本章全部检测和渲染步骤，并在 manifest 中记录 `skip_reason=user_disabled`；值为 `true` 但没有有效人脸时必须强制阻断，不允许发送未加网格参考视频。

### 5.1 当前实现缺口

当前 `02_ReferenceFaceMaskedVideoBuild` 每个采样帧可以得到多个人脸候选，但随后只保留置信度和面积最高的一张脸；每个 segment 最终只生成一条人脸轨迹。

隐私网格要求覆盖参考视频中的一个或多个人脸，因此必须修改为：

- 保留每个采样帧中所有通过阈值的人脸候选。
- 直接按时间戳保留全部候选框，用于全局区域聚合。
- 不再用单个 `select_detected_face()` 结果作为隐私网格输入。

MVP 不做跨帧身份匹配，不创建稳定 `face_track_id`；固定区域计算不需要知道两个候选框是否属于同一个人。

### 5.2 采样策略

建议默认：

- 沿用现有 segment 边界。
- 每秒采样 2 帧，每个 segment 不少于 9 帧。
- 强制采样每个 segment 的首帧、中间帧和尾帧。
- 全视频采样总数上限为 120 帧；超过时在保留所有 segment 首/中/尾帧的前提下均匀下采样其余候选帧。
- 使用现有 detector fallback：`insightface_scrfd -> mediapipe_blazeface -> opencv_haar`。

每个采样项至少记录：

```json
{
  "frame_index": 120,
  "timestamp_seconds": 5.0,
  "faces": [
    {
      "bbox": [410, 170, 150, 180],
      "confidence": 0.96,
      "engine": "insightface_scrfd"
    }
  ]
}
```

### 5.3 固定区域计算

V1 使用一个全视频固定矩形区域，所有输出 segment 使用完全相同的归一化坐标。

计算步骤：

1. 收集所有采样帧中通过置信度阈值的人脸框。
2. 转换为 `[0, 1]` 归一化坐标。
3. 使用稳健边界计算初始区域：所有人脸框左边界和上边界取 P01，右边界和下边界取 P99，避免单帧误检把区域拉满。
4. 按脸框尺寸增加边距，推荐左右各 15%，上方 25%，下方 20%。
5. 裁剪到画面范围。
6. 一次性计算面积和覆盖率；若覆盖率不足且区域未超过 45%，确定性放宽一次到全部有效框的 min/max 边界后重算。不得继续循环扩张。

推荐默认覆盖率：

```text
face_sample_coverage_ratio >= 0.98
face_area_coverage_ratio   >= 0.95
```

定义：

- 单个人脸框“充分覆盖”：固定区域与该人脸框的交集面积 / 该人脸框面积 `>= 0.95`。
- `face_sample_coverage_ratio`：达到“充分覆盖”的人脸采样框数量 / 全部有效人脸采样框数量。
- `face_area_coverage_ratio`：所有有效人脸框的“交集面积 / 人脸框面积”取 P05。

若固定区域超过画面面积上限 45%，则阻断：

```text
privacy_grid_region_too_large
```

若区域面积未超过 45%，但任一覆盖率未达标，则阻断：

```text
privacy_grid_coverage_failed
```

第一版不静默切换为多个网格区域。多人分布过散时，要求用户裁剪视频或选择其他隐私模式。多固定区域可作为 V2。

### 5.4 固定性合同

“固定”必须满足：

- 同一任务的所有 segment 使用相同 `normalized_region`。
- 每一帧的网格左上角、宽高、行列相位不变化。
- 不跟随逐帧人脸框移动。
- 转码或缩放时按归一化区域重新计算像素坐标，不能直接复用旧分辨率像素值。

示例：

```json
{
  "coordinate_space": "normalized_0_1",
  "normalized_region": {
    "x1": 0.125,
    "y1": 0.2,
    "x2": 0.875,
    "y2": 0.4
  },
  "fixed_across_segments": true
}
```

用户提出的横向 `1/8 -> 7/8`、纵向 `1/5 -> 2/5` 只作为人工模板或检测失败诊断参考，不作为硬编码默认值，也不得在“网格开关已开启但未检测到人脸”时自动降级使用。

## 6. 目标人物图处理

本章仅在 `apply_privacy_grid_to_target_identity_image == true` 时执行。值为 `false` 时不要求目标图是真人或恰好包含一张脸，并在 manifest 中记录 `skip_reason=user_disabled`。

### 6.1 输入约束

目标人物图是最终视频身份锚点。隐私网格模式下仍要求模型能够读取其身份，因此只绘制透明底红色细线，不增加模糊、马赛克或实心填充。

第一版要求目标人物图恰好检测到一个有效主人脸：

- 0 张脸：`target_identity_face_not_detected`。
- 多于 1 张脸：`target_identity_multiple_faces`。
- 人脸区域越界或检测置信度不足：阻断并要求更换图片。

### 6.2 区域计算

目标图网格区域以检测人脸框为基础，使用与参考视频相同的边距语义：

- 左右各扩张 15%。
- 上方扩张 25%。
- 下方扩张 20%。
- 区域裁剪到图片边界。

目标图要求人脸框 100% 位于网格矩形内。

### 6.3 派生文件

应用网格时原图不得覆盖。建议输出：

```text
SessionContext/Target_Identity_Image_PrivacyGrid.png
```

并记录：

```json
{
  "source_path": "SessionContext/Target_Identity_Image.png",
  "provider_path": "SessionContext/Target_Identity_Image_PrivacyGrid.png",
  "face_count": 1,
  "face_bbox": [410, 170, 150, 180],
  "expanded_bbox": [387, 125, 195, 243],
  "source_sha256": "...",
  "provider_sha256": "..."
}
```

未应用网格时不生成该派生文件，`provider_path` 指向本次请求实际使用的未加网格输入，并设置 `grid_applied=false`。不得生成一份与原图相同但名称带 `PrivacyGrid` 的伪派生文件。

## 7. 网格渲染规范

### 7.1 默认视觉参数

```text
fill_alpha          = 0
line_color          = #ff1f1f
border_color        = #ff1f1f
line_width_1080p    = 1px
cell_size_1080p     = 44px
line_style          = solid
```

`line_width_1080p=1px` 是实验初始值，不是最终发布常量。付费 smoke 必须对 1px 和 2px 做 A/B，验证 H.264 转码和 provider 缩放后的可见性、身份保持与输出残留率，再确定发布值。

缩放规则：

- 线宽根据画面短边缩放，最小 1px，最大 3px。
- 网格间距根据画面短边缩放，1080px 短边参考 40-48px。
- 视频和目标图使用相同颜色、线宽和间距比例。
- 不使用半透明红色填充。
- 不改变网格区域之外的像素。

### 7.2 渲染顺序

```text
读取原帧/原图
  -> 计算最终像素区域
  -> 绘制矩形红色外框
  -> 从固定原点绘制等距横线和竖线
  -> 写入派生文件
```

网格相位必须锁定到矩形左上角。禁止每帧重新从人脸位置计算网格起点。

### 7.3 Provider 视频输出路径

参考视频应用网格时建议新增：

```text
SessionOutput/reference/segments/Segment_NNNN/Segment_NNNN_Reference_PrivacyGrid.mp4
```

为降低迁移风险，可同时在 manifest 中保留兼容字段：

```json
{
  "provider_reference_video_path": "...Reference_PrivacyGrid.mp4",
  "face_masked_reference_video_path": "...Reference_PrivacyGrid.mp4"
}
```

新代码读取 `provider_reference_video_path`，旧代码在字段缺失时回退 `face_masked_reference_video_path`。

参考视频未应用网格时，继续使用现有标准化/分段参考视频路径，不创建 `Reference_PrivacyGrid.mp4`。manifest 必须记录 `grid_applied=false` 和实际 provider 路径。

## 8. 数据合同

### 8.1 Variables.json

建议扩展：

```json
{
  "reference_face_masked_video_build": {
    "reference_privacy_mode": "red_grid_guide",
    "privacy_grid": {
      "apply_to_reference_video": true,
      "apply_to_target_identity_image": true,
      "identity_visible": true,
      "privacy_strength": "low",
      "output_grid_allowed": false,
      "line_color": "#ff1f1f",
      "line_width_reference": 1,
      "cell_size_reference": 12
    }
  }
}
```

### 8.2 PrivacyGridManifest.json

新增：

```text
SessionOutput/reference/privacy_grid_manifest.json
```

建议结构：

```json
{
  "schema_version": "dance_mimic_v1_privacy_grid_0.2",
  "mode": "red_grid_guide",
  "apply_to_reference_video": true,
  "apply_to_target_identity_image": false,
  "effective_grid_scope": "reference_video",
  "identity_visible": true,
  "privacy_strength": "low",
  "reference_video": {
    "grid_applied": true,
    "skip_reason": null,
    "source_path": "SessionContext/Video_Reference_Source.mp4",
    "source_sha256": "...",
    "normalized_region": {"x1": 0.125, "y1": 0.2, "x2": 0.875, "y2": 0.4},
    "valid_face_sample_count": 34,
    "face_sample_coverage_ratio": 0.992,
    "face_area_coverage_ratio": 0.978,
    "fixed_across_segments": true,
    "provider_segments": [
      {
        "segment_id": "segment_0001",
        "provider_path": "SessionOutput/reference/segments/Segment_0001/Segment_0001_Reference_PrivacyGrid.mp4",
        "provider_sha256": "..."
      }
    ]
  },
  "target_identity": {
    "grid_applied": false,
    "skip_reason": "user_disabled",
    "source_path": "SessionContext/Target_Identity_Image.png",
    "source_sha256": "...",
    "provider_path": "SessionContext/Target_Identity_Image.png",
    "provider_sha256": "...",
    "face_count": null,
    "expanded_bbox": null
  },
  "render": {
    "line_color": "#ff1f1f",
    "line_width": 1,
    "cell_size": 44,
    "fill_alpha": 0
  }
}
```

`effective_grid_scope` 只允许 `none`、`reference_video`、`target_identity`、`both`，由两个持久化开关直接计算。

该 manifest 是隐私网格输入事实的唯一来源。05 run state 只记录执行状态和 `privacy_grid_manifest_path`，不复制开关、区域、路径和 QA 指标。

### 8.3 StoryBoard 和 VideoPlan

隐私网格模式下，路径按各自开关绑定：

- 目标图开关为 `true` 时，`dialogue.image_path`、`source_image_paths[]` 和 `dance_mimic.target_identity_image_path` 必须指向隐私网格派生图。
- 目标图开关为 `false` 时，上述字段指向实际未加网格输入，并显式记录 `target_identity_grid_applied=false`。
- 参考视频开关为 `true` 时，`dance_mimic.reference_video_path` 必须指向隐私网格派生视频。
- 参考视频开关为 `false` 时，该字段指向现有标准化/分段参考视频，并显式记录 `reference_video_grid_applied=false`。
- 禁止根据文件是否包含真人自动覆盖用户开关；检测器只在开关为 `true` 时运行。
- 每个 reference segment 使用 `privacy_grid_manifest.reference_video.provider_segments[]` 中同 `segment_id` 的 `provider_path`，不使用不存在的单数 `reference_video.provider_path`。

建议增加：

```json
{
  "dance_mimic": {
    "privacy_grid_mode": true,
    "reference_video_grid_applied": true,
    "target_identity_grid_applied": false,
    "provider_target_identity_image_path": "SessionContext/Target_Identity_Image.png",
    "provider_reference_video_path": "...PrivacyGrid.mp4",
    "prompt_contract": "dance_mimic_privacy_grid_clean_output_0.1"
  }
}
```

两项都为 `false` 时，`prompt_contract` 必须为空或省略，并写入 `effective_grid_scope=none`；不得保留一个实际未执行的清理合同版本。

## 9. Prompt 合同

### 9.1 注入条件

只有满足以下两个条件时注入隐私网格 Prompt：

```text
reference_privacy_mode == red_grid_guide
AND (reference_video_grid_applied OR target_identity_grid_applied)
```

两项开关都关闭时不注入去网格 Prompt。其他三种模式保持现有提示词。

该约束必须进入每个 segment 的最终 provider 请求，不能只保存在任务级说明或 UI 文案中。

### 9.2 Positive Prompt

在 `Video_SDR2V_DanceMimic.md` 正向模板中加入：

```text
Preserve the exact identity, facial structure, expression, skin texture,
and appearance of the target person. The thin red rectangular grid visible
on {gridded_input_scope} is a temporary input-only privacy marker. It is not
part of the person, face, skin, makeup, clothing, background, or scene.
Ignore the grid completely. The generated video must contain no red grid,
red lines, red border, lattice, mesh, tracking marks, or privacy overlay.
```

`{gridded_input_scope}` 必须按开关渲染为 `the input identity image`、`the motion reference video` 或 `the input identity image and motion reference video`，不得声称未处理的输入上存在网格。

不得用“first image”或“second image”定位目标身份图。当前 `input_references` 的顺序是 images、audios、videos，连续段的 images 内又可能先放 `continuity_first_frame`、再放 `target_identity`。Prompt 构建必须读取 `reference_image_roles`，按角色生成不依赖索引的措辞：

```text
Use the image reference whose role is target_identity as the identity anchor.
Use the image reference whose role is continuity_first_frame only for
cross-segment visual continuity. The thin red grid on the target_identity
reference, when present, is an input-only marker and must not appear in output.
```

如果同一图片同时承担 `continuity_first_frame,target_identity`，Prompt 必须明确该合并角色。实际 provider 不接收 role 字段时，Prompt package 仍需根据最终引用顺序生成准确描述，但实现不得假设目标图永远位于固定 index。

### 9.3 Negative Prompt

追加：

```text
red privacy grid, red rectangular border, red lines, red lattice,
mesh overlay, facial grid, face markings, tracking box, scan lines,
privacy overlay, privacy marker residue, grid residue, mask residue
```

### 9.4 冲突规则

现有模板中的“第一张图片是身份锚点”与连续段引用顺序可能不一致，实施时必须一并修正为角色语义。目标图或参考视频至少一项应用网格时必须补充：

- 保持网格下的人物身份、脸部结构和外观。
- 不保持网格本身。
- 网格不是妆容、纹身、面罩、服装、场景或 UI。

禁止使用可能让模型重新创造身份的泛化指令，例如只写“reconstruct a new clean face”。

## 10. Provider 请求安全合同

05_02 必须在 `dance_mimic_video_reference_images()` / `prepare_dance_mimic_reference_videos()` 完成路径解析后、调用 `generate_video_with_provider()` 和构造最终 OpenRouter `input_references` 前执行 preflight：

1. 隐私网格 manifest 存在且 hash 与当前输入和两个开关一致。
2. 目标图和参考视频可使用 03/05 物化到 Working/assets 的副本，但其 SHA-256 必须分别与 `target_identity.provider_sha256` 和当前 `segment_id` 对应的 `provider_segments[].provider_sha256` 完全一致。
3. 参考视频开关为 `true` 时，只允许隐私网格派生视频；为 `false` 时，只允许 manifest 记录的未加网格标准化/分段视频。
4. 目标图开关为 `true` 时，只允许隐私网格派生图；为 `false` 时，只允许 manifest 记录的未加网格目标图。
5. 开关为 `true` 时，禁止因派生失败而回退到未加网格素材。
6. 至少一项开关为 `true` 时，Positive/Negative Prompt 必须包含与实际作用范围一致的清理合同。
7. 两项都为 `false` 时，`privacy_grid_manifest.json` 必须记录 `effective_grid_scope=none`；05_02 run state 只记录 manifest 路径。
8. 隐私网格模式的视频公网发布只允许配置的 R2 通道，禁止 `tmpfiles` fallback；R2 未配置或上传失败时阻断。
9. 目标人物图开关为 `true` 且连续段使用上一段/上一场尾帧时，05_02 必须对尾帧重新检测全部人脸并生成隐私网格副本；故事板 `Image_New`、`continuity_first_frame` 和 provider 请求使用同一网格图。检测不到人脸或线条 QA 低于阈值时阻断，禁止发送干净尾帧。原始尾帧文件保持不变。

现有两个静默分支必须改成严格模式：

- 隐私网格合同要求目标身份图时，目标图不存在或不可读必须 raise，不能让 `dance_mimic_video_reference_images()` 静默不追加。
- `video_openrouter.generate()` 在隐私网格严格模式下不得用 `Path.exists()` 静默过滤缺失引用；任一 manifest 声明的路径缺失都必须 raise。

违反任一条件必须阻断。只有用户明确取消对应复选框时才允许发送该类未加网格素材：

```text
privacy_grid_provider_preflight_failed
privacy_grid_public_asset_transport_invalid
```

## 11. 输入侧 QA

### 11.1 参考视频 QA

仅在参考视频开关为 `true` 时执行并验证：

- 所有 segment 的网格像素区域一致。
- 固定区域覆盖率满足阈值。
- 红色外框和横纵网格线存在。
- 网格区域之外的像素与原视频在允许的转码误差内一致。
- 输出视频时长、fps、帧数、宽高有效。
- H.264/provider 文件大小 QA 继续通过。

推荐指标：

```text
privacy_grid_line_presence_ratio >= 0.95
privacy_grid_region_variance     == 0
face_sample_coverage_ratio       >= 0.98
face_area_coverage_ratio         >= 0.95
```

开关为 `false` 时写入 `status=skipped`、`reason=user_disabled`，不得伪造覆盖率或网格线指标。

### 11.2 目标人物图 QA

仅在目标图开关为 `true` 时执行并验证：

- 只检测到一个目标人脸。
- expanded bbox 完全位于图片范围。
- 红色网格覆盖 expanded bbox。
- 原图和派生图 hash 不同。
- 网格外区域像素保持一致。
- 派生图片可正常解码，尺寸与原图一致。

开关为 `false` 时写入 `status=skipped`、`reason=user_disabled`，不执行人脸数量约束。

### 11.3 诊断与预览输出

不生成复合 QA sheet。普通 Tool report 只记录 manifest 路径和成功/失败状态，详细开关、检测数量、区域、覆盖率和预览图哈希保存在 manifest。任务详情只通过 task-scoped 接口读取 manifest 声明的参考视频代表帧和目标人物派生图。

## 12. 生成结果人工验收

固定红色网格可能被模型复制到生成视频。MVP 通过 Prompt 降低风险，并在视觉 fixture 和付费 smoke 中人工检查：

- 人物脸部、皮肤和服装上没有红色规则线、交点或红框。
- 背景和画面其他位置没有复制出的固定网格。
- 红色衣服和正常红色背景不视为失败。
- 发现残留时将该 smoke 样例记为失败，不自动重试或发布为通过样例。

生产首版不新增自动残留检测、自动重试、provider fingerprint 控制或 ExecutionTracker 状态。若 smoke 证明残留具有显著发生率，再为第二阶段单独设计检测阈值和重试成本控制。

## 13. Resume、Force 和失效规则

V1 不新建内容签名比较或自动 resume 框架。当前 `file_fingerprint()` 仅作为 provenance 记录，`resume` 参数也没有可复用的分支语义；本文不得把它们描述为已经存在的失效机制。

V1 复用 `SessionReport/stale_manifest.json`、`mark_downstream_stale()` 和 `clear_stale_items()` 的现有体系。backend 在更新任务配置时比较旧值与新值；发生下列变化后，按现有 stale manifest schema 写入 `marked_stale` 记录：

- 切换进入或离开隐私网格模式。
- 修改任意一个“应用隐私网格”复选框。
- 替换参考视频或目标人物图。
- 修改分段时长、检测 manifest 或无人脸阻断配置。

失效项至少覆盖：

```text
02_reference_face_masked_video_build
03_storyboard_standard_task_build
storyboard_reference_video_assets
video_generation_plan
video_only_generation_plan
```

V1 对两个开关采用有意的粗粒度失效：即使只修改目标图开关，也将 02 及全部下游标记 stale。这样会重复部分参考视频处理，但合同简单且不会复用错误组合的派生素材；细粒度依赖图留到 V2。

`--force` 必须先归档旧派生素材和 QA，再按现有机制标记下游 stale。重新生成成功后只清除本步骤负责的 stale item。后续如要实现基于 SHA-256、算法版本和 Prompt contract 的自动 resume，必须单独设计，不属于本 V1 范围。

## 14. 错误码

| 错误码 | 层级 | 含义 |
| --- | --- | --- |
| `privacy_grid_face_not_detected` | 02 | 参考视频未检测到有效人脸 |
| `privacy_grid_region_too_large` | 02 | 达到覆盖率需要的固定区域过大 |
| `privacy_grid_coverage_failed` | 02 | 一次计算得到的固定区域覆盖率不达标 |
| `privacy_grid_render_failed` | 02 | 网格视频渲染失败 |
| `privacy_grid_line_qa_failed` | 02 | 输入网格线存在性 QA 失败 |
| `target_identity_face_not_detected` | 02 | 目标人物图没有检测到人脸 |
| `target_identity_multiple_faces` | 02 | 目标人物图存在多张脸 |
| `target_identity_privacy_grid_render_failed` | 02 | 目标图派生失败 |
| `privacy_grid_provider_preflight_failed` | 05_02 | provider 请求引用或 Prompt 合同不正确 |
| `privacy_grid_public_asset_transport_invalid` | 05_02 | R2 未配置/失败，或请求试图使用 tmpfiles |

## 15. 代码改造清单

为降低单次变更范围，实施拆成三个可独立审核的提交：

1. 前置修复：保持现有 `reference_privacy_mode` 默认兼容，并把现有“第一张图片”Prompt 改为引用角色语义。
2. MVP 功能：双开关、02 网格生成、最小 manifest、03/05 路径传播和严格 preflight。
3. 测试与文档：四组合 contract、视觉 fixture 和旧文档废弃提示。

### 15.1 Frontend

`frontend/src/modules/koubo/KouboTaskList/KouboTaskCreateDanceMimicModal.jsx`

- 增加 `red_grid_guide` 选项。
- 增加身份可见、低强度说明。
- 在参考视频区和目标人物图区分别增加默认勾选的“应用隐私网格”复选框。
- 实现新建默认、模式切换保留、旧任务默认迁移和编辑回显规则。
- 两项都关闭时显示非阻断风险提示。
- 将两个布尔值交给提交 payload；对应 `kouboTaskListApi.js` 的 `danceMimicFormData()` 必须追加两个 multipart 字段。

`frontend/src/modules/koubo/DanceMimicV1/DanceMimicV1Module.jsx`

- 显示 `reference_privacy_mode`、两个开关和 `effective_grid_scope` 文本状态。
- 显示参考视频网格代表帧和目标人物网格图，不加载派生视频。
- 根据 `ready/pending/stale/blocked` 显示预览状态，使用 manifest SHA-256 作为缓存版本。

### 15.2 Backend

`backend/opcrew_backend/koubo/schemas.py`

- 为 `reference_privacy_mode` 增加显式枚举或 validator。
- 增加两个布尔字段并校验：缺失时仅对旧任务迁移为 `true/true`，不得用通用 `value or true` 覆盖显式 `false`。
- 统一默认值为 `face_mask_only`：schema、router、Tool config 和 Tool 空值 fallback 必须一致。
- `DanceMimicRunPayload.reference_privacy_mode=""` 仍表示继承 task meta，run payload validator 必须放行空串；创建/更新 payload 不接受空串。

`backend/opcrew_backend/koubo/dance_mimic_router.py`

- 接受并保存 `red_grid_guide`。
- 接受、保存并回传两个独立开关。
- 在 JSON 创建/更新接口和两个 multipart `/with-uploads` 接口的 `Form(...)` 参数中都接收两个布尔值。
- 将两个值写入 `dance_config_from_payload()`、`storyboard_quick_config_json`、task meta、详情 API 和事件摘要。
- 将模式和两个开关传给 00-03。
- 详情 API 只返回模式、两个开关、`effective_grid_scope` 和 manifest 路径，不展开完整 manifest。
- 修改模式、开关或输入素材时，按 §13 写入现有 stale manifest。

### 15.3 ToolLibrary 00-03

`ToolLibrary/DanceMimic_V1/00_PrepareSessionVariables.py`

- 写入隐私网格配置、两个开关和原始目标图路径。
- 为两个布尔值增加显式 CLI flag，并保持显式 `false`，不能只提供 `store_true` 后失去关闭语义。

`ToolLibrary/DanceMimic_V1/02_ReferenceFaceMaskedVideoBuild.py` / `_tool_impl.py`

- 仅在参考视频开关开启时保留所有人脸候选、计算全视频固定区域并生成网格视频。
- 仅在目标图开关开启时检测并生成网格目标图。
- 目标图检测和网格渲染统一归属 02；00 只 staging 原图并写路径。
- `red_grid_guide` 下参考视频开关开启但无脸时忽略通用直通配置并强制阻断。
- 开关关闭时跳过对应检测和渲染，记录实际 provider 路径及 `user_disabled`。
- 生成最小 `privacy_grid_manifest.json` 和普通 Tool report，不生成复合 QA sheet。

`ToolLibrary/DanceMimic_V1/03_StoryBoardStandardTaskBuild.py`

- 按两个开关分别绑定派生素材或未加网格素材。
- 写入两个 `grid_applied` 值和条件化 Prompt contract 标记。

### 15.4 05 执行链

`ToolLibrary/Analysis_V1/Reference/05_02/Video_SDR2V_DanceMimic.md`

- 增加隐私网格专用 Prompt blocks；不得依赖固定图片 index。

`ToolLibrary/Analysis_V1/05_01_VideoPlanGenerator.py`

- 将隐私网格模式、两个开关、实际 provider 路径和 Prompt contract 写入 plan。

`ToolLibrary/Analysis_V1/05_02_VideoPlanExecutor.py`

- 在素材引用组装和 provider 调用前执行严格 preflight；开启时禁止未加网格回退，关闭时只允许 manifest 指定的未加网格输入。
- 修改 `dance_mimic_video_reference_images()` 的身份图缺失静默分支，隐私网格严格模式下必须 raise。

`ToolLibrary/Analysis_V1/video_plan_executor_modules/video_openrouter.py`

- 在 `build_prompt_package()` 中根据两个开关和 `reference_image_roles` 选择模板 block并生成 `gridded_input_scope`；只增加必要的字符串拼装，不引入通用条件模板引擎。
- 在 `generate()` 增加隐私网格严格引用校验，不得静默过滤 manifest 要求但不存在的路径。
- 不新增传输实现；部署环境必须已有 R2 配置，隐私网格模式只增加一条禁止 `tmpfiles_publish_file()` fallback 的 guard。

## 16. 测试计划

### 16.1 单元测试

- 单人脸、静止脸：固定区域正确。
- 单人脸、大幅移动：达到覆盖率且区域不随帧移动。
- 多人脸、位置接近：单固定区域覆盖全部候选。
- 多人脸、位置过散：阻断 `privacy_grid_region_too_large`。
- 区域面积不超过 45% 但覆盖率不足：阻断 `privacy_grid_coverage_failed`。
- 短暂入镜人脸：被采样并纳入区域。
- 采样候选超过 120 帧：保留每段首/中/尾并均匀下采样。
- P01/P99 初始区域、单框 0.95 充分覆盖、min/max 单次放宽和两个聚合覆盖率计算正确。
- 参考视频开关开启但无人脸：无论通用 `block_on_face_not_detected` 值为何都强制阻断。
- 目标图单人脸：生成派生图。
- 目标图零人脸/多人脸：阻断。
- 参考视频开关关闭：非真人视频跳过检测并正常进入计划。
- 目标图开关关闭：非真人图、零人脸图和多人图跳过检测并正常进入计划。
- 两项开关关闭：不生成网格派生素材，不注入去网格 Prompt。
- 横屏、竖屏、不同分辨率：归一化坐标一致。
- 网格线宽、间距缩放正确。

### 16.2 Contract 测试

- 前端第四个选项存在且提交 `red_grid_guide`。
- 两个复选框默认均选中，可以独立取消，也允许全部取消。
- API 对 `true/false` 的保存、详情和编辑回显一致，显式 `false` 不会被默认值覆盖。
- JSON API、multipart `Form(...)` 和前端 `danceMimicFormData()` 都传播两个布尔字段。
- Tool 只接受四个正式模式。
- StoryBoard 和 VideoPlan 对四种开关组合均引用正确素材。
- OpenRouter mock payload 在开关开启时只包含对应派生素材，在关闭时包含 manifest 指定的未加网格素材。
- 至少一项开启时 VideoPlan 带 `prompt_contract`，Positive/Negative Prompt 的输入范围与开关一致。
- 两项关闭时不带去网格 Prompt，manifest 包含 `effective_grid_scope=none`，run state 只引用 manifest。
- manifest 要求的身份图/参考视频缺失时在 provider 请求前硬失败，不能静默过滤。
- 隐私网格模式未配置 R2 时阻断，mock 断言不会调用 tmpfiles publisher。
- 模式、开关或素材变化会写入现有 stale manifest；重新生成后只清除本步骤 stale item。
- 更新 `test_dance_mimic_frontend_create_contract.py` 和 `test_dance_mimic_backend_surface_contract.py` 中 pin 住 `face_mask_only` 及现有传播链的断言，覆盖新增模式但保留旧默认回归测试。

### 16.3 视觉 Fixture

至少准备：

- 单人正面舞蹈视频。
- 单人大范围左右移动视频。
- 双人同框视频。
- 人物中途进入/离开视频。
- 单人目标图。
- 多人目标图阻断样例。
- 红色衣服/红色背景样例，用于人工区分正常红色内容与网格残留。

### 16.4 付费 Smoke

每个目标模型至少验证：

1. 身份保持是否可接受。
2. 动作迁移是否可接受。
3. 最终视频是否残留网格。
4. 红色衣服/背景是否仍保持自然。
5. 1080p 1px 与 2px 网格经过 H.264、R2 和 provider 输入缩放后是否仍清晰可见。

至少完成以下 A/B：

- 参考视频开、目标图开：双输入网格 + 完整清理 Prompt。
- 参考视频开、目标图关：仅参考视频网格 + 范围匹配的清理 Prompt。
- 参考视频关、目标图开：仅目标图网格 + 范围匹配的清理 Prompt。
- 参考视频关、目标图关：双输入不加网格，不注入清理 Prompt。
- 双输入网格但无清理 Prompt，仅作为对照组，不进入生产路径。
- 双输入网格 1px 与 2px A/B，根据身份保持、动作迁移、输入可见性和输出残留率确定发布线宽。

## 17. 验收标准

功能验收：

- 弹窗可选择隐私网格并正确回显。
- 两个“应用隐私网格”复选框默认选中、可独立取消，四种组合均可保存和恢复。
- 已勾选的输入生成派生素材；未勾选的输入跳过人脸检测和网格处理。
- 参考视频已勾选时，网格在全视频中固定。
- 多人脸候选没有被静默丢弃。
- Provider 请求严格匹配两个开关，已勾选输入不会因处理失败而发送未加网格素材。
- 参考视频开关开启但无有效人脸时强制阻断，不执行模板区域或未加网格降级。
- 至少一项勾选时，每段最终 Prompt 包含作用范围正确的清理合同，付费 smoke 人工检查输出残留。
- 两项都取消时，界面和 manifest 明确声明未应用网格。

质量验收：

- 目标人物身份保持达到现有业务人工验收标准。
- 动作、节奏和姿态迁移不明显弱于现有路径。
- 最终视频不出现可见红色网格、红框或追踪线。
- 红色衣服和背景不被误判为规则网格。

安全验收：

- 日志、事件和 provider payload 可证明每类输入是否应用网格，以及实际发送了哪个文件。
- 任务详情明确声明身份仍可见、隐私强度为 low。
- 已勾选的输入不会因为派生失败而静默回退上传；未勾选输入的发送具有明确用户选择记录。
- 视频公网引用只通过配置的 R2 发送，隐私网格模式不会使用 tmpfiles 中转。
- manifest 记录两个开关语义、实际 provider 路径和 `effective_grid_scope`；run state 只记录 manifest 路径和执行状态。

## 18. 发布策略

第一阶段只在 macmini-4 测试环境开放，不引入一套仅用于本模式的前后端 feature flag 分支。通过本地输入验证和 provider 付费 smoke 后，再随已审核提交晋升生产。

建议顺序：

1. 验证部署环境 R2 配置，并确认预签名视频 URL 可被 OpenRouter 拉取。
2. 完成本地 Tool fixture 和输入 QA。
3. 完成 provider payload mock contract。
4. 在 macmini-4 使用测试素材做付费 smoke。
5. 记录不同模型的身份保持和网格残留结果。
6. 达到验收标准后再对普通用户开放。

实验期需要记录：

- 使用次数。
- 四种开关组合的使用次数。
- 检测人脸数量。
- 固定区域面积。
- 输入覆盖率。
- 付费 smoke 人工记录的输出残留率。
- 最终人工通过率。

## 19. 审核决策点

审核意见 v1 后的决策如下：

| # | 决策点 | 本文建议 |
| --- | --- | --- |
| D1 | UI 名称 | `隐私网格（身份可见）` |
| D2 | 内部模式值 | `red_grid_guide` |
| D3 | 参考视频区域 | V1 全视频一个固定矩形 |
| D4 | 多人脸 | 所有候选参与区域计算；区域超过 45% 时阻断 |
| D5 | 目标图多人脸 | 第一版阻断，要求单人图 |
| D6 | 网格视觉 | 透明底、`#ff1f1f`、间距 44px；线宽由 1px/2px smoke A/B 定稿 |
| D7 | 隐私声明 | `identity_visible=true`、`privacy_strength=low` |
| D8 | Provider 未加网格素材 | 仅对应开关被用户明确取消时允许；已开启时禁止失败回退 |
| D9 | 输出残留自动检测/重试 | MVP 延期；首版由付费 smoke 人工检查 |
| D10 | 发布方式 | macmini-4 先行验证，付费 smoke 后晋升生产 |
| D11 | 独立开关 | 参考视频区和目标图区各一个，默认均开启 |
| D12 | 两项都关闭 | 允许继续，但必须显示风险提示并记录 `effective_grid_scope=none` |
| D13 | 网格开启但无脸 | 强制阻断；不受通用无脸直通配置影响 |
| D14 | 失效机制 | V1 复用 stale manifest，采用有意的粗粒度失效 |
| D15 | 公网素材通道 | 隐私网格模式要求 R2，禁止 tmpfiles fallback |

## 20. 与现有文档的关系

本文扩展以下既有设计，不替换原有三种 `reference_privacy_mode`：

- 旧文档中 `mask_style=red_grid_guide` 的定义正式废弃，不再作为五种遮盖样式之一实施。
- 新的 `red_grid_guide` 只表示 `reference_privacy_mode`，区域是全视频固定矩形，不是跟随逐帧 expanded bbox 的动态样式。
- `DanceMimic_V1_02_ReferenceFaceMaskedVideoBuild_工具研究与设计.md` 中仅复用红色透明网格的视觉候选参数：`#ff1f1f`、1080p 线宽 1px 候选值、间距 44px。
- 本文新增目标人物图可选网格处理、参考视频/目标图独立开关、全视频固定区域、Provider 双输入约束、去网格 Prompt 和人工残留验收。

如本文与旧文档在 `red_grid_guide` 语义、隐私网格双输入或输出验收上存在差异，以本文为准。后续实施提交应同步给旧文档增加“已废弃/由本文替代”提示，避免读者继续采用旧 mask_style 定义。
