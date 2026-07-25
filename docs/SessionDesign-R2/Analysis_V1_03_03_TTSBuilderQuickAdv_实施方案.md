# Analysis_V1 03_03_TTSBuilderQuickAdv 实施方案

日期：2026-06-15
状态：实施方案草案
范围：新增 Analysis_V1 工具 `03_03_TTSBuilderQuickAdv.py`，作为 `03_02_TTSBuilderQuick.py` 的高级版，不替换现有快速匹配工具。

## 1. 结论

可以实现 `03_03_TTSBuilderQuickAdv.py`。

它应定位为“高级声音匹配工具”：先从原声中自动挑选高质量 16 秒参考片段，再用 Resemblyzer 做首轮全量音色粗筛，用 SpeechBrain 做次轮重排；同时支持云端声音克隆生成自定义 `voice_id`；最后让大模型生成 provider-specific 声音提示词，并调用 Gemini / 千问 / 抖音 / CosyVoice 等 TTS provider 生成候选试听样本。

第一版不建议一次性追求“所有 provider 的所有音色都完整可用”。合理落地方式是：

1. 新增 `03_03_TTSBuilderQuickAdv.py` 和 `S5_03_03_TTSBuilderQuickAdv/`。
2. 先复用当前 Gemini voice catalog 跑通端到端高级匹配。
3. 抽象 provider adapter 和 voice catalog schema。
4. 再逐步接入阿里云 CosyVoice voice clone、Qwen / ByteDance voice catalog。

## 2. 工具定位

### 2.1 与现有工具关系

当前已有：

```text
03_01_TTSBuilderG.py
S5_03_01_TTSBuilderG/

03_02_TTSBuilderQuick.py
S5_03_02_TTSBuilderQuick/
```

新增：

```text
03_03_TTSBuilderQuickAdv.py
S5_03_03_TTSBuilderQuickAdv/
```

三者关系：

| 工具 | 定位 | 成本 | 支持 provider | 匹配方式 |
|---|---|---:|---|---|
| `03_01_TTSBuilderG` | 全量 Builder-G 候选生成 | 高 | Gemini | 偏模型生成和场景理解 |
| `03_02_TTSBuilderQuick` | 快速声音匹配 | 中 | Gemini | 本地 catalog 综合打分后生成 Top 3 |
| `03_03_TTSBuilderQuickAdv` | 高级声音匹配 | 中高 | Gemini / CosyVoice clone / Qwen / ByteDance 分阶段支持 | 智能采样 + Resemblyzer 首筛 + SpeechBrain 复筛 + 云端克隆 + LLM prompt |

`03_03` 不修改 `03_02` 的行为。UI 和运行链路可以让用户在 `skip / 03_01 / 03_02 / 03_03` 之间选择。

### 2.2 目标

`03_03_TTSBuilderQuickAdv` 的目标是为后续视频执行工具生成 3 个可试听、可解释、可复跑的 TTS 候选声音方案。

每个候选必须包含：

1. provider / model / voice / voice_source。
2. 声音提示词和 provider-specific generation payload 摘要。
3. 试听音频。
4. 音色、语速、音调、能量、清晰度等维度评分。
5. Resemblyzer 和 SpeechBrain 分数。
6. 推荐理由和排除理由。
7. tempo / duration fit 信息。

### 2.3 非目标

第一版不做以下事情：

1. 不训练本地自定义模型。
2. 不做未授权声音克隆；云端声音复刻必须要求用户确认参考音频有使用授权。
3. 不保证跨 provider 的分数绝对可比，只保证同一次运行内可解释排序。
4. 不在没有 catalog 样本或 clone voice_id 的情况下盲选 provider 音色。
5. 不把 API key 或 provider secret 写进输出 JSON。

## 3. 需求拆解

用户需求：

```text
声音匹配工具：Resemblyzer / Speechbrain
1. 原声采样，从原有声音截取16秒，需要原音，辅音完整，声音特色覆盖全面
2. 确定声音比对维度并进行分析，确认打分范围，音色，语速，音调等
3. 首轮比对，Resemblyzer进行比对评分
4. 次轮比对，SpeechBrain进行比对评分
5. 生成样本，用大模型根据声音生成提示词，并结合提示词生成样本
6. 希望兼容 Gemini / 千问 / 抖音的所有音色
7. 需要云端声音克隆功能，例如阿里云 CosyVoice 声音复刻生成 voice_id
```

落地映射：

| 需求 | 实现模块 | 第一版策略 |
|---|---|---|
| 原声采样 16 秒 | `ReferenceSamplerAdv` | VAD + 字幕边界 + 能量稳定性 + 文本覆盖度评分 |
| 比对维度和范围 | `VoiceScoreNormalizer` | 输出 0 到 100 维度分和综合分 |
| Resemblyzer 首轮 | `ResemblyzerStageRanker` | 从全量 catalog 取 Top N，默认 16 |
| SpeechBrain 次轮 | `SpeechBrainStageReranker` | 对 Top N 重排取 Top M，默认 6 |
| 大模型提示词 | `VoicePromptPlanner` | 根据参考 profile、候选 profile、场景生成 prompt |
| 多 provider | `TTSProviderAdapter` + `VoiceCatalog` | Gemini 先落地，Qwen / ByteDance 分阶段接入 |
| 云端声音克隆 | `VoiceCloneProviderAdapter` + `ClonedVoiceStore` | 先接入阿里云 CosyVoice，创建 voice_id 后纳入可试听音色 |

## 4. 主流程设计

### 4.1 执行流程

```text
输入校验
  -> 参考音频智能采样
  -> 参考音频特征提取
  -> 可选：云端声音克隆创建 voice_id
  -> 加载多 provider voice catalog
  -> catalog 样本特征提取或读取缓存
  -> Resemblyzer 首轮排名
  -> SpeechBrain 次轮排名
  -> 维度评分归一化与解释
  -> LLM 生成候选声音提示词
  -> TTS provider 生成 Top 3 样本
  -> 音频 duration fit
  -> 输出 candidates / audit / report
```

### 4.2 参考音频智能采样

输入：

```text
SessionOutput/Audio_Reference.wav
SessionOutput/subtitle/final_srt_frame_items.json
```

输出：

```text
S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav
S5_03_03_TTSBuilderQuickAdv/Output/reference_sampling_audit.json
```

采样规则：

1. 默认目标长度 16 秒。
2. 优先从字幕 item 边界开始和结束，避免切断一句话。
3. 过滤明显静音、噪声、音乐占比过高的窗口。
4. 优先选择信息量较高的对白窗口，避免只含语气词或短句。
5. 能量和 pitch 变化要覆盖主要声音特征，但不能过度包含情绪极端片段。
6. 如果用户指定 `--reference-start` 和 `--reference-duration`，尊重用户选择，但仍输出质量报告。

第一版可用的评分维度：

| 维度 | 范围 | 说明 |
|---|---:|---|
| `voice_activity_score` | 0 到 100 | 有效人声占比 |
| `boundary_score` | 0 到 100 | 是否贴合字幕/句子边界 |
| `text_coverage_score` | 0 到 100 | 中文字符数量和语义覆盖 |
| `energy_stability_score` | 0 到 100 | 音量是否稳定 |
| `feature_diversity_score` | 0 到 100 | pitch / energy / speaking rate 是否足够覆盖 |
| `noise_risk_score` | 0 到 100 | 噪声、音乐、重叠声风险，分数越高风险越高 |

综合采样分：

```text
sampling_score =
  0.25 * voice_activity_score
+ 0.20 * boundary_score
+ 0.20 * text_coverage_score
+ 0.15 * energy_stability_score
+ 0.10 * feature_diversity_score
+ 0.10 * (100 - noise_risk_score)
```

“辅音完整”第一版不做音素级强保证。可通过“不截断字幕句子边界”和“起止处保留短 padding”降低切断辅音风险。后续如果接入强制对齐器，再升级到音素级边界。

## 5. 声音比对维度

`03_03` 的评分输出统一使用 0 到 100 分，便于 UI 展示和审计。底层模型分数保留原始值。

### 5.1 维度定义

| 维度 | 字段 | 范围 | 来源 |
|---|---|---:|---|
| 音色相似度 | `timbre_score` | 0 到 100 | Resemblyzer / SpeechBrain embedding |
| 音调匹配 | `pitch_score` | 0 到 100 | median F0 / pitch range |
| 语速匹配 | `pace_score` | 0 到 100 | CJK chars per second / syllable proxy |
| 明亮度匹配 | `brightness_score` | 0 到 100 | spectral centroid |
| 能量匹配 | `energy_score` | 0 到 100 | RMS / loudness proxy |
| 清晰度 | `clarity_score` | 0 到 100 | zero crossing / high frequency / signal quality |
| 稳定性 | `stability_score` | 0 到 100 | catalog sample quality and duration stability |
| 性别匹配 | `gender_score` | 0 或 100 | catalog hint + pitch fallback |
| provider 可用性 | `provider_score` | 0 到 100 | provider enabled / key / adapter status |

### 5.2 评分模式

`03_03` 必须显式输出评分模式，避免默认部署缺少 SpeechBrain 时分数无定义。

```text
scoring_mode = full_speechbrain | degraded_resemblyzer_acoustic
```

| 模式 | 触发条件 | 含义 |
|---|---|---|
| `full_speechbrain` | SpeechBrain 后端可用，并成功产出 reference/candidate embedding | 使用 Resemblyzer 首筛 + SpeechBrain 复筛 |
| `degraded_resemblyzer_acoustic` | SpeechBrain 被禁用、模型缓存缺失、加载失败或 embedding 失败 | 使用 Resemblyzer + 声学特征重排，不使用 SpeechBrain 权重 |

无论哪种模式，所有 UI 对外展示的 `match_score` 都是 0 到 100，含义为“本次可用后端下的综合匹配度”。UI 必须同时展示一个短标签：

```text
高精度匹配       -> full_speechbrain
基础匹配         -> degraded_resemblyzer_acoustic
```

高级详情里保留 `scoring_mode`、`scoring_mode_reason`、`available_backends` 和各原始分数。不同模式的 `match_score` 不用于跨任务、跨部署做绝对比较；只用于当前 ranking board 内排序和用户选择。

### 5.3 综合分

首轮 Resemblyzer 排名使用：

```text
stage1_score =
  0.70 * resemblyzer_score_normalized
+ 0.10 * pitch_score
+ 0.08 * pace_score
+ 0.07 * brightness_score
+ 0.05 * gender_score
```

次轮评分根据 `scoring_mode` 选择公式。

#### 5.3.1 Full SpeechBrain 模式

```text
stage2_score =
  0.45 * speechbrain_score_normalized
+ 0.25 * stage1_score
+ 0.10 * pitch_score
+ 0.08 * pace_score
+ 0.05 * energy_score
+ 0.04 * clarity_score
+ 0.03 * stability_score
```

#### 5.3.2 Degraded 模式

当 SpeechBrain 不可用时，`stage2_score` 仍然存在，但语义变为“Resemblyzer + acoustic rerank”。公式为：

```text
stage2_score =
  0.40 * stage1_score
+ 0.20 * resemblyzer_score_normalized
+ 0.12 * pitch_score
+ 0.10 * pace_score
+ 0.08 * brightness_score
+ 0.05 * energy_score
+ 0.03 * clarity_score
+ 0.02 * stability_score
```

权重合计仍为 1.0。这里保留 `stage2_score` 字段，是为了让 UI 和后续工具不用区分字段名；但必须同时写：

```json
{
  "scoring_mode": "degraded_resemblyzer_acoustic",
  "scoring_mode_reason": "SpeechBrain disabled or unavailable",
  "speechbrain_cosine": null
}
```

最终候选分：

```text
final_score =
  0.55 * stage2_score
+ 0.15 * prompt_fit_score
+ 0.10 * provider_score
+ 0.10 * sample_duration_fit_score
+ 0.10 * human_review_prior
```

第一版 `human_review_prior` 默认为 100。后续 UI 可在用户选择/拒绝候选后更新偏好。

对外展示的 `match_score` 定义为：

```text
match_score = round(final_score)
```

推荐列表显示 `match_score`，详情中显示 `final_score`、`stage2_score` 和 `scoring_mode`。克隆音色不显示匹配分，显示“自定义”。

## 6. Voice Catalog 设计

### 6.1 目录结构

新增统一 catalog 根目录：

```text
ToolLibrary/Analysis_V1/VoiceCatalogAdv/
  google/gemini-3.1-flash-tts-preview/
    voice_catalog_index.json
    *.wav
  qwen/qwen3-tts-flash/
    voice_catalog_index.json
    *.wav
  bytedance/seed-tts-2.0/
    voice_catalog_index.json
    *.wav
```

第一版也可以先兼容现有目录：

```text
ToolLibrary/Analysis_V1/VoiceCatalog/gemini-3.1-flash-tts-preview/
```

### 6.2 Catalog index schema

```json
{
  "schema_version": "analysis_v1_voice_catalog_adv_0.1",
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "sample_text_id": "fixed_cn_v1",
  "sample_text": "你好，我现在用一种自然、清楚的普通话，说一段用于声音测试的中文。",
  "sample_policy": {
    "duration": 16.0,
    "language": "zh-CN",
    "normalization": "wav_48k_stereo",
    "clip_policy": "normal_speed_truncate_first_16s"
  },
  "voices": [
    {
      "provider": "google",
      "model": "gemini-3.1-flash-tts-preview",
      "voice": "Kore",
      "voice_label": "Kore",
      "voice_mode": "preset",
      "language": "zh-CN",
      "gender": "female",
      "style_tags": ["clear", "warm"],
      "sample_audio_path": "Kore_fixed_cn_v1_16s.wav",
      "audio": {
        "path": "Kore_fixed_cn_v1_16s.wav",
        "duration": 16.0,
        "sample_rate": 48000,
        "channels": 2,
        "sha256": ""
      },
      "features": {
        "profile_path": "profiles/Kore_profile.json",
        "resemblyzer_embedding_path": "embeddings/Kore_resemblyzer.npy",
        "speechbrain_embedding_path": "embeddings/Kore_speechbrain.pt"
      }
    }
  ]
}
```

### 6.3 Catalog 生成策略

新增脚本：

```text
scripts/generate_analysis_v1_voice_catalog_adv.py
```

职责：

1. 读取 provider voice list。
2. 生成固定中文样本文本音频。
3. 统一转码为 wav。
4. 写入音频元数据和 sha256。
5. 可选预计算 Resemblyzer / SpeechBrain embedding。
6. 支持 `--check-only` 校验 catalog 完整性。

## 7. Provider Adapter 设计

### 7.1 统一接口

```python
class TTSProviderAdapter:
    provider: str

    def list_voices(self, config: ProviderConfig) -> list[VoiceInfo]:
        ...

    def synthesize(
        self,
        *,
        config: ProviderConfig,
        model: str,
        voice: str,
        text: str,
        prompt: str,
        output_path: Path,
        extra: dict[str, Any],
    ) -> TTSResult:
        ...
```

`03_03` 不直接写 provider HTTP 细节。脚本只调用 adapter。

### 7.2 Google / Gemini

第一版直接复用 `03_02_TTSBuilderQuick.py` 里的 Gemini TTS 逻辑，再逐步抽出 adapter。

特点：

1. `model` 示例：`gemini-3.1-flash-tts-preview`。
2. voice 使用 `prebuiltVoiceConfig.voiceName`。
3. prompt 可控制 style、pace、tone。
4. 返回 inline audio。

### 7.3 Qwen / DashScope

目标 provider id：

```text
provider=qwen
kind=tts
```

特点：

1. 支持 `voice` 参数。
2. 部分模型支持 `instructions` 描述声音风格。
3. 输出可能是 URL 或二进制/任务结果，adapter 负责下载和转码。
4. 不同地域和模型的音色列表不同，catalog 必须记录地域和模型。

### 7.4 ByteDance / Volcengine / Doubao

目标 provider id：

```text
provider=bytedance
kind=tts
```

特点：

1. 鉴权不只是单一 API key，通常需要 app_id / access_key / resource_id / cluster。
2. `access_key` 走 secret store。
3. app_id、resource_id、base_url、audio_format、sample_rate 放入 `extra_json`。
4. 结果可能是 base64 音频或临时 URL，adapter 负责落盘。

### 7.5 Aliyun CosyVoice Voice Clone

新增云端声音克隆能力，用于根据参考音频创建 provider 侧自定义 `voice_id`。

阿里云 CosyVoice 声音复刻流程：

1. 准备 10 到 20 秒清晰参考音频。
2. 将本地参考音频发布成 provider 可访问 URL。
3. 调用声音复刻接口创建音色，指定 `target_model` 和 `prefix`。
4. 保存接口返回的 `voice_id`。
5. 后续合成时使用相同 `target_model` 和返回的 `voice_id`。

脚本模块：

```text
tts_quick_adv/audio_publish.py
tts_quick_adv/voice_cloning.py
tts_quick_adv/providers/aliyun_voice_clone.py
tts_quick_adv/providers/aliyun_cosyvoice.py
```

工作区输出：

```text
S5_03_03_TTSBuilderQuickAdv/Interactive/cloned_voices.json
S5_03_03_TTSBuilderQuickAdv/Output/voice_clone_audit.json
```

`ClonedVoice` 记录：

```json
{
  "clone_id": "clone_20260615_abcdef12",
  "provider": "aliyun-cosyvoice",
  "target_model": "cosyvoice-v3.5-plus",
  "voice_id": "opencrew_task_123_xxx",
  "voice_source": "cloud_voice_clone",
  "reference_audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav",
  "reference_audio_sha256": "",
  "consent": {
    "status": "confirmed",
    "purpose": "analysis_v1_tts_quick_adv"
  },
  "status": "ready"
}
```

页面上，克隆 voice 与系统 voice 使用统一 preview 流程。额外约束是：克隆 voice 必须用创建时绑定的 `target_model`，不能跨模型使用。

## 8. LLM 声音提示词生成

### 8.1 输入

`VoicePromptPlanner` 输入：

```json
{
  "reference_profile": {
    "gender_gate": {},
    "features": {},
    "sampling_window": {},
    "dialogue": ""
  },
  "scene_profile": {},
  "candidate_voice": {
    "provider": "google",
    "model": "gemini-3.1-flash-tts-preview",
    "voice": "Kore",
    "style_tags": []
  },
  "score_parts": {}
}
```

### 8.2 输出

```json
{
  "voice_prompt": "普通话女性短视频口播，近距离自然收音，语气真实，语速略快但清晰，不要播音腔。",
  "provider_prompt": "请用自然普通话朗读以下正文，只朗读正文，不要读说明。声音方向：...",
  "negative_prompt": ["不要播音腔", "不要广告腔", "不要夸张表演"],
  "tempo_hint": "slightly_fast",
  "reason": "候选声线和参考音频的 pitch、语速、明亮度接近。"
}
```

### 8.3 审计要求

所有 LLM prompt 必须先写入：

```text
S5_03_03_TTSBuilderQuickAdv/Prompt/
```

模型调用只能从 prompt 文件读取，不允许在调用前临时拼接不可审计 prompt。

## 9. 输入输出协议

### 9.1 CLI

```text
python ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py \
  --workspace <workspace> \
  --voice-catalog-root ToolLibrary/Analysis_V1/VoiceCatalogAdv \
  --providers google,qwen,bytedance \
  --reference-duration 16 \
  --stage1-count 16 \
  --stage2-count 6 \
  --final-count 3 \
  --database-url-env OPENCREW_DATABASE_URL \
  --force \
  --print-json
```

参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--workspace` | 当前目录 | Analysis_V1 workspace |
| `--voice-catalog-root` | 默认 Adv catalog | 多 provider catalog 根目录 |
| `--voice-catalog-dir` | 空 | 兼容单 catalog 调试 |
| `--providers` | `google` | provider allowlist |
| `--reference-start` | `0` | 用户指定采样起点 |
| `--reference-duration` | `16` | 用户指定采样时长 |
| `--stage1-count` | `16` | Resemblyzer 首轮保留数量 |
| `--stage2-count` | `6` | SpeechBrain 次轮保留数量 |
| `--final-count` | `3` | 最终样本数量 |
| `--disable-speechbrain` | false | 关闭 SpeechBrain |
| `--force` | false | 强制重跑 |
| `--resume` | false | 复用已完成输出 |
| `--print-json` | false | 输出 JSON |

### 9.2 必需输入

```text
SessionContext/Variables.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/Audio_Reference.wav
```

### 9.3 工具目录输出

```text
S5_03_03_TTSBuilderQuickAdv/
  Working/
    InputFrom_0_Variables.json
    InputFrom_4_final_srt_frame_items.json
    Audio_Reference_Selected.wav
    raw_candidates/
    fitted_candidates/
    State_progress.json
  Prompt/
    prompt_planner_candidate_001.md
    tts_candidate_001_prompt.txt
  Output/
    reference_sampling_audit.json
    reference_voice_profile.json
    catalog_stage1_resemblyzer.json
    catalog_stage2_speechbrain.json
    voice_prompt_plan.json
    tts_builder_adv_candidates.json
    provider_call_audit.json
  Report/
    Result.json
```

### 9.4 SessionOutput

```text
SessionOutput/tts/tts_builder_candidates.json
SessionOutput/tts/tts_builder_candidate_001.wav
SessionOutput/tts/tts_builder_candidate_002.wav
SessionOutput/tts/tts_builder_candidate_003.wav
```

为了兼容现有 UI 和后续工具，最终仍写 `SessionOutput/tts/tts_builder_candidates.json`。同时在 payload 内写：

```json
{
  "tool": "03_03_TTSBuilderQuickAdv",
  "source_tool_dir": "S5_03_03_TTSBuilderQuickAdv"
}
```

### 9.5 candidates schema

```json
{
  "schema_version": "analysis_v1_tts_builder_adv_candidates_0.1",
  "tool": "03_03_TTSBuilderQuickAdv",
  "tool_version": "0.1.0",
  "sample_policy": {
    "selected_duration": 16.0,
    "selected_range": {"start": 12.4, "end": 28.4},
    "sampling_score": 87.2,
    "reason": "The selected window has high voice activity, stable energy, and complete subtitle boundaries."
  },
  "reference_audio_profile": {
    "audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav",
    "features": {},
    "gender_gate": {}
  },
  "ranking_policy": {
    "stage1": "resemblyzer_top_16",
    "stage2": "speechbrain_top_6",
    "final": "llm_prompt_plus_tts_sample_top_3",
    "scoring_mode": "full_speechbrain",
    "scoring_mode_reason": ""
  },
  "selected_candidate_id": "tts_001",
  "candidates": [
    {
      "rank": 1,
      "candidate_id": "tts_001",
      "provider": "google",
      "model": "gemini-3.1-flash-tts-preview",
      "voice": "Kore",
      "voice_label": "Kore",
      "voice_source": "system_catalog",
      "clone_id": "",
      "selected": true,
      "voice_prompt": "普通话女性短视频口播，近距离自然收音，语气真实，语速略快但清晰。",
      "prompt_path": "S5_03_03_TTSBuilderQuickAdv/Prompt/tts_candidate_001_prompt.txt",
      "sample_audio_path": "SessionOutput/tts/tts_builder_candidate_001.wav",
      "raw_audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/raw_candidates/tts_001_raw.wav",
      "fit_audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/fitted_candidates/tts_001_fit.wav",
      "raw_duration": 17.3,
      "target_duration": 16.0,
      "fit_duration": 16.0,
      "tempo": 1.08125,
      "match_score": 91,
      "scores": {
        "scoring_mode": "full_speechbrain",
        "final_score": 91.4,
        "stage1_score": 88.2,
        "stage2_score": 90.6,
        "resemblyzer_cosine": 0.71,
        "speechbrain_cosine": 0.64,
        "timbre_score": 90.1,
        "pitch_score": 86.4,
        "pace_score": 82.0,
        "brightness_score": 88.0,
        "energy_score": 80.5,
        "clarity_score": 84.0,
        "gender_score": 100.0,
        "provider_score": 100.0
      },
      "reason": "音色 embedding、pitch 和语速都接近参考音频，provider 当前可用，样本时长拟合稳定。",
      "needs_review": false
    }
  ],
  "created_at": "2026-06-15T00:00:00Z"
}
```

## 10. 后端与前端接入

### 10.1 tool registry

新增：

```json
{
  "id": "03_03",
  "name": "03_03_TTSBuilderQuickAdv",
  "display_name_zh": "高级快速声音匹配",
  "display_name_en": "Advanced quick voice matching",
  "script": "ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py",
  "stage": "tts_builder",
  "required_by_default": false,
  "cost_level": "high",
  "uses_llm": true,
  "uses_vlm": false,
  "uses_tts": true,
  "supports_resume": true,
  "hard_dependencies": ["02_02"],
  "soft_dependencies": ["voice_catalog_adv", "resemblyzer", "speechbrain", "tool_media_provider_configs"],
  "estimated_runtime": {
    "basis": "smart reference sampling, two-stage local voice matching, prompt planning, and bounded TTS candidate generation",
    "relative": "high"
  },
  "main_outputs": [
    "SessionOutput/tts",
    "S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_speechbrain.json"
  ],
  "writes_session_context": []
}
```

### 10.2 backend router

需要改动：

1. 增加 `ANALYSIS_V1_TTS_BUILDER_QUICK_ADV` 常量。
2. `normalize_analysis_v1_tts_builder_mode()` 增加别名：

```text
03_03, 03-03, quick_adv, quick-adv, adv, tts_builder_quick_adv
```

3. `analysis_v1_tts_builder_spec()` 支持返回 `03_03` spec。
4. `analysis_v1_step_command()` 对 `03_03` 注入：

```text
--voice-catalog-root
--providers
--reference-start
--reference-duration
--database-url-env
--force / --resume
```

5. step output map 增加：

```text
03_03:
  SessionOutput/tts/tts_builder_candidates.json
  S5_03_03_TTSBuilderQuickAdv/Report/Result.json
```

### 10.3 frontend

第一版 UI 可最小改动：

1. TTS Builder 区域新增按钮 `Builder-Adv`。
2. 点击后发送：

```json
{
  "mode": "run_only_step",
  "run_only_step_id": "03_03",
  "include_tts_builder": true,
  "tts_builder_mode": "quick_adv",
  "force": true,
  "options": {
    "source": "tts_builder_dialog",
    "reference_start": 0,
    "reference_duration": 16
  }
}
```

3. 候选读取仍复用 `SessionOutput/tts/tts_builder_candidates.json`。
4. 候选详情里可逐步展示高级评分字段。

## 11. 实施分期

### Phase 1：Gemini-only Adv MVP

目标：不引入多 provider 风险，先验证高级匹配流程。

任务：

1. 复制 `03_02_TTSBuilderQuick.py` 为 `03_03_TTSBuilderQuickAdv.py`。
2. 改工具名、目录名、schema version。
3. 新增 `ReferenceSamplerAdv`，输出 sampling audit。
4. 把现有综合排序改成两阶段：
   - Resemblyzer stage1 Top 16。
   - SpeechBrain stage2 Top 6，SpeechBrain 不可用时降级为 Resemblyzer + acoustic rerank。
5. 输出 0 到 100 维度评分。
6. 仍使用当前 Gemini catalog 和 Gemini TTS 生成 Top 3。
7. 接入 router、tool registry、运行进度、输出检查。
8. 增加 contract tests。

完成标准：

1. 可通过 UI 单步运行 `03_03`。
2. 成功写出 `SessionOutput/tts/tts_builder_candidates.json`。
3. `Result.json` 标明 `tool=03_03_TTSBuilderQuickAdv`。
4. SpeechBrain 不可用时工具不失败，只降级并写 warning。

### Phase 2：Prompt Planner

目标：让候选提示词从规则拼接升级为可审计 LLM 生成。

任务：

1. 增加 prompt planner prompt 模板。
2. 从数据库读取 text/LLM provider config。
3. 生成 `voice_prompt_plan.json`。
4. 每个候选 TTS prompt 从 prompt plan 派生。
5. provider audit 记录 LLM prompt planning 调用。

完成标准：

1. 每个候选都有 `voice_prompt`、`negative_prompt`、`reason`。
2. prompt 文件可复跑。
3. LLM 调用失败时可降级到规则 prompt。

### Phase 3：Provider Adapter

目标：抽出统一 TTS adapter，先保持 Gemini 行为不变。

任务：

1. 新建 `ToolLibrary/Analysis_V1/tts_provider_adapters.py`。
2. 实现 `GoogleGeminiTTSAdapter`。
3. `03_03` 只通过 adapter 调 TTS。
4. 保持现有 provider audit。

完成标准：

1. Gemini output 与 Phase 1 等价。
2. adapter 单元测试覆盖 payload、音频解析、错误处理。

### Phase 4：Qwen Catalog + Adapter

目标：接入千问 TTS 系统音色。

任务：

1. 新增 Qwen provider config 读取。
2. 实现 `QwenTTSAdapter`。
3. 生成 `VoiceCatalogAdv/qwen/<model>/`。
4. 让 `03_03 --providers google,qwen` 混合排名。

完成标准：

1. Qwen catalog check-only 通过。
2. Qwen 候选可生成试听音频。
3. 输出中 provider/model/voice 可区分。

### Phase 5：Aliyun CosyVoice Cloud Voice Clone

目标：让 QuickAdv 页面支持用参考声音创建云端克隆 `voice_id`，并反复试听该 `voice_id`。

任务：

1. 实现 `clone-voice` / `clone-list` 子命令。
2. 实现音频质量检查和 consent gating。
3. 实现本地音频发布到短期可访问 URL 的策略。
4. 实现阿里云 CosyVoice voice-enrollment adapter。
5. 实现 CosyVoice cloned voice preview adapter。
6. 页面新增“创建自定义音色”区域。

完成标准：

1. 用户确认授权后可创建 `voice_id`。
2. `voice_id` 写入 `Interactive/cloned_voices.json`。
3. 用户可用该 `voice_id` 多次 preview。
4. 用户可把任意 preview 保存到候选槽位。
5. 未确认授权、音频质量不足、无法发布音频 URL 时工具返回 blocked，不发起云端克隆。

### Phase 6：ByteDance Catalog + Adapter

目标：接入抖音/火山/豆包语音音色。

任务：

1. 读取 `tool_media_provider_configs.extra_json` 中的 app_id / resource_id / base_url / format。
2. 实现 `ByteDanceTTSAdapter`。
3. 生成 `VoiceCatalogAdv/bytedance/<model_or_resource>/`。
4. 混合排名并生成候选。

完成标准：

1. ByteDance catalog check-only 通过。
2. ByteDance 候选可生成试听音频。
3. 临时 URL 下载走安全下载器或 adapter 内安全校验。

## 12. 测试计划

### 12.1 单元测试

新增：

```text
backend/tests/contracts/test_analysis_v1_tts_quick_adv_contract.py
```

覆盖：

1. `03_03` 脚本存在且可 import。
2. CLI 参数可解析。
3. 缺必需输入时返回 blocked。
4. catalog 缺音频时返回 blocked。
5. SpeechBrain disabled 时写 warning 不失败。
6. scoring 输出 0 到 100 范围。
7. resume 复用已有结果。

### 12.2 集成测试

使用小型 fixture workspace：

```text
SessionContext/Variables.json
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/Audio_Reference.wav
VoiceCatalog fixture with 4 voices
```

mock：

1. Gemini TTS HTTP response。
2. LLM prompt planner response。
3. provider config secret loader。

验证：

1. 输出 candidates JSON schema。
2. Top 3 wav 文件生成。
3. audit 文件完整。
4. force rerun 失败时恢复旧 `SessionOutput/tts`。

### 12.3 UI smoke

验证：

1. `Builder-Adv` 按钮可启动后台运行。
2. run progress 显示 `03_03_TTSBuilderQuickAdv`。
3. 候选音频可播放。
4. 候选评分不撑破 UI。

## 13. 风险与处理

| 风险 | 影响 | 处理 |
|---|---|---|
| SpeechBrain 模型缓存缺失 | 首次运行慢或失败 | 默认不联网下载，缺缓存降级；提供一次性缓存生成说明 |
| 多 provider 音色数量大 | catalog 生成成本高 | 分 provider、分 model、分 voice allowlist 生成 |
| 跨 provider 分数不公平 | 排名偏向某 provider | 输出 provider calibration，第一版只保证同运行可解释 |
| TTS provider 输出格式不同 | 样本落盘不稳定 | adapter 统一转 wav，失败写 provider_call_audit |
| 用户误解系统音色匹配为声音复刻 | 合规风险 | 系统音色匹配和云端克隆在 UI 中分区展示 |
| 未授权声音克隆 | 合规风险 | clone-voice 必须要求 consent_confirmed，并记录参考音频 hash 和用途 |
| CosyVoice 需要可访问音频 URL | 克隆创建失败 | 设计 `audio_publish.py`，无 signed URL 能力时返回 blocked |
| 克隆 voice_id 绑定 target_model | 后续合成失败 | preview 前校验 model 与 clone record 的 target_model 一致 |
| Qwen / ByteDance 鉴权差异 | 接入复杂 | 通过 provider config extra_json 和 adapter 隔离 |
| prompt planner 不稳定 | 样本风格漂移 | prompt 失败降级规则 prompt，保留所有 prompt 文件 |

## 14. 建议首个开发切片

首个 PR 只做 Phase 1：

1. 新建 `03_03_TTSBuilderQuickAdv.py`。
2. 使用现有 Gemini catalog。
3. 实现智能采样 audit。
4. 明确 Resemblyzer stage1 / SpeechBrain stage2。
5. 输出高级评分 JSON。
6. 后端支持 `tts_builder_mode=quick_adv`。
7. 前端增加 `Builder-Adv` 按钮。

这能最快验证核心价值，并把多 provider 风险留到后续 adapter PR。

## 15. Python 脚本套件设计

`03_03_TTSBuilderQuickAdv.py` 不应实现成单个巨大脚本。为了支撑工具页面里的反复试音色、prompt 调整、tempo 调整、保存候选槽位等交互，需要拆成薄入口和可复用 Python package。

详细脚本和子命令设计见：

```text
docs/SessionDesign-R2/Analysis_V1_03_03_TTSBuilderQuickAdv_Python脚本套件设计.md
```

页面设计见：

```text
docs/SessionDesign-R2/Analysis_V1_03_03_TTSBuilderQuickAdv_工具页面设计.md
```

核心设计是：

1. `03_03_TTSBuilderQuickAdv.py run` 支撑 Analysis_V1 自动链路。
2. `state / sample-reference / catalog-list / rank / plan-prompt / preview / save-candidate / finalize` 支撑 TTSBuilderQuickAdv 页面。
3. `clone-voice / clone-list` 支撑阿里云 CosyVoice 等云端声音克隆，创建并管理 `voice_id`。
4. 每次试听都写入 `S5_03_03_TTSBuilderQuickAdv/Interactive/preview_attempts.jsonl`。
5. 用户保存候选槽位后，再由 `finalize` 写标准 `SessionOutput/tts/tts_builder_candidates.json`。

## 16. 外部文档参考

1. Gemini TTS 官方文档：`https://ai.google.dev/gemini-api/docs/speech-generation`
2. 阿里云 Model Studio 非实时语音合成 / Qwen-TTS 文档：`https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide`
3. 阿里云 Model Studio 声音复刻文档：`https://help.aliyun.com/zh/model-studio/voice-cloning-user-guide`
4. 火山引擎豆包语音 TTS HTTP 文档：`https://www.volcengine.com/docs/6561/79820`
