# Analysis_V1 03_03 TTSBuilderQuickAdv 工具页面设计

日期：2026-06-15
状态：易用性优化版页面设计
范围：设计一个直观、简单、但仍支持高级声音匹配和云端声音克隆的 TTSBuilderQuickAdv 页面。

## 1. 设计结论

TTSBuilderQuickAdv 页面默认不做成复杂工作台，而做成三步流程：

```text
1. 选择参考声音
2. 获取声音方案
3. 试听并保存候选
```

高级能力仍保留，但默认折叠：

1. provider / model / target_model。
2. Resemblyzer / SpeechBrain 分数。
3. prompt 原文。
4. request id / clone_id。
5. 历史试听详细记录。

用户不理解 provider、模型和 embedding 分数，也能完成任务。懂技术的用户可以展开高级面板调细节。

最终仍写入：

```text
SessionOutput/tts/tts_builder_candidates.json
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
```

## 2. 页面入口

现有 `AnalysisV1TTSBuilder.jsx` 增加入口：

```text
Builder-Adv
```

打开后标题使用中文：

```text
高级声音匹配
```

副标题：

```text
选择一段参考声音，推荐相似音色，或创建云端克隆音色。
```

入口状态：

| 状态 | 页面行为 |
|---|---|
| 未完成 `02_02` | 显示阻断提示：请先完成字幕帧对齐 |
| 缺参考音频 | 只显示上传参考声音 |
| 有参考音频 | 自动进入第 1 步 |
| 有历史候选 | 自动恢复候选槽位和试听历史 |

## 3. 信息架构

页面只显示三个主区：

```text
顶部：步骤条和主要动作
中部：当前步骤内容
底部：最终候选 1 / 2 / 3
```

不默认显示三栏复杂布局。高级信息通过展开项展示。

步骤条：

```text
参考声音 -> 声音方案 -> 试听保存
```

当前步骤高亮；已完成步骤显示 check 状态。

## 4. 第 1 步：选择参考声音

目标：让用户确认用于匹配/克隆的 10-20 秒声音片段。

默认内容：

1. 参考音频播放器。
2. waveform 选区。
3. 当前选区时长。
4. `播放选区`。
5. `重新分析`。
6. `下一步`。

主要按钮：

```text
播放选区
重新分析
下一步：获取声音方案
```

质量提示只显示一句话：

| 分数 | 文案 |
|---:|---|
| >= 80 | 这段声音清晰，适合匹配和克隆 |
| 60-79 | 这段声音可用，但建议避开噪声或停顿 |
| < 60 | 建议重新选择更清晰、连续的人声片段 |

高级详情折叠：

```text
采样详情
- 有效人声
- 句子边界
- 文本覆盖
- 音量稳定
- 噪声风险
- selected_range
```

## 5. 第 2 步：获取声音方案

用户只需要看到两个选择：

```text
推荐相似音色
克隆这个声音
```

### 5.1 推荐相似音色

卡片文案：

```text
从系统音色库里找最接近参考声音的音色，生成试听样本。速度快，适合大多数场景。
```

按钮：

```text
推荐相似音色
```

行为：

1. 调 `rank`。
2. 自动展示 Top 推荐。
3. 默认选中第 1 个。

推荐列表默认只展示：

| 字段 | 展示 |
|---|---|
| 排名 | `#1` |
| 名称 | voice label |
| 来源 | Gemini / Qwen / ByteDance |
| 匹配度 | 例如 `92` |
| 精度 | `高精度匹配` 或 `基础匹配` |
| 操作 | `试听` |

`匹配度` 是当前可用后端下的 0 到 100 综合分。SpeechBrain 可用时显示 `高精度匹配`；SpeechBrain 不可用、降级到 Resemblyzer + 声学特征时显示 `基础匹配`。两种模式都可用于当前列表排序，但不用于跨任务、跨部署做绝对比较。

默认不显示 timbre / pitch / pace。点击详情才显示。

详情折叠：

```text
为什么推荐
- 音色相似度
- 音调匹配
- 语速匹配
- 清晰度
- Resemblyzer
- SpeechBrain
- 评分模式
```

### 5.2 克隆这个声音

卡片文案：

```text
使用云端声音复刻创建一个自定义 voice_id，再用它生成试听。更接近参考声音，但需要确认你有权使用这段声音。
```

按钮：

```text
克隆这个声音
```

点击后打开确认弹窗。

弹窗默认只显示：

1. 使用服务：`阿里云 CosyVoice`。
2. 目标模型：默认 `cosyvoice-v3.5-plus`。
3. 参考片段时长。
4. 授权确认 checkbox。
5. `创建自定义音色`。

授权确认文案：

```text
我确认对这段参考音频拥有使用授权，并允许将该音频发送到所选云端 TTS provider 用于创建本任务的自定义音色。
```

高级设置折叠：

```text
高级设置
- target_model
- voice_name_prefix
- audio_url
- region
```

成功后：

1. 显示“自定义音色已创建”。
2. 自动进入第 3 步。
3. 新 voice 出现在 `我的克隆音色` 区域。

失败提示必须用户可理解：

| 原因 | 文案 |
|---|---|
| 未授权 | 请先确认参考声音的使用授权 |
| 质量不足 | 这段声音不够清晰，请回到第 1 步重新选择 |
| 无音频 URL | 当前环境没有配置云端可访问的音频链接 |
| provider 失败 | 创建失败，请稍后重试或查看高级详情 |

## 6. 第 3 步：试听并保存候选

第 3 步显示两个区域：

```text
可试听声音
最终候选
```

### 6.1 可试听声音

列表合并展示：

1. 推荐相似音色。
2. 我的克隆音色。
3. 历史试听。

默认字段：

| 字段 | 说明 |
|---|---|
| 名称 | voice label 或自定义名称 |
| 类型 | 推荐音色 / 克隆音色 |
| 匹配度 | 推荐音色显示分数，克隆音色显示“自定义” |
| 操作 | 试听 / 保存 |

试听设置默认只显示：

1. 朗读文本。
2. 风格提示词。
3. 语速。
4. `试听`。

高级设置折叠：

```text
高级设置
- provider
- model
- voice_id
- voice_source
- prompt 原文
- tempo 精确值
```

### 6.2 保存候选

每次 preview 成功后显示：

```text
保存到候选 1
保存到候选 2
保存到候选 3
```

底部固定候选槽位：

```text
候选 1  [播放] [替换] [清空]
候选 2  [播放] [替换] [清空]
候选 3  [播放] [替换] [清空]
```

只要至少 1 个槽位有内容，启用：

```text
保存为最终候选
```

点击后调用 `finalize`。

完成提示：

```text
已保存到 SessionOutput/tts，后续视频生成会使用这些候选声音。
```

## 7. 按钮文案

页面使用用户语言，不使用工程名。

| 工程动作 | 页面文案 |
|---|---|
| `sample-reference` | `重新分析` |
| `rank` | `推荐相似音色` |
| `clone-voice` | `克隆这个声音` |
| `preview` | `试听` |
| `save-candidate` | `保存到候选` |
| `finalize` | `保存为最终候选` |
| `state` | 页面自动加载 |

`Builder-G`、`QuickAdv`、`Resemblyzer`、`SpeechBrain` 默认不出现在主流程按钮中，只在高级详情里出现。

## 8. 后端 API

页面仍使用这些 API：

```text
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/state
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/sample-reference
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/catalog
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/rank
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-voice
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/cloned-voices
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/plan-prompt
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/preview
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/save-candidate
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/finalize
```

后端 API 设计不变；变化只在 UI 层把复杂能力组织成三步流程。

## 9. 页面状态模型

`state` API 返回建议结构：

```json
{
  "ok": true,
  "task_id": 123,
  "session_id": 456,
  "current_step": "reference",
  "reference": {
    "audio_path": "SessionOutput/Audio_Reference.wav",
    "selected_range": {"start": 0, "end": 16},
    "profile_exists": true,
    "sampling_score": 88.2,
    "quality_label": "good"
  },
  "providers": [
    {"provider": "google", "enabled": true, "has_api_key": true},
    {"provider": "aliyun-cosyvoice", "enabled": true, "has_api_key": true}
  ],
  "recommended_voices": [],
  "cloned_voices": [],
  "preview_attempts": [],
  "saved_candidates": {}
}
```

UI 根据数据自动决定当前步骤：

| 数据状态 | 默认步骤 |
|---|---|
| 无参考音频 | 第 1 步 |
| 有参考音频，无 profile | 第 1 步 |
| 有 profile，无推荐/克隆 | 第 2 步 |
| 有推荐/克隆 | 第 3 步 |
| 有保存候选 | 第 3 步 |

## 10. 第一版范围

第一版页面只做：

1. 三步流程。
2. 参考音频选区和质量提示。
3. 推荐相似音色。
4. 阿里云 CosyVoice 克隆声音。
5. 试听。
6. 保存到 3 个候选槽位。
7. 保存为最终候选。
8. 高级详情折叠。

暂不做：

1. 多 provider 混合评分校准图表。
2. 批量生成几十个 preview。
3. 复杂音频分析图表。
4. 远端删除 provider clone。
5. 多人协作标注。

## 11. 易用性标准

实现时按以下标准验收：

1. 用户首次打开页面，能在 3 个主按钮内完成流程。
2. 第一屏不出现 `Resemblyzer`、`SpeechBrain`、`clone_id`、`target_model` 等术语。
3. 所有危险或计费动作都有明确按钮和状态提示。
4. 克隆声音必须有授权确认。
5. 试听成功后，保存到候选槽位的按钮必须紧邻播放器。
6. 最终保存后，页面明确显示输出路径。

## 12. UI 风险

| 风险 | 处理 |
|---|---|
| 用户不知道下一步做什么 | 使用三步步骤条和单一主按钮 |
| 用户混淆系统音色和克隆音色 | 文案分成“推荐相似音色”和“克隆这个声音” |
| 克隆音色跨模型使用失败 | 默认隐藏 model，并在后台锁定 target_model |
| 重复试听产生费用 | 显示 cached/new，重复请求走缓存 |
| 克隆音频无授权 | Clone 弹窗强制授权确认 |
| 页面信息过载 | 高级信息全部折叠 |
