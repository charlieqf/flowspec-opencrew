# DanceMimic_V1 测试素材

两个单人舞蹈测试视频，用于 DanceMimic_V1 的 01 音画拆分 / 02 人脸遮挡 / 分段算法 / 后续执行链测试。

> 注：YouTube 在本构建机被网络层屏蔽（`youtube.com` / `googlevideo.com` 连接 000，`google.com`/`pypi.org` 正常），无法下载。改用 Wikimedia Commons 的 CC 授权片源——这反而更合适，因为 DanceMimic 执行链会把参考视频上传到云端 provider（OpenRouter MaxSR2），CC 片源避免版权问题。**CC BY 系列要求署名**，见 `manifest.json`。

## 两个片子

| 文件 | 时长 | 分辨率 | 角色 | 测什么 |
| --- | --- | --- | --- | --- |
| `dance_solo_frontal_studio.mp4` | 7.22s | 1080×1920 竖屏 | **正面清晰**、单人、全身、舞蹈室 | 02 人脸检测/跟踪/遮挡的「干净正脸」基线；01 拆分；单分段冒烟 |
| `dance_solo_rotation_smallface.mp4` | 14.08s | 1280×720 横屏 | 单人、旋转、**脸常背对/小而远** | 02 脸小/背对时的跟踪（漏脸 false-negative 风险）；多分段；01 拆分 |

两者都带音轨，可一并测 01 的 demux + demucs 人声分离。

## 建议测试参数（对应 §14.3 分段可行性 `ceil(D/target) ≤ floor(D/minimum)`）

**Clip A（7.22s）**
- 默认 `target=8, min=4` → `ceil(7.22/8)=1` → **1 段**（7.22s）：单分段 + 遮脸冒烟。
- 想要 2 段：`target=4, min=2` → **4.0s, 3.22s**。

**Clip B（14.08s）**
- 默认 `target=8, min=4` → **8.0s, 6.08s**（2 段，尾段≥min 保留满段+尾段）。
- `target=4, min=3` → 尾段 2.08 < min → 近似均分 **4 段 ~3.52s**：测尾段近似均分（near-even tail guard）。

**不可行用例（应 blocked，无需额外视频）**
- 用 Clip B：`target=8, min=10` → `ceil(14/8)=2 > floor(14/10)=1` → blocked `segment_constraints_infeasible`。
- 任意片：`target=4, min=5`（target<min）→ blocked `split_config_invalid`。

## 人眼验收（02 / 执行链）

1. **遮脸不漏**：逐帧拖 02 输出的 `Segment_*_Reference_FaceMasked.mp4`，任何一帧脸露出即 fail。Clip B 的脸小/背对段是重点。
2. **生成不漂移**：执行链（MaxSR2）输出里身份/服装/背景应来自首帧，动作来自参考视频，不得有遮脸网格残留或身份迁移。

## 重新下载

`manifest.json` 含每个片子的 `download_url`、`license`、`attribution`，可随时重新拉取或补充更多片源。
