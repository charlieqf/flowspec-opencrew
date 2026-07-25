# Analysis_V1 03_03_TTSBuilderQuickAdv Python 脚本套件设计

日期：2026-06-15
状态：脚本架构设计
目标：设计一套可实现高级声音匹配能力、并能支撑 TTSBuilderQuickAdv 工具页面反复试音色的 Python 脚本和模块边界。

## 1. 设计结论

`03_03_TTSBuilderQuickAdv.py` 不应写成一个巨大脚本，把采样、建库、匹配、prompt、TTS preview、保存候选全部塞进去。

建议设计成“薄入口 + Python package + 多个可复用子命令”：

```text
ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py
ToolLibrary/Analysis_V1/tts_quick_adv/
scripts/generate_analysis_v1_voice_catalog_adv.py
scripts/precompute_analysis_v1_voice_features_adv.py
```

其中：

1. `03_03_TTSBuilderQuickAdv.py` 是 Analysis_V1 正式工具入口，负责一次性跑完整高级匹配，产出 `SessionOutput/tts/tts_builder_candidates.json`。
2. `tts_quick_adv/` 是核心 Python package，封装采样、特征、embedding、ranking、prompt planning、provider TTS、preview cache、candidate finalize。
3. 后端页面接口调用 package 的 CLI 子命令或 Python 函数，不直接复制算法。
4. 页面上的每一次“试听某个音色 / 调 prompt / 调 tempo / 另存候选”都是一个独立、可审计、可缓存的 workspace artifact。

这样既能满足 Analysis_V1 主链路自动运行，也能支撑一个交互式 TTSBuilderQuickAdv 页面。

QuickAdv 还需要支持云端声音克隆。用户可以用参考音频调用阿里云 CosyVoice / Qwen-TTS 等声音复刻接口创建自定义 `voice_id`，再把该 `voice_id` 当作可试听音色反复 preview、保存候选和 finalize。

## 2. 页面能力目标

TTSBuilderQuickAdv 工具页面需要支持用户反复试：

1. 上传或使用 `SessionOutput/Audio_Reference.wav` 作为参考声音。
2. 自动推荐 16 秒高质量采样区间。
3. 手动拖动采样区间并重新分析。
4. 选择 provider：Gemini / Qwen / ByteDance。
5. 浏览所有可用 voice catalog。
6. 运行 Resemblyzer 首筛和 SpeechBrain 复筛。
7. 按性别、音色标签、provider、分数范围过滤候选。
8. 使用云端声音复刻创建自定义 `voice_id`。
9. 对系统 voice 或克隆 voice 生成试听样本，不限于 Top 3。
10. 修改 prompt、tempo、朗读文本后重复试听。
11. 把任意试听结果保存为候选 1/2/3。
12. 最终写入标准 `SessionOutput/tts/tts_builder_candidates.json` 给后续工具使用。

页面不是只点一次“Run Adv”。它需要像声音工作台一样支持多轮试错。

## 3. 目录结构

新增结构：

```text
ToolLibrary/Analysis_V1/
  03_03_TTSBuilderQuickAdv.py
  tts_quick_adv/
    __init__.py
    cli.py
    schemas.py
    paths.py
    state_store.py
    io_utils.py
    media_utils.py
    reference_sampler.py
    audio_features.py
    embedding_backends.py
    catalog_store.py
    ranking.py
    score_normalizer.py
    prompt_planner.py
    audio_publish.py
    voice_cloning.py
    preview_service.py
    finalizer.py
    provider_config.py
    provider_audit_bridge.py
    providers/
      __init__.py
      base.py
      google_gemini.py
      qwen_dashscope.py
      aliyun_cosyvoice.py
      aliyun_voice_clone.py
      bytedance_volcengine.py

scripts/
  generate_analysis_v1_voice_catalog_adv.py
  precompute_analysis_v1_voice_features_adv.py
  smoke_analysis_v1_tts_quick_adv.py
```

`03_03_TTSBuilderQuickAdv.py` 只做：

```python
from ToolLibrary.Analysis_V1.tts_quick_adv.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

## 4. 模块职责

### 4.1 `schemas.py`

定义内部数据结构，避免不同脚本之间传随意 dict。

核心 dataclass / TypedDict：

```text
ReferenceWindow
ReferenceProfile
VoiceCatalog
VoiceCatalogItem
VoiceFeatureProfile
Stage1RankingRow
Stage2RankingRow
PromptPlan
PreviewRequest
PreviewAttempt
FinalCandidate
ToolResult
```

原则：

1. 所有可写入 JSON 的对象必须有 `to_json()`。
2. 所有路径字段存 workspace 相对路径。
3. 所有分数字段统一声明范围。
4. provider secret 不进入 schema。

### 4.2 `paths.py`

集中管理路径。

```text
SessionContext/Variables.json
SessionOutput/Audio_Reference.wav
SessionOutput/subtitle/final_srt_frame_items.json
SessionOutput/tts/tts_builder_candidates.json

S5_03_03_TTSBuilderQuickAdv/
  Working/
  Interactive/
  Prompt/
  Output/
  Report/
```

页面交互产物放在：

```text
S5_03_03_TTSBuilderQuickAdv/Interactive/
  state.json
  preview_attempts.jsonl
  saved_candidates.json
  filters.json
  last_rank_request.json
  previews/
```

主链路产物放在：

```text
S5_03_03_TTSBuilderQuickAdv/Output/
```

### 4.3 `state_store.py`

支撑页面反复尝试的核心状态层。

职责：

1. 读取/写入 `Interactive/state.json`。
2. 追加 `preview_attempts.jsonl`。
3. 管理用户保存的候选槽位。
4. 根据 preview signature 复用历史试听音频。
5. 提供页面初始 load 所需的汇总 payload。

关键函数：

```python
load_interactive_state(workspace: Path) -> dict[str, Any]
save_interactive_state(workspace: Path, state: dict[str, Any]) -> None
append_preview_attempt(workspace: Path, attempt: PreviewAttempt) -> None
list_preview_attempts(workspace: Path, limit: int = 200) -> list[PreviewAttempt]
save_candidate_slot(workspace: Path, slot: int, attempt_id: str) -> dict[str, Any]
```

### 4.4 `reference_sampler.py`

负责自动选择和分析 16 秒参考片段。

子命令：

```text
sample-reference
```

输入：

```text
--workspace
--reference-start optional
--reference-duration default 16
--manual false/true
```

输出：

```text
S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav
S5_03_03_TTSBuilderQuickAdv/Output/reference_sampling_audit.json
S5_03_03_TTSBuilderQuickAdv/Output/reference_voice_profile.json
```

页面用途：

1. 打开页面时可先读取已有 profile。
2. 用户拖动 waveform 后点“Analyze”，后端调用 `sample-reference --manual`。
3. 页面展示采样质量分和风险。

第一版采样逻辑：

1. 按字幕 item 边界枚举候选窗口。
2. 用 ffmpeg 抽取临时 wav。
3. 计算 voice activity、能量、pitch、文本覆盖、边界完整度。
4. 选综合分最高窗口。
5. 手动窗口只分析不改选。

### 4.5 `audio_features.py`

负责基础音频特征。

函数：

```python
extract_audio_features(path: Path, text: str = "", duration_hint: float = 0.0) -> VoiceFeatureProfile
compare_acoustic_features(reference: VoiceFeatureProfile, candidate: VoiceFeatureProfile) -> dict[str, float]
```

特征：

```text
duration
sample_rate
rms
zero_crossing
spectral_centroid
pitch_hz
pitch_confidence
speaking_rate_cps
signal_quality
brightness
energy
clarity
```

### 4.6 `embedding_backends.py`

封装 Resemblyzer / SpeechBrain。

函数：

```python
load_resemblyzer_backend() -> EmbeddingBackend | None
load_speechbrain_backend() -> EmbeddingBackend | None
embed_audio(path: Path, backend: EmbeddingBackend) -> EmbeddingVector
cosine_similarity(left: EmbeddingVector, right: EmbeddingVector) -> float
```

缓存策略：

```text
S5_03_03_TTSBuilderQuickAdv/Working/cache/embeddings/
  <backend>_<audio_sha256>_<backend_version>.json
```

SpeechBrain 默认不自动联网下载。没有模型缓存时：

1. 页面显示 `SpeechBrain unavailable`。
2. ranking 降级到 `degraded_resemblyzer_acoustic`。
3. `Result.json` 写 warning。

### 4.7 `catalog_store.py`

负责加载和查询 voice catalog。

子命令：

```text
catalog-list
catalog-check
catalog-profile
```

页面用途：

1. 打开页面时列出所有 provider / model / voice。
2. 筛选 voice。
3. 展示 catalog 音色标签和样本状态。

核心函数：

```python
load_catalogs(root: Path, providers: list[str], models: list[str]) -> list[VoiceCatalog]
list_catalog_items(...) -> list[VoiceCatalogItem]
validate_catalog_item_audio(item: VoiceCatalogItem) -> CatalogValidationResult
load_or_build_catalog_profile(item: VoiceCatalogItem) -> VoiceFeatureProfile
```

### 4.8 `ranking.py`

负责两阶段排名。

子命令：

```text
rank
```

输入：

```text
--workspace
--providers google,qwen,bytedance
--models optional
--voices optional
--stage1-count 16
--stage2-count 6
--disable-speechbrain
```

输出：

```text
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage1_resemblyzer.json
S5_03_03_TTSBuilderQuickAdv/Output/catalog_stage2_speechbrain.json
S5_03_03_TTSBuilderQuickAdv/Interactive/ranking_board.json
```

页面用途：

1. 用户点“Scan Voices”。
2. 页面展示 ranking board。
3. 用户可以从排名中点任意 voice 试听。

排名结果必须保留：

```text
scoring_mode
scoring_mode_reason
stage1_rank
stage1_score
stage2_rank
stage2_score
match_score
resemblyzer_cosine
speechbrain_cosine
score_parts
exclude_reason
```

### 4.9 `score_normalizer.py`

统一把底层分数转成 0 到 100。

函数：

```python
normalize_cosine(value: float | None, low: float = 0.2, high: float = 0.85) -> float
ratio_score(left: float, right: float) -> float
bounded_score(value: float, min_value: float, max_value: float) -> float
build_score_parts(reference, candidate, raw_scores) -> dict[str, float]
```

页面只展示 0 到 100 分；原始 cosine 留在详情里。

`score_normalizer.py` 必须定义两种评分模式：

```text
full_speechbrain
degraded_resemblyzer_acoustic
```

Full SpeechBrain 模式：

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

Degraded 模式：

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

`match_score` 统一定义为：

```text
match_score = round(final_score)
```

因此 UI 中的“匹配度 92”始终是当前可用后端下的 0 到 100 综合匹配度。Full 模式和 Degraded 模式都可排序，但不能跨任务或跨部署做绝对比较。ranking 输出必须包含 `scoring_mode`，页面用它显示“高精度匹配”或“基础匹配”。

### 4.10 `prompt_planner.py`

负责为候选 voice 生成 provider-specific prompt。

子命令：

```text
plan-prompt
batch-plan-prompts
```

输入：

```text
--workspace
--candidate-key
--provider
--model
--voice
--text
--style-mode close_reference|natural_selfie|energetic|calm|custom
--custom-instruction optional
```

输出：

```text
S5_03_03_TTSBuilderQuickAdv/Prompt/prompt_planner_<attempt_id>.md
S5_03_03_TTSBuilderQuickAdv/Output/voice_prompt_plan.json
```

第一版可以有两种模式：

1. `rule`：不调用 LLM，按 reference profile + candidate profile 生成 prompt。
2. `llm`：调用 run model 或配置的文本模型，生成 prompt plan。

页面应允许用户编辑 prompt，因此 prompt planner 输出只是默认草稿。

### 4.11 `providers/base.py`

统一 TTS provider 接口。

```python
class TTSProviderAdapter(Protocol):
    provider: str

    def list_voices(self, config: ProviderConfig) -> list[VoiceInfo]:
        ...

    def synthesize(self, request: TTSRequest) -> TTSResult:
        ...
```

`TTSRequest`：

```text
workspace
provider
model
voice
text
prompt
output_path
audio_format
sample_rate
extra
```

`TTSResult`：

```text
status
output_path
duration
raw_response_summary
provider_request_id
cost_units
warnings
```

### 4.12 `providers/google_gemini.py`

第一版必须实现。

职责：

1. 从 provider config / secret loader 读取 Gemini key。
2. 调 Gemini TTS。
3. 解析 inline audio。
4. 写 wav。
5. 记录 provider audit。

### 4.13 `providers/qwen_dashscope.py`

第二阶段实现。

职责：

1. 读取 DashScope / Model Studio API key。
2. 支持 Qwen TTS `voice` 和 `instructions`。
3. 下载或解析输出音频。
4. 转成统一 wav。

### 4.14 `providers/bytedance_volcengine.py`

第三阶段实现。

职责：

1. 读取 access key secret。
2. 从 `extra_json` 读取 app_id / resource_id / base_url / audio_format。
3. 调火山 TTS。
4. 处理 base64 或临时 URL。
5. 使用安全下载策略保存音频。

### 4.15 `audio_publish.py`

云端声音复刻接口通常不能读取本地 workspace 文件，需要 provider 可访问的音频 URL。`audio_publish.py` 负责把本地参考音频发布成短期可访问 URL。

第一版支持三种策略：

| 策略 | 状态 | 说明 |
|---|---|---|
| `disabled` | 默认 | 不发布本地音频；需要外部传入 `--audio-url` |
| `oss_signed_url` | 推荐 | 上传到用户配置的 OSS/S3/对象存储，生成短期 signed URL |
| `public_callback_url` | 开发/内网 | 后端暴露一次性 HTTPS 下载 URL，要求部署环境可被阿里云访问 |

输出记录：

```text
audio_sha256
published_url_host
expires_at
storage_provider
```

完整 signed URL 不写入最终业务 JSON；只允许存在短期工作文件或进程内。

### 4.16 `voice_cloning.py`

负责创建、查询、删除云端克隆音色。

子命令：

```text
clone-voice
clone-list
clone-delete
```

页面用途：

1. 用户选择参考音频片段。
2. 用户确认参考音频有使用授权。
3. 选择 target provider / target model。
4. 创建云端 voice。
5. 得到 `voice_id` 后加入当前 workspace 的 cloned voice list。
6. 用户可以立即用该 `voice_id` preview。

`clone-voice` 输入：

```text
--workspace
--provider aliyun-cosyvoice|qwen
--target-model cosyvoice-v3.5-plus
--voice-name-prefix opencrew_<task_id>
--reference-audio S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav
--audio-url optional
--consent-confirmed
--print-json
```

`clone-voice` 输出：

```text
S5_03_03_TTSBuilderQuickAdv/Interactive/cloned_voices.json
S5_03_03_TTSBuilderQuickAdv/Output/voice_clone_audit.json
```

`ClonedVoice` schema：

```json
{
  "clone_id": "clone_20260615_abcdef12",
  "created_at": "2026-06-15T00:00:00Z",
  "provider": "aliyun-cosyvoice",
  "target_model": "cosyvoice-v3.5-plus",
  "region": "cn-beijing",
  "voice_id": "myvoice_xxx",
  "voice_label": "OpenCrew cloned voice",
  "voice_source": "cloud_voice_clone",
  "reference_audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav",
  "reference_audio_sha256": "",
  "consent": {
    "status": "confirmed",
    "purpose": "analysis_v1_tts_quick_adv",
    "confirmed_at": "2026-06-15T00:00:00Z"
  },
  "provider_limits": {
    "target_model_locked": true,
    "expires_policy": "provider_default"
  },
  "status": "ready",
  "error": ""
}
```

关键规则：

1. voice clone 必须绑定 `target_model`，后续合成必须用同一个模型。
2. clone voice 不进入系统 `VoiceCatalogAdv`，而是进入 workspace 的 `Interactive/cloned_voices.json`。
3. 页面 ranking 可以把 cloned voice 固定置顶，但不需要用 catalog embedding 排名。
4. 克隆音频质量检查复用 `reference_sampler.py`，低于阈值时提示用户重新选择。
5. 删除 clone 时先从 OpenCrew workspace 移除记录；如果 provider 支持 delete，再调用 provider 删除接口。

### 4.17 `providers/aliyun_voice_clone.py`

封装阿里云声音复刻接口。

CosyVoice 创建音色：

```text
POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
model: voice-enrollment
input.action: create_voice
input.target_model: cosyvoice-v3.5-plus
input.prefix: <prefix>
input.url: <public_audio_url>
```

Qwen-TTS 创建音色：

```text
POST https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization
model: qwen-voice-enrollment
input.action: create
input.target_model: qwen3-tts-vc-2026-01-22
input.preferred_name: <name>
input.audio.data: <url or data URI>
```

输出统一解析为：

```text
voice_id / voice
request_id
target_model
region
```

### 4.18 `providers/aliyun_cosyvoice.py`

负责使用 CosyVoice 系统音色或克隆音色合成试听。

输入 voice 可以是：

```text
system voice: longanhuan_v3
cloned voice: voice_id returned by voice-enrollment
```

如果 `voice_source=cloud_voice_clone`，adapter 必须校验 request model 等于 clone record 的 `target_model`。

### 4.19 `preview_service.py`

支撑页面反复试听的核心模块。

子命令：

```text
preview
batch-preview
```

输入：

```text
--workspace
--provider
--model
--voice
--voice-source system_catalog|cloud_voice_clone|manual
--clone-id optional
--text
--prompt-path optional
--prompt-text optional
--tempo 1.0
--candidate-key optional
--reuse-cache true
```

输出：

```text
S5_03_03_TTSBuilderQuickAdv/Interactive/previews/<attempt_id>.wav
S5_03_03_TTSBuilderQuickAdv/Interactive/previews/<attempt_id>.raw.wav
```

同时追加：

```text
S5_03_03_TTSBuilderQuickAdv/Interactive/preview_attempts.jsonl
```

`PreviewAttempt` schema：

```json
{
  "attempt_id": "pv_20260615_abcdef12",
  "created_at": "2026-06-15T00:00:00Z",
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "voice": "Kore",
  "voice_label": "Kore",
  "voice_source": "system_catalog",
  "clone_id": "",
  "text_sha256": "",
  "prompt_sha256": "",
  "prompt": "普通话女性短视频口播...",
  "tempo": 1.04,
  "raw_audio_path": "S5_03_03_TTSBuilderQuickAdv/Interactive/previews/pv_x.raw.wav",
  "audio_path": "S5_03_03_TTSBuilderQuickAdv/Interactive/previews/pv_x.wav",
  "duration": 16.0,
  "source": "manual_preview",
  "ranking_ref": {
    "stage2_rank": 3,
    "final_score": 88.4
  },
  "status": "completed",
  "error": ""
}
```

缓存 signature：

```text
provider + model + voice + text_sha256 + prompt_sha256 + tempo + adapter_version
```

如果用户重复点同样配置，直接返回旧 preview，避免重复扣费。

### 4.20 `finalizer.py`

负责把用户试听结果保存为正式候选。

子命令：

```text
save-candidate
finalize
```

`save-candidate`：

```text
--workspace
--attempt-id pv_x
--slot 1
```

效果：

1. 把 preview 音频复制到 `SessionOutput/tts/tts_builder_candidate_001.wav`。
2. 更新 `Interactive/saved_candidates.json`。
3. 不强制覆盖完整 `tts_builder_candidates.json`，除非用户点 finalize。

`finalize`：

```text
--workspace
--slots 1,2,3
```

效果：

1. 读取 saved candidates。
2. 生成最终 `SessionOutput/tts/tts_builder_candidates.json`。
3. 写 `S5_03_03_TTSBuilderQuickAdv/Output/tts_builder_adv_candidates.json`。
4. 写 `Report/Result.json`。

### 4.21 `provider_config.py`

隔离 provider config 和 secret 获取。

职责：

1. 从 `OPENCREW_DATABASE_URL` 读取 `tool_media_provider_configs`。
2. 解密或解析 secret。
3. 输出内存态 `ProviderConfig`。
4. 对外不返回 secret。

页面状态只展示：

```text
provider
model
enabled
has_api_key
extra_json public subset
```

## 5. CLI 子命令设计

统一入口：

```text
python ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py <subcommand> [args]
```

没有 subcommand 时默认执行完整 pipeline，兼容 Analysis_V1 runner：

```text
python ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py \
  --workspace <workspace> \
  --print-json \
  --force
```

等价于：

```text
python ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py run \
  --workspace <workspace> \
  --print-json \
  --force
```

### 5.1 `state`

页面初始加载调用。

```text
03_03_TTSBuilderQuickAdv.py state --workspace <workspace> --print-json
```

返回：

```text
reference profile
sampling audit summary
available providers
catalog summary
last ranking board
preview attempts
saved candidates
final candidates status
```

### 5.2 `sample-reference`

重新选择或分析参考声音。

```text
03_03_TTSBuilderQuickAdv.py sample-reference \
  --workspace <workspace> \
  --reference-start 12.4 \
  --reference-duration 16 \
  --manual \
  --print-json
```

### 5.3 `catalog-list`

列出可试音色。

```text
03_03_TTSBuilderQuickAdv.py catalog-list \
  --workspace <workspace> \
  --providers google,qwen,bytedance \
  --print-json
```

### 5.4 `rank`

运行推荐排名。

```text
03_03_TTSBuilderQuickAdv.py rank \
  --workspace <workspace> \
  --providers google,qwen \
  --stage1-count 16 \
  --stage2-count 6 \
  --print-json
```

### 5.5 `plan-prompt`

给某个 voice 生成默认 prompt。

```text
03_03_TTSBuilderQuickAdv.py plan-prompt \
  --workspace <workspace> \
  --provider google \
  --model gemini-3.1-flash-tts-preview \
  --voice Kore \
  --style-mode close_reference \
  --print-json
```

### 5.6 `preview`

页面反复试听的主命令。

```text
03_03_TTSBuilderQuickAdv.py preview \
  --workspace <workspace> \
  --provider google \
  --model gemini-3.1-flash-tts-preview \
  --voice Kore \
  --voice-source system_catalog \
  --text "给我老公买这个润喉糖啊..." \
  --prompt-text "普通话女性短视频口播..." \
  --tempo 1.04 \
  --candidate-key rank_003 \
  --print-json
```

### 5.7 `clone-voice`

创建云端克隆音色并返回 `voice_id`。

```text
03_03_TTSBuilderQuickAdv.py clone-voice \
  --workspace <workspace> \
  --provider aliyun-cosyvoice \
  --target-model cosyvoice-v3.5-plus \
  --voice-name-prefix opencrew_task_123 \
  --reference-audio S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav \
  --consent-confirmed \
  --print-json
```

如果环境不能把本地音频发布成公网可访问 URL，则需要显式传入：

```text
--audio-url https://example.com/reference.wav
```

返回：

```json
{
  "ok": true,
  "clone_id": "clone_20260615_abcdef12",
  "provider": "aliyun-cosyvoice",
  "target_model": "cosyvoice-v3.5-plus",
  "voice_id": "opencrew_task_123_xxx",
  "voice_source": "cloud_voice_clone",
  "status": "ready"
}
```

### 5.8 `clone-list`

列出当前 workspace 已创建或导入的克隆音色。

```text
03_03_TTSBuilderQuickAdv.py clone-list \
  --workspace <workspace> \
  --print-json
```

### 5.9 `save-candidate`

保存某次试听到候选槽位。

```text
03_03_TTSBuilderQuickAdv.py save-candidate \
  --workspace <workspace> \
  --attempt-id pv_20260615_abcdef12 \
  --slot 1 \
  --print-json
```

### 5.10 `finalize`

写最终标准输出。

```text
03_03_TTSBuilderQuickAdv.py finalize \
  --workspace <workspace> \
  --slots 1,2,3 \
  --print-json
```

### 5.11 `run`

完整自动流程。

```text
03_03_TTSBuilderQuickAdv.py run \
  --workspace <workspace> \
  --providers google \
  --stage1-count 16 \
  --stage2-count 6 \
  --final-count 3 \
  --force \
  --print-json
```

内部执行：

```text
sample-reference
rank
batch-plan-prompts
batch-preview top3
finalize
```

## 6. 后端页面接口建议

后端不直接实现算法，只负责：

1. 鉴权和 role check。
2. task_id -> workspace。
3. payload 校验。
4. 调 Python CLI 或 Python function。
5. 返回 JSON。

建议新增接口：

```text
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/state
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/sample-reference
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/catalog
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/rank
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/plan-prompt
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/clone-voice
GET  /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/cloned-voices
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/preview
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/save-candidate
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/finalize
```

页面 preview 接口要替代当前只支持 Google 的 `/analysis-v1/tts/preview`，但可以保留旧接口兼容现有 TTSBuilder 页面。

## 7. 页面交互流程

### 7.1 页面打开

```text
GET quick-adv/state
```

页面展示：

1. 参考音频 waveform。
2. 当前采样区间。
3. provider 配置状态。
4. catalog 可用数量。
5. 上次 ranking。
6. 历史 preview attempts。
7. 已保存候选槽位。

### 7.2 用户重新采样

```text
POST quick-adv/sample-reference
```

返回新的：

```text
sampling_score
reference_profile
selected_range
warnings
```

### 7.3 用户扫描音色

```text
POST quick-adv/rank
```

返回：

```text
ranking_board
stage1_count
stage2_count
fallback_mode
```

### 7.4 用户试听任意音色

```text
POST quick-adv/preview
```

请求：

```json
{
  "provider": "google",
  "model": "gemini-3.1-flash-tts-preview",
  "voice": "Kore",
  "voice_source": "system_catalog",
  "text": "给我老公买这个润喉糖啊...",
  "prompt": "普通话女性短视频口播...",
  "tempo": 1.04,
  "candidate_key": "rank_003"
}
```

返回：

```json
{
  "ok": true,
  "attempt_id": "pv_20260615_abcdef12",
  "audio_path": "S5_03_03_TTSBuilderQuickAdv/Interactive/previews/pv_20260615_abcdef12.wav",
  "raw_audio_path": "...raw.wav",
  "duration": 16.0,
  "cached": false
}
```

### 7.5 用户创建克隆音色

```text
POST quick-adv/clone-voice
```

请求：

```json
{
  "provider": "aliyun-cosyvoice",
  "target_model": "cosyvoice-v3.5-plus",
  "voice_name_prefix": "opencrew_task_123",
  "reference_audio_path": "S5_03_03_TTSBuilderQuickAdv/Working/Audio_Reference_Selected.wav",
  "consent_confirmed": true
}
```

返回：

```json
{
  "ok": true,
  "clone_id": "clone_20260615_abcdef12",
  "voice_id": "opencrew_task_123_xxx",
  "voice_source": "cloud_voice_clone"
}
```

页面随后把它显示在“自定义音色”区域，用户可立即 preview：

```json
{
  "provider": "aliyun-cosyvoice",
  "model": "cosyvoice-v3.5-plus",
  "voice": "opencrew_task_123_xxx",
  "voice_source": "cloud_voice_clone",
  "clone_id": "clone_20260615_abcdef12"
}
```

### 7.6 用户保存候选

```text
POST quick-adv/save-candidate
```

请求：

```json
{
  "attempt_id": "pv_20260615_abcdef12",
  "slot": 1
}
```

返回：

```text
SessionOutput/tts/tts_builder_candidate_001.wav
Interactive/saved_candidates.json
```

### 7.7 用户完成选择

```text
POST quick-adv/finalize
```

写：

```text
SessionOutput/tts/tts_builder_candidates.json
```

后续 `05_02_VideoPlanExecutor.py` 可以继续按现有路径读取。

## 8. 支撑反复试音色的关键设计

### 8.1 preview attempts 是一等产物

每次试听都写入 `preview_attempts.jsonl`，包括 prompt、tempo、voice、audio path、ranking ref、错误信息。

这样用户可以：

1. 回听历史尝试。
2. 比较同一 voice 不同 prompt。
3. 比较同一 prompt 不同 provider。
4. 把历史尝试随时保存为候选。

### 8.2 缓存避免重复扣费

`preview_service` 用 signature 查重：

```text
provider
model
voice
text_sha256
prompt_sha256
tempo
adapter_version
```

相同请求直接返回旧音频。

### 8.3 ranking 与 preview 解耦

用户可以试听：

1. Top 3。
2. Top 16。
3. 任意 catalog voice。
4. 手动输入 voice。

ranking 只负责推荐，不限制用户尝试。

### 8.4 保存候选与最终输出解耦

用户保存 slot 不等于立刻 finalize。页面可以显示：

```text
候选 1: preview attempt A
候选 2: preview attempt B
候选 3: empty
```

只有点完成时才写标准 `tts_builder_candidates.json`。

### 8.5 主链路自动运行复用同一套脚本

`run` 子命令使用相同模块自动执行：

```text
sample-reference -> rank -> preview top3 -> finalize
```

页面和主链路不会出现两套评分逻辑。

### 8.6 克隆 voice_id 与系统 voice 统一试听

页面上的 voice 统一抽象为：

```text
voice_source = system_catalog | cloud_voice_clone | manual
provider
model
voice
```

`preview_service` 不关心 voice 是系统音色还是克隆音色，只根据 `voice_source` 做额外校验。

克隆 voice 的特殊校验：

1. `model` 必须等于创建时的 `target_model`。
2. `consent.status` 必须是 `confirmed`。
3. provider config 必须仍可用。
4. 如果 provider 查询接口显示 voice 不存在，则标记为 stale。

## 9. 与现有代码的衔接点

### 9.1 可复用现有 `03_02_TTSBuilderQuick.py`

可先迁移或复用：

1. ffmpeg / ffprobe 查找。
2. wav duration / media duration。
3. audio_features。
4. Resemblyzer load 和 patch。
5. SpeechBrain load。
6. Gemini TTS call。
7. fit_audio_to_duration。
8. provider audit 记录。
9. force rerun 时恢复旧 `SessionOutput/tts`。

### 9.2 当前后端 preview 的迁移

现有：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/preview
```

问题：

1. 只支持 Google。
2. provider 调用逻辑写在 router。
3. 没有 preview attempt 历史。
4. 没有跨 provider adapter。

QuickAdv 页面应改用：

```text
POST /api/openclip/tasks/{task_id}/analysis-v1/tts/quick-adv/preview
```

后端内部调用：

```text
03_03_TTSBuilderQuickAdv.py preview ...
```

### 9.3 当前 TTSBuilder UI 的兼容

当前页面读取：

```text
SessionOutput/tts/tts_builder_candidates.json
```

QuickAdv finalize 仍写这个文件，所以旧页面和后续执行器不需要立即改 schema，只需要能容忍新增字段：

```text
scores
preview_attempt_id
source_tool_dir
ranking_policy
```

## 10. 开发顺序建议

### PR 1：脚本骨架和状态层

1. 新增 package 目录。
2. 新增 `03_03_TTSBuilderQuickAdv.py` 薄入口。
3. 实现 `paths.py`、`schemas.py`、`state_store.py`、`cli.py`。
4. 实现 `state` 子命令。
5. 加 import/CLI contract tests。

### PR 2：参考采样和特征

1. 实现 `reference_sampler.py`。
2. 实现 `audio_features.py`。
3. 支持 `sample-reference`。
4. 写 reference audit。

### PR 3：Catalog 和 ranking

1. 实现 `catalog_store.py`。
2. 实现 `embedding_backends.py`。
3. 实现 `score_normalizer.py`。
4. 实现 `ranking.py`。
5. 支持 `catalog-list`、`rank`。

### PR 4：Gemini preview 和 candidate finalize

1. 实现 `providers/base.py`。
2. 实现 `providers/google_gemini.py`。
3. 实现 `preview_service.py`。
4. 实现 `finalizer.py`。
5. 支持 `preview`、`save-candidate`、`finalize`。

### PR 5：阿里云 CosyVoice 克隆 voice_id

1. 实现 `audio_publish.py`。
2. 实现 `voice_cloning.py`。
3. 实现 `providers/aliyun_voice_clone.py`。
4. 实现 `providers/aliyun_cosyvoice.py` 的 cloned voice 合成。
5. 支持 `clone-voice`、`clone-list`。
6. 页面可创建 clone 并立即 preview。

### PR 6：完整 run 模式和后端接入

1. 实现 `run` 自动流程。
2. 注册 `03_03`。
3. 后端支持 `tts_builder_mode=quick_adv`。
4. 增加 QuickAdv 页面接口。

### PR 7：QuickAdv 页面

1. 新建或扩展 TTSBuilder 页面。
2. 接入 state、sample-reference、catalog、rank、clone-voice、preview、save-candidate、finalize。
3. 展示 preview history、自定义音色和候选槽位。

### PR 8：Qwen / ByteDance adapter

1. 先实现 provider config 读取。
2. 再实现 catalog 生成。
3. 最后接入 preview 和 ranking。

## 11. 测试策略

### 11.1 Contract tests

```text
test_analysis_v1_tts_quick_adv_cli_contract.py
test_analysis_v1_tts_quick_adv_state_contract.py
test_analysis_v1_tts_quick_adv_preview_contract.py
test_analysis_v1_tts_quick_adv_voice_clone_contract.py
test_analysis_v1_tts_quick_adv_finalize_contract.py
```

覆盖：

1. 每个子命令 `--help` 可运行。
2. 缺 workspace 输入时 blocked。
3. state 返回稳定 schema。
4. preview signature 相同会复用缓存。
5. save-candidate 正确复制音频到 slot。
6. finalize 正确生成标准 candidates JSON。
7. clone-voice 在未确认 consent 时 blocked。
8. clone-voice 在没有 audio URL 发布能力时 blocked。
9. cloned voice preview 校验 target_model。

### 11.2 Mock provider tests

provider adapter 必须支持 mock：

```text
OPENCREW_TTS_QUICK_ADV_PROVIDER_MOCK=1
```

mock 模式生成短 wav，不调用真实 provider。

这样页面和后端 smoke 不依赖外部 API key。

### 11.3 Fixture workspace

测试 fixture：

```text
tests/fixtures/analysis_v1_tts_quick_adv_workspace/
  SessionContext/Variables.json
  SessionOutput/Audio_Reference.wav
  SessionOutput/subtitle/final_srt_frame_items.json
  VoiceCatalogAdv/google/mock/
```

## 12. 最小可实现版本

如果要尽快开始编码，最小版本只做：

1. `03_03_TTSBuilderQuickAdv.py state`
2. `sample-reference`
3. `rank`，只支持现有 Gemini catalog。
4. `preview`，只支持 Gemini。
5. `clone-voice`，先只支持阿里云 CosyVoice。
6. `save-candidate`
7. `finalize`

暂不做：

1. ByteDance adapter。
2. LLM prompt planner。
3. SpeechBrain 强依赖。
4. provider 侧 clone delete。

但接口和目录先按完整设计预留，后续 provider 不需要推翻页面。

## 13. 当前已实施状态（2026-06-15）

已落地的第一阶段能力：

1. 新增入口脚本 `ToolLibrary/Analysis_V1/03_03_TTSBuilderQuickAdv.py`。
2. 新增包 `ToolLibrary/Analysis_V1/tts_quick_adv/`，包含 `state`、`sample-reference`、`catalog-list`、`rank`、默认 `run`。
3. `rank` 已支持 Resemblyzer + 声学特征重排，并在 SpeechBrain 不可用时输出 `degraded_resemblyzer_acoustic` 评分模式。
4. `run` 使用 03_03 自己的 stage2 推荐结果驱动最终 TTS 样本生成，最终写回 `SessionOutput/tts/tts_builder_candidates.json`；`--resume` 会在重排前复用既有候选，`--force` 失败会恢复旧的 SessionOutput TTS 产物。
5. 新增阿里云 DashScope 声音克隆 CLI：`clone-voice`、`clone-list`、`clone-query`、`clone-delete`。
6. `clone-voice` 使用 `dashscope.audio.tts_v2.VoiceEnrollmentService.create_voice`，默认 target model 为 `cosyvoice-v1`，必须传入 `--clone-consent-confirmed`，会记录 `reference_audio_sha256` 和 consent 块；相同 reference hash + target model 默认复用已有 `voice_id`，并写入 `S5_03_03_TTSBuilderQuickAdv/Output/cloud_voice_clone.json` 和 `SessionOutput/tts/cloud_voice_clones.json`。
7. 后端 Analysis V1 runner 已支持 `tts_builder_mode=quick_adv`，对应步骤 `03_03`。
8. 前端运行弹窗已加入 `03_03 高级声音匹配` 选项；音色选择弹窗已加入 `高级匹配` 按钮，可触发 03_03 后台运行。

未在本阶段完成的能力：

1. 独立的 QuickAdv 三步专用页面。
2. 前端“克隆这个声音”按钮和授权确认弹窗。
3. Qwen / ByteDance 全量音色 catalog 生成与统一 preview。
4. LLM prompt planner。
5. 用户候选槽位持久化和 finalize 专用 API。
