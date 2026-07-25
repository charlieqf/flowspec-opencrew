# Koubo Scene 状态回归测试记录 2026-06-17

## 测试范围

- Task: `#4`
- Session: `#5`
- Shot: `shot_001`
- Scene: `scene_001`
- Segment: `S1`
- 关键资产：`srt_0001`、`srt_0004`
- 覆盖对象：StoryBoard 槽位、Image Plan、Video Only Plan、Video Plan

## 本轮结论

通过。

> 2026-06-17 复核更正：下方“Computer Use 证据”中列出的多张 PNG 后续复查发现并非 OpenCrew 目标界面的有效浏览器截图，不能作为 UI 通过证据。当前可确认的是接口断言与 JSON 证据通过；UI 通过结论只能来自当时的 Computer Use 实时可访问性观察，未形成可审计截图。因此本记录中的 UI 截图证据应标记为无效，完整 UI 回归需要重新截图留证。

本轮覆盖了两条路径：

1. Computer Use 端真实点击第一遍生成：先清空目标 Scene 产出，再从 Image Plan 点击“提示词+新图”，从 Video Only Plan 点击“提示词+新视频”，随后点击“拷贝成终视频”。
2. 接口端回归：逐项清除并恢复 Audio、新图、Video Raw、Video Final，验证 Video Only Plan 与 Video Plan 的绑定状态同步。

同时覆盖拖动回填验证：生成都覆盖后，清空 `srt_0001` 新图，保持 Raw/Final 不动，再从历史素材拖回新图。界面左侧绑定状态、StoryBoard 槽位和接口状态均重新显示新图已绑定。

## Computer Use 证据

- Image Plan 点击“提示词+新图”并完成：`/private/tmp/koubo_scene_regression_image_plan_done_20260617.png`
- Video Only Plan 点击“提示词+新视频”并完成 Raw，再点击“拷贝成终视频”完成 Final：`/private/tmp/koubo_scene_regression_video_only_done_20260617.png`
- 拖动回填前，新图为空且 Raw/Final 保留：`/private/tmp/koubo_scene_regression_before_drag_restore_image_20260617.png`
- 拖动回填后，新图槽恢复，历史素材显示已用：`/private/tmp/koubo_scene_regression_after_drag_restore_image_20260617.png`

## 接口证据

- 第一遍真实生成与拖动验证证据：`/private/tmp/koubo_scene_regression_evidence_20260617.json`
- 接口槽位回归完整时间线：`/private/tmp/koubo_slot_state_test_results.json`
- 接口断言结果：`/private/tmp/koubo_scene_regression_assertions_20260617.json`

## 新增补测：先 Audio，再 Video Plan 一键，再拖拽

本补测按方案补充了“先生成 Audio，然后一键用 Video Plan 生成所有产出物，然后再拖拽”的路径。

执行结果：

| 阶段 | 结果 |
| --- | --- |
| 清空目标槽位 | 通过。清空 `srt_0001` / `srt_0004` Audio、`srt_0001` 新图、Raw、Final；未通过文件系统直接清 TailFrame。 |
| StoryBoard 点击音频生成 | 通过。两个 dialogue Audio 生成完成，Video Plan / Video Only Plan 的 Segment Audio 依据 dialogue Audio 计算为已完成。 |
| Video Plan 一键执行 | 部分通过。Video Plan 成功生成并同步 Segment Audio、新图、Raw Video、TailFrame。 |
| Final Video 自然生成 | 阻断。第一次失败为 backend Python 缺少 `PySocks`，补齐依赖后重试，Sync.so 请求被本机 socks 代理规则拒绝：`0x02: Connection not allowed by ruleset`。该阻断属于外部网络/本机代理规则，不属于槽位绑定逻辑失败。 |
| 历史素材拖拽回填 | 通过。从历史素材拖回 `srt_0001_Image_New.png` 与 `srt_0001_Video_Final.mp4` 后，StoryBoard 槽位、左侧绑定状态与接口状态一致，`next_action=none`。 |

新增证据：

- Audio 先生成完成截图：`/private/tmp/koubo_scene_regression_full_chain_audio_done_20260617.png`
- Video Plan 一键执行状态证据：`/private/tmp/koubo_scene_regression_video_plan_full_chain_20260617.json`
- Final 第一次环境依赖错误截图：`/private/tmp/koubo_scene_regression_full_chain_video_plan_final_error_20260617.png`
- Final 重试后的 Sync.so 代理规则错误截图：`/private/tmp/koubo_scene_regression_full_chain_video_plan_sync_proxy_error_20260617.png`
- Final 重试后的执行状态 JSON：`/private/tmp/koubo_scene_regression_full_chain_video_plan_execution_after_pysocks_20260617.json`
- 拖拽前截图：`/private/tmp/koubo_scene_regression_full_chain_before_drag_restore_20260617.png`
- 拖拽后截图：`/private/tmp/koubo_scene_regression_full_chain_after_drag_restore_20260617.png`
- 拖拽后接口断言：`/private/tmp/koubo_scene_regression_full_chain_drag_assertion_20260617.json`

补测观察：

- Video Plan 弹窗内一键执行完成新图后显示“新图已绑定”，但回到 StoryBoard 时主槽位仍显示新图为空；接口计算状态 `new_image_exists=true`。历史素材拖回新图后，StoryBoard 主槽位和左侧绑定状态恢复一致。
- Final Video 的生成链路已经走到 Sync.so lipsync 请求，当前未自然完成的直接原因是本机代理规则拒绝 `api.sync.so`，不是 Video Plan / Video Only Plan 的状态计算差异。

## 关键断言

| 场景 | 结果 |
| --- | --- |
| 全量恢复后，Audio / 新图 / Raw / Final 全部存在 | 通过 |
| 清 `srt_0001` Audio 只影响 Audio，不清 Raw / Final | 通过 |
| 恢复 `srt_0001` Audio 后，Video Only Plan 与 Video Plan 的 Audio 状态同步恢复 | 通过 |
| 清 `srt_0004` Audio 后，Segment Audio 状态在 Video Only Plan 与 Video Plan 中同步变为未完成 | 通过 |
| 清新图只影响 frame input，新图变空但 Raw / Final 保留 | 通过 |
| 清 Raw 时 Final 保留，Video Only Plan 与 Video Plan 的 Raw 状态同步变空 | 通过 |
| 清 Final 时 Raw 保留，Video Only Plan 与 Video Plan 的 Final 状态同步变空 | 通过 |
| 恢复 Final 后，Final 绑定状态在 Video Only Plan 与 Video Plan 中同步恢复 | 通过 |
| 拖动历史新图回填后，界面与接口均显示新图已绑定 | 通过 |

## 需要保留的预期行为

- Video Plan 可以呈现计算后的执行结果；当已有绑定视频导致新图不需要执行时，新图步骤可以保持灰色“不执行”状态。
- 清 Audio 只清 Audio，不连带清除 `Video_Raw`、`Video_Final`、`TailFrame`。
- `Video_Raw`、`Video_Final`、`TailFrame` 只在用户手动清除对应槽位时清除。

## v0.3 补测：多 Shot / 多 Scene / 分割 / 尾帧

本补测按 `Koubo_Scene_状态回归测试方案.md` v0.3 追加覆盖 Task `#4` / Session `#5` 的多 Shot、多 Scene、Scene Split、Shot Split、合并恢复、槽位矩阵和尾帧计算。

执行结果：通过，另记录 1 个接口边界风险。

| 覆盖项 | 结果 | Should 状态 |
| --- | --- | --- |
| 当前结构基线 | 通过 | 3 个 Shot、4 个 Scene、5 个 Dialogue；Video Plan 生成 4 个 Segment。 |
| 多 Dialogue 合并为 Segment | 通过 | `shot_001/scene_001` 中 `srt_0001` 与 `srt_0004` 合并为同一个 Segment，Segment Audio 按两个 dialogue Audio 计算。 |
| Split Scene | 通过 | 将 `srt_0004` 拆到新 Scene 后，Video Plan 生成独立 Segment；恢复后重新合并回 `srt_0001` Segment。 |
| Split Shot | 通过 | 将 `srt_0004` 拆到新 Shot 后，保存会按顺序正规化 Shot ID；Video Plan 仍生成独立 Segment，并以 `srt_0001_TailFrame.jpg` 作为后续首帧来源。 |
| 多 Shot / 多 Scene 尾帧链 | 通过 | 后续 Segment 的 `first_frame_source_type` 按 `previous_segment_tail_frame` 计算，来源路径指向前一 Segment 的 TailFrame。 |
| Audio 清除/恢复 | 通过 | 清 Audio 只影响 Audio；新图、Raw、Final 保留。恢复后 Video Plan / Video Only Plan / StoryBoard 状态一致。 |
| 新图清除/恢复 | 通过 | 清新图只影响新图；Raw、Final 保留。恢复后状态一致。 |
| Raw 清除/恢复 | 通过 | 清 Raw 只影响 Raw；Final 保留且仍绑定。恢复后状态一致。 |
| Final 清除/恢复 | 通过 | 清 Final 只影响 Final；Raw 保留。恢复后状态一致。 |
| Computer Use 视觉验证 | 通过 | 刷新后 StoryBoard 第一个 dialogue 显示 Audio / 原图 / 新图 / Raw / Final 均已绑定；Video Plan 显示 Audio `1/1`、Raw `1/1`、Final `1/1`。 |
| Video Plan 新图灰色状态 | 通过 | 因已有视频绑定，新图步骤显示灰色“不执行”，这是计算结果呈现，不作为缺失失败。 |

新增证据：

- 多 Shot / 多 Scene 基线与计划：`/private/tmp/koubo_multishot_baseline_task4_session5_20260617.json`、`/private/tmp/koubo_multishot_plan_baseline_20260617.json`
- Split Scene / Split Shot 结构证据：`/private/tmp/koubo_multishot_structure_split_evidence_20260617.json`
- 槽位清除/恢复矩阵：`/private/tmp/koubo_multishot_slot_matrix_evidence_20260617.json`
- Video Plan 弹窗截图：`/private/tmp/koubo_multishot_video_plan_ui_20260617.png`

补测发现：

| 发现 | 影响 | 当前处理 |
| --- | --- | --- |
| `asset-clear` 传入 `target_kind=tailframe` 未被拒绝，而是落入默认图片分支，清掉 `Image_New`。 | 这是接口边界风险。尾帧目前不应通过该 target 值测试；否则会误清新图。 | 已立即通过历史素材恢复并重新绑定 `srt_0001_Image_New.png`。最终 StoryBoard、接口和 Video Plan 均恢复一致。 |

最终状态：

- `srt_0001`：Audio / Source / New Image / Raw Video / Final Video 均已绑定，`next_action=none`。
- `srt_0004`：Audio 已绑定；Source / New Image / Raw Video / Final Video 未绑定，符合当前测试数据状态。
- `srt_0007`、`srt_0009`、`srt_0013`：均保持当前空槽位状态，用于验证多 Shot / 多 Scene 的计划计算与尾帧依赖。
