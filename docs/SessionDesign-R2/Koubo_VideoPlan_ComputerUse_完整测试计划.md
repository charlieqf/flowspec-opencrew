# 故事版（口播）VideoPlan 界面端 Computer Use 完整测试计划

版本：v1.0

范围：验证 `故事版（口播）` 工作台底部 Timeline 新增的 VideoPlan 图标按钮、参数浮层、计划生成 API、缓存复用、重新生成判断和只读计划 Modal。

## 1. 测试前置条件

1. 打开本地 OpenCrew 前端：`http://127.0.0.1:18080`
2. 后端可访问：`http://127.0.0.1:8011`
3. Task #31 / Session #87 的 StoryBoard 已存在：
   - `SessionOutput/storyboard/srt_storyboard.json`
   - 如已有 UI 保存版本，则使用 `SessionOutput/storyboard/koubo_storyboard_edit.json`
4. 进入页面：`#/koubo-storyboard/tasks/31/detail`
5. 主保存按钮为 disabled 时，VideoPlan 按钮才允许点击。

## 2. 验收点

1. 底部 Timeline 中央控制区出现 VideoPlan 图标按钮，位置在播放/倍速控件左侧。
2. VideoPlan 按钮为纯图标，title / aria-label 为 `VideoPlan`。
3. 参数按钮打开紧凑浮层，样式与倍速浮层一致。
4. 参数浮层包含：
   - `Max Video`
   - `Min Video`
   - `Split Tolerance`
   - `Cancel`
   - `Apply`
5. StoryBoard 有未保存改动时，VideoPlan 按钮 disabled，并提示先保存。
6. 点击 VideoPlan 后：
   - 若缓存匹配，直接打开 Modal。
   - 若缓存不匹配，删除旧 plan 与 ui cache，重新调用 `05_01`，完成后打开 Modal。
7. Modal 样式、图标、布局与 `video_generation_plan_tracker.html` 保持一致的视觉语言。
8. Modal 只展示 plan，不执行 `05_02`。
9. Modal 顶部展示：
   - Shots / Scenes / Segments
   - Audio / Image / Video / Lip Sync 总任务数
   - Cache Hit 或 Regenerated
   - mismatch reason
   - 当前参数
10. `completed_with_blocked_items` 或 `completed_with_skipped_items` 时，Modal 仍打开并展示说明。
11. API 失败时，不打开成功 Modal，页面显示全局 error banner。
12. 连续点击时不会并发触发多个生成请求。

## 3. Computer Use 测试步骤

### 3.1 页面加载与按钮位置

1. 用浏览器打开 `http://127.0.0.1:18080/#/koubo-storyboard/tasks/31/detail`。
2. 等待 StoryBoard 详情加载完成。
3. 观察底部 Timeline 中央控制区。
4. 确认 VideoPlan 图标在播放按钮左侧。
5. 悬停按钮，确认 tooltip 为 `VideoPlan`。

通过标准：按钮位置正确，未出现额外消息区。

### 3.2 保存状态 gating

1. 修改任意一句 dialogue 文本或时长，让主保存按钮变为可用。
2. 观察 VideoPlan 按钮状态。
3. 悬停 VideoPlan 按钮。
4. 点击 VideoPlan 按钮。

通过标准：按钮 disabled，提示保存后再使用，不触发 Modal 或 API 生成。

### 3.3 参数浮层

1. 点击 VideoPlan 旁的参数图标。
2. 修改 `Max Video`、`Min Video`、`Split Tolerance`。
3. 点击 `Apply`。
4. 再次打开参数浮层。

通过标准：参数被保留在前端状态中，浮层样式与播放速度浮层一致。

### 3.4 Scene 范围生成

1. 点击任意 Scene 时间条，确保当前 scope 为 Scene。
2. 在保存按钮 disabled 的状态下点击 VideoPlan。
3. 等待 Modal 打开。
4. 查看 Modal 顶部 scope 文案和 plan 内容。

通过标准：Modal 展示当前 scene 的 plan，`target_type=scene`，只出现该 scene 的 segments。

### 3.5 Shot 范围生成

1. 点击任意 Shot 时间条，确保当前 scope 为 Shot。
2. 点击 VideoPlan。
3. 等待 Modal 打开。

通过标准：Modal 展示当前 shot 的 plan，`target_type=shot`，只出现该 shot 下的 scenes。

### 3.6 全量 ShotPlan 范围生成

1. 点击 Timeline 空白区域，确保 scope 为 all。
2. 点击 VideoPlan。
3. 等待 Modal 打开。

通过标准：Modal 展示完整 ShotPlan，`target_type=task`，summary 覆盖所有 shots / scenes / segments。

### 3.7 缓存命中

1. 在不改 StoryBoard、不改参数、不改媒体绑定的情况下，再次点击 VideoPlan。
2. 观察 Modal 顶部状态。

通过标准：显示 `Cache Hit`，不重新生成。

### 3.8 参数变化触发重跑

1. 修改 VideoPlan 参数并 Apply。
2. 点击 VideoPlan。
3. 观察 Modal 顶部状态。

通过标准：显示 `Regenerated`，reason 包含参数签名不匹配。

### 3.9 媒体绑定变化触发重跑

1. 绑定或清除当前 dialogue 的图片/音频/视频素材。
2. 保存 StoryBoard，让保存按钮重新 disabled。
3. 点击 VideoPlan。

通过标准：显示 `Regenerated`，reason 包含媒体绑定或输入签名不匹配。

### 3.10 并发点击防护

1. 点击 VideoPlan 后，在生成完成前连续点击同一按钮。
2. 观察按钮状态和请求结果。

通过标准：生成中按钮 disabled，不产生多个并发请求。

## 4. 文件级验证

生成或重跑后检查：

1. `SessionOutput/storyboard/video_generation_plan.json` 存在。
2. `SessionOutput/storyboard/video_generation_plan.ui_cache.json` 存在。
3. `ui_cache` 中包含：
   - `scope_signature`
   - `parameter_signature`
   - `storyboard_structure_signature`
   - `media_binding_signature`
   - `input_signature`
   - `cache_status`
   - `reason`
4. 重跑前旧的 `S8_05_01_VideoPlanGenerator/`、主 plan 与 ui cache 会被删除。

## 5. 不在本轮测试范围

1. 不测试 `05_02_VideoPlanExecutor.py`。
2. 不测试多人同时操作。
3. 不测试媒体文件内容 hash，只验证 path / slot / source_type。
4. 不测试远端模型真实视频生成。
