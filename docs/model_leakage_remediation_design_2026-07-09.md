# 模型泄露治理 —— 实施设计文档

日期：2026-07-09
配套：审计报告 `docs/model_leakage_audit_2026-07-09.md`（What / Where）。本文回答 **How**——每条修复的具体改法、代码骨架、落点、验证、上线顺序与回滚。
状态：**设计稿，尚未改代码**。代码骨架为示意（标 `# 示意`），落地时以实际签名为准。

---

## 0. 设计总纲：单一出口边界（Egress Boundary）

问题的根因是"真名边界"散落在前后端各处、逐点手动掩码有遗漏。治理的统一原则：

> **真实 provider/model 只允许存在于两个地方：后端进程内存、后端出站给供应商的请求。**
> 任何**可能到达客户**的字节——前端 bundle、下发 JSON、生成文件、错误消息、事件流——**只允许出现别名或脱敏值**。

据此每通道策略：

| 通道 | 策略 | 一句话 |
|---|---|---|
| A 前端 bundle | **移除数据，非隐藏 UI** | 真名/价目/映射全部撤出客户 bundle，下沉后端；前端只拿别名 |
| B 文件元数据 | **出口统一清洗** | 落盘即过一道 strip；serve/zip 排除内部 JSON |
| C 后端响应 | **补齐掩码 + 统一异常包装** | 复用已有 `model_policy`，堵 debug 旁路，错误不透传 |
| D 配置 | **深度防御加固** | dev-server/openapi/CORS/auth 默认值 |

一个贯穿性的产物：新增 `backend/opcrew_backend/media_sanitize.py`（通道 B 用）与一个 CI 防回归守卫（第 5 节）。

---

## 1. 通道 B —— 生成文件元数据/水印清洗（P0，先做）

现状确认（已读代码）：
- 图片落盘：`koubo/koubo_storyboard/clean_image_services.py:264` `output_path.write_bytes(image_bytes)`（原始供应商字节）；升库拷贝 `:320` `shutil.copyfile`；侧车/清单在 `:299`（manifest.json）与 `:322-340`（`*.json` sidecar，含 `provider`/`model`）。另有 `asset_routes.py:1955-1956` 同类 write_bytes。
- 视频落盘：`asset_video_generation_services.py:592-600` `download_video_binary` 用 `shutil.copyfileobj(res, handle)` 原始流拷贝，无 `-map_metadata`。
- serve：`routes/sessions.py:797` `/raw/{path}` FileResponse 直通；`:786` `files.zip` 打包；`:771/781` 目录列表。
- provider/model 已另存 DB：`record_storyboard_usage`（`clean_image_services.py:265`）已写本地用量，**故文件内的 provider/model 是冗余的，可安全去除**。

### B0. 新增统一清洗工具 `media_sanitize.py`

**当前实现（2026-07-09 B0）**：已新增 `backend/opcrew_backend/services/media_sanitize.py`。图片通过 Pillow 重新编码并只保留 ICC；视频/音频通过 ffmpeg `-map_metadata -1 -map_chapters -1 -c copy` 清容器元数据，音频在容器/扩展名不一致时有 ffmpeg 转封装/转码 fallback。ffmpeg/Pillow 缺失或清理失败均 fail-closed，调用方删除未清理输出并返回 502。

```python
# backend/opcrew_backend/media_sanitize.py  # 示意
from __future__ import annotations
import io, subprocess, tempfile, shutil, os
from pathlib import Path
from PIL import Image, ImageOps

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".m4v"}
# 修正(同事复审 #2)：直接复用应用的 constants.AUDIO_EXTS,勿另列子集
from .koubo.koubo_storyboard.constants import AUDIO_EXTS as _AUDIO_EXTS
# = {.wav .m4a .mp3 .aac .ogg .oga .flac .opus .aiff .aif .caf .weba .wma}

def strip_image_bytes(data: bytes) -> bytes:
    """重编码去除 EXIF/XMP/tEXt/iTXt/C2PA(JUMBF) 等身份类元数据；像素不变。
    保留 ICC 色彩配置(非供应商指纹)以避免客户看到色差(体验修正)。
    注意：Google SynthID 是像素水印，此步无法去除（见 §1.4）。"""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)          # 落地 orientation，避免丢 EXIF 后转向错误
        icc = im.info.get("icc_profile")          # ← 保留色彩,防止色差
        fmt = (im.format or "PNG").upper()
        out = io.BytesIO()
        if fmt in ("JPEG", "JPG"):
            im.convert("RGB").save(out, format="JPEG", quality=95, optimize=True, icc_profile=icc)
        elif fmt == "WEBP":
            im.save(out, format="WEBP", quality=95, icc_profile=icc)
        else:
            im.save(out, format="PNG", optimize=True, icc_profile=icc)  # 只丢 pnginfo/text/C2PA,留 ICC
        return out.getvalue()

def _strip_container(path: Path, faststart: bool) -> None:
    """就地清空容器级+流级 metadata；-c copy 无损、快。ffmpeg 缺失则抛错（fail-closed）。
    注意：临时文件必须保留原扩展名，否则 ffmpeg 无法从 .tmp 推断 muxer。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found; media metadata cannot be stripped")
    tmp = path.with_name(path.stem + ".clean" + path.suffix)   # ← 保留原扩展名(.mp4/.mp3…)
    cmd = [ffmpeg, "-y", "-i", str(path),
           "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1", "-c", "copy"]
    if faststart:
        cmd += ["-movflags", "+faststart"]                      # 仅 mp4/mov 加，音频不加
    subprocess.run(cmd + [str(tmp)], check=True, capture_output=True)
    os.replace(tmp, path)

def strip_video_file(path: Path) -> None:
    _strip_container(path, faststart=True)

def strip_audio_file(path: Path) -> None:
    _strip_container(path, faststart=False)                     # 音频独立命令，无 +faststart

def sanitize_file_in_place(path: Path) -> None:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        path.write_bytes(strip_image_bytes(path.read_bytes()))
    elif ext in _VIDEO_EXTS:
        strip_video_file(path)
    elif ext in _AUDIO_EXTS:
        strip_audio_file(path)
```

要点：
- 图片**重编码**是刻意的——只有重编码能保证剥掉未知 chunk（C2PA 以私有 chunk/APP 段存储）。PNG 无损；JPEG q95 视觉无损。
- 视频 `-c copy` 不重编码、无损、毫秒级，仅重写容器丢 tag。`-map_metadata:s -1` 连流级 `encoder` tag 一并清（Veo 的 `encoder=Google` 就在这里）。
- **修正（同事复审 #5）**：临时文件保留原扩展名（`.mp4`/`.mp3`），不能用 `.tmp`——否则 ffmpeg 推断不出 muxer 直接失败；`+faststart` 只对 mp4/mov 加，音频走独立 `strip_audio_file`。
- **fail-closed**：ffmpeg 缺失时抛错而非静默放行（宁可生成失败也不泄露）。仓库 rebuild 链已依赖 ffmpeg，本机有。

### B1. 图片 sink 接入

**当前实现（2026-07-09 B1）**：新生成图片已在落盘 sink 清理，而不是改 `generate_image_bytes()` 的返回契约：
- `clean_image_services.py`：clean-image provider 输出、升库、promote consistency 均走 `write_sanitized_image_bytes()` / `sanitize_image_file()`。
- `asset_routes.py`：asset-library agent image 输出走 `write_sanitized_image_bytes()`。
- `host_product_services.py`：host/product builder image 输出走 `write_sanitized_image_bytes()`。
- `asset_video_generation_services.py`：xAI video portrait reframe 临时图走 `write_sanitized_image_bytes()`。

`clean_image_services.py:262-264` 改为：
```python
image_bytes = sc.generate_image_bytes(...)
image_bytes = strip_image_bytes(image_bytes)   # ← 新增
generation_dir.mkdir(parents=True, exist_ok=True)
output_path.write_bytes(image_bytes)
```
同样落点：`asset_routes.py:1955-1956`（图片 write_bytes）、`clean_image_services.py:320` 升库 `shutil.copyfile` 后补一次 `sanitize_file_in_place(output_path)`（源已清则冗余但廉价，可只在源未清路径加）。
**最稳做法**：在 `sc.generate_image_bytes` 的**唯一返回处**（`provider_services.py:542-581`）内联 strip，则所有下游 write 自动干净——**一处堵死**，优先此方案。

### B2. 视频/音频 sink 接入

**当前实现（2026-07-09 B2）**：
- `asset_video_generation_services.py::download_video_binary()` 下载成功后立即 `sanitize_video_output()`；Wan R2V / OpenRouter / Chanjing HappyHorse 这类 Analysis_V1 helper 内部自行下载的路径，在 helper 返回后由 koubo 聚合层统一清理。
- `asset_digital_human_services.py::_download_to_path()` 下载 HeyGen digital-human/video-agent 视频后立即 `sanitize_video_file_metadata()`。
- `provider_services.py::download_binary()` 覆盖 TTS URL 下载音频；`media_tts_provider_services.py::write_sanitized_audio_bytes()` 覆盖 Google/xAI/ByteDance/CosyVoice/Qwen 直接返回音频字节的路径。

`download_video_binary`（`asset_video_generation_services.py:600`）成功写盘后：
```python
with output_path.open("wb") as handle:
    shutil.copyfileobj(res, handle)
strip_video_file(output_path)   # ← 新增：落盘即清
return
```
音频同理接 `provider_services.py:584-594` `download_binary`。注意库内已有的 ffmpeg reframe/normalize 步骤**保持不变**（它们顺带清了 tag 属侥幸，不可依赖，故独立加 strip）。

### B3. 侧车/清单 JSON

两层处理（belt & suspenders）：
1. **写入层去字段**（`clean_image_services.py:322-340` sidecar、`:275-299` manifest、`asset_routes.py` 同类）：删除 `provider`/`model` 键（DB 已有用量记录，不丢账）。若内部调试需要，改存独立的 **非 serve 目录**或仅存 DB。
2. **出口层排除——落点必须在 `SessionFileService`，不是 `routes/sessions.py`（同事复审 #4，关键修正）**：
   普通下载 `/api/sessions/{id}/files/{file_id}`、share 下载、zip、raw **全部**走 `SessionFileService.resolve_download` / `visible_file_rows` / `zip_entries`，它们统一用 `is_sensitive_path()` + `default_file_visibility()` 判定可下载性（`services/session_files.py:49,58,101,114,120`）。JSON 默认被判为 `("public","normal",1)` = **可下载**。
   - 正确改法：把媒体侧车/清单**加入 `SENSITIVE_PARTS`/`SENSITIVE_NAMES` 或 `is_sensitive_path()` 判定**（如 `manifest.json`、`assets/{images,videos}/*.json`、或统一内部前缀 `_internal_*.json`）。一处生效，`files/{file_id}`、share、zip、raw **全部覆盖**。
   - 若落在 `routes/sessions.py` 的 list/zip/raw，会**漏掉** `files/{file_id}` 和 share 下载（它们直接调 `resolve_download`）——这是原设计的 bug，已修正。
   - 过滤要精确：只挡"媒体侧车/清单"，放行前端确需的 `plan.json` 等（用目录+命名白名单）。

### B4. 存量文件批量清洗（一次性脚本）

对已生成的约 167 图 / 81 视频 + 音频，覆盖 `~/.opencrew/sessions/*/workspace/` 下的 **`SessionOutput/storyboard/assets/{images,videos,audios}`、`SessionOutput/storyboard/assets/history` 与 `SessionScratch/CleanImageGenerations`**（clean-image 生成物在 scratch 下，不在 assets/，五轮 #2；history 目录也可能经 raw/zip 暴露）。

**当前实现（2026-07-09 B4-stock）**：已新增 `backend/scripts/sanitize_existing_assets.py`，复用 `backend/opcrew_backend/services/media_sanitize.py::sanitize_file_in_place()`：
- 默认 dry-run；`--write`/`--apply` 才改字节。
- 只扫描白名单媒体输出目录，不全库 `rglob`。
- 只处理媒体文件扩展名（image/video/audio），不改任意 JSON；媒体侧车/manifest 的客户出口由 B3 `SessionFileService` 排除负责。
- 支持 `--workspace`、`--session-id`、`--min-age-seconds`、`--snapshot-dir`、`--json`、`--fail-on-candidates`。
- `--snapshot-dir` 会在写入前按 session/相对路径复制原文件；生产仍建议先对 assets 做 tar 快照，脚本快照作为第二层回滚材料。

**生产注意**：先 dry-run 出清单；执行前对 assets 目录 `tar` 快照（可回滚）；分 session 灰度；避开在跑任务（挑低峰，或加 `--min-age-seconds 600` 跳过近 10 分钟改动的文件）。

### B5. digital-human / voice-clone 文件名与 asset 元数据去品牌（同事复审 #1）

审计初版"文件名/URL 干净"只对 image/video 成立。**digital-human 路径的文件名直接嵌 `heygen`**，且 asset source/label/manifest 也带真名（`asset_digital_human_services.py`）：
- `:847` `output_name = f"{batch}_heygen_video_agent_{...}.mp4"`、`:1025` `f"{batch}_heygen_digital_human_{index}_{...}.mp4"` — **文件名含 `heygen`**，经 `/raw/{path}` URL 与 files.zip 直接暴露。
- `:889`/`:1117` `_asset_payload(rel, "heygen_video_agent"/"heygen_digital_human", "HeyGen ... video", {origin:...})` — asset `source`/`label`/`origin` 含真名，随列表/详情返回体下发。
- 同类 sidecar/manifest JSON 含 provider/model。

整改（B/C/A 三面都要动）：
- **文件名**：改中性方案（对齐其它资产的 `{ts}_agent_generated_<hex>.mp4`），去掉 `heygen`/`avatar_iv` 等词。
- **asset source/label/origin**：改中性标识（如 `source:"digital_human"`、`label:"数字人视频"`），真名不进返回体；或经 A3 别名映射。
- **sidecar/manifest**：同 B3，写入层去 provider/model + 出口层（`SessionFileService`）排除。
- **事件 payload**：同通道 C，digital-human 生成事件里的 provider/model/上游 body 一并掩码/包装。
- **agent 记录返回体（同事复审 #1 五轮）**：客户路由 `digital-human/agents/{provider_session_id}`（GET，`asset_digital_human_routes.py:178`）与 `/stop`（:190）返回 `_write_video_agent_record()`（`asset_digital_human_services.py:781`），含 `provider:"heygen"`、`model`、`generation_model:"video_agent"`、`provider_result`、`agent_snapshot`。→ 返回体**白名单化或按 role 掩码**（USER 去 provider/model/provider_result/snapshot）；该记录若落盘 JSON，同 B3 出口排除。
- **clean-image 路径（同事复审 #2 五轮）**：生成物在 `SessionScratch/CleanImageGenerations`（`constants.py:14`，**不在 assets/ 下**），`clean_image_services.py:264` 原样写图片字节、`:282` manifest 写真名。客户可达：`/clean-image/generations`（:52，列 manifest 真名）、`/clean-image/{id}/image`（:80，预览图带元数据）、`/clean-image/generate`（:45）、promote 系列。→ **live**：图片清洗走 B1 的 `generate_image_bytes` 唯一返回处即覆盖此路径；manifest/列表/generate/promote **响应按 role 掩码**（通道 C）；**存量**：B4 已含 `CLEAN_IMAGE_REL`（上方脚本）。

**当前状态（2026-07-09）**：新生成 digital-human/video-agent 已改为中性 `digital_human*` 文件名与 asset `source/label`；`origin` 仍保留内部 provider/model，靠 C0 + B3 文件出口排除挡住客户出口。存量 `*_heygen_*` 文件仍需下方单独迁移脚本处理。注意：存量 rename 不只是防泄露，也是防功能损坏——旧 path 若经 C0 scrub 会变成 `*_ [model] _*.mp4` 类死链，而 `/raw/` 下载路径本身不脱敏，最终 404。

- **存量（拆两个脚本，同事复审 #1）**：
  - `backend/scripts/sanitize_existing_assets.py`（B4，**当前已实现，待生产执行**）：只做**元数据清洗**，覆盖 images/videos/**audios**、assets/history 与 CleanImageGenerations，不改文件名。
  - `backend/scripts/rename_digital_human_assets.py`（**当前已实现，待生产执行**）：把存量 active videos + assets/history 下的 `*_heygen_*` 文件**重命名**为中性名，并**同步更新** manifest（`koubo_storyboard_assets.json`）、DB `session_files.path`、sidecar/工作区 JSON 里的路径引用，避免断链。默认 dry-run；`--write` 才落盘。风险较高（改文件名+引用），单独 dry-run + 快照 + 灰度，与 B4 分开跑。

### 1.4 SynthID —— 须业主决策（代码不可解）

Google（Gemini 图片 / Veo 视频）嵌入 **SynthID 像素级隐形水印**，元数据清洗**去不掉**，Google 的 SynthID Detector 可判定"由 Google AI 生成"。选项（业主决策，非技术）：
1. 接受残留风险（多数客户不会主动验 SynthID）；
2. 对高敏客户/高敏场景改用无隐形水印供应商产图产视频；
3. 二次重编码/轻度处理削弱——**效果有限且损画质，不推荐依赖**。
建议：默认 (1)，在合规说明里记录，给"高敏客户"开关走 (2)。

### 1.5 验证

- 单测：对已知带 C2PA 的样本跑 `strip_image_bytes`，断言输出 `Image.open().info` **无 exif/xmp/text(tEXt/iTXt)/C2PA(JUMBF)**、`strings` 无 `c2pa|contentauth|openai|google|synthid` 等；**ICC 允许保留**（色彩，非供应商指纹）。统一口径：**允许 ICC，禁止 EXIF/XMP/tEXt/iTXt/C2PA/JUMBF 及供应商字符串**。
- 视频：`ffprobe -show_format -show_streams` 断言无 `encoder`/`comment` tag。
- 端到端：真实生成一张 Gemini 图 + 一段 Veo 视频，下载后 `exiftool`/`ffprobe`/`strings` 复检。
- 侧车：`curl /api/session-tasks/{id}/files.zip` 解包 grep 无 `provider|model`。
- **当前合同（2026-07-09 B0/B1/B2）**：`backend/tests/contracts/test_media_sanitize_contract.py` 生成带敏感 PNG tEXt、MP4 format tags、WAV format tags 的真实媒体，断言清理后敏感 tag 消失且媒体仍可解析；相关写入路径由 clean-image、asset-video provider retry、CosyVoice retry、digital-human 合同覆盖。
- **当前合同（2026-07-09 B4-stock）**：`backend/tests/contracts/test_sanitize_existing_assets_contract.py` 覆盖存量清洗脚本 dry-run/write：只扫白名单媒体目录，跳过非目标目录，写入后图片文本元数据、视频 tag、音频 tag 消失，且 `--snapshot-dir` 先复制原文件。
- **当前合同（2026-07-09 B5-new/C0）**：`backend/tests/contracts/test_koubo_asset_digital_human_contract.py` 断言新生成 digital-human 与 video-agent asset 经过 `sanitize_customer_payload(USER)` 后，`asset.path` 保持中性原值、不含 `[model]`/`heygen`，且仍指向磁盘真实文件，防止 C0 把下载 URL scrub 成死链。
- **当前合同（2026-07-09 B5-stock）**：`backend/tests/contracts/test_rename_digital_human_assets_contract.py` 覆盖存量 rename dry-run/write：active videos 与 assets/history 下的视频及同名 sidecar 重命名、manifest/source/label/filename 去品牌、工作区 JSON 路径替换、DB `session_files.path` 同步，以及目标文件冲突时阻断写入。

---

## 2. 通道 A —— 前端 bundle 去真名（P0，先做）

**关键认知**：价目表 `MEDIA_PRICE_POINTS`/`LIPSYNC_PRICE_COMPARISON`（`lib/meteringFormat.js:5-81`）、`pricing.ts`、`ModelPresetCards` 的 `MODEL_PRESETS` 都是**编译进 bundle 的静态常量**。消费点：价目表仅 `useMediaSettingsController.jsx:81,92`；`ModelPresetCards` 用于 7+ 客户模块。
→ **只把 UI 藏进 `canViewMetering()`（admin gate）无效**——字符串仍在每个客户下载的 JS 里。必须让真名**不进入客户 bundle**。

> **通道 A 的完整下沉设计见 `docs/model_leakage_channel_a_design_2026-07-09.md`**（含运行时 config 端点掩码、别名注册表、逐文件清单、迁移顺序）。本节为概要。
> **2026-07-09 决策已并入该文**：① 范围**全覆盖** image/video/**TTS/voice-clone/digital-human**（不止 image/video；TTS config 端点 `asset_routes.py:2670` 与 TTS 请求 `AnalysisV1TTSBuilder.jsx:1498`/`AnalysisV1Module.jsx:1533` 同样漏真名，须一并治理）；② 别名**沿用现有 Max 系**（可逆性残留风险已知接受）；③ 迁移**USER 首版即 alias-only，无泄露窗口**，双发仅 admin/flag。

### A1. 价目表下沉后端（改数据来源）

- 后端新增 `GET /api/media-pricing`（放入 `ADMIN_ONLY_PATH_PREFIXES`，见 `auth.py:27`，非 admin 403），返回 `MEDIA_PRICE_POINTS` 内容（真名仅 admin 可见，用于运营）。
- 客户可见的"费用/用量"页：只展示**按别名聚合**或**总额**，真名/单条价目不下发。若客户不需要看单模型价，直接删除前端常量与其 UI。
- 前端 `meteringFormat.js` 删除 `MEDIA_PRICE_POINTS`/`LIPSYNC_PRICE_COMPARISON` 两个 export；`useMediaSettingsController.jsx:81,92` 改为按需向 `/api/media-pricing` 拉取（仅 admin 视图渲染）。`pricing.ts`（ModelConfig 树）同样删除重复表。

### A2. `ModelPresetCards` 去真名（别名驱动）

- `components/ModelPresetCards.jsx:4-19`：`MODEL_PRESETS` 移除 `providerValues`/`modelValues` 里的真名（`openai/gpt-5.5`、`deepseek-v4-flash-free`），只保留 `presetKey`（`max`/`flash`）+ 别名 `label`。
- 匹配逻辑 `:39-40` 依赖的 `provider_label_real`/`model_label_real` **停止下发**：后端返回给这些 surface 的 prompt-models 已经过 `mask_prompt_models_for_role`（`model_policy.py:334`，masked item 只含 alias 字段），确保客户拿到的 item 无 `*_real`。前端匹配改用 alias 字段（`providerID`/`modelID` 已是别名）。
- 复核 `_masked_item`（`model_policy.py:311-322`）确实不含 `*_real`（已确认）——所以只要前端不再引用 `*_real` 字段即可；`DEFAULT_USER_MODEL_POLICY` 里的 `*_real` 是后端内部映射，不下发，保留。

### A3. 扩展 `model_policy` 覆盖"媒体模型"面（结构性补强）

现状：`model_policy.py` 的 surfaces 只覆盖**文本/prompt 模型**（Max/Flash）与 TTS timing，**媒体生成模型（image/video/lipsync）完全不在 policy 内**——这正是通道 A 价目表和通道 C 口播漏掉的根因。

- 新增 surface 常量：`SURFACE_MEDIA_IMAGE` / `VIDEO` / `LIPSYNC` / **`TTS` / `VOICE_CLONE` / `DIGITAL_HUMAN`**（全覆盖，同事复审 #4；与 channel_a §2.1 一致），在 `DEFAULT_USER_MODEL_POLICY` 里给出媒体别名映射（对齐已有的 `Max {Provider}{Mode}{Ver}` 别名体系，见 memory `max-video-alias-naming`：SI2/SR2/WR2.7/HR1.0 等）。
- 媒体模型选择/展示接口（媒体设置抽屉、图库能力表）统一走 `mask_model_fields_for_role`/`mask_prompt_models_for_role`，客户只见别名。
- 前端 `videoModelCapabilities.js:144-160`、`useMediaSettingsController.jsx:167-169` 的**客户端别名派生逻辑删除**，改由后端下发别名。

### A4. 清除 UI 文案里的供应商真名

改为中性别名（逐文件）：
- `ModelConfig/frontend/src/ModelConfigModule.tsx:69-72`、`digital-human/DigitalHumanConfigModal.tsx:8`：`"HeyGen 数字人设置"`→`"数字人设置"`；`"Sync.so Lip Sync…"`→`"对口型设置"`。
- `frontend/src/shell/SettingsDrawers.jsx:260`、`ModelConfig/.../MediaConfigModalBase.tsx:237`：`"Drag OpenAI, Gemini, or xAI…"`→别名描述。
- `PromptBuilderModal.jsx:78` `"切换到 Grok"`、`AgentPanel.jsx:1198` 错误文案 → 别名。
- `XaiVoiceGuide.tsx:26`：删除硬编码 xAI console URL（含真实 team UUID `b6d215fa-…`）与 `docs.x.ai` 链接；voice guide 下沉后端或去品牌化。
- `googleTtsScenarioGuide.ts`、各 `*_prompt`/`model_notes`（`OCRebuildModule.jsx`）文档链接（`developers.openai.com`/`docs.x.ai`/`ai.google.dev`）→ 移除或改内部代理。
- 各模块默认常量（`OpenClipModule.jsx:12-13`、`AnalysisV1Module.jsx:68-69`、`OCRebuildModule.jsx:251-257`、`TalkingHeadV1Module.jsx:168-169` 请求体默认 `wan/wan2.7-r2v-…`）：默认值改别名/空，真名解析交后端。

### A5. 请求参数

前端向后端发的 `provider`/`model`/`model_id` 等字段改为发**别名/presetKey**，后端 `resolve_*_for_role`（`model_policy.py:381`）解析为真名。抓包只见别名。重点先改硬编码真名进请求体的 `TalkingHeadV1Module.jsx:168-169`。

### A6. 重构建 + 验证（必做）

- 改完 `cd frontend && npm run build`（及 ModelConfig 树），重生成 `dist/`。**现有 `frontend/dist/` 已含真名，是当前现网泄露源，必须覆盖。**
- 验证：`grep -riE 'openai|gemini|sora|veo-|grok|kling|wan2|gpt-image|provider_label_real' frontend/dist/assets/` 应**零命中**（或仅剩不可避免的中性词）。CI 化见第 5 节。

---

## 3. 通道 C —— 后端响应补齐（P1）

### C0. 集中式出口掩码（默认拦截）—— 主机制，非逐路由（五轮复审的结构性结论）

**为什么改路线**：系统枚举发现 koubo-storyboard 下有 **~95 个客户可达路由**、**11 个服务文件**（`agent_chat_services`、`asset_digital_human_services`、`asset_search_services`、`asset_video_generation_services`、`clean_image_services`、`host_product_services`、`media_tts_provider_services`、`provider_services`、`storyboard_plan_services`、`tool_runner_services`、`tts_workflow_services`）会在响应/记录里吐 `provider`/`model`/`heygen`。**逐路由手动掩码必然漏**——连续五轮 review 每轮都能再找出一个漏点（tts-model-config、digital-human 文件名、agents 记录、clean-image…）就是铁证。

**当前实现落点（2026-07-09）**：`backend/opcrew_backend/model_leakage_guard.py` + `app.py` 已接入 C0；`services/session_files.py` 已排除媒体 sidecar/manifest 下载与 zip；`asset_digital_human_services.py` 已对新生成 digital-human/video-agent 文件名与 asset source/label 去品牌；`scripts/check_model_leakage_guard.py` 已接入 CI；`scripts/smoke_model_leakage_live.py` 提供现网/本机只读 smoke。

**方案**：对**所有客户可达的 JSON/SSE 出口**，加一层集中式出口掩码，**default-deny**：新增端点自动被掩，无需记得逐个改。以下 **5 条硬约束**必须写进实现，否则 C0 兜不住（六轮复审）：

#### 硬约束 1 — 覆盖范围 = 所有客户 `/api/*` JSON/SSE，非单一前缀
不能只限 `/api/koubo-storyboard/tasks/`。通道 C 泄露跨多个前缀：`/api/koubo-tasks/{id}`、`/api/talking-head-v1/tasks/{id}`、`/api/sessions/{id}/events`、`/api/ocrebuild/*` 都在审计里。
- 规则：**拦截所有 `/api/*` 的 JSON/SSE 响应**，再**排除**：`ADMIN_ONLY_PATH_PREFIXES`（admin 端点）、`/api/auth/*`、`/api/health`、二进制/文件下载（`/raw`、`files.zip`、`*/image`、`*/preview` 等 `FileResponse`/`StreamingResponse(非 SSE)`）。
- 二进制不走 JSON 掩码，其泄露由通道 B（元数据清洗）+ SessionFileService（侧车排除）负责——两条线分工，别互相漏。

#### 硬约束 2 — 角色 fail-closed，且在**响应阶段**读 role（不在入口缓存）
`request_role()` 拿不到 `request.state.opencrew_auth_role` 时**默认 ADMIN**（`model_policy.py:193`）——对出口掩码是**致命默认**：若 C0 取不到 role，会把非 admin 当 admin 放行真名。
- **读取时机是关键（同事复审八轮）**：`add_middleware` 后加者为外层，C0 的 `__call__` **入口（请求阶段）可能先于 auth 执行**，此刻 `scope` 里还没有 role。**绝不能在入口缓存 role**。必须在 **`http.response.start` / `http.response.body` 处理时**、即 inner app（含 auth）跑完后，从 **`scope["state"]["opencrew_auth_role"]`** 读取。
- **fail-closed**：`role = scope.get("state",{}).get("opencrew_auth_role") or USER`——**缺失按 USER**。
- **绝不绕回 `request_role()`**：它的 ADMIN 回退会把缺失 role 变成泄露。
- 两种最常见误实现（都要避免）：① 入口缓存 role → admin 访问客户路由也被当 USER 误掩（功能坏）；② 图省事调 `request_role()` → 缺失 role 当 admin → 泄露。
- （并建议一并把 `request_role` 默认改 USER，§4 M3，双保险。）

#### 硬约束 3 — 覆盖成功**与错误**响应，且在压缩前处理（含错误路径，六→七轮）
两个要点：**(a) 必须覆盖错误 JSON**、**(b) 必须在 gzip 前掩码**。
- **(a) 错误路径不能漏（同事复审七轮 #1）**：`HTTPException(detail=…)`、`RequestValidationError`、兜底 `@app.exception_handler(Exception)`（`app.py:131` 返回 `{"detail": str(exc)}`）产生的 JSON **不经过** handler 正常返回值——**`APIRoute`/返回值包装抓不到**。而 H3/M2 恰是错误路径泄露(上游 body/provider 域名)。
- **结论：C0 用 ASGI body-filter，不用 APIRoute 包装。** 异常处理器的 `JSONResponse` 仍**出栈穿过用户中间件**，故一个正确放置的 ASGI body-filter **同时覆盖成功 + 错误 + validation 响应**；APIRoute 只覆盖成功返回，会漏错误。
- **(b) 压缩顺序**：`JsonGZipMiddleware`（`app.py:141`）在响应路径压缩。C0 body-filter 必须放在 **gzip 内侧**（响应先经 C0 掩码、后被 gzip 压缩），否则看到压缩字节 / 破坏 Content-Length。放置：在 `create_app` 里把 C0 middleware 加在 **auth 之后（role 已由 auth 在 `:111` 写入 state）、`JsonGZip` 之前**（`add_middleware` 后加者为外层→C0 加在 JsonGZip 之前使其为内层）。掩码后重算/删除 Content-Length 交给下游 gzip。
- **纵深防御**：同一 `sanitize_customer_payload` 额外注册到三个异常处理器（`Exception` 已在 `:131`；补 `HTTPException`、`RequestValidationError`），即使 body-filter 将来被绕过，错误 JSON 仍被掩。two-layer。

#### 硬约束 4 — SSE 按 frame 处理，非按 chunk 替换
storyboard 路由有大量 `StreamingResponse`（`*/generate/events`、`*/chat/events`、`scene-tts/events`…）。不能对 chunk 做字符串替换。
- 实现：**缓冲到 `\n\n` 边界**，逐个解析 `data: <JSON>`，掩码后重新序列化 `data:` 行；**保留** heartbeat/`:` 注释行；正确处理**跨 chunk 半包**（残留缓冲到下一 chunk）。

#### 硬约束 5 — 独立 `sanitize_customer_payload()`，非只复用 pair masker
`mask_model_fields_for_role` 只处理**成对字段**（`MODEL_FIELD_PAIRS`：provider/model、providerID/modelID…，`model_policy.py:485`）。C0 需独立 sanitizer，但不能把通用字段无条件删掉，否则会破坏功能（例如 asset search 的 `raw.asset`、本地 `video_url`、本地 `endpoint`、用户 `snapshot`）。

```python
# 示意；词表抽成单一共享源(见下)
_KEY_DENYLIST = {"provider_result","agent_snapshot","generation_model","docs_url",
                 "provider_label_real","model_label_real","raw_response","raw_request",
                 "synthid","synth_id"}
_STR_SCRUB = re.compile("(?i)" + "|".join(MODEL_LEAKAGE_DENY_TERMS))  # ← 共享词表,勿另写
def sanitize_customer_payload(ctx, role, surface, value):
    if role == ADMIN: return value
    value = _drop_denylist_keys(value)          # 只删明确内部/供应商子树
    value = mask_real_provider_model_fields(value)  # 真名 blank；别名保留
    value = _scrub_non_free_text_strings(value, _STR_SCRUB)  # 域名/模型 id 窄兜底
    return value
```
三段缺一不可：**精确 key denylist + 真实 provider/model blank + 非自由文本窄 regex scrub**。

**已收窄的实现细则（防误伤）**：
- `raw` / `snapshot` / `video_url` / `endpoint` 是通用字段，**不得裸键删除**；本地 asset search、播放 URL、用户编辑快照要保留。若值含外部供应商域名，只 scrub 值，不删字段。
- `prompt` / `message` / `text` / `title` / `label` / `name` / `query` 等客户自由文本字段**不做裸模型词 scrub**，避免把 "Google 风格"、"Sora as a word"、"volcano" 改坏；但明确品牌词仍 scrub（如服务端写入的 `HeyGen digital human video`）。
- 词表只匹配域名、供应商域组件、明确模型 id 形态（如 `api.openai.com`、`generativelanguage.googleapis.com`、`gpt-image-2`、`veo-3.1`、`wan2.7`），不裸匹配 `google/sora/flux/grok/volc` 等普通词。品牌词边界不得用 `\b`，需能跨 `_`/`-` 命中 `heygen_digital_human` 这类 source/filename。
- 通道 A 别名兼容：C0 **保留** `Max` / `Flash` / `MaxWR2.7` 等别名；只 blank 看起来像真实 provider/model 的值。
- B 通道前置守卫：`synthid/synth_id` 类元数据键按内部风险键删除；Google SynthID 像素水印仍属于 §1.4 的业主决策残留。

**单一共享词表（同事复审七轮 #2）**：C0 的 `_STR_SCRUB` 与 §5 的 CI bundle grep、契约测试**必须共用同一个 `MODEL_LEAKAGE_DENY_TERMS`**（放 `media_sanitize.py` 或独立常量模块），否则两套词表漂移会漏。完整词表（对齐 audit 全部供应商）：
```
api.openai.com api.x.ai api.heygen.com generativelanguage.googleapis.com googleapis
aliyuncs dashscope sync.so synthid
openai[_-]* xai[_-]* heygen[_-]* bytedance[_-]* dashscope[_-]* aliyuncs[_-]* sync.so
gpt-* dall-e* whisper-* sora-* gemini-* imagen-* veo-* grok-* kling-* wan2.* wanx-*
qwen-* tongyi-* seedance-* seedream-* doubao-* minimax-* hailuo-* deepseek-* flux-* bfl-* cosyvoice-* nano-banana-*
provider_label_real model_label_real
```
（实现中为正则形式，长域名优先，避免 `googleapis` 被切成 `[model]apis`。）

#### 配套
- admin 短路：`role==admin` 直接返回原值（运营需要）。
- 逐路由 H1/H3/agents/clean-image **仍做**（白名单化返回体），作为"高危子集"，不是唯一防线。
- 与 §5：C0 兜底 + 契约测试盯高危端点 + CI 词表扫全响应（三重）。

> **原则回归 §0**：真名边界收敛到"后端出口一处"。C0 就是这个"一处"，但只有满足上述 5 条硬约束它才**真的**是一处；否则是"看起来集中、实则处处漏"。逐点掩码是六轮 whack-a-mole 的教训——**不可作为主机制**。

### H2. 堵 `audience=debug` 鉴权旁路（一处，收益最大）

`routes/sessions.py:738-741` `session_events` 当前直接把 query `audience` 透传，无角色校验。改：
```python
@router.get("/api/sessions/{session_id}/events")
async def session_events(session_id: int, request: Request, since: int = 0,
                         audience: str = Query(default="customer")) -> dict[str, Any]:
    get_session(session_id)
    if audience == "debug" and request_role(request) != AUTH_ROLE_ADMIN:
        raise HTTPException(status_code=403, detail="debug audience requires admin")   # ← 直接 403,不静默降级
    aud = "debug" if audience == "debug" else "customer"
    return {"items": list_session_events(session_id, since, aud)}
```
`session_events_stream:743` 同样处理（已有 request 参数）。`import` from `..model_policy import request_role` + `..routes.auth import AUTH_ROLE_ADMIN`。
**行为约定（同事复审 #3）**：非 admin 带 `audience=debug` **返回 403**（不静默降级为 customer），避免调用方误以为拿到了 debug 数据；CI 守卫按 403 断言。

### H1. 口播任务详情掩码

`koubo/task_list_router.py:326-342` 的 `talking_head_meta_config` 硬编码 `wan`/`wan2.7-r2v-2026-06-12`/`heygen` 写入 task_meta，经 `serialize_task(detail=True):530` 与 `GET /api/talking-head-v1/tasks/{id}:1110`（原样回传整份 `meta`）泄露。改：
- `serialize_task` 返回前，对 `talking_head.video_model` / `voice_timing.provider` 走 `mask_model_fields_for_role(ctx, role, SURFACE_MEDIA_VIDEO, ...)`（依赖 A3 的媒体 surface）或直接剔除 provider/model/executor/alias 字段。
- `GET /api/talking-head-v1/tasks/{id}:1110-1111` **停止原样回传整份 `meta`**，只回前端渲染必需的白名单字段。
- `task_list_router.py` 顶部 `import` `request_role` + mask 函数（当前该文件完全没 import model_policy）。注意当前你有未提交改动在此文件，一并处理。

### H3. 上游报错统一包装（禁透传）

新增 helper：
```python
# 示意，置于 model_policy.py 或新 errors.py
def upstream_failure(purpose: str, internal_detail: str, *, log) -> HTTPException:
    log.warning("upstream_failure %s: %s", purpose, internal_detail)   # 真名/上游 body 只进日志
    return HTTPException(status_code=502, detail=f"{purpose}失败，请稍后重试")
```
替换以下站点的 `HTTPException(detail=f"Gemini/Wan/Seedance/OpenAI/xAI/HeyGen/DashScope … {上游JSON}")`：
- `asset_video_generation_services.py:1371,1378,1407,1415,1452,1461,1478,1484,1509,1062` 及 `:604,612`（download 错误含上游 body）
- `rebuild_router.py:1050-1229,1572,2591`
- `router.py:2306,2345,2349,2382`
- `asset_digital_human_services.py:446-1094`
- `provider_services.py:346`
统一为通用文案；provider 名如需给前端，先经别名映射。

### M1. `/api/ocrebuild/*` 定性

`koubo/rebuild_router.py` 全文件无掩码，回传 `provider`/`model`/`endpoint`（`api.openai.com` 等）/`docs_url`。先确认该功能**是否面向客户**：
- 面向客户 → 全面套 `mask_model_fields_for_role` + 删 `endpoint`/`docs_url`（`:2606-2614,153-164,842,2728`）。
- 仅 admin → 把 `/api/ocrebuild/` 加入 `ADMIN_ONLY_PATH_PREFIXES`（`auth.py:27`）。

### M2. 兜底异常

`app.py:131-134` `unhandled_exception_handler` 返回 `{"detail": str(exc)}` → 生产改通用 500 文案，`str(exc)` 仅日志。

---

## 4. 通道 D —— 配置加固（P2）

- **vite `fs.allow:['..']`**（`frontend/vite.config.ts:81`）：生产确保只跑 dist 静态托管（当前如此），**永不**把 `vite dev` 挂公网隧道；如需 dev 设 `server.fs.strict:true` 并收窄 allow 到 `frontend` 自身。
- **openapi/docs**（`app.py:110`）：`FastAPI(docs_url=None, redoc_url=None, openapi_url=None)`（或纳入 admin）；清理 `koubo/schemas.py:42,207,209,213,214,223,230,231` 与 `task_list_router.py:655,825` 里的真实 provider/model 默认值。
- **`request_role` 默认改最小权限**：`model_policy.py:191-193` 取不到角色时默认 `AUTH_ROLE_USER`（而非 ADMIN），并生产强制 `OPENCREW_AUTH_REQUIRED=1`（`auth.py:73-74`）。⚠️ 此改动会影响所有掩码行为，需回归测试全部已掩码路由。
- **CORS 收敛**：`app.py:142-149` 把 `*.nip.io`/localhost 通配改为业主实际前端域名白名单。
- `vite.config.ts:8` `allowedHosts` 改环境变量注入。

---

## 5. 跨切面 —— CI 防回归守卫

泄露是"逐点手动"漏出来的，必须加自动守卫，否则会复发：

1. **bundle grep 守卫**（构建后）——**修正（同事复审 #7）扩大范围与词表**：
   - 扫描范围：**整个部署产物**（`frontend/dist/**` 全部 `.js/.css/.html/.json/.map`，不只 `dist/assets/*.js`；含 ModelConfig 树构建产物）。
   - 词表：**复用共享 `MODEL_LEAKAGE_DENY_TERMS`**（§3 硬约束 5，勿另写一份，否则与 C0 漂移）。命中即 CI 失败。
2. **响应/文件契约守卫**——**扩到所有客户可达出口（#7）**：对以下逐个做快照/断言"不含真名字段或值"：
   - 接口（**真实客户路由，同事复审 #3/#4/五轮#1#2**）：任务详情；事件（`audience=customer`；非 admin `debug` 断言 **403**）；`/asset-library/{image,video,tts}-model-config`；digital-human 客户出口 `/asset-library/digital-human/{settings,avatars,voices,voices/clone,generate/events,agents/{id},agents/{id}/stop}`；**clean-image `/clean-image/{generations,{id}/image,generate,{id}/promote/*}`**；metering；口播详情。（`digital-human`/`voice-clone` 无 `*-model-config`，勿列。）
   - **文件出口**：`/api/sessions/{id}/files/{file_id}`、**share 下载**、`files.zip`、`/raw/{path}`——断言媒体侧车/清单不可下载、下载的图/视频元数据已清、**返回的文件名与 asset source/label 不含 `heygen`/`avatar_iv` 等供应商词**（digital-human 路径专测）。
   - 可复用现有 `__contracts__/` 机制（如 `useOpenCrewAppController.return-surface.json`）。
   - **当前实现（2026-07-09 C0）**：`scripts/check_model_leakage_guard.py` 已接入 `.github/workflows/ci-gate.yml` 的 `backend-contract-tests` job；无 DB 依赖，使用生产同源 `include_app_routers()` 构建 route inventory + 样本响应扫描。当前 inventory 基线：`/api` route entries 385 条、guarded 309 条、`koubo-storyboard` guarded 113 条。
   - **当前样本**：`task_detail`、`asset_search_local_raw`、真实 digital-human asset 形态（`source`/`label`/`filename`）、`video_generation_result`、`channel_a_alias_payload`，以及 `SessionFileService` 真实下载/zip 侧车排除样本；合同测试落在 `backend/tests/contracts/test_model_leakage_guard_ci_contract.py`。
3. **路由清单自校验守卫（同事复审七轮 #3）**——手写高危清单会随新端点腐化。既然 C0 目标是"所有客户可达 /api/* JSON/SSE"，CI 也要**自动生成路由 inventory**：
   - 遍历 app 全部路由，筛出**客户可达 JSON/SSE**（排除 `ADMIN_ONLY_PATH_PREFIXES`/auth/health/二进制下载）。
   - 每条路由**必须**：要么有契约测试样本、要么在**显式排除表**里带原因（如"纯二进制，走通道 B"）。
   - **新增路由未分类 → CI 失败**。这样 C0 实现即使退化，新端点也不会静默逃过测试。
   - **当前实现**：`check_route_inventory()` 复用生产 `include_app_routers()`，再通过精确 route count 基线锁住覆盖面（385/76/309/113/113）。新增、删除或重分类 `/api` 路由都会失败，要求实施者复核 C0 分类并更新基线。
4. **C0 中间件机制单测（同事复审八轮）**——路由 inventory 只防"新端点漏测",C0 机制本身也要单测，否则实现顺序/streaming 退化时只能靠端到端样本碰运气。逐项断言：
   - 普通 JSON 响应 → provider/model 被掩、denylist 子树被删、裸串被 scrub。
   - `HTTPException(detail=…)` → error JSON 也被掩（不透传上游 body/provider）。
   - `RequestValidationError`、兜底 `Exception`（500）→ 同样被掩。
   - `Accept-Encoding: gzip` 下响应 → 客户端解压后无真名（验证 C0 在 gzip 内侧、顺序对）。
   - SSE 跨 chunk 半包 frame → 缓冲到 `\n\n` 后掩码，heartbeat 保留。
   - **admin 短路** → admin 角色响应保留真名（不误掩）。
   - **缺失 role fail-closed** → 无 `scope.state` role 时按 USER 掩码（不泄露）。
5. **文件出口测试**：见 §1.5，纳入 CI。当前已把媒体 sidecar/manifest 下载与 zip 排除落到 `SessionFileService` 并加合同；新生成图/视频/音频字节级元数据清洗已按 B0/B1/B2 落地；B4 存量媒体清洗脚本已实现，剩余是生产执行与真实公网端到端抽样。

---

## 6. 实施顺序、生产风险与回滚

**这是生产环境、公网在服务真实客户**，按风险从低到高、可独立上线的顺序：

| 步 | 内容 | 风险 | 回滚 |
|---|---|---|---|
| 0（已实施） | **C0 ASGI 出口掩码** + CI route/response guard + 只读 live smoke 脚本 | 低~中（集中改出口，需盯误伤） | 移除 `CustomerEgressSanitizerMiddleware` 注册；移除 CI guard step |
| 1（已实施） | B0+B1+B2 清洗管道（新代码，只在落盘处加一步） | 低（不改响应结构） | 移除 strip 调用 |
| 1b（已实施） | **新生成** digital-human 文件名去 heygen（中性 `digital_human*` 命名，asset source/label 去品牌） | **低~中**（文件名进 asset path/raw URL/files.zip，需回归依赖文件名的前端/引用） | 还原命名逻辑 |
| 2 | H2 debug 旁路（改 403） | 低 | 单行还原 |
| 3（脚本已实现，待执行） | B4 存量元数据清洗（images/videos/audios + assets/history + **SessionScratch/CleanImageGenerations**，先 dry-run+快照） | 中（改动存量文件字节） | tar 快照恢复 |
| 3b（脚本已实现，待执行） | **存量 digital-human 重命名**（`backend/scripts/rename_digital_human_assets.py`，同步 manifest/DB/sidecar/工作区 JSON 引用） | **中高**（改文件名+引用，断链风险） | 快照恢复 + 引用回滚；**单独灰度** |
| 4 | H1/H3/M2 后端响应掩码+异常包装（含 digital-human/voice-clone 事件/返回体/manifest） | 中（改响应内容，需回归客户端解析） | 按文件还原 |
| **5（原子发布）** | **通道 A 一次性发布**：A3 媒体别名注册表（含 TTS/voice-clone/digital-human 全部 surface）+ 后端 config/请求入口对 USER **alias-only**（role 分流）+ 前端 A1-A4 只读/只发 alias + dist rebuild。**必须同批上线**——见下方红线 | 高（前后端契约同批变更） | 整批回退上一版 dist+后端；admin 路径不受影响 |
| 6 | M1 ocrebuild 定性 + D 加固（request_role 默认改 USER 等） | 中高（影响掩码全局/鉴权） | 分项还原+回归 |
| 7 | 第 5 节剩余 CI 守卫（bundle grep、真实文件下载/zip/share 字节级测试） | 低 | — |

**红线（同事复审 #2）**：通道 A **不可拆成"先改前端 / 后改后端"两步**——原表把前端下沉放步 5、媒体 surface 放步 6 会造成**要么 USER 仍拿真名、要么前端读不到新契约**。正确做法：后端 role 分流（USER alias-only）+ 前端 alias 切换 + dist rebuild 是**同一次原子发布**（详见 channel_a §6）。步 5 内部可用 feature flag 灰度，但对 USER 的真名下发**从发布那一刻即关闭**。

每步上线后按 §1.5 / 第 5 节验证；后端改动需重启 `opencrew-backend` screen（会短暂影响在线客户，挑低峰）。

**当前状态**：步 0 已先落地，目的不是替代 A/B/H2/H3 的根治，而是把"漏一个路由"先降级为"出口兜底 + CI 报警"。下一步仍建议做步 1（B 清洗）+ 步 2（H2）——风险最低、对现有客户无感、堵住"下载即得"和"一次请求拿全表"两个最狠的口子。

---

## 7. 业主决策

### 已定（2026-07-09）
- **别名命名**：沿用现有 `Max {Provider}{Mode}{Ver}`（SI2/SR2/WR2.7/HR1.0），零迁移；可逆性残留风险已知并接受（硬化路径见 channel_a §2.1）。
- **通道 A 范围**：全覆盖 image/video/TTS/voice-clone/digital-human。
- **迁移窗口**：不允许 USER 泄露窗口，普通客户首版即 alias-only，双发仅 admin/flag。

### 仍待定
1. **SynthID**（§1.4）：接受残留 / 高敏场景换供应商 / 其他——代码不可解，需拍板。
2. **客户是否需要看价目**：决定通道 A 价目表是"删除"还是"下沉后端 admin-only + 客户端别名聚合展示"。
3. **`/api/ocrebuild/*` 是否面向客户**：决定 M1 是"套掩码"还是"admin-only"。
4. **`request_role` 默认改 USER**（§4）：是否接受为此做一轮全掩码路由回归。
