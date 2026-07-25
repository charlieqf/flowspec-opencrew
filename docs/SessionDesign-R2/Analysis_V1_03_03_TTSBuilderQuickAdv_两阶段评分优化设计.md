# Analysis_V1 03_03 TTSBuilderQuickAdv 两阶段匹配与评价维度优化设计

日期：2026-06-15

范围：在现有 `03_03_TTSBuilderQuickAdv.py` 和 TTSBuilderQuickAdv 页面基础上，优化两个方向：

1. 把匹配流程改成真正两阶段：Resemblyzer 高召回粗筛，SpeechBrain 精排。
2. 增加更细的评价维度：让系统不仅给一个总分，还能解释“为什么这个音色更接近”。

本设计不替换 `03_02_TTSBuilderQuick.py`，也不改变已上线的快速匹配入口。所有改动限定在 `03_03` 高级匹配链路和对应页面。

## 1. 背景和目标

用户补充要求：

> 算法也可以把细节优化一下，特别是评价维度，尽量找到最接近的。

这句话的产品含义是：用户不只要求流程里出现 Resemblyzer / SpeechBrain，而是希望最终推荐结果真的更接近参考原声，并且能看到足够细的匹配依据。

当前实现已经具备：

1. 从参考声音截取片段。
2. 读取 Gemini / Qwen 音色 catalog。
3. 计算 Resemblyzer、可选 SpeechBrain 和基础声学特征。
4. 输出 ranking board 和最终 TTS 候选。
5. 页面支持高级匹配、试听、云端克隆。

当前不足：

1. `rank_voices()` 目前对全量候选直接计算综合分，再切出 `stage1` 和 `stage2`。这不是真正“首轮粗筛、次轮精排”。
2. SpeechBrain 可用时会对全量音色计算，`stage1_count` 没有控制 SpeechBrain 的计算范围。
3. UI 主要展示总匹配分，细分维度没有充分暴露，用户不知道为什么推荐。
4. 评价维度偏少，声音质感、发音清晰度、节奏、口音/风格等还没有形成稳定的数据结构。

目标：

1. 建立真实两阶段 ranking：
   - Stage 1：全量 catalog 使用 Resemblyzer + 基础声学特征粗筛，保留高召回候选池。
   - Stage 2：仅对 Stage 1 候选池使用 SpeechBrain 精排；SpeechBrain 不可用时，对同一候选池做增强声学重排。
2. 输出稳定的 0 到 100 分维度体系。
3. 页面能展示“匹配度”和“为什么推荐”，让非技术用户也能理解。
4. 保持降级路径可用，不因 SpeechBrain 不可用导致工具失败。
5. 为后续 Gemini / Qwen / ByteDance / 云端克隆统一音色库保留扩展点。

## 2. 不做范围

本阶段不实现以下内容：

1. 不接入 ByteDance / 火山 TTS 全音色。
2. 不强制新增重量级音素对齐器。
3. 不把 LLM prompt planner 纳入本次算法改造。
4. 不把 `03_02` 迁移到新评分体系。
5. 不把评分用于跨任务、跨部署的绝对比较。

这些内容可以作为后续阶段继续推进。

### 2.1 前置依赖

Qwen ranking 属于本设计的验收范围，但前提是 Qwen 音色 catalog 已经存在。也就是说，以下目录必须先由 catalog 生成脚本产出样本音频和索引：

```text
ToolLibrary/Analysis_V1/VoiceCatalog/qwen3-tts-flash/voice_catalog_index.json
ToolLibrary/Analysis_V1/VoiceCatalog/qwen3-tts-flash/*.wav
```

本设计不负责重新实现 Qwen catalog 生成脚本，只要求两阶段评分能读取并排序已生成的 Qwen catalog。没有该 catalog 时，Qwen ranking 验收应标记为 blocked，而不是要求 `03_03` 在运行时临时生成全量音色库。

## 3. 设计原则

### 3.1 高召回优先

第一阶段不能过早淘汰可能接近的声音。即使某个候选在 Resemblyzer 总分不是最高，只要它在音高、语速、性别、语言或 provider 标签上很接近，也应有机会进入 Stage 2。

因此 Stage 1 不是简单 `top stage1_count`，而是：

1. 主排序 Top N。
2. 加入若干 rescue lanes。
3. 去重后裁剪到 `stage1_count` 或 `stage1_pool_count`。

### 3.2 技术分和用户解释分分离

底层可以保留 `resemblyzer_cosine`、`speechbrain_cosine`、`spectral_centroid`、`rms` 等技术字段；页面默认展示用户能理解的维度：

1. 音色
2. 音高
3. 语速
4. 清晰度
5. 声音质感
6. 风格/口音

### 3.3 降级态分数必须有定义

SpeechBrain 不可用时仍输出 `stage2_score` 和 `match_score`，但必须标明：

```json
{
  "scoring_mode": "degraded_resemblyzer_acoustic",
  "scoring_mode_reason": "SpeechBrain disabled or unavailable",
  "speechbrain_cosine": null
}
```

### 3.4 保持输出兼容

既有页面和后续工具依赖：

```text
SessionOutput/tts/tts_builder_candidates.json
S5_03_03_TTSBuilderQuickAdv/Interactive/ranking_board.json
```

新字段只能增量添加，不删除旧字段。旧字段 `match_score`、`score_parts`、`scores`、`scoring_mode` 继续保留。

## 4. 目标流程

### 4.1 总体流程

```text
参考原声
  -> 16 秒采样和 reference_profile
  -> 提取参考声音特征
  -> 读取 provider catalog
  -> Stage 1: Resemblyzer + 基础声学粗筛
  -> Stage 1 rescue lanes 补充候选
  -> Stage 2: SpeechBrain 精排，或 degraded acoustic rerank
  -> 输出 ranking_board
  -> 生成 Top final_count 个 TTS 候选
  -> 页面试听和人工选择
```

### 4.2 Stage 1 粗筛

输入：

1. 全量 catalog 音色。
2. 参考音频 embedding 和声学特征。
3. `stage1_count`，默认 24，最小建议不低于 `final_count * 8`。

计算字段：

```text
resemblyzer_cosine
resemblyzer_score_normalized
pitch_score
pace_score
brightness_score
gender_score
catalog_quality_penalty
stage1_score
```

Stage 1 公式：

```text
stage1_base_score =
  0.65 * resemblyzer_score_normalized
+ 0.13 * pitch_score
+ 0.10 * pace_score
+ 0.07 * brightness_score
+ 0.05 * gender_score

stage1_score = clamp(stage1_base_score - catalog_quality_penalty)
```

权重说明：

1. Resemblyzer 仍是主维度，但从 0.70 降到 0.65，给音高、语速更多召回机会。
2. `catalog_quality_penalty` 防止破音、异常时长、过低音量的 catalog 样本进入前列。
3. `gender_score` 只做轻量惩罚，不在 Stage 1 硬排除，避免标签错误导致误删。
4. catalog 质量只作为惩罚项进入 Stage 1，不再同时作为正向分和负向惩罚，避免双算。

### 4.3 Stage 1 rescue lanes

粗筛候选池由以下 lanes 合并：

| Lane | 数量建议 | 目的 |
|---|---:|---|
| `stage1_score_top` | `stage1_count` 的 70% | 主排序召回 |
| `pitch_nearest_top` | 10% | 保留音高接近但 embedding 不突出的音色 |
| `pace_nearest_top` | 10% | 保留语速接近的音色 |
| `same_gender_top` | 10% | 保留性别/年龄标签接近的音色 |
| `provider_quota_top` | 每个 provider 至少 1 到 2 个 | 避免多 provider 混排时某 provider 被完全挤出 |

合并规则：

1. 使用 `(provider, model, voice)` 去重。
2. 如果合并后少于 `stage1_count`，按 `stage1_score` 补齐。
3. 如果合并后超过 `stage1_count`，按 `stage1_score` 裁剪，但保留每个 lane 的最高分候选。

输出文件：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage1_resemblyzer.json
```

关键字段：

```json
{
  "stage": "stage1",
  "scoring_mode": "stage1_resemblyzer_acoustic",
  "candidate_count_total": 96,
  "candidate_count_stage1": 24,
  "rescue_lanes": {
    "pitch_nearest_top": 3,
    "pace_nearest_top": 3,
    "provider_quota_top": 4
  },
  "ranked": []
}
```

### 4.4 Stage 2 精排

Stage 2 只处理 Stage 1 候选池。

Full SpeechBrain 模式触发条件：

1. UI 勾选高精度模式，或后端参数未禁用 SpeechBrain。
2. SpeechBrain 后端可加载。
3. 参考音频和候选音频都成功产出 SpeechBrain embedding。

Full SpeechBrain 公式：

```text
stage2_score =
  0.42 * speechbrain_score_normalized
+ 0.14 * timbre_rank_component
+ 0.10 * stage1_recall_prior
+ 0.08 * pitch_score
+ 0.07 * pace_score
+ 0.07 * texture_score
+ 0.05 * articulation_score
+ 0.03 * persona_score
+ 0.02 * style_score
+ 0.02 * provider_readiness_score
```

其中：

```text
timbre_rank_component = 0.75 * resemblyzer_score_normalized + 0.25 * spectral_shape_score
stage1_recall_prior = stage1_score
spectral_shape_score = weighted average of brightness_score and roughness_score
texture_score = weighted average of brightness_score, warmth_score, roughness_score, nasality_score
articulation_score = weighted average of clarity_score, consonant_proxy_score, sibilance_score
style_score = language/accent/style label match proxy
```

命名约束：

1. `timbre_rank_component` 是 ranking 内部组件，不含 SpeechBrain，只用于 Stage 2 公式里的平滑和召回延续。
2. `timbre_score` 是展示给 UI 的音色维度分，Full 模式下包含 SpeechBrain。
3. 实现中不得把 `timbre_rank_component` 写入 `dimension_scores.timbre_score`，也不得把 `timbre_score` 回灌到 Stage 2 公式。
4. `stage1_recall_prior` 只保留 0.10 权重，用于平滑 Stage 1 的排序证据；SpeechBrain 在 Full 模式中仍是最大单项权重。

Degraded 模式公式：

```text
stage2_score =
  0.24 * timbre_rank_component
+ 0.20 * stage1_recall_prior
+ 0.12 * pitch_score
+ 0.10 * pace_score
+ 0.10 * texture_score
+ 0.08 * articulation_score
+ 0.04 * energy_score
+ 0.04 * persona_score
+ 0.04 * style_score
+ 0.02 * stability_score
+ 0.02 * provider_readiness_score
```

Degraded 模式没有 SpeechBrain，因此 `timbre_rank_component` 和 `stage1_recall_prior` 会承担更多排序权重。这里的重复是有意的平滑，但权重被限制在 0.44，避免单一 Resemblyzer 证据完全主导最终分。

输出文件：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_rerank.json
S5_03_03_TTSBuilderQuickAdv/Interactive/ranking_board.json
```

兼容旧路径：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_speechbrain.json
```

旧路径可以继续写同一份 payload，避免已有读者失效。

### 4.5 最终候选

最终候选从 Stage 2 排名中取 `final_count`。

硬性规则：

1. `match_score = round(stage2_score)`。
2. `final_score` 可以保留，但 UI 默认展示 `match_score`。
3. 如果 Stage 2 可用候选少于 `final_count`，使用 Stage 1 备用候选补齐，并标记 `fallback_from_stage1=true`。
4. 生成 TTS 候选时只对最终候选调用 provider TTS，避免额外成本。

## 5. 评价维度设计

### 5.1 维度分层

新评分体系分为三层：

1. 原始特征：从音频或 catalog 标签直接得到的数值。
2. 标准化分数：0 到 100 的维度分。
3. 用户解释：页面展示的短文本和条形图。

### 5.2 维度清单

| 用户维度 | 字段 | 范围 | 来源 | 默认展示 |
|---|---|---:|---|---|
| 音色相似 | `timbre_score` | 0 到 100 | Resemblyzer / SpeechBrain / 频谱 | 是 |
| 音高接近 | `pitch_score` | 0 到 100 | F0 median / F0 range | 是 |
| 语速接近 | `pace_score` | 0 到 100 | CJK chars per second / duration | 是 |
| 清晰度接近 | `articulation_score` | 0 到 100 | zero crossing / high frequency / consonant proxy | 是 |
| 声音质感 | `texture_score` | 0 到 100 | brightness / warmth / roughness / nasality | 是 |
| 能量强弱 | `energy_score` | 0 到 100 | RMS / loudness proxy | 详情 |
| 稳定性 | `stability_score` | 0 到 100 | duration / clipping / silence / quality | 详情 |
| 性别/年龄感 | `persona_score` | 0 到 100 | catalog label + pitch fallback | 是 |
| 语言/口音/风格 | `style_score` | 0 到 100 | provider labels / language hints / transcript | 详情 |
| Provider 可用性 | `provider_readiness_score` | 0 到 100 | key / catalog / adapter status | 详情 |

### 5.3 新增细分特征

在现有 `audio_features()` 基础上，新增或补齐以下字段。第一版优先用轻量实现，不强依赖新模型。

| 特征 | 字段 | 计分语义 | 实现建议 | 说明 |
|---|---|---|---|---|
| F0 中位数 | `pitch_hz` | similarity-to-reference | 复用现有 pitch | 已有 |
| F0 范围 | `pitch_range_hz` | similarity-to-reference | percentile 90 - percentile 10 | 衡量音高起伏是否接近 |
| F0 稳定性 | `pitch_stability` | similarity-to-reference | voiced F0 std 的反向归一 | 判断抖动/稳定是否接近 |
| 明亮度 | `spectral_centroid` | similarity-to-reference | 复用现有频谱中心 | 已有 |
| 厚度/温暖感 | `warmth_ratio` | similarity-to-reference | low-mid energy / high energy | 不是越高越好，而是越接近参考越好 |
| 沙哑/粗糙感 | `roughness_proxy` | similarity-to-reference | spectral flatness + jitter proxy | 沙哑原声应匹配沙哑候选，干净原声应匹配干净候选 |
| 鼻音感 | `nasality_proxy` | similarity-to-reference | low formant / mid band ratio | MVP 只作为弱信号 |
| 齿音/擦音 | `sibilance_proxy` | similarity-to-reference | 4k-8k energy ratio | 匹配齿音强弱，不默认越低越好 |
| 辅音覆盖代理 | `consonant_proxy` | absolute-quality + weak similarity | high frequency onset density | 候选过低会扣分；参考可用时再比较接近度 |
| 停顿密度 | `pause_density` | similarity-to-reference | VAD/silence segmentation | 影响口播节奏是否接近 |
| 音量稳定性 | `energy_stability` | absolute-quality | frame RMS std 反向归一 | 候选和参考都希望稳定，主要用于质量 |
| 削波风险 | `clipping_risk` | absolute-quality penalty | near-max sample ratio | 越低越好，只进入质量惩罚 |
| Catalog 质量 | `catalog_quality_penalty` | absolute-quality penalty | clipping / silence / duration / low RMS | 只作惩罚，不参与相似度正向加分 |

### 5.4 维度标准化

基础函数：

```python
ratio_score(reference_value, candidate_value)
bounded_inverse_score(delta, tolerance, hard_limit)
normalize_cosine(value, low=0.20, high=0.85)
quality_penalty_score(...)
```

示例：

```text
pitch_score = ratio_score(reference.pitch_hz, candidate.pitch_hz)
pace_score = ratio_score(reference.speaking_rate_cps, candidate.speaking_rate_cps)
brightness_score = ratio_score(reference.spectral_centroid, candidate.spectral_centroid)
energy_score = ratio_score(reference.rms, candidate.rms)
warmth_score = ratio_score(reference.warmth_ratio, candidate.warmth_ratio)
roughness_score = ratio_score(reference.roughness_proxy, candidate.roughness_proxy)
nasality_score = ratio_score(reference.nasality_proxy, candidate.nasality_proxy)
sibilance_score = ratio_score(reference.sibilance_proxy, candidate.sibilance_proxy)
```

对于越小越好的距离类特征：

```text
pause_score = bounded_inverse_score(
  abs(reference.pause_density - candidate.pause_density),
  tolerance=0.08,
  hard_limit=0.35
)
```

质量类字段不能用 `ratio_score(reference, candidate)`：

```text
catalog_quality_penalty = quality_penalty_score(candidate.clipping_risk, candidate.silence_ratio, candidate.duration_error, candidate.rms)
stability_score = absolute_quality_score(candidate.energy_stability, candidate.duration_fit, candidate.clipping_risk)
provider_readiness_score = 100 if provider key/catalog/adapter are usable else 0
```

实现约束：

1. `warmth_score`、`roughness_score`、`nasality_score`、`sibilance_score` 都是与参考声音的相似度，不是绝对“越低越好”或“越高越好”。
2. `clipping_risk`、`catalog_quality_penalty`、`provider_readiness_score` 是绝对质量/可用性，不跟参考声音比较。
3. 如果某个 proxy 特征缺失，单项 score 取 50，并在 `score_warnings` 中记录，不得让候选评分失败。

### 5.5 维度聚合

聚合字段：

```text
timbre_score =
  0.50 * speechbrain_score_normalized
+ 0.30 * resemblyzer_score_normalized
+ 0.20 * texture_score
```

`timbre_score` 只用于 `dimension_scores.timbre_score` 和 UI 展示“音色”分。它和 Stage 2 公式里的 `timbre_rank_component` 不是同一个字段。

如果 SpeechBrain 不可用：

```text
timbre_score =
  0.68 * resemblyzer_score_normalized
+ 0.32 * texture_score
```

```text
texture_score =
  0.35 * brightness_score
+ 0.25 * warmth_score
+ 0.20 * roughness_score
+ 0.20 * nasality_score
```

```text
articulation_score =
  0.45 * clarity_score
+ 0.30 * consonant_proxy_score
+ 0.25 * sibilance_score
```

```text
persona_score =
  0.60 * gender_score
+ 0.25 * age_proxy_score
+ 0.15 * pitch_band_score
```

`age_proxy_score` 定义：

1. 如果 catalog 有年龄标签，先归一到 `child | young | adult | senior`，与参考 `scene_profile` 或参考声音推断年龄段匹配则 100，相邻年龄段 70，不匹配 30。
2. 如果 catalog 没有年龄标签，但 voice label 含 `童声`、`少年`、`少女`、`大叔`、`老者` 等词，使用标签推断。
3. 如果参考或候选年龄都无法推断，取 50，不作为强惩罚。

`pitch_band_score` 定义：

1. 根据 `pitch_hz` 归一到 `low | mid | high | child_like`，不同性别阈值可以不同。
2. 同 band 为 100，相邻 band 为 70，跨两档为 40。
3. 缺少 `pitch_hz` 时取 50。

`persona_score` 是弱解释维度，不得单独硬排除候选。性别明显不匹配时通过 `gender_mismatch_penalty` 轻惩罚。

### 5.6 惩罚项

惩罚项不直接作为维度展示，但会进入 `penalties`，便于审计。

| 惩罚 | 字段 | 建议 |
|---|---|---|
| 性别明显不匹配 | `gender_mismatch_penalty` | 最多扣 12 分，不硬剔除 |
| 音频质量差 | `catalog_quality_penalty` | 最多扣 8 分 |
| 语速差异极大 | `pace_outlier_penalty` | 最多扣 8 分 |
| 音高差异极大 | `pitch_outlier_penalty` | 最多扣 8 分 |
| provider 不可用 | `provider_unavailable_penalty` | 不进入可生成候选 |

Stage 2 最终分：

```text
stage2_score = clamp(weighted_score - total_penalty)
match_score = round(stage2_score)
```

## 6. 数据契约

### 6.1 `reference_profile`

新增字段：

```json
{
  "score_schema_version": "quick_adv_score_v2",
  "features": {},
  "dimension_profile": {
    "pitch": {
      "pitch_hz": 182.4,
      "pitch_range_hz": 96.2,
      "pitch_stability": 78.5
    },
    "pace": {
      "speaking_rate_cps": 4.8,
      "pause_density": 0.12
    },
    "texture": {
      "brightness": 62.0,
      "warmth": 55.0,
      "roughness": 28.0,
      "nasality": 35.0
    },
    "quality": {
      "voice_activity_score": 91.0,
      "clipping_risk": 0.0,
      "sampling_score": 86.0
    }
  }
}
```

### 6.2 candidate row

新增字段：

```json
{
  "score_schema_version": "quick_adv_score_v2",
  "stage1_rank": 5,
  "stage1_score": 88.125,
  "stage2_rank": 2,
  "stage2_score": 91.442,
  "match_score": 91,
  "dimension_scores": {
    "timbre_score": 92.1,
    "pitch_score": 86.4,
    "pace_score": 94.0,
    "articulation_score": 79.5,
    "texture_score": 88.2,
    "persona_score": 100.0,
    "style_score": 72.0
  },
  "raw_scores": {
    "resemblyzer_cosine": 0.782,
    "speechbrain_cosine": 0.811
  },
  "penalties": {
    "gender_mismatch_penalty": 0.0,
    "pace_outlier_penalty": 0.0,
    "catalog_quality_penalty": 1.5
  },
  "explanation": {
    "summary": "音色和语速最接近，音高略低，清晰度比原声更高。",
    "best_dimensions": ["音色", "语速"],
    "watch_dimensions": ["音高", "清晰度"]
  }
}
```

### 6.3 ranking board

新增字段：

```json
{
  "score_schema_version": "quick_adv_score_v2",
  "ranking_strategy": "two_stage_high_recall",
  "stage1": [],
  "stage2": [],
  "recommended": [],
  "stage_counts": {
    "catalog_total": 96,
    "stage1_pool": 24,
    "stage2_ranked": 24,
    "recommended": 3
  },
  "available_backends": {
    "resemblyzer": true,
    "speechbrain": true,
    "acoustic": true
  }
}
```

## 7. UI 设计

### 7.1 默认列表

高级匹配排行列表默认展示：

| 字段 | 展示示例 |
|---|---|
| 排名 | `#1` |
| 音色 | `Cherry - 芊悦：阳光积极女声` |
| 来源 | `Qwen / qwen3-tts-flash` |
| 匹配 | `91` |
| 精度 | `高精度匹配` 或 `基础匹配` |
| 最接近 | `音色、语速` |
| 操作 | 播放、测试、复制 Voice ID |

### 7.2 详情折叠

点击候选详情后显示：

```text
为什么推荐
音色       92
音高       86
语速       94
清晰度     80
声音质感   88
性别/年龄  100

说明：音色和语速最接近，音高略低，清晰度比原声更高。

技术详情
Resemblyzer cosine 0.782
SpeechBrain cosine 0.811
评分模式 高精度匹配
```

默认不把 `resemblyzer_cosine`、`speechbrain_cosine` 放在第一屏。

### 7.3 采样质量提示

采样区显示：

```text
采样质量：好 86
已选范围：12.30s - 28.30s
提示：语音连续，语速稳定；辅音覆盖为代理估计，建议试听确认。
```

如果质量较低：

```text
这段参考声音可能不够理想：静音偏多或声音特征覆盖不足。建议拖动波形换一段 16 秒原声。
```

### 7.4 模式文案

把技术开关转成用户语言：

| 当前文案 | 建议文案 |
|---|---|
| `SpeechBrain` | `高精度匹配` |
| `基础模式` | `基础匹配` |
| `Resemblyzer + 声学特征` | `快速匹配评分` |

高级 tooltip 里再解释：

```text
高精度匹配会在粗筛后使用 SpeechBrain 对候选重新排序，速度更慢，但通常更适合找最接近原声的音色。
```

## 8. 实施计划

### 8.1 Phase A：评分结构重构

目标：先不改变 UI，只让后端输出 v2 分数结构。

改动文件：

```text
ToolLibrary/Analysis_V1/tts_quick_adv/scoring.py
ToolLibrary/Analysis_V1/tts_quick_adv/core.py
backend/tests/contracts/test_analysis_v1_tts_quick_adv_contract.py
```

任务：

1. 在 `scoring.py` 新增 `SCORE_SCHEMA_VERSION = "quick_adv_score_v2"`。
2. 新增维度聚合函数：
   - `build_texture_score()`
   - `build_articulation_score()`
   - `build_timbre_rank_component()`
   - `build_timbre_score()`
   - `build_persona_score()`
   - `build_age_proxy_score()`
   - `build_pitch_band_score()`
   - `build_penalties()`
   - `build_candidate_explanation()`
3. 保留旧函数 `build_stage1_score()` / `build_stage2_score()`，但内部改为调用 v2 结构。
4. 所有分数统一 clamp 到 0 到 100。
5. 合同测试覆盖：
   - 权重合计为 1.0。
   - SpeechBrain 缺失时无 `None` 算术。
   - `match_score` 在 0 到 100。
   - `timbre_rank_component` 不进入 UI 展示分。
   - catalog 质量只进入 penalty，不作为正向维度重复计分。

验收：

1. 旧字段仍存在。
2. 新字段 `dimension_scores`、`penalties`、`explanation` 存在。
3. 现有 UI 不崩。

### 8.2 Phase B：真实 Stage 1 粗筛

目标：把全量候选先按 Resemblyzer + 声学特征粗筛。

改动文件：

```text
ToolLibrary/Analysis_V1/tts_quick_adv/core.py
ToolLibrary/Analysis_V1/tts_quick_adv/paths.py
```

任务：

1. 拆分 `rank_voices()`：
   - `build_reference_match_profile()`
   - `score_stage1_candidate()`
   - `select_stage1_pool()`
   - `score_stage2_candidate()`
2. Stage 1 对全量 catalog 跑 Resemblyzer 和基础声学评分。
3. `select_stage1_pool()` 实现 rescue lanes。
4. `OUTPUT_STAGE1_REL` 写入真实 Stage 1 pool。
5. Stage 1 输出每个候选的 `stage1_rank`、`stage1_score`、`stage1_lane_sources`。

验收：

1. `stage1` 数量不超过 `stage1_count`，除非为了 provider quota 明确标记。
2. Stage 1 中所有候选都有 `resemblyzer_cosine` 和 `stage1_score`。
3. 没有 SpeechBrain 环境也能跑通 Stage 1。

### 8.3 Phase C：真实 Stage 2 精排

目标：SpeechBrain 只对 Stage 1 pool 运行。

改动文件：

```text
ToolLibrary/Analysis_V1/tts_quick_adv/core.py
ToolLibrary/Analysis_V1/tts_quick_adv/scoring.py
```

任务：

1. `speechbrain_embedding()` 只对 `stage1_pool` 内候选调用。
2. Full 模式下写：
   - `scoring_mode = "full_speechbrain"`
   - `speechbrain_cosine`
   - `speechbrain_score_normalized`
3. Degraded 模式下写：
   - `scoring_mode = "degraded_resemblyzer_acoustic"`
   - `speechbrain_cosine = null`
   - `scoring_mode_reason`
4. `stage2` 排序基于 `stage2_score`。
5. `recommended` 来自 `stage2[:final_count]`。

验收：

1. Full 模式下，SpeechBrain 调用数量小于或等于 Stage 1 pool 数量。
2. Full 模式下，Stage 2 候选都有 SpeechBrain 分。
3. Degraded 模式下，Stage 2 候选无 SpeechBrain 分但有明确公式和说明。
4. `stage1_count` 真的影响 Stage 2 输入规模。

### 8.4 Phase D：细粒度特征

目标：补齐声音质感、清晰度、节奏等维度的轻量代理特征。

改动文件：

```text
ToolLibrary/Analysis_V1/03_02_TTSBuilderQuick.py
ToolLibrary/Analysis_V1/tts_quick_adv/core.py
```

说明：当前 03_03 复用 03_02 的 `audio_features()`。第一版可以继续复用并扩展该函数；如果担心影响 03_02，则在 03_03 内新增包装函数 `advanced_audio_features()`，只在 03_03 使用。

任务：

1. 增加 `pitch_range_hz`、`pitch_stability`。
2. 增加 `warmth_ratio`、`roughness_proxy`、`nasality_proxy`、`sibilance_proxy`。
3. 增加 `pause_density`、`energy_stability`、`clipping_risk`。
4. 新增 `catalog_quality_penalty`。
5. 给每个新特征提供缺失 fallback，避免老音频或异常音频导致失败。

验收：

1. 没有 librosa 高级特征时不失败。
2. 异常音频给低质量分，不让工具崩溃。
3. 输出 audit 中能看到参考声音和候选声音的关键特征。

### 8.5 Phase E：UI 展示优化

目标：让用户理解结果，而不是只看一个分。

改动文件：

```text
OpenClip/frontend/src/AnalysisV1/components/AnalysisV1TTSBuilder.jsx
OpenClip/frontend/src/styles/analysis-v1-tts.css
frontend/e2e/analysis-v1-tts-quick-adv.mjs
```

任务：

1. 排行列表增加“最接近”摘要。
2. 详情区域展示维度条：
   - 音色
   - 音高
   - 语速
   - 清晰度
   - 声音质感
   - 性别/年龄感
3. 技术分折叠显示。
4. 把 `SpeechBrain` checkbox 改成 `高精度匹配`。
5. 采样区增加质量提示和换段建议。

验收：

1. 非技术用户能看懂 Top 候选为什么推荐。
2. UI 自动化测试覆盖 Full 和 Degraded 两种 `scoring_mode` 展示。
3. 移动端不溢出。

### 8.6 Phase F：测试和回归

后端测试：

1. `test_stage1_pool_limits_speechbrain_scope`
2. `test_degraded_stage2_formula_has_no_speechbrain_weight`
3. `test_score_schema_v2_fields_are_present`
4. `test_rescue_lanes_keep_pitch_nearest_candidate`
5. `test_match_score_range_and_ordering`
6. `test_qwen_catalog_ranking_uses_provider_model_fields`
7. `test_timbre_rank_component_is_not_dimension_timbre_score`
8. `test_proxy_scores_distinguish_similarity_and_absolute_quality`
9. `test_persona_score_fallbacks_to_neutral_when_age_missing`
10. `test_catalog_quality_is_penalty_only`

前端测试：

1. 页面显示五步流程。
2. 高精度模式开关 payload 正确。
3. 排行详情显示维度条和解释。
4. Degraded 模式显示“基础匹配”。
5. Full 模式显示“高精度匹配”。
6. 候选试听仍可打开。

手工验证：

1. Gemini catalog ranking。
2. Qwen `qwen3-tts-flash` 48 音色 ranking。前置条件：`ToolLibrary/Analysis_V1/VoiceCatalog/qwen3-tts-flash/voice_catalog_index.json` 和对应 wav 样本已存在。
3. SpeechBrain unavailable 降级。
4. SpeechBrain available 高精度。
5. 云端克隆入口不受影响。

## 9. 兼容性和迁移

### 9.1 文件兼容

继续写：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage1_resemblyzer.json
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_speechbrain.json
S5_03_03_TTSBuilderQuickAdv/Interactive/ranking_board.json
SessionOutput/tts/tts_builder_candidates.json
```

可以新增：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_rerank.json
```

### 9.2 字段兼容

保留旧字段：

```text
score
match_score
scores
score_parts
scoring_mode
recommended
stage1
stage2
```

新增字段：

```text
score_schema_version
ranking_strategy
dimension_scores
raw_scores
penalties
explanation
stage1_rank
stage2_rank
stage1_lane_sources
```

### 9.3 参数兼容

保留：

```text
--stage1-count
--stage2-count
--final-count
--disable-speechbrain
```

建议 UI 默认值：

```text
stage1_count = 24
stage2_count = 6
final_count = 3
enable_speechbrain = false for public stability, true for controlled test
```

如果后续 SpeechBrain 缓存和启动稳定，可把页面默认改成高精度模式。

## 10. 风险和对策

| 风险 | 影响 | 对策 |
|---|---|---|
| Stage 1 误删最佳候选 | 推荐质量下降 | 高召回 pool + rescue lanes + 默认 stage1_count 不低于 24 |
| SpeechBrain 慢或不可用 | 页面等待时间长 | 默认可关闭；只对 Stage 1 pool 跑；失败降级 |
| 新维度代理不准确 | 评分误导 | 页面标注“代理估计”；权重保守；保留试听复核 |
| 相似度 proxy 和质量 proxy 混用 | 排名偏离参考声音 | 每个 proxy 标注 scoring semantics；合同测试覆盖 |
| `timbre_rank_component` 和 `timbre_score` 混用 | UI 分数或 ranking 公式失真 | 字段命名隔离；实现中分别进入 `score_parts` 和 `dimension_scores` |
| 多 provider 音色标签不一致 | 性别/风格分不准 | 标签只做轻惩罚，不硬排除 |
| 分数变化影响用户预期 | 旧任务和新任务分数不可比 | 输出 `score_schema_version`，UI 不做跨任务比较 |
| 特征计算增加耗时 | 公网体验变差 | catalog 特征缓存；只对缺失音频计算 |

## 11. 验收标准

### 11.1 算法验收

1. `rank` 输出 `ranking_strategy = "two_stage_high_recall"`。
2. `stage1` 由全量 catalog 粗筛得出。
3. `stage2` 的输入只来自 `stage1`。
4. SpeechBrain 可用时只对 Stage 1 pool 运行。
5. SpeechBrain 不可用时，降级公式权重合计 1.0，且输出 `degraded_resemblyzer_acoustic`。
6. 所有候选都有 `dimension_scores`。
7. `match_score` 仍为 0 到 100。

### 11.2 产品验收

1. 用户能看到“高精度匹配/基础匹配”。
2. 用户能看到每个候选的主要匹配维度。
3. 用户能看到推荐解释。
4. 用户能按页面步骤完成：选原声、采样、排行、生成候选、试听选用。
5. Qwen catalog 和 Gemini catalog 均可用。
   - Qwen 验收依赖预生成 catalog；缺失时验收结论为 blocked，不视为两阶段评分失败。

### 11.3 回归验收

1. `03_02_TTSBuilderQuick.py` 行为不变。
2. `03_03 run` 仍写出 `SessionOutput/tts/tts_builder_candidates.json`。
3. `--resume` 和 `--force` 行为不退化。
4. 云端克隆 consent 和去重逻辑不退化。
5. 前端 build、后端 contract tests、UI e2e 全部通过。

## 12. 建议实施顺序

优先级：

1. Phase A：评分结构重构。
2. Phase B：真实 Stage 1 粗筛。
3. Phase C：真实 Stage 2 精排。
4. Phase E：UI 展示优化。
5. Phase D：细粒度特征扩展。
6. Phase F：补齐测试和回归。

原因：

1. 先把数据结构稳定下来，UI 和测试才有明确契约。
2. 真实两阶段是最大结构性改进，应先做。
3. 细粒度特征可以分批增加，避免一次引入太多不稳定因素。
