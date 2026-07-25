# 通道 A 下沉设计 —— 真名边界收敛到后端

日期：2026-07-09
配套：`docs/model_leakage_audit_2026-07-09.md`（审计）、`docs/model_leakage_remediation_design_2026-07-09.md`（总实施设计，本文是其第 2 节 A 的深化）。
状态：**设计稿，未改代码**。代码/JSON 为示意，落地以实际签名为准。

> 一句话目标：**客户永远只看到别名（Max 系）；真实 provider/model 只活在后端内存与出站请求里。** 前端不再持有解码表，接口不再下发真名。

**范围（2026-07-09 业主决策）**：本文覆盖 **image / video / TTS / voice-clone / digital-human 全部媒体面**（不止 image/video）。凡"客户可达且带 provider/model 真名"的 config 接口、能力表、请求体都在治理范围内。
**别名方案（业主决策）**：**沿用现有 `Max {Provider}{Mode}{Ver}` 体系**（SI2/SR2/WR2.7/HR1.0，已生产上线，零迁移）。⚠️ 已知残留风险：该命名把供应商首字母（S/W/H）+ 真实版本号（2.7/1.0）编码进别名，技术型客户可**部分反推** provider/version。业主选择接受此残留以避免改名迁移成本；如日后要收紧，硬化路径见 §2.1。此风险与 SynthID 同列"业主知情接受"项。
**迁移红线（业主决策）**：**不允许**"前端已切别名、后端对 USER 仍下发真名"的窗口——**普通客户从第一版起就是 alias-only**，双发兼容只对 admin/内部 flag 开放（见 §6）。

---

## 1. 现状数据流（已读代码核实）

通道 A 不是单点，而是**三个子面**，必须一起下沉，缺一个都能反推：

### 子面 ①：静态 bundle 常量（打包即在，与鉴权无关）
随每个客户下载的 JS 常量里硬编码真名：
- 价目表 `frontend/src/lib/meteringFormat.js:5-81`（`MEDIA_PRICE_POINTS`/`LIPSYNC_PRICE_COMPARISON`）、`ModelConfig/frontend/src/shared/pricing.ts:24-105`（重复表）。消费点仅 `useMediaSettingsController.jsx:81,92`（**admin 设置抽屉**——但常量对所有人下发）。
- 文本预设 `frontend/src/components/ModelPresetCards.jsx:4-19`（`MODEL_PRESETS`：`Max→openai/gpt-5.5`、`Flash→deepseek-v4-flash-free`），用于 7+ 客户模块。
- 视频能力表 `frontend/src/modules/koubo/UploadAssetLibrary/videoModelCapabilities.js:133-159`（`maxsr2↔bytedance/seedance-2.0`、`maxwr27↔wan2.7`、`xai/grok-imagine-video-1.5` 特判）。
- UI 文案 `HeyGen/Sync.so/OpenAI/Gemini/xAI/Grok`（`ModelConfigModule.tsx:69`、`DigitalHumanConfigModal.tsx:8`、`SettingsDrawers.jsx:260`、`PromptBuilderModal.jsx:78`、`XaiVoiceGuide.tsx:26` 含真实 team UUID 等）。

### 子面 ②：运行时 config 接口下发真名（**咽喉点，客户抓包即得**）
客户打开图库/TTS 即调这几个接口（非 admin 可达），返回未掩码真名：
- `asset_routes.py:2660` image-model-config → `asset_library_image_model_config()`（:1220-1251）
- `asset_routes.py:2665` video-model-config → `asset_library_video_model_config()`（:1253）`= load_config(ctx,"video")`
- `asset_routes.py:2670` **tts-model-config** → `asset_library_tts_model_config()`（:1256-1275）——返回 `active_provider` + `providers[].provider/model`（仅剥了 api_key，真名照发）。**同事复审补漏，务必纳入。**
- voice-clone / digital-human 的 config/设置接口同理（凡 `load_config(ctx, kind)` 直出者）——全部纳入。

返回体（`MediaModelConfigResponse`，`api.ts:121`）三处漏真名：
```jsonc
{
  "active_provider": "openai",                         // ← 真名
  "providers": [
    { "provider": "openai", "provider_label": "OpenAI",// ← 真名
      "docs_url": "https://developers.openai.com/...",  // ← 真名
      "models": [ { "model": "gpt-image-2" } ] } ],     // ← 真名
  "agent_model_aliases": [
    { "alias": "MaxSI2", "provider": "openai", "model": "gpt-image-2" } ]  // ← 别名对象自带真名！
}
```
`MediaAgentModelAlias`（`api.ts:113-119`）= `{alias, provider, model}`——**别名对象本身就夹带 provider/model 真名**。

### 子面 ③：请求回传真名（抓包）
前端把选中的真名塞回生成请求：`TalkingHeadV1Module.jsx:168-169`（`video_provider:"wan"`、`video_model:"wan2.7-r2v-…"`）；video agent 用 `capability.model`（真名）提交；**TTS 请求同样发 `providers`/`model` 真名**：`AnalysisV1TTSBuilder.jsx:1498`、`AnalysisV1Module.jsx:1533`（同事复审补漏）。

### 现有可复用资产
- 后端**已有**文本模型的 alias/hide 框架 `model_policy.py`（surfaces + `mask_prompt_models_for_role`/`resolve_prompt_model_for_role`，masked item 只含 alias），但**不覆盖媒体模型**。
- 媒体侧已有 `agent_model_aliases`（`load_agent_model_aliases(ctx)`，`asset_routes.py:1250`）与别名命名体系 `Max {Provider}{Mode}{Ver}`（SI2/SR2/WR2.7/HR1.0，见 memory `max-video-alias-naming`）——**但目前 alias 与 real 一起下发，且 alias 对象含真名**。
下沉即是：**把这套 alias 变成客户能看到的唯一标识，真名留后端。**

---

## 2. 目标契约

### 2.1 别名注册表（后端单一事实源）
在后端建立媒体别名注册表（复用/扩展 `load_agent_model_aliases` + `model_policy`）：

```
alias_key           →  { real_provider, real_model, capability, price_ref, docs_ref }
"MaxSI2"            →  { "openai", "gpt-image-2",           {首帧,img:0-1,...}, ... }
"MaxSR2"            →  { "bytedance","seedance-2.0",         {input_references,img:0-8,...}, ... }
"MaxWR2.7"         →  { "wan","wan2.7-r2v-2026-…",          {参考图0-5,视频0-5,...}, ... }
"MaxHR1.0"         →  { "gemini","veo-3.1-generate-preview",{...}, ... }
```
- 存放：`model_policy.py` 新增 `SURFACE_MEDIA_IMAGE/VIDEO/LIPSYNC/TTS/VOICE_CLONE/DIGITAL_HUMAN`，或独立 `media_alias.py`；capability/price/docs 一并挂在别名上（前端不再自己算）。
- 唯一真名源，出站给供应商时才解析。
- **别名字符串沿用现有 Max 系（业主决策，零迁移）**。⚠️ 反推残留风险已在顶部记录并接受。**日后可选硬化**（不改本次范围）：把 `provider` 首字母与真实 `Ver` 从别名里抹掉——如 `MaxWR2.7`→`MaxV07`（纯序号 SKU），注册表内部映射不变，仅对客户展示字符串去线索。届时只需改别名展示层，真名解析逻辑不动。

### 2.2 客户可见 config（掩码后）
**全部** asset-library config 接口（image/video/**tts/voice-clone/digital-human**）对**非 admin**返回 alias-only：
```jsonc
{
  "active_alias": "MaxSI2",
  "models": [
    { "alias": "MaxSI2", "label": "Max 单图 v2",
      "capability": { "referenceMode": "first_frame", "images": {"min":0,"max":1} },
      "price_hint": "≈¥0.3/张" } ],       // 可选，已脱敏聚合
  "has_api_key": true
}
```
**不含** `provider`/`model`/`provider_label`/`docs_url`/真名。admin 角色仍返回完整真名（运营需要）——复用 `request_role`。

### 2.3 请求（alias-in）
生成/连接测试请求体只带 `alias`（如 `"model_alias":"MaxWR2.7"`），后端解析为真名再出站。抓包只见别名。

---

## 3. 后端改动

> ⚠️ **代码位置注记（核验发现，防找错树）**：媒体配置代码分散在**两个同名 `media_model_config.py` 跨层**：
> - 别名/配置数据源 `load_agent_model_aliases` / `load_config` / `option_by_provider` → `backend/opcrew_backend/routes/media_model_config.py`（被 `asset_routes.py:27` import）。
> - admin 媒体配置路由 `/api/setup/media-models/*`（config/test）→ **桥接的 ModelConfig 树** `ModelConfig/backend/opcrew_model_config/media_model_config.py:2809`（`APIRouter(prefix="/api/setup/media-models")`）。
> 别名注册表（B-1）与客户 config 掩码（B-2）动 `backend/` 树；admin 侧真名展示/新 `media-pricing` 端点动 ModelConfig 树。两棵都要改，勿只改一棵。

### B-1. 别名注册表 + 解析器
```python
# media_alias.py（或 model_policy.py 扩展）  # 示意
def media_alias_catalog(ctx, kind: str) -> list[dict]:
    """返回 [{alias, label, capability, price_hint}]，无真名。"""
def resolve_media_alias(ctx, kind: str, alias: str) -> dict:
    """alias → {'provider':real, 'model':real}；找不到 raise 400（通用文案）。"""
def mask_media_config_for_role(ctx, role, kind, raw_config) -> dict:
    if role == AUTH_ROLE_ADMIN: return raw_config          # 运营看真名
    return {"active_alias": ..., "models": media_alias_catalog(ctx, kind),
            "has_api_key": raw_config.get(...)}             # 客户只见别名
```

### B-2. 掩码 config 端点（image / video / **tts** / voice-clone / digital-human）
`asset_routes.py:2660/2665/2670` 三个 handler（及 voice-clone/digital-human 同类）返回前套 `mask_media_config_for_role(ctx, request_role(request), kind, cfg)`。需给 handler 注入 `Request`（现签名 `(task_id)` → 加 `request: Request`）。同时清掉 `providers[].docs_url`/`provider_label` 对客户的下发。TTS 的 `asset_library_tts_model_config`（:1256）当前只剥了 api_key，**provider/model 仍需按 role 掩码**。

### B-3. 生成/测试入口 alias→real 解析
所有媒体生成/连接测试入口改为接收 `alias`，内部 `resolve_media_alias` 得真名再走原逻辑：
- 图库图/视频生成（`asset_routes.py` generate 系列、`asset_video_generation_services.py`）
- **TTS 生成/预览**（`AnalysisV1TTSBuilder.jsx:1498`、`AnalysisV1Module.jsx:1533` 对应的后端入口、`/api/setup/media-models/tts/voices/preview`）
- **voice-clone / digital-human** 生成入口
- 口播 `task_list_router.py:326-342`（当前硬编码 wan/heygen → 改为存 alias，生成时解析）
- 连接测试 `/api/setup/media-models/{kind}/test`（admin-only，可保留真名）
**兼容边界（红线）**：入口对 **USER 只接受 alias**，拒绝 real；real 仅在 `role==admin` 或内部 feature flag 下接受。**不存在对 USER 双发/双收真名的窗口**（见 §6）。

### B-4. `agent_model_aliases` 去真名
`load_agent_model_aliases` 返回给**客户**时剥掉 `provider`/`model`，只留 `alias`/`label`（admin 保留）。或直接不在客户 config 里放 `agent_model_aliases`，改用 2.2 的 `models`。

---

## 4. 前端改动

### F-1. 删除静态真名常量
- `meteringFormat.js:5-81` 删 `MEDIA_PRICE_POINTS`/`LIPSYNC_PRICE_COMPARISON`；`pricing.ts:24-105` 删重复表。价目仅 admin 视图按需从 `GET /api/media-pricing`（admin-only）拉取（见总设计 A1）。
- `ModelPresetCards.jsx:4-19` `MODEL_PRESETS` 去 `providerValues`/`modelValues` 真名，仅留 `presetKey`+alias `label`；匹配逻辑 `:39-40` 停用 `provider_label_real`/`model_label_real`（后端已不下发）。
- `videoModelCapabilities.js:133-159` **删除 alias↔real 硬编码分支**；capability 改从后端 config 的 `models[].capability` 读（2.2）。`VideoAgentPanel.jsx:200` 的真名/别名混排正则改纯别名。

### F-2. config 消费改 alias-only
- `UploadAssetLibraryOverlay.jsx:902,908`、`VideoAgentPanel.jsx:733-741`、`AgentPanel.jsx:1078` 拿到的 config 只读 `active_alias`/`models[].alias`/`capability`，不再读 `providers[].model`。
- `resolveVideoModelCapability(source, config)`（`videoModelCapabilities.js:169`）签名改为吃后端 capability，不再本地推导。

### F-3. 请求发 alias
- `TalkingHeadV1Module.jsx:168-169` 请求体 `video_model` 改发 alias（如 `MaxWR2.7`）。
- video/image agent 提交改发选中的 `alias` 而非 `capability.model`。
- **TTS**：`AnalysisV1TTSBuilder.jsx:1498`、`AnalysisV1Module.jsx:1533` 停止发 `providers`/`model` 真名，改发 TTS alias。
- voice-clone / digital-human 请求同改 alias。

### F-4. UI 文案去品牌（子面①剩余）
按总设计 A4 逐文件把 `HeyGen/Sync.so/OpenAI/Gemini/xAI/Grok`、`docs.x.ai`/`ai.google.dev`、xAI team UUID 改中性别名/删除。

### F-5. 重构建 + grep 验证
`npm run build` 覆盖 `frontend/dist/`（+ ModelConfig 树）；`grep -riE 'openai|gemini|sora|veo-|grok|kling|wan2|gpt-image|seedance|provider_label_real|api\.openai|api\.x\.ai|googleapis' dist/assets/` 零命中。

---

## 5. 逐文件改动清单（落地对照）

后端：
- `model_policy.py`（或新 `media_alias.py`）：注册表 + `resolve_media_alias`/`media_alias_catalog`/`mask_media_config_for_role`
- `asset_routes.py:1220-1275,2660/2665/2670`：image/video/**tts** 三个 config handler 注入 request + 掩码；去 docs_url/provider_label；`agent_model_aliases` 去真名（voice-clone/digital-human 同类一并处理）
- `asset_routes.py` generate 系列 + `asset_video_generation_services.py` + TTS/voice-clone/digital-human 生成入口：user 只收 alias→real
- `task_list_router.py:326-342`：talking_head 存 alias、生成时解析
- （admin）`GET /api/media-pricing` 新端点，`ADMIN_ONLY_PATH_PREFIXES` 加白

前端：
- `lib/meteringFormat.js`、`ModelConfig/.../shared/pricing.ts`：删价目常量
- `components/ModelPresetCards.jsx`：去真名
- `modules/koubo/UploadAssetLibrary/videoModelCapabilities.js`、`components/VideoAgentPanel.jsx`、`AgentPanel.jsx`、`UploadAssetLibraryOverlay.jsx`：config alias-only、capability 从后端
- `modules/koubo/TalkingHeadV1/TalkingHeadV1Module.jsx`：请求发 alias
- **TTS**：`modules/koubo/AnalysisV1/components/AnalysisV1TTSBuilder.jsx:1498`、`AnalysisV1/AnalysisV1Module.jsx:1533`：config 只读 alias、请求发 alias
- UI 文案：`ModelConfigModule.tsx`、`DigitalHumanConfigModal.tsx`、`SettingsDrawers.jsx`、`PromptBuilderModal.jsx`、`XaiVoiceGuide.tsx`、`MediaConfigModalBase.tsx`、`googleTtsScenarioGuide.ts`

---

## 6. 迁移顺序（USER 首版即 alias-only，无泄露窗口）

**红线（业主决策）**：**不允许**"前端已切、后端对 USER 仍下发真名"的窗口。普通客户从第一版起 config/请求就 alias-only；真名双发只对 `role==admin` 或内部 feature flag 开放。据此，顺序不是"后端先兼容双发给所有人"，而是**按角色分流、前后端同批上线**：

1. **后端建注册表 + 掩码，按角色分流（一次上线）**：
   - config 端点：`role==admin`（或 flag）→ 完整真名（运营/连接测试用）；`role==user` → **alias-only**（`active_alias`/`models[].alias/capability`，无 `providers`/真名/`docs_url`）。
   - 生成入口：admin/flag 接受 real+alias；user **只接受 alias**。
   - 此步对 USER **立即 alias-only**，无泄露窗口。
2. **前端切 alias（与步 1 同批或紧随，重构建 dist）**：F-1~F-4 只读/只发 alias。因步 1 已对 USER 停发真名，前端必须同批跟上，否则 USER 界面拿不到数据——故**步 1、2 需协调为同一次发布**（后端可短暂对 user 也留 alias 字段的同时移除真名，前端同刻切读 alias）。
3. **收尾**：删除前端所有静态真名常量（F-1）、UI 文案去品牌（F-4），grep dist 干净；关闭任何遗留的 user-real 兼容分支。

**兼容只走 admin/flag，不走"对 user 临时保留真名"。** 回滚：前后端同批发布，出问题整批回退上一版 dist+后端；admin 路径不受影响，运营可用。

> 与原三步的区别：原设计步 1 "对客户暂时保留 providers/真名" 会在生产留下 USER 抓包仍得真名的窗口——**已按红线删除**。代价是前后端 user 路径需同批发布（协调成本↑），换取零泄露窗口。

---

## 7. 验证

- **抓包（真实客户路由，同事复审 #3）**——`digital-human`/`voice-clone` **没有** `*-model-config` 端点，别测空。登录普通客户，对以下**真实**客户出口 grep 无 `openai|gemini|wan2|seedance|gpt-image|heygen|avatar_iv|sync|qwen|dashscope|aliyuncs|docs\.` 等真名、无 `provider`/`model` 字段：
  - config 类（`asset_routes.py:2660/2665/2670`）：`/asset-library/{image,video,tts}-model-config`。
  - digital-human（`asset_digital_human_routes.py`）：`/asset-library/digital-human/settings`（GET）、`/digital-human/avatars`、`/digital-human/voices`、`/digital-human/generate/events`（事件流）、**`/digital-human/agents/{provider_session_id}`（:178）与 `/stop`（:190）**——返回 `_write_video_agent_record` 含 `heygen`/model（五轮 #1）。
  - voice-clone：`/asset-library/digital-human/voices/clone`（请求/响应）。
  - **clean-image（`clean_image_routes.py`）**：`/clean-image/generations`（:52 manifest 真名）、`/clean-image/{id}/image`（:80 预览图元数据）、`/clean-image/generate`（:45）、`/clean-image/{id}/promote/*`（五轮 #2）。
  - `agent_model_aliases`（若保留）无 provider/model。
- **文件出口**：digital-human 生成的视频**文件名与 asset source/label 不含 `heygen`/`avatar_iv`**；媒体侧车/清单不可被客户下载。
- **bundle**：F-5 的 dist grep 零命中。
- **请求**：抓生成请求（image/video/tts/voice-clone/digital-human），body 只见 alias。
- **admin 回归**：admin 角色仍拿到真名（运营/连接测试不受影响）。
- **端到端**：客户用别名走通图/视频/TTS/口播/数字人生成（alias→real 解析正确出片）。
- **C0 兼容性守卫**：`scripts/check_model_leakage_guard.py` 的 `channel_a_alias_payload` 样本保证 `Max` / `Flash` / `MaxWR2.7` 等别名不会被 C0 blank；`backend/tests/contracts/test_customer_egress_sanitizer_contract.py` 也锁住 alias 保留行为。
- **现网/本机 smoke**：C0 落地后可用 `backend/.venv/bin/python scripts/smoke_model_leakage_live.py --base-url http://127.0.0.1:8011 --cookie '<customer cookie>' --task-id <id>` 对真实客户出口做只读扫描。
- **CI 守卫**（总设计第 5 节）：dist grep + 关键接口响应契约快照 + C0 route/response guard，纳入 CI 防复发。

---

## 8. 与总设计的关系 / 决策（2026-07-09 已定）
- 本文深化总设计 `remediation` 第 2 节（A1-A6）与 §3 的媒体 surface（A3）。
- **已定决策**：
  1. 范围 = **全覆盖** image/video/TTS/voice-clone/digital-human。
  2. 别名 = **沿用现有 Max 系**（零迁移）；可逆性残留风险已知并接受，硬化路径见 §2.1。
  3. 迁移 = **USER 首版即 alias-only，无泄露窗口**（§6）；双发仅 admin/flag。
- **仍待定**：客户是否需看价目（决定 F-1 是"删"还是"admin-only 拉取"）——见 remediation 决策清单第 2 项。默认：客户不看单模型价，仅总额/别名聚合。

---

## 9. 兼容与体验影响（客户使用方式/配置是否变化）

**结论：操作流程与可选项保持不变，但不能保证"零变化"——有 4 处刻意的、可控的变化，另有 1 个配置前提。**

### 保持不变（设计即为此）
- **操作流程不变**：客户仍是"选别名 → 点生成"。主选择 UI **本就别名优先**（`VideoAgentPanel.jsx:77` = `item.alias || item.label || item.model || …`），改造后别名恒在，交互一致。
- **可选项不变**：别名沿用现有 Max 系，模型选项集合不变。
- **无需客户改配置**：客户已保存的选择/别名仍有效；真名映射与 API Key 全在 admin 配置侧，客户侧无迁移。
- **视频/音频**：`-c copy` 逐字节不变，画质/体验无差异。
- **图片**：重编码保留 ICC 色彩（已修正设计），像素与颜色一致；仅去身份类元数据。

### 会变化（刻意，且是治理目标本身）
1. **含供应商名的 UI 文案变别名**：如"切换到 Grok"、"HeyGen 数字人设置"、"Sync.so…"、图库里以真名回退显示的少数标签 → 变中性别名。**行为不变，措辞变**。
2. **错误提示变通用**：上游报错从"Gemini/Wan…具体错误"→"生成失败，请重试"（通道 C）。信息变少，功能不变。
3. **digital-human 文件名变**：从 `..._heygen_digital_human_....mp4` / `..._heygen_video_agent_...mp4` → 中性 `..._digital_human_...mp4` / `..._digital_human_agent_...mp4`。客户若曾按文件名识别，会看到命名变化；存量重命名需同步 manifest/DB 引用（有断链风险，单独灰度）。
4. **轻微延迟**：每次生成多一步图片重编码/视频容器重写（毫秒~百毫秒级），不改选项。

### 一个配置前提（可能需要 admin 补配置）
- **别名覆盖必须完整**：alias-only 下，客户可选的每个模型都**必须有别名**。今天若某个客户可见模型因缺别名而回退显示真名（`VideoAgentPanel.jsx:77` 的 fallback），改造后它要么显示别名、要么**从客户选项中消失**。→ 上线前需**核对 admin 已为所有客户可见模型配置别名**；缺失的要补（admin 侧一次性配置，非客户侧）。这是唯一可能的"改配置"，且属运营准备而非客户操作。

### 迁移期风险（运营）
- 通道 A 前后端须**同批原子发布**（§6）：若前端先上而后端未 alias-only，或反之，客户可能短暂看到空选项/真名。→ 按红线同批发，出问题整批回退。

**一句话**：客户"怎么用"不变、"选什么"不变、不用自己改配置；变的是**带供应商名的文案/错误/文件名会变中性**（这正是要达到的效果），且 admin 需**保证别名覆盖完整**、通道 A **同批发布**。
