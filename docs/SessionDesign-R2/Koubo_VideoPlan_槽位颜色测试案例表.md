# Koubo VideoPlan 槽位颜色测试案例表

## 通用颜色优先级

1. 规范业务文件存在且非空时，对应步骤优先绿色；旧 execution state 的运行中或失败不能覆盖已落盘文件。
2. 只有当前任务正在运行且规范业务文件尚未落盘时，才显示黄色。
3. 只有规范业务文件不存在且当前 execution state 明确失败时，才显示红色。
4. 白色表示前置条件已满足、当前步骤可执行但尚未落盘；灰色表示该步骤不执行、跳过或 blocked。

## Video Plan 基础槽位

| 用例编号 | Slots [Audio, Source, Image, Raw, Final] | Audio | Image | Raw Video | Final Video | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| VP-S1 | [0,1,0,0,0] | 白 | 白 | 灰 | 灰 | 原图存在，待补新图 |
| VP-S2 | [0,0,1,0,0] | 白 | 绿 | 白 | 灰 | 新图存在，可生成 Raw |
| VP-S3 | [0,0,0,1,0] | 白 | 灰 | 绿 | 灰 | Raw 存在但音频缺失，Video Plan 音频合成条件不足 |
| VP-S4 | [0,0,0,0,1] | 白 | 灰 | 灰 | 绿 | Final 已存在 |
| VP-S5 | [1,0,0,1,1] | 绿 | 灰 | 绿 | 绿 | 下游产物已存在 |

## Video Plan Prompt 槽位

| 用例编号 | 场景 | 输入 | Prompt 文件 | Prompt | 预期 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| VP-P1 | Image Prompt | 原图存在，新图/Raw/Final 不存在 | 不存在 | 白 | 可生成 Image Prompt | 原图就绪 |
| VP-P2 | Image Prompt | 原图存在，新图/Raw/Final 不存在 | 存在 | 绿 | Image Prompt 已存在 | 文件优先 |
| VP-P3 | Video Prompt | 新图存在，Raw/Final 不存在 | 不存在 | 白 | 可生成 Video Prompt | 新图就绪 |
| VP-P4 | Video Prompt | 新图存在，Raw/Final 不存在 | 存在 | 绿 | Video Prompt 已存在 | 文件优先 |
| VP-P5 | Video Prompt | Raw 存在，Final 不存在 | 不存在 | 灰 | Raw 已消费 Prompt | 下游已存在 |

## Image Plan 基础槽位

| 用例编号 | Slots [Audio, Source, Image, Raw, Final] | Image Prompt | Image | 备注 |
| --- | --- | --- | --- | --- |
| IP-S1 | [0,1,0,0,0] | 白 | 灰 | Image 必须等待 Prompt |
| IP-S2 | [0,0,1,0,0] | 灰 | 绿 | 新图已存在 |
| IP-S3 | [0,0,0,1,0] | 灰 | 灰 | Raw 已消费新图 |
| IP-S4 | [0,0,0,0,1] | 灰 | 灰 | Final 已存在 |
| IP-S5 | [0,1,0,1,0] | 灰 | 灰 | Raw 已存在，不再提示补新图 |
| IP-S6 | [0,1,0,0,1] | 灰 | 灰 | Final 已存在，不再提示补新图 |

## Image Plan Prompt 槽位

| 用例编号 | Slots [Audio, Source, Image, Raw, Final] | Prompt 文件 | Image Prompt | Image | 备注 |
| --- | --- | --- | --- | --- | --- |
| IP-P1 | [0,1,0,0,0] | 存在 | 绿 | 白 | 原图和 Prompt 就绪 |
| IP-P2 | [0,0,0,0,0] | 存在 | 绿 | 灰 | 缺少原图 |
| IP-P3 | [0,0,0,0,1] | 存在 | 绿 | 灰 | Final 已存在 |
| IP-P4 | [0,1,0,0,0] | 不存在 | 白 | 灰 | 只有原图不能直接执行 Image，必须先生成 Image Prompt |

## Video Only Plan 基础槽位

| 用例编号 | Slots [Audio, Source, Image, Raw, Final] | Audio | Image | Video Prompt | Raw Video | Copy Final | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| VOP-S1 | [0,1,0,0,0] | 白 | 白 | 灰 | 灰 | 灰 | 原图存在，先补新图 |
| VOP-S2 | [0,0,1,0,0] | 白 | 绿 | 白 | 灰 | 灰 | Image_New 存在但 Prompt 不存在时，Raw 不能白，必须先生成 Video Prompt |
| VOP-S3 | [0,0,0,1,0] | 白 | 灰 | 灰 | 绿 | 白 | Raw 存在，可 Confirm Final |
| VOP-S4 | [0,0,0,0,1] | 白 | 灰 | 灰 | 灰 | 绿 | Final 已存在 |
| VOP-S5 | [1,0,0,1,1] | 绿 | 灰 | 灰 | 绿 | 绿 | Raw 和 Final 已存在 |
| VOP-S6 | [0,0,0,1,0] | 白 | 灰 | 灰 | 绿 | 白 | Confirm Final 不依赖音频，Raw 存在即可拷贝成终视频 |

## Video Only Plan Prompt 槽位

| 用例编号 | Slots [Audio, Source, Image, Raw, Final] | Prompt 文件 | Video Prompt | Raw Video | Copy Final | 预期 | 备注 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| VOP-P1 | [0,0,1,0,0] | 不存在 | 白 | 灰 | 灰 | 可生成 Prompt | 新图就绪 |
| VOP-P2 | [0,0,1,0,0] | 存在 | 绿 | 白 | 灰 | 可生成 Raw | Prompt 就绪 |
| VOP-P3 | [0,0,0,1,0] | 不存在 | 灰 | 绿 | 白 | 可 Confirm Final | Raw 已存在 |
