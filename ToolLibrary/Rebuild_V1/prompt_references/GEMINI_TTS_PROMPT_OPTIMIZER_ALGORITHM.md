# Gemini TTS 声音匹配与 Prompt 迭代算法文档

## 背景

本次目标是：给定一段参考声音、一个目标画面场景和一段中文口播文本，自动寻找 Gemini TTS 中最接近参考声音的预设 voice，并迭代生成更合适的 TTS prompt，最终输出一个 8 秒 WAV 用于试听和后续视频合成。

本次参考素材：

- 参考声音：`wan27_segment01_audio_8.000s.wav`
- 参考画面：`../../1.png`
- 目标文本：`final_validation_16s/srt_8x8/segment_01_00-08s.srt`
- Gemini 脚本：`gemini_tts_prompt_optimizer.py`
- 最佳结果：`gemini_tts_prompt_optimizer/best_gemini_tts_fit_8s.wav`
- 完整报告：`gemini_tts_prompt_optimizer/optimization_manifest.json`

本次最佳 Gemini 结果：

- model：`gemini-3.1-flash-tts-preview`
- voice：`Aoede`
- score：`0.852438`
- raw duration：`7.8s`
- fit duration：`8.0s`

最佳 prompt：

```text
请按以下声音方向朗读，且只输出正文语音：只朗读正文，不读提示; 年轻中国女性; 生活短视频口播; 近距离自然收音; 语速更快一点，停顿更短; 中高音区，女声明亮但不尖; 清透温暖; 情绪更亲切，像给家人准备东西时顺口说明。
正文：给老公准备的化橘红，润肺膏在家里放了快三礼拜了啊，现在每天早上他都自己拿着喝了啊，特别是一到这个季节啊，
```

## 核心判断

Gemini TTS 不能直接用参考音频做真实声纹克隆。它的有效控制手段主要是：

1. 选择预设 voice。
2. 在 text prompt 中描述语气、节奏、场景和表达方式。
3. 批量生成候选音频。
4. 用声学特征和时长贴合度自动评分。
5. 根据评分差异生成下一轮 prompt。

所以这个算法不是“音频反推唯一提示词”，而是一个搜索过程：

```text
reference audio -> acoustic target
scene/text -> prompt candidates
Gemini TTS -> candidate audios
candidate audios -> acoustic score
top candidates -> prompt refinement
best candidate -> 8s fitted wav
```

## 输入

必需输入：

- `--reference-audio`：参考声音 WAV。
- `--srt` 或 `--text`：要合成的正文。

可选输入：

- `--reference-image`：用于记录场景，不直接参与 Gemini TTS 调用。
- `--voices`：候选 Gemini voice 列表。
- `--rounds`：迭代轮数。
- `--candidates-per-round`：每轮候选数。
- `--target-duration`：最终时长，默认取参考音频时长。

默认候选 voice：

```text
Aoede, Kore, Callirrhoe, Vindemiatrix, Sulafat, Achernar
```

## 输出

脚本会输出：

- `round_01/*.wav`：第一轮候选音频。
- `round_02/*.wav`：第二轮 refinement 候选音频。
- `round_XX/round_report.json`：每轮候选评分。
- `best_prompt.txt`：最佳 prompt。
- `best_gemini_tts_fit_8s.wav`：最终定长音频。
- `optimization_manifest.json`：完整实验报告。

默认不输出替换音频后的视频。只有显式传入 `--mux-video` 时才会额外生成视频。

## 算法流程

### 1. 读取 API Key

从 OpenCrew 数据库读取 TTS 配置：

```sql
SELECT api_key_ciphertext
FROM tool_media_provider_configs
WHERE kind = 'tts'
  AND provider IN ('google', 'gemini')
  AND enabled = TRUE
ORDER BY active DESC
LIMIT 1
```

也支持环境变量：

```text
OPENCREW_TTS_API_KEY
OPENCREW_TTS_PROVIDER=google 或 gemini
```

### 2. 解析正文

如果传入 `--text`，直接使用该文本。

如果传入 `--srt`，会去掉序号和时间轴，只保留字幕正文，并拼接为一段连续口播文本。

本次文本：

```text
给老公准备的化橘红，润肺膏在家里放了快三礼拜了啊，现在每天早上他都自己拿着喝了啊，特别是一到这个季节啊，
```

### 3. 提取参考声音特征

参考音频会统一转成 mono、16kHz，然后提取：

- duration
- active_duration
- active_ratio
- RMS energy
- spectral centroid
- F0 median
- F0 variation in semitones
- voiced_ratio
- MFCC-like timbre vector
- signal_confidence

本次参考音频特征：

```json
{
  "duration": 8.0,
  "active_duration": 7.795,
  "active_ratio": 0.9744,
  "signal_confidence": 0.9208,
  "rms": 0.1927,
  "centroid": 1487.4734,
  "f0_median": 177.7778,
  "f0_variation_semitones": 4.5339,
  "voiced_ratio": 0.8067
}
```

### 4. 第一轮候选生成

第一轮同时搜索 voice 和 prompt。

候选 prompt 类型包括：

- `text_only`：只给正文，不加指令。
- `home_natural`：年轻女性、家庭产品分享、自然口播。
- `soft_bright`：清透、柔和、近距离、稍快。
- `clear_lively`：清晰、明亮、生活化。
- `breathy_warm`：温暖偏亮、轻微气声。
- `urgent_daily`：年轻妻子/妈妈生活分享口吻。

为了避免只测到第一个 voice，候选生成顺序应横向扫 voice：

```text
style_1 voice_1
style_1 voice_2
style_1 voice_3
...
style_2 voice_1
style_2 voice_2
...
```

### 5. 调用 Gemini TTS

Gemini TTS 请求结构：

```json
{
  "contents": [
    {
      "parts": [
        {
          "text": "prompt text"
        }
      ]
    }
  ],
  "generationConfig": {
    "responseModalities": ["AUDIO"],
    "speechConfig": {
      "voiceConfig": {
        "prebuiltVoiceConfig": {
          "voiceName": "Aoede"
        }
      }
    }
  }
}
```

Gemini 返回的音频可能是 inline PCM，需要包装成 WAV：

```text
PCM bytes -> RIFF/WAVE header -> .wav
```

### 6. 候选评分

每个候选音频都提取同样的声学特征，并计算总分：

```text
score =
  0.30 * timbre
+ 0.23 * pitch
+ 0.12 * brightness
+ 0.07 * energy
+ 0.12 * rhythm
+ 0.11 * duration
+ 0.05 * expressiveness
```

各子项含义：

- `timbre`：MFCC-like 向量余弦相似度。
- `pitch`：F0 median 和 F0 variation 的综合相似度。
- `brightness`：spectral centroid 接近程度。
- `energy`：RMS 接近程度。
- `rhythm`：active_ratio 接近程度。
- `duration`：raw duration 与 target duration 的接近程度。
- `expressiveness`：F0 variation 接近程度。

额外惩罚：

```text
if candidate_duration > target_duration * 1.55:
    score *= 0.55

if candidate_duration < target_duration * 0.55:
    score *= 0.70
```

这个惩罚用于处理 Gemini 把提示词读出来、导致音频过长的情况。

### 7. 第二轮 prompt refinement

取上一轮 Top 2 候选，根据声学差异自动调整 prompt。

调整规则：

```text
if generated_duration > target * 1.15:
    add "语速更快一点，停顿更短"

if generated_duration < target * 0.88:
    add "语速稍慢一点，句子更从容"

if generated_f0 < reference_f0 - 25:
    add "音调略高更年轻"

if generated_f0 > reference_f0 + 25:
    add "音调略低更稳"

if generated_centroid > reference_centroid + 450:
    add "音色更柔和，减少刺亮感"

if generated_centroid < reference_centroid - 450:
    add "音色更清亮，口腔共鸣更靠前"

if generated_active_ratio < reference_active_ratio - 0.08:
    add "减少长停顿"
```

第二轮生成 3 个变体：

- 更亲切，像给家人准备东西时顺口说明。
- 更像手机自拍视频里的真实说话。
- 保持紧凑节奏，减少播音腔。

### 8. 最佳音频定长

最佳 raw audio 不一定正好 8 秒，所以需要后处理：

1. 用 FFmpeg `atempo` 将 raw audio 变速到 target duration。
2. loudnorm 到可听音量。
3. 转成 48kHz stereo WAV。
4. 最后用 `soundfile` 做采样级 pad/trim，保证精确 8 秒。

最终保证：

```text
ffprobe duration = 8.000000
```

## 本次 Top 结果

```text
0.852438  Aoede  r2_Aoede_r1_Aoede_text_only_v2  raw=7.8s
0.844721  Aoede  r2_Aoede_r1_Aoede_text_only_v1  raw=7.68s
0.826232  Aoede  r2_Aoede_r1_Aoede_text_only_v3  raw=7.76s
0.797950  Aoede  r2_Aoede_r1_Aoede_home_natural_v2  raw=9.52s
0.797120  Aoede  r1_Aoede_text_only  raw=9.96s
0.796484  Aoede  r1_Aoede_home_natural  raw=8.92s
0.772853  Callirrhoe  r1_Callirrhoe_home_natural  raw=9.08s
```

结论：本段参考声音下，`Aoede` 明显优于其他 Gemini voice。

## 本次踩坑记录

### 1. Prompt 不是稳定的控制通道

Gemini TTS 的 `contents.parts.text` 同时承担“正文”和“指令”的角色。写得太像说明书时，模型可能把提示词也读出来。

处理方式：

- 候选中保留 `text_only` baseline。
- prompt 中强约束“只朗读正文，不读提示”。
- 用 raw duration 惩罚过长候选。
- 最终还是依靠自动评分选结果，而不是相信 prompt 字面效果。

### 2. 一开始候选生成顺序只测到了第一个 voice

早期实现是：

```text
for voice in voices:
  for style in styles:
```

当 `candidates_per_round` 很小时，会只生成第一个 voice 的多个 style，导致 voice 搜索不公平。

修复为：

```text
for style in styles:
  for voice in voices:
```

这样第一轮会先横向覆盖多个 voice。

### 3. 8 秒定长不能只信 FFmpeg atempo

用 `atempo + apad + atrim` 后，ffprobe 仍然出现过 `7.984083s`。

原因是音频滤镜链在采样边界上可能有尾差。

最终修复：

```text
FFmpeg 变速/响度处理
-> soundfile 读取 WAV
-> 按 target_duration * sample_rate 精确 pad/trim
-> 写回 PCM_16 WAV
```

### 4. macOS Python py_compile 写全局缓存失败

第一次跑：

```text
python3 -m py_compile ...
```

报错：

```text
PermissionError: /Users/duheng/Library/Caches/com.apple.python/...
```

解决：

```text
PYTHONPYCACHEPREFIX=/private/tmp/opencrew_pycache python3 -m py_compile ...
```

### 5. sandbox 下无法连接本机 PostgreSQL

普通 sandbox 命令连接 `127.0.0.1:5432` 时失败：

```text
Operation not permitted
```

解决：

- 需要 escalated run。
- 脚本仍然保持从数据库读取 key，不硬编码 API key。

### 6. 项目根目录不能用固定 parents 层级

最初使用固定：

```python
PROJECT_ROOT = SCRIPT_DIR.parents[6]
```

导致找不到 bundled `ffmpeg`，最后报：

```text
No such file or directory: 'ffmpeg'
```

修复：

```python
def find_project_root():
    for candidate in [SCRIPT_DIR, *SCRIPT_DIR.parents]:
        if (candidate / "OpenCrew").exists() and (candidate / "consistency").exists():
            return candidate
```

### 7. 已生成候选需要缓存复用

第一次跑到最后一步失败时，候选音频其实已经生成成功。如果重跑重新请求 Gemini，会浪费时间和费用。

修复：

```python
if raw_path.exists() and media_duration(raw_path) > 0:
    use cached wav
else:
    call Gemini TTS
```

### 8. 不要默认生成替换音频后的视频

用户当前关心的是“提示词和声音效果”，不是视频合成。

修复：

- 默认只输出 WAV。
- 只有传 `--mux-video` 时才生成视频。

### 9. Gemini、Qwen instruct、xAI 的控制能力不同

本次对比结果：

```text
Gemini Aoede          0.852438
Qwen instruct Seren   0.829958
xAI Mei               0.763677
```

差异：

- Gemini：prompt 和 voice 都可参与搜索，但 prompt 可能被读出，需要 duration 惩罚。
- Qwen instruct：有独立 `instructions` 字段，prompt 控制更干净。
- xAI：当前 OpenCrew 集成主要靠 `voice_id`，缺少独立 prompt/instruction 字段。

## 推荐复跑命令

```bash
python3 gemini_tts_prompt_optimizer.py \
  --rounds 2 \
  --candidates-per-round 12 \
  --voices Aoede,Kore,Callirrhoe,Vindemiatrix,Sulafat,Achernar
```

如果只想快速验证：

```bash
python3 gemini_tts_prompt_optimizer.py \
  --rounds 1 \
  --candidates-per-round 6
```

如果要额外合成到视频：

```bash
python3 gemini_tts_prompt_optimizer.py \
  --rounds 2 \
  --candidates-per-round 12 \
  --mux-video
```

## 后续优化方向

1. 加 ASR 检查，判断候选是否真的只读了正文。
2. 加人工评分入口，把“听感更像”写回 reward。
3. 为每个 voice 建 preview embedding，先做 voice 粗筛，再跑昂贵 TTS。
4. 把 scene image 送入视觉模型生成 scene profile，而不是手写场景描述。
5. 对 Gemini prompt 做模板库，区分“正文型 prompt”和“指令型 prompt”的风险。
6. 对短视频口播单独训练一个权重配置，提高语速、停顿、句尾语气的权重。
