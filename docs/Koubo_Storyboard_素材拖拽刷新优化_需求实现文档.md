# Koubo StoryBoard 素材拖拽刷新优化需求实现文档

日期：2026-06-24

## 1. 背景

故事版（口播）页面右侧 `Asset Pool` 承载原始素材、上传素材和历史素材。用户会从右侧素材池拖拽图片、音频或视频到左侧 Dialogue 的对应槽位，例如原图、新图、Audio、新视频、终视频。

当前实现优先保证一致性：每次绑定或清除素材后，后端返回完整 StoryBoard 详情，前端用返回结果整体替换 `state` 和 `plan`。这个方案在素材较少时简单可靠，但当右侧素材很多，尤其包含大量视频素材时，慢网络下会出现明显卡顿。

本需求目标是在不改变 StoryBoard 文件真相源和现有素材绑定语义的前提下，将“拖放绑定素材”的刷新从全量刷新优化为局部刷新。

## 2. 当前实现位置

### 2.1 前端入口

主页面：

```text
OpenCrew/frontend/src/modules/koubo/KouboStoryBoardModule.jsx
```

右侧素材面板：

```text
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/components/AssetPanel.jsx
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/components/AssetThumb.jsx
```

左侧 Dialogue 槽位：

```text
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/components/DialogueCard.jsx
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/components/ShotCard.jsx
```

API 封装：

```text
OpenCrew/frontend/src/modules/koubo/KouboStoryBoard/kouboStoryboardApi.js
```

### 2.2 后端入口

StoryBoard 绑定接口：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_routes.py
```

完整详情组装：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/video_plan_load_services.py
```

素材池扫描：

```text
OpenCrew/backend/opcrew_backend/koubo/koubo_storyboard/asset_pool_services.py
```

## 3. 当前刷新链路

### 3.1 页面初始加载

页面进入 detail 后调用：

```text
kbApi.detail(taskId)
```

前端执行：

```text
setState({ task: result.task, meta: result.meta })
setPlan(renumberPlan(copy(result.plan)))
```

`meta` 内包含：

- `source_asset_groups`
- `manual_assets`
- `uploaded_images`
- `uploaded_audios`
- `uploaded_videos`
- `history_versions`
- `video_plan_settings`
- `storyboard_video_slots`

右侧 `AssetPanel` 依赖这些字段渲染素材池。

### 3.2 拖拽素材

右侧素材卡片在 `onPointerDown` / `onMouseDown` 时调用：

```text
beginPointerAssetDrag(event, asset, activeTab)
```

拖动过程中主要做本地状态：

- 记录 `selectedAsset`
- 监听 pointer / mouse move
- 给 body 加 `kbsp-dragging-asset`
- 通过 `elementFromPoint` 查找左侧目标槽位

拖动过程中不主动请求网络。

### 3.3 松手绑定

用户松手落到左侧槽位后，前端调用：

```text
assignAsset(dialogueId, asset, targetKind)
```

`assignAsset` 会构造 payload：

```json
{
  "dialogue_id": "srt_0001_dialogue_001",
  "target_kind": "image",
  "plan": "<当前完整 plan>",
  "regroup_working_assets": false,
  "asset_path": "SessionOutput/storyboard/assets/images/example.png"
}
```

随后调用：

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-bind
POST /api/koubo-storyboard/tasks/{task_id}/asset-clear
```

后端保存后执行：

```text
loaded, meta = load_plan(task)
return {"ok": True, "task": task, "meta": meta, "plan": loaded}
```

前端再次整体替换：

```text
setState({ task: result.task, meta: result.meta })
setPlan(renumberPlan(copy(result.plan)))
setSelectedAsset(null)
```

## 4. 当前卡顿原因

### 4.1 网络响应过大

绑定一个 Dialogue 的单个槽位，本质只改变一小段 StoryBoard 数据，但当前返回完整 `task + meta + plan`。

其中 `meta.history_versions`、`uploaded_*`、`source_asset_groups` 会随着素材数量增长变大。慢网络下，用户会感知为拖放后卡住。

### 4.2 后端重复扫描素材池

`load_plan(task)` 每次都会调用 `asset_pool_meta(workspace)`。该函数会扫描上传图片、音频、视频目录，并读取 history versions。

绑定素材时，素材池本身通常没有变化；真正变化的是某个 Dialogue 的绑定关系。当前每次绑定都重新组装素材池，属于不必要工作。

### 4.3 前端全局重绘

前端 `setState` 和 `setPlan` 替换整棵对象后，会触发以下派生计算和渲染：

- `uploadedImages`
- `uploadedAudios`
- `uploadedVideos`
- `historyVersions`
- `assetGroups`
- `sourceImages`
- `usedPaths`
- `assetTextByPath`
- 左侧所有 Shot / Scene / Dialogue 卡片
- 右侧所有素材卡片

素材卡片越多，重绘越明显。

### 4.4 视频缩略图加载放大问题

`AssetThumb` 对视频素材直接渲染：

```text
<video preload="metadata" />
```

当右侧大量视频素材被重新渲染时，浏览器可能重新请求或解析大量视频元数据，进一步放大卡顿。

## 5. 优化目标

### 5.1 产品目标

1. 用户拖拽素材到槽位后，界面应立即反馈绑定结果。
2. 慢网络下，右侧素材池不应因为单次绑定而整体卡住。
3. 上传、删除、导入素材时，素材池仍然能够正确刷新。
4. StoryBoard 的真实状态仍以服务端保存后的 StoryBoard JSON 和 Working 文件为准。
5. 保留现有全量详情接口，避免影响其它入口。

### 5.2 性能目标

以素材池包含 300 个上传素材、100 个历史素材、20 个视频素材为基线：

1. 单次拖放绑定后，左侧目标槽位在 300ms 内出现本地反馈。
2. 单次绑定请求的响应体不包含完整素材池时，payload 大小应明显低于完整 detail。
3. 单次绑定不触发右侧素材池全量重建。
4. 视频素材卡片不因绑定操作批量重新加载 metadata。

## 6. 不在本次范围

1. 不改变 StoryBoard 文件结构。
2. 不改变 `assets/images`、`assets/videos`、`assets/audios`、`assets/history` 的目录语义。
3. 不重做 Asset Library 页面。
4. 不改变生成素材进入 Working 的规则。
5. 不改变 VideoPlan / ImagePlan / VideoOnlyPlan 的执行状态判断规则。

## 7. 后端需求

### 7.1 绑定接口支持轻量返回

现有接口保留：

```text
POST /api/koubo-storyboard/tasks/{task_id}/asset-bind
POST /api/koubo-storyboard/tasks/{task_id}/asset-clear
```

新增可选请求参数：

```json
{
  "response_mode": "patch"
}
```

兼容规则：

- 未传 `response_mode` 时，继续返回完整 `task/meta/plan`。
- 传 `response_mode = "patch"` 时，只返回本次绑定所需的局部结果。

### 7.2 Patch 返回结构

绑定成功：

```json
{
  "ok": true,
  "response_mode": "patch",
  "task_id": 31,
  "storyboard_revision": "sha256-or-mtime",
  "dialogue_id": "srt_0001_dialogue_001",
  "target_kind": "image",
  "asset_path": "SessionOutput/storyboard/assets/images/example.png",
  "dialogue_patch": {
    "dialogue_id": "srt_0001_dialogue_001",
    "source_image_paths": [],
    "image_path": "",
    "bound_image_path": "SessionOutput/storyboard/assets/images/example.png",
    "working_assets": {
      "audio": {},
      "images": [
        {
          "path": "SessionOutput/storyboard/assets/images/example.png",
          "source_type": "upload",
          "asset_type": "Image"
        }
      ],
      "video": {}
    }
  },
  "video_slot_patch": null,
  "regroup_backup": null
}
```

清除成功：

```json
{
  "ok": true,
  "response_mode": "patch",
  "task_id": 31,
  "storyboard_revision": "sha256-or-mtime",
  "dialogue_id": "srt_0001_dialogue_001",
  "target_kind": "image",
  "asset_path": "",
  "old_path": "SessionOutput/storyboard/assets/images/example.png",
  "deleted_working_file": false,
  "dialogue_patch": {
    "dialogue_id": "srt_0001_dialogue_001",
    "bound_image_path": "",
    "working_assets": {
      "audio": {},
      "images": [],
      "video": {}
    }
  },
  "video_slot_patch": null
}
```

### 7.3 后端实现要求

1. `bind_asset` 和 `clear_asset` 仍然必须保存完整 StoryBoard JSON。
2. 保存逻辑继续复用 `coerce_edit_plan`、`bind_asset_to_plan`、`clear_asset_from_plan`、`save_edit_and_source_storyboard`、`recalculate`。
3. Patch 模式下不调用完整 `load_plan(task)`。
4. Patch 模式下可以在保存后的 `plan` 内定位目标 Dialogue，抽取其最新字段作为 `dialogue_patch`。
5. 如 `target_kind` 是 `raw_video` 或 `final_video`，需要返回对应 `video_slot_patch`，让前端同步主界面视频槽显示。
6. 如发生 regroup 或后端判断局部 patch 不安全，应返回：

```json
{
  "ok": true,
  "response_mode": "full_required",
  "reason": "regroup_working_assets",
  "task": {},
  "meta": {},
  "plan": {}
}
```

前端收到 `full_required` 后走现有全量刷新。

### 7.4 拆分完整详情加载能力

建议将 `load_plan(task)` 内部拆成可选加载：

```python
load_plan(task, include_asset_pool=True, include_video_slots=True)
```

其中：

- `include_asset_pool=False` 时不调用 `asset_pool_meta(workspace)`。
- `include_video_slots=False` 时不扫描 `storyboard_video_slot_states`。
- detail 页面初始加载继续使用默认完整模式。
- 绑定 patch 模式尽量不走 `load_plan`。

## 8. 前端需求

### 8.1 绑定请求使用 patch 模式

`assignAsset` 调用 `kbApi.bindAsset` / `kbApi.clearAsset` 时增加：

```json
{
  "response_mode": "patch"
}
```

API 封装保持兼容，不影响其它调用方。

### 8.2 局部更新 plan

新增工具函数：

```text
applyDialoguePatchToPlan(plan, dialogueId, dialoguePatch)
```

职责：

1. 在 `plan.shots[].scenes[].dialogues[]` 中定位目标 Dialogue。
2. 只替换该 Dialogue 的绑定相关字段。
3. 保留其它 Dialogue / Shot / Scene 对象引用，减少重绘范围。
4. 必要时调用轻量 `recalculate` 或只更新受影响 scene/shot duration；素材绑定通常不改变时长，可不重算全部结构。

绑定成功后的前端流程：

```text
1. setBindingState(dialogueId, targetKind, "saving")
2. 发送 patch 请求
3. 收到 dialogue_patch
4. 局部 patch plan
5. 局部 patch meta.storyboard_video_slots
6. setSelectedAsset(null)
7. setBindingState(..., "idle")
8. 后台 debounce reconcile
```

### 8.3 右侧素材池保持稳定

绑定或清除素材时，不更新以下字段：

- `meta.uploaded_images`
- `meta.uploaded_audios`
- `meta.uploaded_videos`
- `meta.manual_assets`
- `meta.history_versions`
- `meta.source_asset_groups`

这些字段只在以下操作后刷新：

- 上传素材
- 删除上传素材
- 删除历史素材
- 从 Asset Library 导入素材
- Clean Image promote 到素材库
- 用户主动刷新素材池
- 页面初始加载

### 8.4 使用状态局部更新

右侧卡片的“已用”状态当前来自 `usedPaths`，它由完整 `shots()` 扫描得到。局部 patch 后，`usedPaths` 仍可从 plan 派生，但应避免右侧全列表重建。

第一阶段可接受 `usedPaths` 重新计算，但 `AssetPanel` 不应因 `state.meta` 替换而重建素材数组。

第二阶段建议新增：

```text
usedPathSetSignal
```

绑定成功时只对 old/new path 做增量更新：

- 绑定：加入新 path。
- 清除：如果该 path 没被其它 Dialogue 使用，则移除。

### 8.5 后台一致性校验

局部 patch 成功后，前端可以做后台 reconcile：

```text
scheduleStoryboardReconcile(taskId, delay=1200ms)
```

规则：

1. 用户连续拖拽多个素材时，只保留最后一次 reconcile。
2. `dirty() === true` 时不执行自动 reconcile。
3. reconcile 只在后台调用完整 detail。
4. 如果完整 detail 与本地 patch 冲突，以服务端完整 detail 为准。
5. reconcile 失败只显示轻量提示，不回滚已经成功的本地 patch。

### 8.6 视频缩略图优化

`AssetThumb` 对视频素材不要在素材池列表中直接加载 `<video preload="metadata">`。

建议第一阶段改为：

```text
视频卡片显示 FilmIcon + 文件名 / 类型标识
```

点击预览或打开详情时才加载真实视频。

第二阶段可增加后端生成或提取 poster：

```text
SessionOutput/storyboard/assets/thumbs/{asset_id}.jpg
```

素材卡片使用 `<img loading="lazy">` 展示 poster。

### 8.7 素材池虚拟化

当单个 tab 素材数量超过阈值时启用虚拟列表：

```text
threshold = 120
```

第一版可只虚拟化 `upload` 和 `history` tab。`source` tab 通常数量跟 StoryBoard Dialogue 数量相关，可后续再做。

虚拟化要求：

1. 保留当前 card column 设置。
2. 保留拖拽能力。
3. 保留删除按钮。
4. 保留 active tab。
5. 不破坏滚动位置。

## 9. 状态和一致性规则

### 9.1 Dirty 状态

如果 `dirty() === true`，当前 `assignAsset` 会把完整当前 `plan` 发送给后端。优化后仍保持该规则。

Patch 模式只改变响应形态，不改变保存输入。

### 9.2 Grouping Dirty 状态

如果 `groupingDirty() === true`，绑定可能触发 Working 素材重组或备份。此时局部 patch 风险更高。

建议规则：

- 前端仍可请求 `response_mode = "patch"`。
- 后端如果发生 regroup，返回 `response_mode = "full_required"`。
- 前端收到后走完整 `setState + setPlan`。

### 9.3 执行弹窗状态

当前 `assignAsset` 成功后会调用：

```text
refreshOpenPlanStates()
```

优化后保留该逻辑。区别是：

- 主 StoryBoard 绑定显示走局部 patch。
- 已打开的 ImagePlan / VideoOnlyPlan / VideoPlan 弹窗仍按现有执行状态接口刷新。

### 9.4 错误回滚

第一版建议不做乐观写入，只做“请求成功后局部 patch”。这样失败时无需回滚。

如果后续要做真正乐观更新：

1. 先保存绑定前的 dialogue snapshot。
2. 请求失败后恢复 snapshot。
3. 弹出错误提示。
4. 禁止同一 dialogue 同一 slot 并发提交。

## 10. 分阶段实施

### Phase 1：轻量绑定返回和局部 patch

后端：

1. `asset-bind` / `asset-clear` 支持 `response_mode = "patch"`。
2. 保存后返回 `dialogue_patch`。
3. regroup 或复杂情况返回 `full_required`。
4. 保留默认完整返回兼容。

前端：

1. `assignAsset` 请求 patch 模式。
2. 新增 `applyDialoguePatchToPlan`。
3. 收到 patch 后只更新目标 Dialogue。
4. 保留 `refreshOpenPlanStates()`。
5. 保留失败时错误提示。

### Phase 2：视频缩略图轻量化

1. `AssetThumb` 视频素材默认不加载 `<video>`。
2. 使用图标或 poster 占位。
3. 预览时再加载真实视频。

### Phase 3：素材池虚拟化

1. 对 `upload` tab 和 `history` tab 做虚拟列表。
2. 保持拖拽、删除、选中态、已用态可用。
3. 大素材池下滚动和拖拽不卡顿。

### Phase 4：后台 reconcile 和素材池独立刷新

1. 新增 debounce 后台完整校验。
2. 新增独立素材池刷新接口：

```text
GET /api/koubo-storyboard/tasks/{task_id}/asset-pool
```

3. 上传、删除、导入素材只刷新素材池，不刷新完整 StoryBoard。

## 11. 验收标准

### 11.1 功能验收

1. 图片素材拖到“新图”槽位后，槽位显示正确。
2. 图片素材拖到“原图”槽位后，原图绑定正确。
3. 音频素材拖到 Audio 槽位后，音频可播放。
4. 视频素材拖到新视频 / 终视频槽位后，视频槽位显示正确。
5. 清除槽位后，对应绑定被保存。
6. 刷新浏览器后，绑定结果仍然存在。
7. 上传素材、删除素材、导入素材后，右侧素材池仍正确刷新。

### 11.2 性能验收

1. 单次绑定接口 patch 模式响应不包含 `uploaded_images`、`uploaded_videos`、`history_versions` 全量列表。
2. 单次绑定不会让右侧素材池所有视频卡片重新加载 metadata。
3. 300 个以上素材时，拖放后的界面反馈不被完整素材池刷新阻塞。
4. 连续拖放 5 次，不出现请求乱序导致旧绑定覆盖新绑定。

### 11.3 兼容验收

1. 不传 `response_mode` 的旧调用仍返回完整 `task/meta/plan`。
2. `groupingDirty` 或后端复杂场景下，`full_required` 能自动降级为完整刷新。
3. ImagePlan / VideoPlan / VideoOnlyPlan 弹窗状态刷新不受影响。
4. StoryBoard 保存、页面初始加载、Session Variables 刷新仍使用完整 detail。

## 12. 风险和处理

### 12.1 局部 patch 与服务端真实状态不一致

处理：

- Patch 只使用后端保存后的返回值，不使用纯前端猜测。
- 后台 debounce reconcile 校验。
- 页面刷新后仍以完整 detail 为准。

### 12.2 多次快速拖放乱序

处理：

- 给每个绑定请求生成 `client_request_id`。
- 前端记录每个 slot 的最新 request id。
- 旧响应返回时，如果不是最新 request id，不应用到 UI。

### 12.3 素材池使用状态不准

处理：

- Phase 1 仍从 plan 派生 `usedPaths`，保证正确优先。
- Phase 2 再做增量 used path 优化。

### 12.4 视频 poster 缺失

处理：

- 第一阶段使用图标占位，不依赖 poster。
- poster 作为后续增强，不阻塞本优化。

## 13. 推荐实现顺序

1. 后端 `asset-bind` / `asset-clear` 增加 patch 返回。
2. 前端 `assignAsset` 支持 patch / full_required 双路径。
3. 新增 `applyDialoguePatchToPlan` 并覆盖图片、音频、视频、清除四类槽位。
4. 移除素材池列表中视频卡片的默认 metadata 加载。
5. 增加拖放绑定回归测试。
6. 大素材池场景下做手工性能验证。
7. 再进入虚拟化和独立素材池刷新。

## 14. 建议测试用例

### 14.1 前端单元测试

1. `applyDialoguePatchToPlan` 能定位目标 Dialogue。
2. 图片 patch 只修改目标 Dialogue。
3. 音频 patch 不影响其它 Dialogue 的音频。
4. raw video / final video patch 能更新对应槽位。
5. clear patch 能清空对应 slot。

### 14.2 后端接口测试

1. `asset-bind response_mode=patch` 返回 `dialogue_patch`。
2. patch 响应不包含完整素材池。
3. 不传 `response_mode` 仍返回完整 detail。
4. `asset-clear response_mode=patch` 返回 `old_path` 和 `deleted_working_file`。
5. regroup 场景返回 `full_required` 或完整 detail。

### 14.3 手工回归

1. 准备一个右侧包含大量图片、音频、视频和历史素材的 StoryBoard 任务。
2. 连续拖拽 10 个素材到不同 Dialogue。
3. 观察右侧素材池滚动位置不跳动。
4. 观察视频卡片不批量重新加载。
5. 刷新页面确认所有绑定仍存在。

