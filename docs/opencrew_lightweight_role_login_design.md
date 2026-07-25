# OpenCrew 轻量管理员/普通用户登录实现方案

## 状态

本文件是 implementation-ready 设计。它定义了后端合同、前端合同、测试断言、代码审核结论和上线 smoke。目标是让实现者可以直接按本文改代码，不再临场决定权限边界。

## 背景

当前 OpenCrew 已有本地 appliance 风格的单密码登录：

- 后端：`backend/opcrew_backend/routes/auth.py`
- Cookie：`opencrew_session`
- Token：当前 payload 只有 `iat/exp`，由 `auth.session_secret` HMAC 签名
- 前端：`frontend/src/App.jsx`
- 默认导航：当前 `activeNav` 初始值为 `connection`

`Connection` 页面会暴露和操作本机配置、模型配置、代理、发布、ASR、media model、metering 等敏感信息。目标是在不引入用户表、用户名、租户、权限矩阵的前提下，让普通用户登录后看不到 `Connection`，并且不能直接访问相关管理 API。

## 目标

1. 支持两类登录身份：
   - `admin`：管理员，可以访问 `Connection` 和本机管理配置 API。
   - `user`：普通用户，不可以访问 `Connection` 和本机管理配置 API。
2. 登录请求仍然只提交 `password`。
3. 不新增用户表。
4. 不新增用户名。
5. 不新增用户管理 UI。
6. 不新增 RBAC/ABAC 或权限矩阵。
7. 不做数据库迁移。
8. 前端隐藏入口，后端强制拦截敏感 API。

## 非目标

- 不区分多个普通用户。
- 不支持单个用户禁用/重置。
- 不支持用户级审计。
- 不做业务数据隔离；普通用户仍然可以访问业务任务页面。
- 不让普通用户管理模型、代理、发布、metering 或 Connection 配置。

## 关键决策

### 登录身份

采用“两套密码，一枚签名 cookie”：

- 管理员密码：沿用现有 `OPENCREW_APP_PASSWORD` / `OPENCREW_APP_PASSWORD_HASH` / setup password。
- 普通用户密码：新增 `OPENCREW_USER_PASSWORD` / `OPENCREW_USER_PASSWORD_HASH`。
- 密码匹配管理员，签发 `role=admin`。
- 密码匹配普通用户，签发 `role=user`。
- 两者都不匹配，返回 401。
- 管理员密码优先。如果 admin/user 密码相同，登录结果为 `admin`。

### 普通用户默认入口

普通用户默认入口固定为：

```text
#/analysis-v1/tasks
```

管理员无 hash 时继续默认进入 `Connection`。

### 旧 token 兼容

旧 token 没有 `role` 时，只要签名和过期时间有效，按 `admin` 处理。

原因：减少升级摩擦，不强制已有管理员重新登录。

可选加强：部署时轮换 `auth.session_secret`，强制所有会话重新登录。第一版不默认做这个。

### 管理 API 保护范围

第一版后端 admin-only gate 使用 denylist prefix：

```text
/api/setup/
/api/model-config/
/api/local-metering/
```

普通用户访问返回 403。

注意：当前多个业务模块也调用了部分 `/api/setup/media-models/*`，例如：

- `OpenClip/frontend/src/AnalysisV1/analysisV1Api.js`
- `OpenClip/frontend/src/OCRebuildModule.jsx`
- `OpenClip/frontend/src/KouboStoryBoard/kouboStoryboardApi.js`

这些接口返回 `has_api_key`、`api_key_ref`、provider/model active 状态等配置元信息，并且部分 preview 接口会使用保存的 provider key 产生调用费用。因此第一版不要给普通用户放开 `/api/setup/media-models/*`。业务页面如果碰到 403，应该降级为隐藏/禁用对应配置控件，不能显示全局错误。

如果未来普通用户必须选择模型，应新增脱敏 runtime endpoint，例如：

```text
/api/runtime/media-model-options/{kind}
```

该 endpoint 只能返回普通用户运行所需的安全选项，不能返回 `api_key_ref`、`has_api_key`、secret 状态、Connection 测试能力。

第一版不新增该 runtime endpoint，避免扩大实现范围。

另外，`ModelConfig` 插件还保留了 `/api/model-config/*` 兼容管理路由。这些接口和 `/api/setup/*` 一样会暴露或使用模型配置、API key 状态、provider/model 真实身份、连接测试和 TTS preview/match 能力，必须纳入 admin-only gate。不能只保护 `/api/setup/*`。

## 模型选择入口盘点

这部分和 `Connection` 隐藏是同一个角色问题：管理员可以看到和选择真实 provider/model；普通用户按 surface 使用隐藏、固定默认值或别名。

本轮只实施仍在使用的页面。已废弃页面保留代码也不进入第一版改造范围。

### 通用别名

普通用户不能看到 `OpenAI`、`OpenCode Zen`、`GPT-5.5`、`DeepSeek v4 flash free` 等真实名称。第一版统一使用：

| alias | 普通用户 catalog `providerID/providerName` | 普通用户 catalog `modelID/modelName` | 真实 provider | 真实 model |
| --- | --- | --- | --- | --- |
| `max` | `Max` | `Max` | `openai` / `OpenAI` | `gpt-5.5` / `GPT-5.5` |
| `flash` | `Flash` | `Flash` | `opencode` / `OpenCode Zen` | `deepseek-v4-flash-free` / `DeepSeek v4 flash free` |

真实 provider/model 的大小写和 ID 以各路由返回的 catalog 为准；策略解析时必须同时支持当前 catalog 中的真实 ID 和显示名。普通用户 masked catalog 的 `providerID` / `modelID` 也要使用显示安全 alias，例如 `Max` / `Flash`，因为现有前端 summary 有些地方会直接拼接 `modelID`。

### 第一版实施范围

管理员保持现状，不做过滤、改名或隐藏。

| surface id | UI 位置 | 当前暴露 | 普通用户 UI | 普通用户提交 | 后端约束 |
| --- | --- | --- | --- | --- | --- |
| `connection.asr` | `frontend/src/App.jsx` Connection ASR；`ModelConfig` ASR modal | provider、model、API key、连接测试 | 不显示 Connection 入口和 ASR 配置 UI | 无 | `/api/setup/asr/*`、`/api/model-config/asr/*` 返回 403 |
| `connection.image` | `frontend/src/App.jsx` Connection Image；`ModelConfig` image modal | provider、model、API key、连接测试 | 不显示 Connection 入口和 Image 配置 UI | 无 | `/api/setup/media-models/image/*`、`/api/model-config/image/*` 返回 403 |
| `connection.video` | `frontend/src/App.jsx` Connection Video；`ModelConfig` video modal | provider、model、API key、连接测试 | 不显示 Connection 入口和 Video 配置 UI | 无 | `/api/setup/media-models/video/*`、`/api/model-config/video/*` 返回 403 |
| `connection.tts` | `ModelConfig` TTS modal | provider、model、voice、API key、voice preview/match | 不显示 Connection 入口和 TTS 配置 UI | 无 | `/api/setup/media-models/tts/*`、`/api/model-config/tts/*` 返回 403 |
| `metering` | `frontend/src/App.jsx` Metering 页面 | provider、model、provider cost、customer charge、profit | 不显示 Metering 入口 | 无 | `/api/local-metering/*` 返回 403 |
| `openflow.prompt` | `frontend/src/App.jsx` OpenFlow `Select Prompt Model` | provider select、model select、搜索、summary | 保留 provider/model 两个下拉框，但只显示 `Max` / `Flash` 别名；summary 不显示真实 provider/model | `Max/Max` 或 `Flash/Flash` alias | 后端映射 alias；普通用户提交真实 provider/model 返回 403；响应不下发真实 catalog |
| `analysis_v1.prompt` | `OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx` `Select Prompt Model` | provider select、model select、搜索、summary | 保留 provider/model 两个下拉框，但只显示 `Max` / `Flash` 别名；summary 不显示真实 provider/model | `Max/Max` 或 `Flash/Flash` alias | 后端映射 alias；普通用户提交真实 provider/model 返回 403；响应不下发真实 catalog |
| `analysis_v1.run` | `OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx` 中文“运行设置”弹窗 | “模型服务商”、模型、ASR、云端 ASR 复选框、TTS Builder、StoryBoard | “模型服务商”只显示 `Max` / `Flash`；选 `Max` 时“模型”只有 `Max`；选 `Flash` 时“模型”只有 `Flash`；ASR select 和云端 ASR 复选框对普通用户隐藏；TTS Builder 和 StoryBoard 保留原样 | provider/model 提交 alias；ASR 字段可省略，由后端固定为 `asr_mode=default`、`allow_cloud_asr_data_transfer=true` | 后端映射 alias；普通用户提交真实 provider/model 返回 403/422；普通用户不能手动切换 ASR，本地或云端由后台默认 ASR 配置决定 |
| `analysis_v1.tts_preview` | `OpenClip/frontend/src/AnalysisV1/components/AnalysisV1TTSBuilder.jsx` TTS preview dialog | Voice、Language；候选项可能显示 provider/model 来源 | 不做改动 | 保持现状 | 第一版不改造该 surface |
| `koubo_storyboard.tts_timing` | `OpenClip/frontend/src/KouboStoryBoard/components/KouboTimingMenu.jsx` `故事版（口播）` Timing/TTS 设置 | Provider、Model、Voice、TTS Tempo | 隐藏 Provider 和 Model；保留 Voice 和 TTS Tempo；Voice 显示名称保持当前配置，不做别名/过滤 | 只提交 voice、tempo；provider/model 使用默认 | 新部署默认 provider/model 为 `google` / `gemini-3.1-flash-tts`；普通用户不能提交真实 provider/model 覆盖 |
| `koubo.host_product.prompt` | `OpenClip/frontend/src/KouboStoryBoard/hostProduct/KouboHostProductBuilder.jsx` `Select Builder Prompt Model` | 真实 provider/model catalog | 显示 `Max` / `Flash` 别名；summary 不显示真实 provider/model | `Max/Max` 或 `Flash/Flash` alias | 后端映射 alias；普通用户提交真实 provider/model 返回 403 |

### 第一版明确忽略

| surface id | 原因 | 第一版处理 |
| --- | --- | --- |
| `openflow.skill` | 当前源码有 `Select Skill Model` 渲染代码，但未找到 `setOpenFlowSkillBuilderOpen(true)` 打开入口，属于不可达遗留 UI | 不改；如果未来恢复入口，按 `Max` / `Flash` 同规则重新纳入 |
| `openclip.prompt` / `openclip.run` | `OC - Analysis` 页面已废弃 | 不改，不写测试断言 |
| `ocrebuild.*` | `OC - Rebuild` 页面已废弃 | 不改，不写测试断言 |
| `ocrebuild.host_product.prompt` | `OpenClip/frontend/src/OCRebuildHostProductBuilder.jsx` 存在独立 provider/model 选择对话框，并会提交真实 provider/model；属于 `OC - Rebuild` 废弃页面链路 | 不改；如果 `OC - Rebuild` 恢复使用，必须重新纳入 surface 决策和后端脱敏 |
| `storyboard.tts_timing` | `OC - StoryBoard` 页面已废弃 | 不改，不写测试断言 |

如果未来恢复这些页面，必须先重新做 surface 决策，不能直接套用旧表。

### 全量扫描结果

本节记录本轮从前端源码中扫描到的 provider/model/voice 相关选择器。`第一版处理` 列说明是否进入本次实现范围。

| 文件 / UI | 暴露内容 | 第一版处理 |
| --- | --- | --- |
| `frontend/src/App.jsx` Connection ASR/Image/Video | provider、model、API key、连接测试 | admin-only |
| `ModelConfig/frontend/src/*` Connection/ModelConfig modals | provider、model、voice、API key、preview/match | admin-only |
| `frontend/src/App.jsx` OpenFlow `Select Prompt Model` | provider/model prompt model | `alias` |
| `frontend/src/App.jsx` OpenFlow `Select Skill Model` | provider/model skill model | 不可达遗留 UI，本轮不改 |
| `OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx` `Select Prompt Model` | provider/model prompt model | `alias` |
| `OpenClip/frontend/src/AnalysisV1/AnalysisV1Module.jsx` “运行设置” | provider/model run model、ASR、云端 ASR 复选框、TTS Builder、StoryBoard | provider/model 用 `alias`；普通用户隐藏 ASR select 和云端 ASR 复选框，后端固定 `default/true`，按后台默认 ASR 配置运行；其余保留 |
| `OpenClip/frontend/src/AnalysisV1/components/AnalysisV1TTSBuilder.jsx` TTS Preview | Scenario、Voice、Language；候选项可能携带 provider/model 来源 | 用户明确选择不改 |
| `OpenClip/frontend/src/KouboStoryBoard/components/KouboTimingMenu.jsx` Timing/TTS 设置 | Provider、Model、Voice、TTS Tempo | Provider/Model `hide`；Voice/Tempo 保留 |
| `OpenClip/frontend/src/KouboStoryBoard/hostProduct/KouboHostProductBuilder.jsx` Host/Product Builder | provider/model prompt model | `Max` / `Flash` alias |
| `OpenClip/frontend/src/OpenClipModule.jsx` OC - Analysis | prompt/run provider/model | 页面已废弃，本轮不改 |
| `OpenClip/frontend/src/OCRebuildModule.jsx` OC - Rebuild | prompt/run provider/model、image/video/TTS/R2V model、voice | 页面已废弃，本轮不改 |
| `OpenClip/frontend/src/OCRebuildSrtBuilder.jsx` OC - Rebuild SRT Builder | run provider/model；逐句 TTS provider/model/voice 输入 | 页面已废弃，本轮不改 |
| `OpenClip/frontend/src/OCRebuildHostProductBuilder.jsx` OC - Rebuild Host/Product Builder | provider/model prompt model | 页面已废弃，本轮不改 |
| `OpenClip/frontend/src/OCStoryBoard/components/StoryboardToolbarMenus.jsx` OC - StoryBoard | Provider、Model、Voice、TTS Tempo | 页面已废弃，本轮不改 |

### 不是选择器但会暴露真实模型的地方

| 位置 | 暴露内容 | 处理要求 |
| --- | --- | --- |
| `frontend/src/App.jsx` Metering 页面 | provider、model、provider cost、customer charge、profit | admin-only |
| `frontend/src/App.jsx` Debug/事件摘要 | prompt/TTS provider/model | 第一版建议普通用户隐藏 Debug Console；如果普通用户可见，必须按 role 脱敏 |
| AnalysisV1/OpenFlow/Koubo detail payload | task、version、attempt、event 中的 `*_provider` / `*_model_id` | 对普通用户 mask 或返回 alias，不能在 devtools 响应里暴露真实 provider/model |

## 普通用户模型策略

### 安全等级

有两个实现等级，不能混淆：

1. UI 收敛：前端按 `role=user` 隐藏或改名选择器。实现简单，但浏览器网络响应里仍可能看到真实 provider/model。这不是安全边界。
2. 后端脱敏：后端按 `role=user` 不下发真实 provider/model，并且 action endpoint 不接受普通用户提交未经授权的真实 provider/model。这才满足“隐藏真实 provider”和“限制用户选择昂贵模型”的要求。

如果目标只是“不让普通用户在界面上看到 Connection 和模型下拉框”，可以先做 UI 收敛。如果目标包括“不让普通用户通过 devtools 看见真实 provider/model”，必须做后端脱敏。

### 实现成本判断

经代码走查，prompt/run 类 selector 大多是 catalog 驱动：

- 后端 `serialize_prompt_models(...)` 产出：
  ```json
  {"items": [{"providerID": "...", "providerName": "...", "modelID": "...", "modelName": "..."}], "default_model": {"providerID": "...", "modelID": "..."}}
  ```
- 前端 `promptModelProviders()` 从 `items` 汇总 provider，`filteredPromptModels()` / `filteredRunModels()` 再按 `providerID` 过滤。
- select option 的显示名直接来自 `providerName` / `modelName`。

因此，`openflow.prompt`、`analysis_v1.prompt`、`analysis_v1.run`、`koubo.host_product.prompt` 这类仍显示 provider/model 下拉框的 surface，第一优先做后端 catalog 改写：`role=admin` 返回真实 catalog，`role=user` 返回 alias catalog。这样现有前端 select 代码会自然显示 `Max` / `Flash`，不需要在前端硬编码 provider/model 别名映射。

真正不能省的工作是三类：

1. action endpoint 校验和解析：普通用户提交的 alias 必须映射到真实 provider/model；普通用户提交真实 provider/model 必须拒绝。这是安全边界。
2. payload 脱敏：task/detail/version/attempt/event/result payload 里的 `*_provider`、`*_model_id`、`provider`、`model`、`run_model_provider`、`run_model_id` 等字段必须按 role mask。这里分散、易漏，是主要工作量。
3. 前端 role flag：只有需要彻底隐藏 selector 或处理非 catalog 控件时才需要，例如隐藏 `Connection` / `Metering`、`koubo_storyboard.tts_timing` 隐藏 Provider/Model、`analysis_v1.run` 隐藏 ASR select 和云端 ASR 复选框、退出登录按钮。

### 推荐第一版范围

为避免引入复杂用户机制，第一版采用“按 role + 按 surface 的静态策略”，不新增用户表、不新增用户管理 UI。

管理员：

- `Connection`、`Metering` 可见。
- 所有 provider/model 原始选择器保持当前行为。
- 后端返回真实 `prompt_models`、media model config、metering。

普通用户：

- `Connection`、`Metering` 不可见。
- 不可访问 `/api/setup/*`、`/api/model-config/*`、`/api/local-metering/*`。
- `openflow.prompt`、`analysis_v1.prompt` 使用 `Max` / `Flash` 别名选择，不显示真实 provider/model。
- `analysis_v1.run` 保留“模型服务商”和“模型”两个下拉框，但 provider/model 显示名都使用 `Max` / `Flash` 别名；普通用户隐藏 ASR select 和云端 ASR 复选框，后端固定 `asr_mode=default`、`allow_cloud_asr_data_transfer=true`，本地或云端由后台默认 ASR 配置决定；TTS Builder 和 StoryBoard 选项保留原样。
- `koubo_storyboard.tts_timing` 隐藏 Provider 和 Model，保留 Voice 和 TTS Tempo；Voice 名称不做别名/过滤；新部署默认 `google` / `gemini-3.1-flash-tts`。
- `koubo.host_product.prompt` 使用 `Max` / `Flash` 别名选择，不显示真实 provider/model。
- `analysis_v1.tts_preview` 第一版不做改动。
- `OC - Analysis`、`OC - Rebuild`、`OC - StoryBoard` 已废弃，第一版不改造这些页面里的模型选择器。

### Capabilities

```python
def auth_capabilities(role: str) -> dict[str, bool]:
    is_admin = role == AUTH_ROLE_ADMIN
    return {
        "can_manage_connection": is_admin,
    }
```

不要新增 `can_select_raw_models` / `can_view_raw_model_ids` 这类全局布尔作为模型策略驱动。模型策略是逐 surface 的，真正驱动来自后端按 role 返回的 masked catalog / masked payload，以及前端少量 surface 级“是否渲染 selector”判断。全局布尔无法表达“有些 surface 显示 alias、有些隐藏、有些保留 voice”的差异，容易和 policy/catalog 形成双端重复配置。

### 策略文件

第一版不做后台配置 UI。策略可先放在代码默认值，后续可加可选 JSON 文件：

```text
OPENCREW_USER_MODEL_POLICY_PATH=/path/to/user_model_policy.json
```

示例：

```json
{
  "surfaces": {
    "openflow.prompt": {
      "mode": "alias",
      "options": [
        {
          "provider_alias": "Max",
          "provider_label": "Max",
          "model_alias": "Max",
          "model_label": "Max",
          "provider": "openai",
          "model": "gpt-5.5"
        },
        {
          "provider_alias": "Flash",
          "provider_label": "Flash",
          "model_alias": "Flash",
          "model_label": "Flash",
          "provider": "opencode",
          "model": "deepseek-v4-flash-free"
        }
      ]
    },
    "analysis_v1.prompt": {
      "mode": "alias",
      "options": [
        {
          "provider_alias": "Max",
          "provider_label": "Max",
          "model_alias": "Max",
          "model_label": "Max",
          "provider": "openai",
          "model": "gpt-5.5"
        },
        {
          "provider_alias": "Flash",
          "provider_label": "Flash",
          "model_alias": "Flash",
          "model_label": "Flash",
          "provider": "opencode",
          "model": "deepseek-v4-flash-free"
        }
      ]
    },
    "analysis_v1.run": {
      "mode": "alias",
      "fixed_fields": {
        "asr_mode": "default",
        "allow_cloud_asr_data_transfer": true
      },
      "options": [
        {
          "provider_alias": "Max",
          "provider_label": "Max",
          "model_alias": "Max",
          "model_label": "Max",
          "provider": "openai",
          "model": "gpt-5.5"
        },
        {
          "provider_alias": "Flash",
          "provider_label": "Flash",
          "model_alias": "Flash",
          "model_label": "Flash",
          "provider": "opencode",
          "model": "deepseek-v4-flash-free"
        }
      ]
    },
    "koubo_storyboard.tts_timing": {
      "mode": "hide",
      "defaults": {
        "provider": "google",
        "model": "gemini-3.1-flash-tts"
      },
      "voice_labels": "unchanged",
      "visible_fields": ["voice", "tts_tempo"]
    },
    "koubo.host_product.prompt": {
      "mode": "alias",
      "options": [
        {
          "provider_alias": "Flash",
          "provider_label": "Flash",
          "model_alias": "Flash",
          "model_label": "Flash",
          "provider": "opencode",
          "model": "deepseek-v4-flash-free"
        }
      ]
    }
  }
}
```

该文件只由本机管理员维护。普通用户不通过 UI 查看或修改。文件中的真实 provider/model 不能下发给普通用户。

### 后端模型策略 helper

新增共享模块，避免 OpenFlow、Analysis V1、Koubo 各自实现一套：

```text
backend/opcrew_backend/model_policy.py
```

核心函数：

```python
def request_role(request: Request) -> str:
    ...

def user_model_policy(ctx: AppContext) -> dict[str, Any]:
    ...

def mask_prompt_models_for_role(ctx: AppContext, role: str, surface: str, prompt_models: dict[str, Any]) -> dict[str, Any]:
    ...

def resolve_prompt_model_for_role(ctx: AppContext, role: str, surface: str, prompt_models: dict[str, Any], provider: str, model_id: str, purpose: str) -> tuple[dict[str, str], dict[str, Any]]:
    ...

def mask_model_fields_for_role(ctx: AppContext, role: str, surface: str, payload: dict[str, Any]) -> dict[str, Any]:
    ...
```

规则：

- `role=admin`：返回原始 catalog，接受原始 provider/model。
- `role=user` + `mode=alias`：后端返回 alias catalog。catalog item 的 `providerID/modelID` 使用 alias value，`providerName/modelName` 使用 alias label；前端照现有 select 渲染即可。action endpoint 收到 alias 后映射到真实 provider/model；提交真实 provider/model 返回 403/422。
- `role=user` + `mode=hide`：后端不返回 provider/model catalog，或返回空 catalog；前端按 surface 不渲染 provider/model selector。action endpoint 使用策略默认 provider/model；普通用户提交 provider/model override 时返回 403/422。
- `fixed_fields`：对非模型字段强制固定值，例如 `analysis_v1.run.asr_mode=default`、`analysis_v1.run.allow_cloud_asr_data_transfer=true`；普通用户提交其他值返回 403/422，省略时由后端补默认值。
- Voice 不是单独 mode。需要隐藏 provider/model 但保留 voice 时，使用 `mode=hide` + `visible_fields=["voice", ...]`，voice label 是否改名由该 surface 的安全策略决定。
- 第一版不支持 `ui_only`。任何普通用户可见 surface 如果返回真实 provider/model，都视为实现缺陷。

### Middleware role 传递

`build_auth_middleware(ctx)` 在 token valid 后设置：

```python
request.state.opencrew_auth_role = role
```

当 `OPENCREW_AUTH_REQUIRED=0` 时，role 固定为 `admin`。

业务 router 如果需要按 role 脱敏，必须接收 `request: Request` 并调用 `request_role(request)`。

### 模型 catalog 接入点

不能假设有一个全局 serializer。当前至少有以下 catalog 生产/合并路径，必须逐个核对：

| surface | catalog producer | action resolver | 必须处理 |
| --- | --- | --- | --- |
| `openflow.prompt` | `backend/opcrew_backend/routes/openflow_analysis.py::serialize_prompt_models` | `resolve_prompt_model` | catalog 返回前 mask；`resolve_prompt_model` 改为 role-aware alias resolve；返回 draft/version payload 前 mask |
| `openflow.skill` | 同上 | `resolve_skill_model` | 当前 UI 不可达；如果恢复入口，必须同样接入 |
| `analysis_v1.prompt` | `OpenClip/backend/openclip_backend/router.py::serialize_prompt_models` | `resolve_model(..., "Prompt")` | catalog 返回前 mask；prompt action alias resolve；task/detail/version/event payload mask |
| `analysis_v1.run` | 同上 | `resolve_model(..., "Analysis V1 run")` | catalog 返回前 mask；run action alias resolve；`fixed_fields.asr_mode` / `fixed_fields.allow_cloud_asr_data_transfer` 校验；attempt/task/run_state/indicator payload mask |
| `koubo.host_product.prompt` | `OpenClip/backend/openclip_backend/koubo_storyboard/provider_services.py::safe_prompt_models` + `prompt_models_with_task_run_model`，由 `host_product_services.py` 组合使用 | `provider_services.py::resolve_model` | 必须在 `prompt_models_with_task_run_model(...)` 合并 task run model 之后再 mask，否则会把真实 run_model 加回 catalog；action 必须 role-aware resolve |
| `koubo_storyboard.tts_timing` | TTS config comes from `/api/setup/media-models/tts/config` and `kouboStoryboardTts.js` helpers, not `prompt_models` | TTS/timing action path | 普通用户不访问 admin-only TTS config；前端隐藏 Provider/Model；action 使用 `hide.defaults` |
| `ocrebuild.*` | `OpenClip/backend/openclip_backend/rebuild_router.py::serialize_prompt_models` and related media config paths | `rebuild_router.py::resolve_model` and media workflow handlers | 页面已废弃，本轮不接入；如果恢复，必须单独审计，不能依赖 Analysis V1 的 `router.py` 接入 |

实现方式：

1. 原始 serializer 保留，只生成真实 catalog。
2. 每个 route/action 根据 surface 调用 role-aware helper。不要在共享 serializer 内无条件 mask，因为同一个 serializer 可能服务多个 surface。
3. 路由返回给前端前调用 `mask_prompt_models_for_role(ctx, role, surface, raw_catalog)`。
4. action endpoint 解析模型时调用 `resolve_prompt_model_for_role(...)`，不能直接信任前端传入的 provider/model。
5. 对普通用户返回 task/detail/version/event/attempt/indicator payload 前，调用 `mask_model_fields_for_role(...)` 处理 `prompt_model_provider`、`prompt_model_id`、`run_model_provider`、`run_model_id`、`provider`、`model`、`used_run_model_provider`、`used_run_model_id` 以及嵌套 current version 字段。
6. 不接入 `OpenClipModule`、`OCRebuildModule`、`OCStoryBoardModule` 对应废弃页面的后端路径，除非这些页面未来恢复使用。

### Frontend 接入方式

前端不实现 alias→real 映射，也不镜像后端 policy。alias 类 surface 只消费后端返回的 masked catalog，现有 provider/model select 会自然显示 alias。

前端只需要知道“当前是否管理员”来处理 admin-only nav 和少量 `hide` / `fixed_fields` UI：

```js
const roleAccess = createMemo(() => ({
  isAdmin: canManageConnection(),
}));
```

传给需要隐藏非 catalog 控件的业务模块：

```jsx
<AnalysisV1Module roleAccess={roleAccess()} ... />
<KouboStoryBoardModule roleAccess={roleAccess()} ... />
```

`frontend/src/App.jsx` 内部的 OpenFlow prompt 弹窗不需要 alias 特例。只要后端返回 masked `prompt_models`，现有 select 会显示 `Max` / `Flash`。

各模块默认值：

```js
const isAdmin = () => Boolean(props.roleAccess?.isAdmin);
```

普通用户 UI 规则：

- `openflow.prompt`、`analysis_v1.prompt`、`analysis_v1.run`、`koubo.host_product.prompt`：不做前端 alias 分支；继续渲染现有 provider/model select，数据来自 masked catalog。用户看到 `Max` / `Flash` 是后端 catalog 的结果。
- `analysis_v1.run` 的 ASR select 和云端 ASR 复选框：普通用户不渲染；提交时省略或提交后端要求的 `asr_mode=default`、`allow_cloud_asr_data_transfer=true`；TTS Builder 和 StoryBoard select 保留原样。
- `koubo_storyboard.tts_timing`：普通用户不渲染 Provider 和 Model select；保留 Voice 和 TTS Tempo；Voice option label 使用当前配置原样。
- `analysis_v1.tts_preview`：第一版不做改动。
- summary、candidate、event label 不应从前端拼接真实 provider/model；如果响应已按 role mask，前端显示响应即可。任何普通用户响应中出现真实 provider/model 都是后端 payload mask 漏洞。

策略 label 由产品和部署配置决定。本文只要求普通用户界面不显示真实 provider/model，不强制具体文案语言。

## 后端实现合同

文件：`backend/opcrew_backend/routes/auth.py`

### 常量

新增：

```python
AUTH_ROLE_ADMIN = "admin"
AUTH_ROLE_USER = "user"
AUTH_ROLES = {AUTH_ROLE_ADMIN, AUTH_ROLE_USER}
ADMIN_ONLY_PATH_PREFIXES = ("/api/setup/", "/api/model-config/", "/api/local-metering/")
```

### 普通用户密码配置

新增 helper：

```python
def configured_user_password_hash(ctx: AppContext) -> str:
    ...
```

规则：

1. `OPENCREW_USER_PASSWORD_HASH` 非空时优先使用。
2. 否则读取 `OPENCREW_USER_PASSWORD`。
3. 明文 env password 使用 settings cache，建议 key：
   - `auth.user_env_password_hash`
   - `auth.user_env_password_marker`
4. 两者都为空返回 `""`。
5. 普通用户密码不参与 `auth_configured(ctx)`；系统是否 configured 仍然由管理员密码决定。

现有 `configured_password_hash(ctx)` 可以保留作为管理员密码 helper，也可以重命名为 `configured_admin_password_hash(ctx)`。如果重命名，保留兼容 wrapper：

```python
def configured_password_hash(ctx: AppContext) -> str:
    return configured_admin_password_hash(ctx)
```

### Token

调整：

```python
def make_token(ctx: AppContext, role: str = AUTH_ROLE_ADMIN) -> str:
    ...
```

payload：

```json
{"iat": 123, "exp": 456, "role": "admin"}
```

新增 token parser：

```python
def parse_token(ctx: AppContext, token: str) -> dict[str, Any]:
    ...
```

返回合同：

```python
{"valid": True, "role": "admin", "payload": {...}}
{"valid": False, "role": "", "payload": {}}
```

规则：

- 签名错误：invalid。
- JSON 解码失败：invalid。
- `exp` 过期：invalid。
- `role` 不在 `{"admin", "user"}`：invalid。
- payload 没有 `role`：兼容为 `admin`。

保留现有：

```python
def valid_token(ctx: AppContext, token: str) -> bool:
    return bool(parse_token(ctx, token)["valid"])
```

新增：

```python
def token_role(ctx: AppContext, token: str) -> str:
    parsed = parse_token(ctx, token)
    return str(parsed["role"]) if parsed["valid"] else ""
```

### Cookie

调整：

```python
def set_session_cookie(ctx: AppContext, response: Response, role: str = AUTH_ROLE_ADMIN) -> None:
    response.set_cookie(..., make_token(ctx, role), ...)
```

`/api/auth/setup` 创建管理员密码后，必须签发 admin cookie：

```python
set_session_cookie(ctx, response, AUTH_ROLE_ADMIN)
```

### Capabilities

新增：

```python
def auth_capabilities(role: str) -> dict[str, bool]:
    is_admin = role == AUTH_ROLE_ADMIN
    return {
        "can_manage_connection": is_admin,
    }
```

### Auth status

`auth_status(ctx, request)` 返回：

```json
{
  "enabled": true,
  "configured": true,
  "authenticated": true,
  "role": "user",
  "capabilities": {
    "can_manage_connection": false
  },
  "debug_console_enabled": false
}
```

规则：

- `OPENCREW_AUTH_REQUIRED=0`：
  - `authenticated=true`
  - `role="admin"`
  - `can_manage_connection=true`
- 未登录：
  - `authenticated=false`
  - `role=""`
  - `can_manage_connection=false`
- 管理员登录：
  - `role="admin"`
  - `can_manage_connection=true`
- 普通用户登录：
  - `role="user"`
  - `can_manage_connection=false`

### Login

`POST /api/auth/login` 请求不变：

```json
{"password": "..."}
```

实现顺序：

1. `password = normalized_password(payload.password)`
2. 读取管理员 hash。
3. 管理员 hash 存在且匹配：签发 admin cookie，返回 `{"ok": true, "role": "admin"}`。
4. 读取普通用户 hash。
5. 普通用户 hash 存在且匹配：签发 user cookie，返回 `{"ok": true, "role": "user"}`。
6. 返回 401。

### Setup / Re-setup

`POST /api/auth/setup` 是管理员密码初始化/重置入口。引入 `user` 角色后，不能继续用“任意 valid token”作为已配置状态下的 re-setup 准入条件。

必须实现为：

```python
@router.post("/setup")
def setup(payload: PasswordPayload, request: Request) -> JSONResponse:
    password = normalized_password(payload.password)
    if len(password) < 8:
        return JSONResponse(status_code=400, content={"detail": "Password must be at least 8 characters."})

    if auth_configured(ctx):
        if token_role(ctx, request.cookies.get(SESSION_COOKIE, "")) != AUTH_ROLE_ADMIN:
            return JSONResponse(status_code=403, content={"detail": "Admin role required."})
    elif not setup_request_allowed(request):
        return JSONResponse(status_code=403, content={"detail": "Initial setup requires local access or setup token."})

    ctx.set_setting("auth.password_hash", hash_password(password))
    response = JSONResponse(content={"ok": True, "status": "configured", "role": AUTH_ROLE_ADMIN})
    set_session_cookie(ctx, response, AUTH_ROLE_ADMIN)
    return response
```

安全要求：

- 未配置状态：仍然使用现有 loopback/setup-token 规则。
- 已配置状态：必须要求 `token_role(...) == "admin"`。
- 已配置状态下，普通用户 token 即使签名有效，也必须返回 403。
- 已配置状态下，无 token 或 invalid token 也返回 403。
- 成功 setup/re-setup 后签发 admin cookie。

### Middleware

`build_auth_middleware(ctx)` 逻辑必须按这个顺序：

1. `not auth_required()` 或非 `/api/`：直接 `call_next`。
2. `/api/auth/login` 和 `/api/auth/setup`：保留 body size 限制。
3. public paths 直接放行：
   ```text
   /api/health
   /api/auth/status
   /api/auth/login
   /api/auth/setup
   /api/auth/logout
   ```
4. `not auth_configured(ctx)`：返回 401。
5. share/im 例外保留：
   ```text
   /api/session-share/*
   /api/sessions/im/send
   ```
6. parse cookie token。
7. token invalid：返回 401。
8. path 以 `ADMIN_ONLY_PATH_PREFIXES` 开头且 role 不是 admin：返回 403。
9. 其他 API：`call_next`。

403 body 固定：

```json
{"detail": "Admin role required."}
```

### 不要改动

- 不改 `SESSION_COOKIE` 名称。
- 不改 cookie `httponly`。
- 不改 `SESSION_TTL_SECONDS`。
- 不改未配置状态下 auth setup 的 loopback/setup-token 规则。
- 已配置状态下 auth setup 必须改为 admin-only re-setup，不能接受普通用户 valid token。
- 不记录明文密码。
- 不把普通用户密码写入数据库为可逆值。

## 前端实现合同

文件：`frontend/src/App.jsx`

### Auth state

当前：

```js
const [authState, setAuthState] = createSignal({
  loading: true,
  enabled: true,
  configured: false,
  authenticated: false,
  debug_console_enabled: false,
});
```

调整为：

```js
const [authState, setAuthState] = createSignal({
  loading: true,
  enabled: true,
  configured: false,
  authenticated: false,
  role: "",
  capabilities: { can_manage_connection: false },
  debug_console_enabled: false,
});
```

新增：

```js
const DEFAULT_USER_ROUTE = "#/analysis-v1/tasks";
const canManageConnection = createMemo(() => Boolean(authState().capabilities?.can_manage_connection));
```

### 初始 activeNav

当前 `activeNav` 初始值是 `"connection"`，普通用户登录前会有错误默认方向。

调整：

```js
const [activeNav, setActiveNav] = createSignal("analysis-v1");
```

管理员无 hash 时，在 auth status 返回后再切回 `connection`。

### Route helper

新增 helper：

```js
function navFromHash(hash) {
  ...
}

function isAdminOnlyNav(nav) {
  return nav === "connection" || nav === "metering";
}

function applyRoleRoute(status, currentHash = window.location.hash || "") {
  const canManage = Boolean(status?.capabilities?.can_manage_connection);
  const nav = navFromHash(currentHash);
  if (!canManage && (!currentHash || isAdminOnlyNav(nav))) {
    window.location.hash = DEFAULT_USER_ROUTE;
    setRouteHash(DEFAULT_USER_ROUTE);
    setActiveNav("analysis-v1");
    return "analysis-v1";
  }
  if (canManage && !currentHash) {
    setActiveNav("connection");
    return "connection";
  }
  setActiveNav(nav);
  return nav;
}
```

`metering` 也应视为 admin-only，因为后端会保护 `/api/local-metering/*`。

### 导航显示

Connection nav：

```jsx
<Show when={canManageConnection()}>
  <button title="Connection" ...>...</button>
</Show>
```

Metering nav：

```jsx
<Show when={canManageConnection()}>
  <button title="Local Metering" ...>...</button>
</Show>
```

Connection header actions：

```jsx
<Show when={activeNav() === "connection" && canManageConnection()}>
  ...
</Show>
```

右侧 Connection health：

```jsx
<Show when={activeNav() === "connection" && canManageConnection()} ...>
```

主内容：

- 普通用户不应进入 `activeNav() === "connection"` 分支。
- 如果因为异常状态进入，渲染前应被 `applyRoleRoute` 或 createEffect 拉回 `analysis-v1`。

### 初始化加载拆分

当前 `loadInitialData()` 会无条件调用 admin-only API：

```js
await refresh();          // /api/setup/summary
await loadSkills();       // /api/setup/npc/skills, /api/setup/publish/skills
await loadNpcConfig();    // /api/setup/npc/config
await loadAsrConfig();    // /api/setup/asr/config
await loadMihomoConfig(); // /api/setup/mihomo/config
await loadPublishConfig();// /api/setup/publish/config
await loadSessions(hashTaskId);
if (activeNav() === "metering") await loadMeteringReport();
```

必须拆成：

```js
async function loadUserInitialData(hashTaskId = null) {
  await loadSessions(hashTaskId);
}

async function loadAdminInitialData(hashTaskId = null) {
  await refresh();
  await loadSkills();
  await loadNpcConfig();
  await loadAsrConfig();
  await loadMihomoConfig();
  await loadPublishConfig();
  await loadSessions(hashTaskId);
  if (activeNav() === "metering") await loadMeteringReport();
}

async function loadInitialData(hashTaskId = null, status = authState()) {
  if (status?.capabilities?.can_manage_connection) {
    return loadAdminInitialData(hashTaskId);
  }
  return loadUserInitialData(hashTaskId);
}
```

登录后：

```js
const status = await api.authStatus();
setAuthState({ ...status, loading: false });
applyRoleRoute(status);
await loadInitialData(parseSessionHash(window.location.hash || ""), status);
```

初次 auth status 后同样处理。

### Hash change

`hashchange` 处理必须尊重角色：

```js
const nextNav = navFromHash(nextHash);
if (!canManageConnection() && isAdminOnlyNav(nextNav)) {
  window.location.hash = DEFAULT_USER_ROUTE;
  setRouteHash(DEFAULT_USER_ROUTE);
  setActiveNav("analysis-v1");
  return;
}
setActiveNav(nextNav);
```

### 退出登录 / 切换身份

区分管理员和普通用户后，前端必须提供退出当前登录的入口，否则无法从普通用户切换到管理员，也无法让管理员完成配置后回到普通用户身份。

当前后端已经有：

```text
POST /api/auth/logout
```

它会清除 `opencrew_session` cookie。当前 `frontend/src/App.jsx` 没有调用该 API，也没有可见退出按钮。第一版必须补上前端入口。

新增 API helper：

```js
authLogout: () => request("/api/auth/logout", { method: "POST" })
```

新增处理函数：

```js
async function logoutCurrentUser() {
  await api.authLogout();
  setAuthState((prev) => ({
    ...prev,
    authenticated: false,
    role: "",
    capabilities: { can_manage_connection: false },
    loading: false,
  }));
  setActiveNav("analysis-v1");
  setRouteHash(DEFAULT_USER_ROUTE);
  window.location.hash = DEFAULT_USER_ROUTE;
}
```

UI 要求：

- 已登录时必须显示“退出登录”按钮。
- 管理员和普通用户都能看到该按钮。
- 必须放在左侧导航底部，作为全局导航动作。
- 不能放在 `Connection` 页面内，否则普通用户看不到。
- 按钮文案：“退出登录”。
- 可选显示当前身份：“管理员” / “普通用户”。
- 点击后回到登录表单；如果 auth status 仍显示未登录，应不再加载业务数据。
- 退出登录不清理任务数据，不取消后台任务。

### 普通用户业务页面中的 setup API

普通用户业务页面不能因为 `/api/setup/*` 403 显示全局错误。

当前代码走查结论：

- `AnalysisV1Module.jsx` 对 TTS config 已经是 `.catch(() => null)`，页面挂载不会因为 403 崩溃。
- `OCRebuildModule.jsx`、`OCStoryBoardModule.jsx`、`KouboStoryboardTts.js` 的 TTS/model config 多数是惰性调用，只在用户触发 TTS/语音相关动作时请求 `/api/setup/media-models/*`。
- 因此第一版不是“普通用户登录即崩”的问题，而是“普通用户触发高级 TTS/model 配置动作时应局部降级”的问题。

第一版要求：

- root `App.jsx` 普通用户初始化不调用 `/api/setup/*`。
- Connection 页面不可见。
- `Metering` 页面不可见。
- 如果某个业务子模块主动调用 `/api/setup/media-models/*` 并收到 403，应局部降级：
  - 对 TTS/model 配置控件显示“管理员配置后可用”或隐藏配置入口。
  - 不把 403 冒泡成全局崩溃。

不要求第一版改造所有业务子模块为 runtime model endpoint。

## 代码审核结论

### 后端审核

1. `auth.py` 目前 `make_token()` 无 role。
   - 必须新增 role payload，否则 status 无法区分 admin/user。

2. `valid_token()` 当前只返回 bool。
   - 必须新增 `parse_token()` / `token_role()`，middleware 需要 role。

3. `auth_status()` 当前只返回 authenticated/debug_console。
   - 必须返回 `role` 和 `capabilities`。

4. `login()` 当前只校验一个 password。
   - 必须按 admin first、user second 校验。

5. `build_auth_middleware()` 当前任意 valid token 都能访问所有 API。
   - 必须在 token valid 后、`call_next` 前增加 admin-only path gate。

6. `/api/auth/setup` 当前创建 cookie 时没有 role。
   - 必须签发 admin role cookie。

7. `/api/auth/setup` 当前已配置状态下只检查 `valid_token()`。
   - 这是 P0 提权漏洞。普通用户 token 也是 valid token，若不改，会允许普通用户覆盖管理员密码并领取 admin cookie。
   - 必须改为已配置状态下要求 `token_role(...) == "admin"`。
   - 必须增加测试：user cookie 调用 `/api/auth/setup` 返回 403，且管理员密码不被覆盖。

8. 不能只保护前端。
   - `/api/setup/*`、`/api/model-config/*` 和 `/api/local-metering/*` 必须由 middleware 拦截。

9. denylist 覆盖当前敏感面，但需要测试守卫。
   - 当前敏感路由经核对均在 `/api/setup/*`、`/api/model-config/*` 或 `/api/local-metering/*` 下。
   - 由于 denylist 是默认开放模型，必须加 source/contract test 约束新增敏感 router 继续挂在 admin-only prefix 下。

### 前端审核

1. `activeNav` 当前默认 `"connection"`。
   - 普通用户登录前不应默认落到 Connection，改为 `"analysis-v1"`，管理员 auth status 后再进入 Connection。

2. `loadInitialData()` 当前无条件加载 admin-only API。
   - 必须拆分 user/admin initial data，否则普通用户登录后会立即收到 403。

3. Connection nav 当前无条件渲染。
   - 必须用 `canManageConnection()` 包裹。

4. Metering nav 当前无条件渲染。
   - 因 `/api/local-metering/*` admin-only，Metering 也必须隐藏。

5. hashchange 当前只按 hash 设置 activeNav。
   - 必须拦截普通用户访问 `connection` / `metering`。

6. 业务模块复用 `/api/setup/media-models/*`。
   - 这是第一版最大兼容风险，但实际触发点主要是惰性 TTS/model 动作，不是普通用户登录即崩。
   - 第一版不放开该 API；业务模块应局部 catch 403 或后续引入脱敏 runtime endpoint。

## 测试合同

### 后端 contract tests

建议新增或扩展：

```text
backend/tests/contracts/test_lightweight_role_login_contract.py
```

必须覆盖：

1. admin env password login
   - env: `OPENCREW_APP_PASSWORD=admin-password`
   - POST `/api/auth/login`
   - GET `/api/auth/status`
   - assert `role == "admin"`
   - assert `capabilities.can_manage_connection is True`

2. user env password login
   - env: `OPENCREW_USER_PASSWORD=user-password`
   - POST `/api/auth/login`
   - GET `/api/auth/status`
   - assert `role == "user"`
   - assert `capabilities.can_manage_connection is False`

3. admin priority
   - admin password 和 user password 相同
   - login 后 role 必须是 `admin`

4. user cannot access setup
   - user cookie
   - GET `/api/setup/summary`
   - assert HTTP 403
   - assert detail `"Admin role required."`

5. user cannot re-setup admin password
   - user cookie
   - POST `/api/auth/setup` with a new password
   - assert HTTP 403
   - assert detail `"Admin role required."`
   - assert original admin password still works
   - assert new submitted password does not work as admin

6. admin can re-setup admin password
   - admin cookie
   - POST `/api/auth/setup` with a new password
   - assert HTTP 200
   - assert response role `admin`
   - assert old admin password no longer works
   - assert new admin password works

7. unauthenticated configured setup is not allowed
   - no cookie
   - POST `/api/auth/setup`
   - assert HTTP 403
   - assert detail `"Admin role required."`

8. user cannot access local metering
   - user cookie
   - GET `/api/local-metering/report`
   - assert HTTP 403

9. user cannot access model config compatibility routes
   - user cookie
   - GET `/api/model-config/image/config`
   - assert HTTP 403
   - POST `/api/model-config/tts/voices/preview`
   - assert HTTP 403

10. user can access business API
   - user cookie
   - GET 一个非 setup/model-config/local-metering API
   - assert middleware calls next / returns non-403

11. unauthenticated admin-only API
   - no cookie
   - GET `/api/setup/summary`
   - assert HTTP 401, not 403

12. auth disabled
   - env: `OPENCREW_AUTH_REQUIRED=0`
   - GET `/api/auth/status`
   - assert `authenticated is True`
   - assert `role == "admin"`
   - assert `can_manage_connection is True`

13. old token compatibility
   - create token without role using old payload shape
   - assert `parse_token(...).role == "admin"`

14. invalid role in token
   - signed token with `role="owner"`
   - assert invalid, no access

15. logout clears session
   - logged-in admin or user cookie
   - POST `/api/auth/logout`
   - assert response clears `opencrew_session`
   - subsequent GET `/api/auth/status` returns `authenticated is False`

16. admin-only prefix guard
   - source-level or route-registration contract test
   - assert `ADMIN_ONLY_PATH_PREFIXES` contains `/api/setup/`、`/api/model-config/` and `/api/local-metering/`
   - assert known sensitive route modules expose only these sensitive prefixes:
     - `step1_opencode.py` -> `/api/setup/opencode`
     - `step2_tunnel.py` -> `/api/setup/npc`
     - `step3_publish.py` -> `/api/setup/publish`
     - `step3_wecom.py` -> `/api/setup/wecom`
     - `step4_verify.py` -> `/api/setup/verification`
     - `mihomo.py` -> `/api/setup/mihomo`
     - `asr_config.py` / ModelConfig ASR router -> `/api/setup/asr`
     - `media_model_config.py` -> `/api/setup/media-models`
     - `ModelConfig/backend/opcrew_model_config/router.py` -> `/api/model-config`
     - `local_metering.py` -> `/api/local-metering`
   - if a future sensitive route is added outside these prefixes, the test must fail until it is moved under an admin-only prefix or explicitly added to the policy.

17. user model policy alias catalog
   - user cookie
   - GET `openflow.prompt` / `analysis_v1.prompt` catalog endpoint
   - assert response contains only `Max` / `Flash` display labels
   - assert response does not contain `OpenAI`、`OpenCode Zen`、`GPT-5.5`、`DeepSeek v4 flash free`

18. user model policy alias resolve
   - user cookie
   - POST an `analysis_v1.prompt` or `analysis_v1.run` action with `Max/Max`
   - assert backend resolves to `openai/gpt-5.5` internally
   - assert returned payload is masked back to `Max` / `Max`
   - repeat for `Flash/Flash` -> `opencode/deepseek-v4-flash-free`

19. user raw model submission rejected
   - user cookie
   - POST `analysis_v1.run` with raw `OpenAI` / `gpt-5.5` or `openai` / `gpt-5.5`
   - assert HTTP 403 or 422
   - assert no run/attempt is created from the rejected request

20. user ASR default fixed
   - user cookie
   - POST `analysis_v1.run` with omitted ASR fields, or with `asr_mode=default` and `allow_cloud_asr_data_transfer=true`
   - assert request can proceed past authorization
   - assert created attempt/run state records `asr_mode=default` and `allow_cloud_asr_data_transfer=true`
   - POST `analysis_v1.run` with `asr_mode=local`, `asr_mode=cloud`, or `allow_cloud_asr_data_transfer=false`
   - assert HTTP 403 or 422

21. Koubo TTS timing hidden provider/model default
   - user cookie
   - action payload for `koubo_storyboard.tts_timing` does not include provider/model
   - assert backend uses `google/gemini-3.1-flash-tts`
   - user-provided provider/model override is rejected with HTTP 403 or 422

22. Koubo host product Max/Flash aliases
   - user cookie
   - GET host product prompt model catalog
   - assert `Max` / `Flash` provider/model alias options are returned
   - assert submitting `Flash/Flash` resolves internally to `opencode/deepseek-v4-flash-free`
   - assert submitting raw `OpenCode Zen` / `DeepSeek V4 flash free` is rejected

### 前端 source contract tests

建议新增或扩展 source-level tests，至少断言：

1. `DEFAULT_USER_ROUTE = "#/analysis-v1/tasks"` 存在。
2. `canManageConnection` 使用 `authState().capabilities?.can_manage_connection`。
3. Connection nav 被 `<Show when={canManageConnection()}>` 包裹。
4. Metering nav 被 `<Show when={canManageConnection()}>` 包裹。
5. 存在 `loadUserInitialData` 和 `loadAdminInitialData`。
6. `loadUserInitialData` 不包含：
   - `refresh(`
   - `loadSkills(`
   - `loadNpcConfig(`
   - `loadAsrConfig(`
   - `loadMihomoConfig(`
   - `loadPublishConfig(`
   - `loadMeteringReport(`
7. hashchange 中普通用户访问 admin-only nav 会跳到 `DEFAULT_USER_ROUTE`。
8. `AnalysisV1Module` 接收 `roleAccess`，仅用于隐藏 ASR select / 云端 ASR 复选框等非 catalog UI。
9. `KouboStoryBoardModule` 或其 Timing menu 接收 `roleAccess`，普通用户不渲染 Provider/Model select，仍渲染 Voice 和 TTS Tempo。
10. `frontend/src/App.jsx`、`AnalysisV1Module.jsx`、`KouboHostProductBuilder.jsx` 不包含普通用户 alias->real 映射表；alias 显示来自后端 masked catalog。
11. `AnalysisV1Module.jsx` 普通用户不渲染 ASR 下拉框和云端 ASR 复选框；提交 payload 省略 ASR 字段或使用 `asr_mode=default`、`allow_cloud_asr_data_transfer=true`。
12. source test 明确不要求 `OpenClipModule`、`OCRebuildModule`、`OCStoryBoardModule` 接入 `roleAccess`，因为这些页面已废弃。

## 手工 smoke

### 管理员

1. 启动：
   ```bash
   OPENCREW_AUTH_REQUIRED=1 OPENCREW_APP_PASSWORD=admin-password OPENCREW_USER_PASSWORD=user-password ...
   ```
2. 用 `admin-password` 登录。
3. `/api/auth/status` 返回 `role=admin`。
4. 页面显示 `Connection` 和 `Metering`。
5. GET `/api/setup/summary` 返回 200。

### 普通用户

1. logout。
2. 用 `user-password` 登录。
3. `/api/auth/status` 返回 `role=user`。
4. 默认进入 `#/analysis-v1/tasks`。
5. 不显示 `Connection`。
6. 不显示 `Metering`。
7. 手动打开根路径空 hash，不进入 Connection。
8. 手动访问 `#/metering`，被重定向到 `#/analysis-v1/tasks`。
9. GET `/api/setup/summary` 返回 403。
10. GET `/api/local-metering/report` 返回 403。
11. `视频分析（口播）` Prompt model 弹窗只显示 `Max` / `Flash`，不显示 `OpenAI` / `OpenCode Zen`。
12. `视频分析（口播）` 运行设置弹窗：
    - “模型服务商”只显示 `Max` / `Flash`。
    - 选 `Max` 时“模型”只显示 `Max`。
    - 选 `Flash` 时“模型”只显示 `Flash`。
    - 不显示 `ASR` 下拉框。
    - 不显示云端 ASR 复选框，后台按 `cloud/true` 运行。
    - `TTS Builder` 和 `StoryBoard` 保留原有选项。
13. `故事版（口播）` Timing/TTS 设置：
    - 不显示 Provider。
    - 不显示 Model。
    - 仍显示 Voice。
    - 仍显示 TTS Tempo。
14. `故事版（口播）` Host/Product Builder model 弹窗显示 `Max` / `Flash` 选项。

## 上线配置

管理员：

```bash
OPENCREW_APP_PASSWORD=...
```

普通用户：

```bash
OPENCREW_USER_PASSWORD=...
```

更安全的生产配置可使用 hash：

```bash
OPENCREW_APP_PASSWORD_HASH=pbkdf2_sha256$...
OPENCREW_USER_PASSWORD_HASH=pbkdf2_sha256$...
```

如果担心旧 token 继续按 admin 兼容，部署时轮换：

```text
auth.session_secret
```

或清空浏览器 cookie。

## 自审

### 正确性

- 前端隐藏不是安全边界；本文要求后端 middleware 强制 admin-only gate。
- role 在 HMAC 签名 token 内，客户端不能改 role 而保持签名有效。
- 无用户名也能区分角色，因为角色由密码匹配结果决定。
- 普通用户初始化不请求 admin-only API，避免登录后立即 403。
- admin/user 密码相同按 admin，避免管理员意外降级。
- 模型 provider/model 隐藏必须以后端 alias catalog 和 action 解析为安全边界；只改前端显示不满足“隐藏真实 provider”的目标。
- 本轮明确排除 `OC - Analysis`、`OC - Rebuild`、`OC - StoryBoard`，避免在废弃页面上扩大实现范围。

### 简洁性

- 不加表。
- 不加用户 UI。
- 不加权限矩阵。
- 不加迁移。
- 登录权限只新增一个 role 字段、一组普通用户密码配置、一个 denylist gate。
- 模型策略只保留两种 mode：`alias` 和 `hide`。
- alias 类 surface 以后端 masked catalog 为唯一策略源，前端不镜像 alias 映射。

### 兼容性

- 管理员登录保持兼容。
- setup 仍然创建管理员密码。
- 普通用户密码未配置时，系统行为等同当前版本。
- 旧 token 按 admin，升级不强制重新登录。

### 风险

- denylist 依赖路径约定。未来新增管理 API 必须继续放在 `/api/setup/`、`/api/model-config/`、`/api/local-metering/`，或明确加入 admin-only prefix；本文已要求加 CI/contract 守卫。
- 业务模块当前复用 `/api/setup/media-models/*`，普通用户可能在某些惰性 TTS/model 动作中看到降级状态。第一版接受这个取舍，因为放开这些接口会暴露模型配置元信息。
- catalog serializer 不是单点；OpenFlow、Analysis V1、Koubo Host/Product、OCRebuild 等各有 producer/resolver。本文已按真实路径列接入矩阵，实施时必须逐 surface 接入。
- `analysis_v1.tts_preview` 第一版不改动，仍可能沿用当前 voice/provider 来源展示；这是用户明确选择的范围取舍。
- `openflow.skill` 当前像不可达遗留 UI；如果未来恢复入口，必须重新纳入 `Max` / `Flash` 策略和测试。
- `/api/auth/setup` 是提权敏感点。已配置状态下必须 admin-only；任何实现若继续用 `valid_token()` 判断 re-setup 准入都是安全缺陷。
- 旧 token 按 admin 偏宽松。敏感部署应轮换 session secret。
- 普通用户仍共享一个身份，不能审计具体个人行为。这符合“不引入复杂用户机制”的约束。

## 结论

本方案可以直接实施。第一版坚持：

1. 两套密码。
2. signed cookie role。
3. 普通用户默认 `#/analysis-v1/tasks`。
4. 前端隐藏 `Connection` / `Metering`。
5. 后端保护 `/api/setup/` / `/api/model-config/` / `/api/local-metering/`。
6. 普通用户模型选择按本文 surface 策略使用 `Max` / `Flash`、隐藏 Provider/Model 或固定默认值。
7. 不新增用户表或权限矩阵。
