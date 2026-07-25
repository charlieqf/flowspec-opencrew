# Koubo 非单个 Scene Plan 状态测试案例

状态：测试验证入口文档
范围：非单个 Scene，也就是跨 Scene / 跨 Shot 的 Plan 状态验证
适用 Plan：Video Plan / Video Only Plan / Image Plan

参考样本：

- `koubo_segment_tailframe_exhaustive_matrix.html`
- `koubo_video_only_plan_test_matrix.html`
- `koubo_image_plan_test_matrix.html`

本文件不是重新穷举 32 矩阵，而是把三个 Plan 中需要进入“非单个 Scene 验证”的最小案例整理成统一执行清单。

---

## 1. 统一输入结构

所有非单个 Scene 测试默认使用同一结构：

```text
Shot1
  Scene1: D1, D2, D3
  Scene2: D1, D2, D3

Shot2
  Scene1: D1, D2, D3
  Scene2: D1, D2, D3
```

时间设置：

| D | 时间 | 时长 |
| --- | --- | --- |
| D1 | 0-2s | 2s |
| D2 | 2-4s | 2s |
| D3 | 4-6s | 2s |

拆分参数：

```text
max_video_seconds = 4s
split_tolerance_seconds = 0
```

所以一个 Scene 内默认期望拆成：

```text
Segment 1 = D1 + D2 = 4s
Segment 2 = D3 = 2s
```

输入矩阵固定为：

```text
[A,O,N,V,F]
A = 音频
O = 原图
N = 新图 / Image_New
V = 新视频 / Raw Video
F = 终视频 / Final Video
```

颜色规则：

| 颜色 | 含义 |
| --- | --- |
| 白 | 可执行 / 待执行 |
| 灰 | 条件不满足 / 无需执行 / 被下游消费 |
| 绿 | 文件存在 / 已完成 |
| 黄 | 正在运行，文件尚未落盘 |
| 红 | 失败 / 阻塞，文件尚未存在 |

---

## 2. 三种 Plan 的右侧固定槽位

### Video Plan

```text
音频 - 新图/尾帧 - 新视频 - 终视频 - 尾帧
```

重点验证：

- 跨 Scene 计划尾帧继承
- 跨 Shot 计划尾帧继承
- 上游 TailFrame 缺失时 blocked
- 空镜 TailFrame 存在但不可继承
- 后续 Scene 自有原图 / 新图可恢复

### Video Only Plan

```text
音频 - 新图 - 提示词 - 新视频 - 拷贝成终视频
```

重点验证：

- Image_New 存在但 Video Prompt 不存在时，新视频灰
- Video Prompt 存在后，新视频白
- Raw 存在后，拷贝成终视频白
- Confirm Final 不依赖音频
- Final 存在时，Raw 不反向点亮

### Image Plan

```text
提示词 - 新图
```

重点验证：

- 只有原图时：提示词白，新图灰
- 原图 + Image Prompt 时：提示词绿，新图白
- 新图存在时：新图绿，Prompt 可灰
- Raw / Final 已存在时：新图灰，无需补图
- 音频完全不影响 Image Plan

---

## 3. Video Plan 非单个 Scene 案例

### VP-MS-01：Task 范围计划尾帧跨 Shot 连续

目标：验证同一次 Task Plan 中，后续 Scene / Shot 可以依赖上一段计划尾帧。

输入矩阵：

```text
Shot1 / Scene1
  D1 0-2s [0,1,0,0,0]  原图绿
  D2 2-4s [0,0,0,0,0]
  D3 4-6s [0,0,0,0,0]

Shot1 / Scene2
  D1 0-2s [0,0,0,0,0]
  D2 2-4s [0,0,0,0,0]
  D3 4-6s [0,0,0,0,0]

Shot2 / Scene1
  D1 0-2s [0,0,0,0,0]
  D2 2-4s [0,0,0,0,0]
  D3 4-6s [0,0,0,0,0]

Shot2 / Scene2
  D1 0-2s [0,0,0,0,0]
  D2 2-4s [0,0,0,0,0]
  D3 4-6s [0,0,0,0,0]
```

期望 Video Plan：

| 位置 | Segment | 包含 D | 时长 | 首帧槽 | 说明 |
| --- | --- | --- | --- | --- | --- |
| Shot1/Scene1 | S1 | D1,D2 | 4s | 新图白 | 原图启动第一段 |
| Shot1/Scene1 | S2 | D3 | 2s | 尾帧灰 S1 | 同 Scene 计划尾帧 |
| Shot1/Scene2 | S1 | D1,D2 | 4s | 尾帧灰 Shot1/Scene1/S2 | 跨 Scene 计划尾帧 |
| Shot1/Scene2 | S2 | D3 | 2s | 尾帧灰 Shot1/Scene2/S1 | 同 Scene 计划尾帧 |
| Shot2/Scene1 | S1 | D1,D2 | 4s | 尾帧灰 Shot1/Scene2/S2 | 跨 Shot 计划尾帧 |
| Shot2/Scene1 | S2 | D3 | 2s | 尾帧灰 Shot2/Scene1/S1 | 同 Scene 计划尾帧 |
| Shot2/Scene2 | S1 | D1,D2 | 4s | 尾帧灰 Shot2/Scene1/S2 | 跨 Scene 计划尾帧 |
| Shot2/Scene2 | S2 | D3 | 2s | 尾帧灰 Shot2/Scene2/S1 | 最后一段 |

---

### VP-MS-02：上游 TailFrame 缺失，后续自有视觉恢复

目标：验证 Shot2 起点缺少可用 TailFrame 时 blocked，但后续 Scene 自己有原图时可以恢复。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,0,1,1,1]  上游完成，TailFrame 绿
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  上游完成，TailFrame 绿

Shot1 / Scene2
  D1 [0,0,0,1,1]  上游完成
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  Final 绿，但 TailFrame 灰

Shot2 / Scene1
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene2
  D1 [0,1,0,0,0]  自有原图
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]
```

期望 Video Plan：

| 位置 | 结果 |
| --- | --- |
| Shot2 / Scene1 | blocked |
| blocked code | `scene_first_dialogue_missing_first_frame_and_previous_tail_missing` |
| Shot2 / Scene2 / S1 | D1,D2，4s，新图白 |
| Shot2 / Scene2 / S2 | D3，2s，尾帧灰 S1 |

---

### VP-MS-03：空镜 TailFrame 存在但不可继承

目标：验证空镜 / cutaway 的 TailFrame 即使存在，也不能驱动下一 Shot 的空视觉 Scene。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,0,1,1,1]  口播完成
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  口播完成

Shot1 / Scene2
  D1 [0,0,0,1,1]  口播完成
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  空镜，TailFrame 绿

Shot2 / Scene1
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene2
  D1 [0,1,0,0,0]  自有原图
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]
```

期望 Video Plan：

| 位置 | 结果 |
| --- | --- |
| Shot2 / Scene1 | blocked |
| blocked code | `previous_segment_cutaway_tail_not_usable` |
| Shot2 / Scene2 / S1 | D1,D2，4s，新图白 |
| Shot2 / Scene2 / S2 | D3，2s，尾帧灰 S1 |

---

## 4. Video Only Plan 非单个 Scene 案例

### VOP-MS-01：Prompt 控制 Raw，跨 Shot 完整展示

目标：验证 Video Only Plan 必须有 `Image_New + Video Prompt` 才能生成 Raw。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,0,1,0,0]  新图绿，Video Prompt 绿
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot1 / Scene2
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene1
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene2
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]
```

期望 Video Only Plan：

| 位置 | Task | 包含 D | 时长 | 音频 | 新图 | 提示词 | 新视频 | 拷贝 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Shot1/Scene1 | T1 | D1,D2 | 4s | 白 | 绿 | 绿 | 白 | 灰 |
| Shot1/Scene1 | T2 | D3 | 2s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot1/Scene2 | T1 | D1,D2 | 4s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot1/Scene2 | T2 | D3 | 2s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot2/Scene1 | T1 | D1,D2 | 4s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot2/Scene1 | T2 | D3 | 2s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot2/Scene2 | T1 | D1,D2 | 4s | 白 | 灰 | 灰 | 灰 | 灰 |
| Shot2/Scene2 | T2 | D3 | 2s | 白 | 灰 | 灰 | 灰 | 灰 |

---

### VOP-MS-02：Raw 已存在，可逐段 Confirm Final

目标：验证 Raw 存在时，拷贝成终视频白；Confirm Final 不依赖音频。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,0,1,1,1]  Raw + Final 完成
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  Raw + Final 完成

Shot1 / Scene2
  D1 [0,0,0,1,1]  Raw + Final 完成
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  Raw + Final 完成

Shot2 / Scene1
  D1 [0,0,0,1,0]  Raw 绿
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,0]  Raw 绿

Shot2 / Scene2
  D1 [0,0,0,1,0]  Raw 绿
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,0]  Raw 绿
```

期望 Video Only Plan：

| 位置 | Task | 包含 D | 时长 | 新视频 | 拷贝 |
| --- | --- | --- | --- | --- | --- |
| Shot1/Scene1 | T1 | D1,D2 | 4s | 绿 | 绿 |
| Shot1/Scene1 | T2 | D3 | 2s | 绿 | 绿 |
| Shot1/Scene2 | T1 | D1,D2 | 4s | 绿 | 绿 |
| Shot1/Scene2 | T2 | D3 | 2s | 绿 | 绿 |
| Shot2/Scene1 | T1 | D1,D2 | 4s | 绿 | 白 |
| Shot2/Scene1 | T2 | D3 | 2s | 绿 | 白 |
| Shot2/Scene2 | T1 | D1,D2 | 4s | 绿 | 白 |
| Shot2/Scene2 | T2 | D3 | 2s | 绿 | 白 |

---

## 5. Image Plan 非单个 Scene 案例

### IP-MS-01：Prompt 控制新图，跨 Shot 完整展示

目标：验证 Image Plan 只有在 `原图 + Image Prompt` 同时存在时，新图才白。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,1,0,0,0]  原图绿，Image Prompt 绿
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot1 / Scene2
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene1
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]

Shot2 / Scene2
  D1 [0,0,0,0,0]
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,0]
```

期望 Image Plan：

| 位置 | Task | 包含 D | 时长 | 提示词 | 新图 |
| --- | --- | --- | --- | --- | --- |
| Shot1/Scene1 | T1 | D1,D2 | 4s | 绿 | 白 |
| Shot1/Scene1 | T2 | D3 | 2s | 灰 | 灰 |
| Shot1/Scene2 | T1 | D1,D2 | 4s | 灰 | 灰 |
| Shot1/Scene2 | T2 | D3 | 2s | 灰 | 灰 |
| Shot2/Scene1 | T1 | D1,D2 | 4s | 灰 | 灰 |
| Shot2/Scene1 | T2 | D3 | 2s | 灰 | 灰 |
| Shot2/Scene2 | T1 | D1,D2 | 4s | 灰 | 灰 |
| Shot2/Scene2 | T2 | D3 | 2s | 灰 | 灰 |

---

### IP-MS-02：Raw / Final 下游消费，Image Plan 不补图

目标：验证 Raw / Final 已存在时，Image Plan 不要求补新图。

输入矩阵：

```text
Shot1 / Scene1
  D1 [0,0,1,1,1]  新图 + Raw + Final
  D2 [0,0,0,0,0]
  D3 [0,0,0,1,1]  Raw + Final

Shot1 / Scene2
  D1 [0,0,0,1,0]  Raw
  D2 [0,0,0,0,0]
  D3 [0,0,0,0,1]  Final

Shot2 / Scene1
  D1 [0,1,0,1,0]  原图 + Raw
  D2 [0,0,0,0,0]
  D3 [0,1,0,0,1]  原图 + Final

Shot2 / Scene2
  D1 [0,0,1,0,0]  新图
  D2 [0,0,0,0,0]
  D3 [0,1,0,0,0]  只有原图
```

期望 Image Plan：

| 位置 | Task | 包含 D | 时长 | 提示词 | 新图 |
| --- | --- | --- | --- | --- | --- |
| Shot1/Scene1 | T1 | D1,D2 | 4s | 灰 | 绿 |
| Shot1/Scene1 | T2 | D3 | 2s | 灰 | 灰 |
| Shot1/Scene2 | T1 | D1,D2 | 4s | 灰 | 灰 |
| Shot1/Scene2 | T2 | D3 | 2s | 灰 | 灰 |
| Shot2/Scene1 | T1 | D1,D2 | 4s | 灰 | 灰 |
| Shot2/Scene1 | T2 | D3 | 2s | 灰 | 灰 |
| Shot2/Scene2 | T1 | D1,D2 | 4s | 灰 | 绿 |
| Shot2/Scene2 | T2 | D3 | 2s | 白 | 灰 |

---

## 6. 开始验证前确认

执行非单个 Scene 验证前，先确认：

1. 测试数据确实包含 2 个 Shot。
2. 每个 Shot 至少 2 个 Scene。
3. 每个 Scene 至少 3 个 D。
4. 每个 D 有明确 start/end/duration。
5. 运行参数为 `max_video_seconds=4`、`split_tolerance_seconds=0`。
6. 打开对应 Plan 页面时，右侧槽位必须严格符合该 Plan 类型。
7. 对照本文件逐项检查颜色、Segment 包含 D、时长、阻塞原因。
