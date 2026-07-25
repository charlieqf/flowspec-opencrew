# OpenCrew LLM 调用、Key 管理与计量计费设计

版本：v0.9

日期：2026-05-28

状态：Phase 0 本机实现已完成本地验收；公网 nip.io 入口等待路由/NAT 放通

实施验收记录（2026-05-28）：

- 已实现并通过本地 smoke：应用登录门禁、本地密钥库 `api_key_ref`、旧 `api_key_ciphertext` 迁移清空、mihomo 订阅 URL 本地密钥库存储、境外 provider 走 mihomo 策略、本地 `local_usage_log`。
- 已通过契约/构建验证：后端 contract tests 30/30，前端 production build 通过。
- Caddy 已验证：`Host: 1.42.112.164.nip.io` 经本机/LAN 到 Caddy 可正常反代 OpenCrew；Caddy 监听 `*:80`，Basic Auth 可用。
- 公网 `http://1.42.112.164.nip.io` 从外部路径仍超时/拒绝连接，判断为路由器公网入站/端口转发/NAT 回环未放通；这不是 OpenCrew/Caddy 配置阻塞。需要把公网 80/443 转发到 Mac mini `192.168.0.6`，或改用受控隧道后再做公网 nip.io 复验。

v0.9 变更：(1) 区分本文档「Phase 0」与 `opencrew_p0_development_plan.md` 的「P0」（并行两轨），盒子端 schema 改动走 `db/migrations.py`（§15）；(2) Phase 0 盒子本地用量改为独立极简表 §7.9 `local_usage_log`，明确 §7.1–§7.8 八表为 Phase 1+ 网关-only（§7 注 / §17.1）；(3) Phase 0 本机 key / 订阅 URL 存储由「macOS Keychain」改为「盒子本地密钥库：0600 加密文件 + 本机 0600 主密钥文件（Keychain 仅 opt-in）」，全文措辞统一（§3.1 / §11.3 / §17.0.1 等）。

v0.8 变更：(1) §14 合规改为分阶段表述——明确 Phase 0 内置 mihomo binary + 用户自填订阅仍构成「随产品分发代理引擎」的残留法律暴露（待 §16.4 法务确认），原「本设计已去掉盒子 mihomo」仅对 Phase 1+ 成立；(2) §3.2/§15 标注交付形态为 **LAN-only**，公网 nip.io 仅为内部异地测试的受控临时通道、非交付配置。

v0.7 变更：在 v0.6 的 Phase 0 Local Box Trial 基础上，明确 Mac mini 的产品形态是 **LAN Web appliance**：正常用户只能通过局域网网页使用系统，不需要也不应进入 Mac mini；backend/frontend/OpenCode/mihomo/PostgreSQL 均只监听 localhost 或内网受控入口；所有配置、健康检查、日志摘要、服务重启、mihomo 订阅、provider key 管理都必须通过 Web UI/launchd/reverse proxy 完成；维护入口独立受控。

相关文档：`docs/opencrew_workflow_data_storage_implementation_prd.md`、`docs/opencrew_macmini_nipio_deployment_plan.md`、`docs/opencrew_p0_development_plan.md`、`docs/opencrew_p0_migration_runbook.md`、`docs/opencrew_phase0_5_local_metering_billing_supplement.md`

---

## 1. 背景与目标

OpenCrew 以「一台 Mac mini + 系统」的形态按客户售卖，单租户。系统内置多家 LLM/媒体模型能力。当前为了快速小范围试用，Phase 0 采用 **Local Box Trial**：真实 provider key（OpenAI、Gemini、xAI、阿里百炼、MiniMax 等）先全部存放在 Mac mini 本机，由本机直接调用 provider；暂不搭建运营方网关、统一 key、余额扣费与远端台账。

Phase 0 的目标是验证产品体验、工作流稳定性、局域网交付与少量客户可用性，不以 provider 用量差价作为强商业闭环。不同模型成本不同，若使用运营方 key，必须用 provider 侧硬预算/独立 key 控制账单风险；若使用客户自己的 key，则 provider 账单由客户自担。

本设计文档同时定义两条路线：

1. **Phase 0：Local Box Trial（当前先做）**。真实 key 在 Mac mini；本机直连 provider；内置 mihomo 但不提供订阅 URL；用户只能通过局域网 Web 使用；收入模型以硬件/软件/试用授权/服务费为主。
2. **Phase 1+：Managed Gateway（后续商业化）**。真实 key 迁到运营方网关；盒子只持统一 key；网关负责计量、余额、差价、限额与台账。

### 1.1 核心结论（前置讨论已确定）

- **Phase 0 先做本机 key 方案**：真实 provider key 放在 Mac mini，用于小范围试用和快速交付；不搭建网关、不做强余额扣费、不以差价计费作为核心收入。默认由运营方为每台 Mac mini 签发独立 provider key/子账号，不使用运营方主 key。
- **Phase 0 必须假设 key 与 Python 代码可被强攻击者提取**：客户物理持有设备，保护只能提高门槛，不能当成安全边界。运营方 key 必须每台/每客户独立、provider 侧硬预算、可随时吊销轮换。
- **用户使用方式是 LAN Web appliance**：正常用户只能通过局域网网页访问 OpenCrew；不需要 SSH、终端、Finder 或直接操作 Mac mini。OpenCrew 作为 launchd 服务自启动；Debug Console/管理页隐藏或独立鉴权；维护入口由运营方受控。
- **内置 mihomo 但不提供订阅 URL**：产品只提供本机代理运行与配置入口；用户自行填写订阅 URL 并承担网络可达性与合规责任；mihomo 只监听 `127.0.0.1`，不得暴露为局域网代理。
- **Managed Gateway 仍是后续商业化目标**：当需要规模化售卖、强制差价计费、统一限额、运营方用量台账时，必须迁移到 Phase 1+ 网关模式。

## 2. 范围与非目标

**范围**：Phase 0 本机 key 小范围试用架构、Mac mini 本机 key 存储、mihomo 本机代理、局域网 Web 使用模式、Python 代码发布保护、provider 账单风险控制、与现有代码的适配；同时保留 Phase 1+ Managed Gateway 的拓扑、统一 key、价格本、用量台账、计量与加价设计。

**非目标**：Phase 0 不实现运营方网关、远端余额扣费、强制 provider 差价计费、客户自托管网关；Python 代码保护不承诺对抗客户物理持有设备后的逆向，只作为发布态保护与抬高门槛。

## 3. 信任边界与威胁模型

| 区域 | 控制方 | 可信度 | 放什么 |
| --- | --- | --- | --- |
| 盒子（Mac mini，Phase 0） | 客户物理持有 | 对「秘密、代码与强计量」不可信 | 真实 provider key、mihomo 订阅 URL、OpenCrew 系统、Python 发布包 |
| 网关（Phase 1+） | 运营方 | 可信 | 真实 provider key、计量、限额、价格本、台账 |

威胁与对策：

- **Phase 0 真实 key 泄露**：必须按「可能泄露」设计。每台/每客户使用独立 provider key 或子账号；provider 侧设置硬预算、RPM/TPM 限速、模型权限白名单；试用结束立即轮换/吊销。不要把运营方主账号 key 放进客户 Mac mini。
- **Phase 0 绕过本地计量**：盒子在客户手上，本地 usage 只能作为诊断/客户自查，不作为运营方强计费账本。商业上应收固定试用费/软件服务费，或让客户自带 provider key 并自担 provider 账单。
- **Python 代码泄露/逆向**：发布态保护只能抬高门槛。需避免交付原始 repo；使用打包/混淆/编译、签名、文件权限、专用运行用户、禁用 shell 登录等组合措施；核心商业秘密不应只靠本机 Python 源码保护。
- **mihomo 订阅 URL 泄露**：订阅 URL 由用户填写、用户负责；存入盒子本地密钥库，不落日志/支持包；mihomo 只监听 `127.0.0.1`，不暴露 LAN 代理端口。
- **用户进入 Mac mini**：产品路径是用户只用 LAN Web，不进系统。交付配置应禁用 SSH/远程登录、不给普通用户管理员密码、不暴露 Finder/终端操作步骤；如需维护，只保留运营方受控维护通道。前端/后端/OpenCode/PostgreSQL/mihomo 原始端口不得直接暴露到局域网。
- **Phase 1+ 网关风险**：若后续迁移到 Managed Gateway，再启用统一 key、mTLS、预授权冻结与网关台账，重新获得强计费与差价控制。

### 3.1 Phase 0 本机 Key 试用架构（当前实施）

Phase 0 目标是快速试用，不做网关。推荐形态：

```
用户浏览器(局域网)
  → http(s)://opencrew.local 或 http://<macmini-lan-ip>
  → Caddy/Nginx 反向代理(仅 LAN)
  → frontend(127.0.0.1) + backend(127.0.0.1)
  → 本机 provider client
      → 境内 provider 直连
      → 境外 provider 经本机 mihomo(127.0.0.1 mixed/http/socks)
  → workspace 本机落盘
```

Phase 0 组件与硬边界：

1. **本机 key 管理**：provider key 存**盒子本地密钥库（默认 `${OPENCREW_DATA_DIR}/secrets.enc`，0600 加密文件 + 本机 0600 主密钥文件；生产安装器可显式设 `OPENCREW_SECRET_STORE_PATH=/Library/Application Support/OpenCrew/secrets.enc`）**；数据库只存 `key_ref`/`has_key`/provider 配置，不再把真实 key 明文写入 `api_key_ciphertext`。存量 `api_key_ciphertext` 仅作为一次性迁移输入，迁移后清空。
2. **provider 独立 key**：Phase 0 默认由运营方按设备/客户拆分 provider key 或子账号，并在 provider 侧设预算与限速；客户 BYOK 不作为首版默认路径。
3. **mihomo 本机代理**：预装 mihomo binary 与默认配置模板，默认禁用，不内置订阅 URL；订阅 URL 由用户在 Web UI 填写后启用；mihomo 只监听 `127.0.0.1`，OpenCrew HTTP client 按 provider/模型选择是否走代理。
4. **局域网 Web 使用**：backend/frontend 只监听 `127.0.0.1`；对局域网只暴露反向代理端口；默认使用 OpenCrew 应用登录；Debug Console 与管理设置不暴露给普通用户。
5. **无用户 shell 使用路径**：交付目标是不让用户 SSH/终端操作；通过 launchd 自启动、Web 配置、Web 健康检查、Web 日志摘要完成日常使用。
6. **Python 发布保护**：不交付源码 repo；首版发布包用 PyInstaller 或 Nuitka 打包；文件 root/admin 拥有、运行用户最小权限、代码签名、launchd 管理。
7. **本地 usage**：记录到本机数据库，供客户自查/运营方远程排障；不是强计费账本。

### 3.2 LAN Web Appliance 交付约束

Phase 0 的客户体验应像一台局域网设备，而不是一台需要用户登录维护的电脑。

**正常用户唯一入口**

- 用户访问 `http://opencrew.local`、`https://opencrew.local` 或 `http(s)://<macmini-lan-ip>`。
- Web UI 必须覆盖日常操作：首次初始化、登录密码、provider key、mihomo 订阅 URL、模型选择、任务创建、文件下载、健康检查、脱敏日志摘要、服务重启、支持包导出。
- 不要求用户 SSH、打开终端、修改文件、运行脚本、使用 Finder 进入应用目录。

**端口与进程暴露**

- `backend`、`frontend dev/static server`、OpenCode、PostgreSQL、mihomo 只监听 `127.0.0.1`。
- 局域网只暴露 Caddy/Nginx 的 `80/443` 或约定端口；反向代理再转发到 localhost。
- 不直接暴露 backend raw port、OpenCode raw port、PostgreSQL、mihomo proxy port、Debug Console。
- Caddy/Nginx 默认转发到 OpenCrew 应用登录；普通用户账号不能访问 Debug Console、系统设置、日志下载、key/mihomo 管理以外的敏感接口。Basic Auth/IP allowlist 仅作为内部异地测试或受控维护入口的附加保护。

**系统账号与启动**

- 创建独立 `opencrew` 运行用户；OpenCrew 文件由 root/admin 拥有，运行用户只有必要读/写权限。
- backend、frontend、OpenCode、mihomo、PostgreSQL 由 launchd 管理，自启动、崩溃重启、统一日志路径。
- 默认关闭 SSH/Remote Login/Screen Sharing；如客户场景必须开启，需作为单独维护选项并记录在交付清单。
- 不给普通客户管理员密码作为正常使用方式；运营方维护账号与客户使用账号分离。Phase 0 默认设置单独运营方维护账号，普通用户完全关闭 Debug Console。

**维护入口**

- 优先通过 Web UI 生成一次性支持包，内容脱敏，不含 provider key、mihomo 订阅 URL、完整 prompt、源码。
- 如需远程维护，采用用户显式开启、限时、可审计的通道（如 VPN/Tailscale/反向隧道），默认关闭。
- **交付形态为 LAN-only，公网 nip.io 仅限内部异地测试、非交付配置**：交付盒子默认不做公网端口转发；如需异地测试，按上一条作为用户显式开启、限时、可审计的受控通道处理（优先 VPN/Tailscale 或 IP allowlist + HTTPS + 强 Basic Auth），使用一次性低预算 provider key、测试后吊销；公网入口不得随交付镜像保留（参见 `docs/opencrew_macmini_nipio_deployment_plan.md`）。
- 维护操作不能成为客户日常使用的前置条件；所有常见问题必须能通过 Web UI 诊断或恢复。

**验收口径**

- 新 Mac mini 开机后，无需终端命令即可访问 Web UI 并完成初始化。
- 普通用户无法从局域网访问 backend raw port、OpenCode raw port、PostgreSQL、mihomo proxy port。
- 断电重启后，OpenCrew 全栈自动恢复；Web 健康页能显示 backend/frontend/OpenCode/PostgreSQL/mihomo/provider 连通性。
- 日志和支持包经脱敏检查，不含 key、订阅 URL、代码源码与敏感路径。

Phase 0 不解决的问题：

- 不能强制模型差价计费。
- 不能从根本上防止客户物理提取 key/代码。
- 不能保证境外 provider 在客户网络中可达；代理订阅 URL 与合规责任由客户承担。
- 不适合大规模、无界账单风险、运营方主账号 key 下发。

## 4. Phase 1+ Managed Gateway 目标架构（后续）

### 4.1 双网关节点

| 节点 | 位置 | 承载模型 | 理由 |
| --- | --- | --- | --- |
| 境内网关 | 国内云 | Wan、CosyVoice、Qwen-TTS、MiniMax、Paraformer(ASR) | 境内模型直连，低延迟，不依赖跨境链路 |
| 境外网关 | 香港/新加坡 | OpenAI、Gemini、xAI、对话 LLM | 离 provider 近、无墙；兼作出海出口 |

两节点共享同一套逻辑数据模型与价格本，用量汇总到统一台账（可单库多写或各自写库后归并）。

### 4.2 控制面 / 数据面分离

- **控制面**（走网关）：鉴权、限额、调用 provider 的生成请求、计量、返回结果对象引用。只有小 JSON。
- **数据面**：媒体大文件不经网关「逐次串流」给盒子。
  - 境内 URL（阿里 OSS 等）：盒子直连境内 CDN。
  - 境外视频：盒子在大陆无法直连境外 CDN，**采用「网关转存境内 OSS」交付，盒子不装 mihomo**（详见 §4.4）。这条是网关的**真实数据面**（每条视频搬一次）。境外图片/TTS 字节小，由网关那次调用直接带回。

### 4.3 组件

1. **Ingress / 鉴权**：校验 mTLS 客户端证书 + 统一 key + 盒子绑定。
2. **授权与限额服务**：解析客户权限、可用模型、余额/配额；花钱前预授权冻结（§8.1）。
3. **路由 + key 池**：按目标模型选真实 key（含限速轮换），调用 provider 适配层。
4. **Provider 适配层**：对话走 OpenAI 兼容传输；媒体各家自研适配。
5. **计量 / 价格 / 台账**：抽取用量 → 价格快照 → 写 line items、结算余额。
6. **转存 job**：境外视频从 provider 拉取并上传境内 OSS（§4.4）。
7. **运营方控制台**：客户、余额、用量、毛利、key 池管理、真实 key 录入。

### 4.4 媒体字节交付与境外视频转存（已定）

物理约束：「境外视频 + 网关零带宽 + 盒子无代理」三者不可兼得——境外字节在境外 CDN 上，盒子在大陆够不着，要么经网关、要么经盒子代理。本设计**选择去掉盒子代理（mihomo）**，由网关把字节搬到境内对象存储交付。

媒体大文件一律不经网关「逐次串流」给盒子，按来源分两条路：

- **境内媒体（Wan / CosyVoice / Qwen / MiniMax / Paraformer）**：provider 返回境内 OSS URL，盒子直连境内 CDN 下载，网关零字节。
- **境外视频（Sora/Veo 等）**：采用「网关转存境内 OSS」，盒子不装 mihomo：

```
境外视频生成完成
 → 境外网关节点 拉取 provider CDN 的视频(abroad→abroad)
 → 直通流式上传到 境内对象存储(OSS/COS)          ← 唯一一次跨境
 → 在 usage_ledger.result_ref 记【OSS 对象引用 object key】(不存签名 URL)
 → 盒子按需向网关请求短 TTL 下载 URL → 从境内 OSS 直连下载(全程境内,无需代理)
 → 重复观看/下载由境内 OSS 服务,不再产生网关带宽
```

- **盒子永不碰境外地址，因此不需要 mihomo / 订阅**，消除翻墙打包的法律风险与订阅被抠风险。
- **网关跨境带宽 ≈ 每条视频搬运一次**（与观看次数无关）；重复服务由境内 OSS 承担（廉价），网关不再串流。
- 境外图片 / TTS 字节小，由生成调用的响应直接带回，无需转存。

带宽对比：

| 交付方式 | 网关境外视频带宽 | 盒子需代理 | 风险 |
| --- | --- | --- | --- |
| 网关逐次串流（per view） | size × 观看次数 | 否 | — |
| **网关转存境内 OSS（本设计采用）** | ≈ 1× size / 每次生成 | 否 | 低 |
| 盒子 mihomo 直取 | 0 | 是 | 翻墙打包 + 订阅可抠 |

转存 job 规格（境外视频是真实数据面，需工程化）：

- **异步 job 队列**：生成完成后入队转存任务，不阻塞生成响应。
- **重试 / 断点续传**：拉取或上传失败可重试；大文件支持分片/断点。
- **校验**：传输完成校验大小 + checksum，确保对象完整。
- **状态机**：`pending → fetching → uploading → ready → failed`，状态写库；盒子按 `ready` 才能取下载 URL。
- **backpressure / 配额**：限制并发转存与单节点带宽，过载时排队而非压垮节点。
- **成本核算**：每条转存的跨境字节计入运营成本（挂到对应 request/usage），供毛利核算与定价校正。

对象引用与按需签发（不在账本存签名 URL）：

- 账本只存 **OSS 对象引用（object key）**，不存签名 URL（签名 URL 会过期、可转发）。
- 盒子下载时调用网关「签发下载 URL」接口，网关**当场鉴权**（mTLS + 统一 key + 境外能力 entitlement `unified_keys.scopes_json` + 对象归属该客户）后，按需签发**短 TTL** 签名 URL。
- bucket 设为私有；对象按客户隔离前缀存放并设生命周期（过期自动清理）；吊销/泄露客户时可按前缀撤销访问。

## 5. 请求生命周期

```
盒子（mTLS 证书 + 统一 key）发起请求
 1. Ingress 校验 mTLS 证书 + 统一 key + 盒子绑定 → 解析客户
 2. 授权 + 预授权冻结：客户启用?模型在作用域内?余额≥预估上限?
       足够 → 冻结(reserve)预估金额(行锁/CAS)；不足 → 402/429 + rejected_quota，不调用 provider
 3. 路由：按 model_id 选真实 provider key(限速轮换)，注入鉴权头
 4. 调用 provider：
       - 对话：流式转发(OpenAI 兼容)
       - 媒体：提交生成任务(异步则轮询)，拿到用量与结果；境外视频入转存 job
 5. 计量：抽取用量(token/图/秒/字符) → 查价格本(快照) → 生成 usage_line_items
 6. 结算(settle)：按实际用量定稿，释放冻结、扣实付、多退少补；写 usage_ledger + line items + balance_transactions
 7. 返回：对话回内容；媒体回 job/对象引用(大字节由盒子按需取下载 URL 自行下载)
```

要点：

- **幂等**：每请求带 `request_id`，reserve/settle/refund 按状态机判定，重试不重复扣费（详见 §8.1）。
- **异步媒体**：在任务**完成**时结算；失败释放冻结并 `refunded`，只按策略计已发生上游成本。
- **流式对话**：完成/中断后按已产生 token 结算，未用完的冻结退回。

## 6. 模型与 key 放置

| 模型类 | Phase 0 key 位置 | Phase 0 出站 | Phase 1+ key 位置 | Phase 1+ 字节交付 |
| --- | --- | --- | --- | --- |
| 对话 LLM（OpenCode） | Mac mini 本地密钥库 / OpenCode provider 配置 | 本机直连或经 mihomo | 网关 | 文本随响应返回 |
| 境外图片 gpt-image/Gemini | Mac mini 本地密钥库 | 经 mihomo | 网关 | 小，网关带回 |
| 境外视频 Sora/Veo/Grok | Mac mini 本地密钥库 | 经 mihomo，盒子直取 provider/CDN | 网关 | 网关转存境内 OSS，盒子境内直取（不装 mihomo） |
| 境外 TTS Gemini/xAI | Mac mini 本地密钥库 | 经 mihomo | 网关 | 小，网关带回 |
| 境内视频 Wan | Mac mini 本地密钥库 | 直连境内 provider/CDN | 网关 | 境内 URL，盒子直连境内 CDN |
| 境内 TTS CosyVoice/Qwen/MiniMax | Mac mini 本地密钥库 | 直连境内 provider | 网关 | 同上 |
| 境内 ASR Paraformer | Mac mini 本地密钥库 | 直连境内 provider | 网关 | 同上 |

Phase 0 对话 LLM 接入：OpenCode 的 provider 直接配置为目标 provider 或本机统一代理层，真实 key 在 Mac mini。OpenCrew→OpenCode 的 `base_url` 仍是本地链路，不改成 provider endpoint。

Phase 1+ 对话 LLM 接入（后续，见 §17.3）：**配置 OpenCode 的 provider = 网关的 OpenAI 兼容端点 + 统一 key**（不是改 OpenCrew→OpenCode 的 `base_url`，那条本地链路不变）。OpenCode 仍在盒子，但只持有统一 key，真实对话 key 移到网关；网关在该端点计量 chat token 并强制配额。

### 6.1 调用/计费模式（Phase 0 先行，Phase 1+ 预留）

Phase 0 **只启用 Local Box Trial**。为了未来迁移到 Managed Gateway 时不重构调用链，数据模型与盒子端 resolver 预留模式字段：

| 模式 | `provider_mode` | `billing_mode` | key 位置 | 收入模型 | 当前状态 |
| --- | --- | --- | --- | --- | --- |
| Local Box Trial 当前试用模式 | `local_box` | `local_usage_only` / `customer_byok` | 真实 provider key 在 Mac mini 本地密钥库 | 硬件/软件/试用授权/服务费；不做强差价计费 | **Phase 0 唯一支持** |
| Managed Gateway 商业化模式 | `managed_gateway` | `managed_resale` | 真实 provider key 在运营方网关；盒子只有统一 key | 模型成本差价 + 系统/服务费 | Phase 1+ |
| Local Direct BYOK 企业/隐私模式 | `local_byok` | `customer_byok` | 客户 provider key 只在 Mac mini / 客户环境 | 软件订阅/授权/支持费；不赚模型差价 | 可并入 Phase 0/企业版 |
| Customer-hosted Gateway 大企业模式 | `customer_hosted_gateway` | `customer_byok` 或定制 | 网关部署在客户云/VPC，key 留在客户环境 | 企业授权/实施/维护费 | 远期选项 |

关键边界：

- Phase 0 的 `local_usage_only` / `customer_byok` 不进入运营方强计费账本；本地 telemetry 只能用于客户自查/诊断。
- `managed_resale` 才进入权威余额扣费、毛利核算与 provider key 池轮换。
- 未来混合模式可按客户/模型/工具选择模式，但同一次请求必须只有一个明确的 `provider_mode` 与 `billing_mode`，避免账单归因混乱。

## 7. 数据模型（字段级，PostgreSQL 风格）

金额统一用整数 `*_micros`（1e-6 货币单位）避免浮点误差；时间用 `BIGINT` 毫秒时间戳。

> Phase 0 落地范围说明：§7.1–§7.8 的 8 张表（customers / unified_keys / provider_keys / models / price_book / usage_ledger / balance_transactions / usage_line_items）是 **Phase 1+ 运营方网关独立库**的权威账本，**不在 Phase 0 盒子上创建**。Phase 0 盒子只新增 §7.9 的极简本地 usage 表用于诊断/自查。§7.6 `usage_ledger` 里 `provider_mode/billing_mode` 的默认值只是为未来迁移预留的字段形状，不代表 Phase 0 在盒子上运行 `usage_ledger`。

### 7.1 customers（客户/盒子账户）

```sql
customers (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,            -- 客户/盒子标识
  box_id          TEXT UNIQUE,              -- 盒子硬件ID/序列号(绑定用)
  status          TEXT NOT NULL,            -- active | suspended | terminated
  price_tier      TEXT NOT NULL DEFAULT 'default',
  currency        TEXT NOT NULL DEFAULT 'CNY',
  balance_micros  BIGINT NOT NULL DEFAULT 0,-- 可用余额(已扣除冻结)
  reserved_micros BIGINT NOT NULL DEFAULT 0,-- 当前预授权冻结合计
  low_balance_threshold_micros BIGINT,      -- 低余额告警线
  created_at      BIGINT NOT NULL,
  updated_at      BIGINT NOT NULL
)
```

### 7.2 unified_keys（统一 key，客户侧凭据）

```sql
unified_keys (
  id            BIGSERIAL PRIMARY KEY,
  customer_id   BIGINT NOT NULL REFERENCES customers(id),
  key_hash      TEXT NOT NULL UNIQUE,       -- 只存 sha256，明文仅签发时返回一次
  key_prefix    TEXT NOT NULL,              -- 可展示前缀，如 ock_live_3f9a
  status        TEXT NOT NULL,              -- active | revoked
  bound_box_id  TEXT,                       -- 绑定盒子
  client_cert_fingerprint TEXT,             -- mTLS 客户端证书指纹(强制匹配)
  scopes_json   JSONB NOT NULL DEFAULT '{}',-- 允许的 modality/model 白名单
  created_at    BIGINT NOT NULL,
  last_used_at  BIGINT,
  revoked_at    BIGINT
)
```

### 7.3 provider_keys（真实 key 池，只在网关）

```sql
provider_keys (
  id                BIGSERIAL PRIMARY KEY,
  provider          TEXT NOT NULL,          -- openai | gemini | xai | dashscope | minimax
  node              TEXT NOT NULL,          -- china | abroad
  label             TEXT,
  key_ciphertext    TEXT NOT NULL,          -- 必须真加密(解密密钥来自网关 KMS/env，不入库)
  status            TEXT NOT NULL,          -- active | disabled
  rpm_limit         INT,                    -- 限速，供轮换调度
  budget_cap_micros BIGINT,                 -- provider 侧硬上限的镜像(纵深防御)
  spent_micros      BIGINT NOT NULL DEFAULT 0,
  last_error_at     BIGINT,
  created_at        BIGINT NOT NULL
)
```

### 7.4 models（模型目录）

```sql
models (
  id          TEXT PRIMARY KEY,             -- 'sora-2' / 'wan2.7-i2v-2026-04-25' ...
  provider    TEXT NOT NULL,
  node        TEXT NOT NULL,                -- china | abroad
  modality    TEXT NOT NULL,                -- chat | image | video | tts | asr
  metrics     TEXT[] NOT NULL,              -- 计费维度，如 {input_token,output_token} / {image} / {second} / {character}
  enabled     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at  BIGINT NOT NULL
)
```

### 7.5 price_book（价格本：批发 + 售价，按时间版本）

```sql
price_book (
  id                  BIGSERIAL PRIMARY KEY,
  model_id            TEXT NOT NULL REFERENCES models(id),
  tier                TEXT NOT NULL DEFAULT 'default',
  metric              TEXT NOT NULL,        -- input_token | output_token | image | second | character
  wholesale_micros_per_unit BIGINT NOT NULL,-- 你的批发成本/单位
  sell_micros_per_unit      BIGINT NOT NULL,-- 客户价/单位(已含加价)
  currency            TEXT NOT NULL,
  effective_from      BIGINT NOT NULL,
  effective_to        BIGINT,               -- NULL=当前生效
  UNIQUE (model_id, tier, metric, effective_from)
)
```

> 加价可表达为 `sell = wholesale × (1+markup)` 或独立定价；同一模型按 metric 分行（对话的 input/output token 单独定价）。价格变化只新增新行（新 `effective_from`），不改历史行；计费时把命中的 `price_book.id` 与单价**快照**进 `usage_line_items`（§7.8）。

### 7.6 usage_ledger（用量台账，每请求一行，权威账本头）

```sql
usage_ledger (
  id               BIGSERIAL PRIMARY KEY,
  request_id       TEXT NOT NULL UNIQUE,    -- 幂等键
  customer_id      BIGINT NOT NULL REFERENCES customers(id),
  unified_key_id   BIGINT REFERENCES unified_keys(id),
  model_id         TEXT NOT NULL,
  provider         TEXT NOT NULL,
  node             TEXT NOT NULL,
  provider_key_id  BIGINT REFERENCES provider_keys(id), -- Phase 0 本机 key 可为空
  provider_mode    TEXT NOT NULL DEFAULT 'local_box', -- local_box | managed_gateway | local_byok | customer_hosted_gateway
  billing_mode     TEXT NOT NULL DEFAULT 'local_usage_only', -- local_usage_only | managed_resale | customer_byok
  status           TEXT NOT NULL,           -- reserved | settled | failed | refunded | rejected_quota
  reserved_micros  BIGINT NOT NULL DEFAULT 0,-- 预授权冻结金额(结算后清零)
  wholesale_micros BIGINT NOT NULL DEFAULT 0,-- 批发成本合计(= line_items 合计)
  sell_micros      BIGINT NOT NULL DEFAULT 0,-- 计费金额合计(扣余额的)
  result_ref       TEXT,                    -- OSS 对象引用 object key(不存签名 URL/大字节)
  provider_request_id TEXT,                 -- provider 侧请求 id
  started_at       BIGINT,
  finished_at      BIGINT,
  created_at       BIGINT NOT NULL
)
```

### 7.7 balance_transactions（余额流水）

```sql
balance_transactions (
  id                   BIGSERIAL PRIMARY KEY,
  customer_id          BIGINT NOT NULL REFERENCES customers(id),
  type                 TEXT NOT NULL,       -- topup | reserve | release | charge | refund | adjust
  amount_micros        BIGINT NOT NULL,     -- 正=入/释放，负=出/冻结
  balance_after_micros BIGINT NOT NULL,
  reserved_after_micros BIGINT NOT NULL,
  ref_request_id       TEXT,                -- 关联 usage_ledger.request_id
  note                 TEXT,
  created_at           BIGINT NOT NULL
)
```

### 7.8 usage_line_items（不可变计费明细，审计/对账用）

每次计费按 metric 拆成不可变明细行，**冻结当时的价格与汇率快照**，供账单复盘与毛利核算：

```sql
usage_line_items (
  id                  BIGSERIAL PRIMARY KEY,
  request_id          TEXT NOT NULL,          -- 关联 usage_ledger
  customer_id         BIGINT NOT NULL,
  model_id            TEXT NOT NULL,
  metric              TEXT NOT NULL,          -- input_token | output_token | image | second | character
  units               BIGINT NOT NULL,
  price_book_id       BIGINT NOT NULL,        -- 命中的价格行(快照引用)
  unit_wholesale_micros BIGINT NOT NULL,      -- 快照: 当时批发单价
  unit_sell_micros      BIGINT NOT NULL,      -- 快照: 当时售价
  wholesale_micros    BIGINT NOT NULL,        -- units × unit_wholesale
  sell_micros         BIGINT NOT NULL,        -- units × unit_sell
  currency            TEXT NOT NULL,
  fx_rate_micros      BIGINT,                 -- 批发币种→计费币种汇率快照(如 USD→CNY)
  rounding_micros     BIGINT NOT NULL DEFAULT 0,
  tax_micros          BIGINT NOT NULL DEFAULT 0,
  provider_invoice_ref TEXT,                  -- 对账用 provider 账单引用
  created_at          BIGINT NOT NULL
)
```

> 行写入后不可变；价格本或汇率变化只新增新行，不改历史。`usage_ledger.wholesale_micros/sell_micros` 是这些行的合计。
> `usage_line_items` 是 Phase 1+ 网关账本，Phase 0 盒子**不写本表**；Phase 0 的本地用量展示/诊断记录写 §7.9 的极简本地表，不作为运营方强计费账本。Phase 1+ 才写 `billing_mode=managed_resale` 的扣费明细。

### 7.9 local_usage_log（Phase 0 盒子本地用量，仅诊断/自查）

Phase 0 盒子不创建 §7.1–§7.8 的权威账本，只写这张极简本地表，用于客户自查、运营排障与试用评估；**不是强计费账本**，且不记录 prompt 内容与 key：

```sql
local_usage_log (
  id              BIGSERIAL PRIMARY KEY,
  request_id      TEXT,                     -- 盒子生成，关联/幂等
  provider        TEXT NOT NULL,
  model_id        TEXT NOT NULL,
  modality        TEXT NOT NULL,            -- chat | image | video | tts | asr
  provider_mode   TEXT NOT NULL DEFAULT 'local_box',        -- local_box | local_byok
  billing_mode    TEXT NOT NULL DEFAULT 'local_usage_only', -- local_usage_only | customer_byok
  proxy_policy    TEXT,                     -- direct | mihomo
  status          TEXT NOT NULL,            -- ok | failed
  units_json      JSONB,                    -- 粗略用量 {input_token,output_token,image,second,character}
  est_cost_micros BIGINT,                   -- 可选粗略成本估算(仅自查，非账本)
  error_code      TEXT,
  started_at      BIGINT,
  finished_at     BIGINT,
  created_at      BIGINT NOT NULL
)
```

> 不落 key、订阅 URL、prompt 敏感内容；支持 Web 导出供试用复盘（§17.0.8）。迁移到 Phase 1+ 后，权威用量改由网关 `usage_ledger`/`usage_line_items` 承担，本表降级为盒子侧诊断。

## 8. 计量与计费

1. **单位归一**：每个模型在 `models.metrics` 声明计费维度；对话=input/output token，图片=张，视频=秒，TTS/ASR=字符或秒。
2. **成本与售价**：按 `units × price_book(metric)` 生成 `usage_line_items`（含价格/汇率快照），合计写入 `usage_ledger`。
3. **对账**：定期用 provider 真实账单核对 `SUM(line_items.wholesale_micros)` 与 `provider_invoice_ref`，校正价格本与漂移。

Phase 0 扣费口径：不做运营方余额 reserve/settle，也不做模型差价强计费；只记录本地 usage、provider 响应、错误与粗略成本估算，供客户自查/运营排障。Phase 1+ 才对 `billing_mode=managed_resale` 做余额 reserve/settle、毛利核算和 provider 账单对账。

### 8.1 事务与原子性（预付强制）

余额是并发资源，必须用状态机 + 行锁防超卖：

- **状态机**：`reserved → settled`（正常）/ `reserved → failed → refunded`（失败退款）/ 直接 `rejected_quota`（余额不足，不调 provider）。
- **预授权冻结(reserve)**：调用 provider **之前**，用 `SELECT ... FOR UPDATE`（或等价 CAS：`UPDATE customers SET balance_micros = balance_micros - X, reserved_micros = reserved_micros + X WHERE id = ? AND balance_micros >= X`）按**预估上限**冻结金额；条件不满足即拒绝，杜绝并发超卖（余额扣成负）。
- **结算(settle)**：provider 返回后按**实际用量**定稿，释放冻结、按实付扣减、多退少补；写 `usage_line_items` + `balance_transactions(type=charge/refund/release)`。
- **幂等**：`request_id` 唯一；同一 request 的 reserve/settle/refund 可重复执行而不重复扣费（按 `usage_ledger.status` 当前态判定）。
- **流式对话**：连接结束/中断时按已产生 token 结算，未用完的冻结退回。
- **异步媒体重试/恢复**：任务以 `request_id` 幂等；重试不重复扣费；最终 `failed` 则释放冻结并 `refunded`。崩溃恢复时扫描 `status=reserved` 且超时未结算的请求，补结算或退款，防止冻结泄漏。
- **失败成本归属**：provider 已产生上游成本但对客户判失败时，`sell=0`，按策略决定是否计 `wholesale`（计入毛利成本但不向客户收）。

## 9. 统一 key 与盒子证书生命周期

- **签发**：开通客户时生成统一 key（明文仅返回一次，库存 `key_hash`）+ 一张**盒子专属 mTLS 客户端证书**；统一 key 绑定 `box_id` 与 `client_cert_fingerprint`。
- **下发到盒子**：统一 key 与证书写入加固后的盒子配置；OpenCrew（媒体/ASR）与 OpenCode（对话 provider）都用统一 key（见 §17.3）。
- **盒子绑定（强制 mTLS）**：网关**强制**双向 TLS；统一 key 仅在 mTLS 证书指纹与 `client_cert_fingerprint`/`bound_box_id` 匹配时有效。
  - **轮换**：统一 key 与证书均支持滚动轮换（新旧并存窗口），到期自动续签。轮换统一 key 时须**同步更新盒子两处配置**（OpenCrew + OpenCode）。
  - **吊销**：失窃/退订时吊销证书 + 吊销统一 key，双重失效，立即生效，不影响其他客户。
  - **重装/换机恢复**：走「重新签发 + 旧证书吊销」流程，需运营侧确认绑定。
  - **失窃处理**：吊销 + 冻结客户 + 审计该客户近期用量。
  - 注意：证书私钥对物主仍可能被抠；mTLS 抬高外借门槛，**损失上限仍由配额/余额保证**（见 §3）。
- **作用域**：`scopes_json` 控制该 key 可用的 modality/模型，支持分档售卖（境内版 / 境外增强版）。

## 10. 真实 key 池管理

- 每个 provider 可放多把 key，按 `rpm_limit` 轮换，缓解「全机队汇聚到一个账号」的限速。
- 每把 key 在 provider 侧设硬预算上限，镜像到 `budget_cap_micros` 做纵深防御。
- 出现鉴权失败/限额触顶时自动 `disabled` 并告警；不向客户暴露任何 provider key 信息。
- `key_ciphertext` **必须真加密**（KMS/独立密钥，密钥不入库）；勿重蹈盒子 `api_key_ciphertext` 命名却明文存储的覆辙。

## 11. Provider 接入

- **对话 LLM**：用现成 OpenAI 兼容传输（one-api / new-api / LiteLLM）承载，OpenCode 指过去即可，开发量小；用量从响应的 usage 字段抽取。
- **媒体**：各家接口形态不一（DashScope 异步 `video-synthesis` 返回 URL、TTS `output_format=url`、Sora/Veo 任务轮询、gpt-image 可能内联 b64），需**逐家写适配路由**：统一成内部「提交 →(轮询)→ 用量 + 结果对象」契约，并抽取计费用量。这是主要自研工作量。

### 11.1 权威边界：现成网关 vs 自研账本

one-api/new-api/LiteLLM 自带按 key 余额/限额/计量，与自研 `customers/usage_ledger/balance_transactions` 并存会**双账本漂移**。**定调：自研账本为唯一真相（source of truth）**：

- 现成网关只作**对话的 OpenAI 兼容传输 + token usage 抽取**，**不启用其计费/余额功能**（或设为不限额）；余额/限额/扣费一律回自研账本（在自研鉴权/计量层做 §8.1 的 reserve/settle）。
- 流式 usage、失败重试、退款都以自研账本状态机为准；现成网关返回的 usage 仅作为计量输入。
- 媒体不经现成网关，本就走自研适配；故全链路计费只有一个权威。

### 11.2 调用模式边界

- **Phase 0 运行时只允许 `provider_mode=local_box`（以及明确的客户 BYOK 变体）**。盒子端配置若出现 `managed_gateway` 但未配置网关，应拒绝启动该 provider 或回退为不可用，而不是静默走半成品链路。
- `local_box` 的 provider API 出站由 Mac mini 发起；境外 provider 可按配置走本机 mihomo；境内 provider 直连。
- `managed_gateway` 未来增加时，必须作为独立调用模式实现：真实 key 迁到运营方网关；盒子只持统一 key；账单标记 `billing_mode=managed_resale`；进入网关余额扣费与毛利核算。
- 不做 “BYOK Through OpenCrew 托管网关” 作为隐私承诺方案，因为该模式会让客户 provider key 对运营方网关可用。若客户要求 key 不被运营方使用，只能 Local Direct BYOK 或 Customer-hosted Gateway。

### 11.3 Phase 0 本机 key / mihomo / 代码保护要求

本机 key 试用版本的可交付质量取决于三件事：key 不以明文散落、代理不暴露、代码不以源码 repo 交付。

**Key 存储**

- provider key 与 mihomo 订阅 URL 存**盒子本地密钥库（默认 `${OPENCREW_DATA_DIR}/secrets.enc`，0600 加密文件 + 本机 0600 主密钥文件；生产安装器可显式设为 `/Library/Application Support/OpenCrew/secrets.enc`）**；由 `opencrew` 运行用户读取，数据库只存 `key_ref`、provider、model、启用状态与 `has_key`。
- 现有 `api_key_ciphertext` 字段不得继续当成“加密字段”使用；Phase 0 迁移为本地密钥库的 `key_ref`，旧值仅作为一次性迁移输入，迁移完成后清空。
- 威胁模型口径：守护进程能自动解包 ⇒ 本机 root 同样能提取，本地密钥库只消除“明文落库/落日志”，**不构成对抗物理提取的边界**（见 §3）；大额账单风险仍靠 provider 侧硬预算/限速兜底。
- Phase 0 默认使用运营方为每台 Mac mini 签发的独立 key/子账号；运营方 key 必须设备级隔离：一台 Mac mini 一组 key/子账号/预算，不共用主 key。
- 所有日志、Debug Console、支持包、异常栈、HTTP trace 对 key 与订阅 URL 做脱敏。

**mihomo**

- 预装 mihomo binary 与默认配置模板，默认禁用；不内置订阅 URL、不销售/提供订阅。
- Web UI 提供订阅 URL 输入、刷新、连通性测试、当前节点/规则状态；订阅 URL 存盒子本地密钥库。
- mihomo 只监听 `127.0.0.1`（如 `127.0.0.1:7890/7891`），不得监听 `0.0.0.0` 或 LAN IP。
- OpenCrew provider client 按 provider/模型选择代理；不要把 macOS 全局代理作为唯一机制，避免影响系统其他流量。

**Python 代码发布保护**

- 不在客户机器交付 git repo、源码目录、测试目录、`.env` 模板、开发脚本。
- 首版后端发布为 Nuitka 或 PyInstaller 打包产物，不叠加 PyArmor/Cython/商业混淆；前端发布为静态 build。
- 用独立 `opencrew` 运行用户、root/admin 拥有文件、只读权限、launchd 启动、代码签名与校验；普通用户不需要也不应获得 shell 使用路径。
- 不承诺“不可逆向”。真正的商业秘密与大额账单风险不能只放在客户物理持有的 Mac mini 上。

**LAN Web appliance**

- Caddy/Nginx 是唯一 LAN 入口；backend/frontend/OpenCode/PostgreSQL/mihomo 均不直接暴露。
- Web UI 必须覆盖初始化、key/mihomo 配置、健康检查、服务重启、脱敏日志摘要、支持包导出。
- 普通用户账号不显示 Debug Console、原始日志、系统路径、服务端口、shell 命令；运营方维护账号单独创建，默认不可用于日常客户操作。
- 维护通道默认关闭；如需开启，必须由用户在 Web UI 显式授权并限时。

## 12. 用量上报与运营视图

- Phase 0：用量台账在 Mac mini 本机，仅用于客户自查、运营排障与试用评估；如需运营方查看，只做客户授权后的导出/上传，不作为强计费账本。
- Phase 0 运营视图：每台设备的健康状态、provider 配置状态、mihomo 状态、错误率、粗略用量、provider 预算告警。
- Phase 1+：台账在运营方网关，用量天然在运营方手里；运营方控制台管理客户、余额、成本、计费、毛利、key 池健康、真实 key 录入。

## 13. 可用性与容量

- 控制面只跑小 JSON，网关 CPU/带宽需求低；应用层无状态，台账放共享库，**横向扩节点**即可。
- **境外视频转存是真实数据面**：按 §4.4 的 job 队列限并发与带宽，过载排队；按生成数（非观看数）线性增长。
- 境外节点近 provider；境内节点在国内。境外节点故障 → 境外模型不可用，**境内功能不受影响**（节点独立）。
- 预付 + 预授权冻结确保任何故障/滥用都不会造成无界账单。

## 14. 合规与法律风险（必须正视）

- 在大陆转售**未备案/未授权的境外 LLM** 属灰色地带；生成式 AI 服务在国内有备案/审核要求。
- **代理引擎（mihomo）的法律暴露分阶段不同**：
  - **Phase 0**：盒子内置 mihomo binary，订阅 URL 由用户自填、用户自担网络可达性与合规。「用户自填订阅」只转移**订阅/规避服务**这一层责任；**随产品分发 mihomo binary 本身仍构成「把翻墙工具打包进出售产品」的法律暴露**——这是更直接的风险点，须经 §16.4 法务确认（合同表述、免责声明、地区策略）。可选降险手段：首启从上游下载 binary 而非预装、默认禁用/可选开关、定位为通用代理客户端不绑任何规避配置。
  - **Phase 1+**：境外视频经网关转存境内 OSS、盒子彻底不装 mihomo（§4.4），消除该暴露。
- 建议：**能境内优先就境内优先**（音频与主力视频 Wan 已可境内直连）；确需境外能力再上境外网关，并就合规咨询法律意见。本设计在技术上对「境内优先」友好（双节点、境内可独立运行）。

## 15. 分期落地

> 术语澄清：本文档的 **Phase 0（Local Box Trial）** 与 `docs/opencrew_p0_development_plan.md` 的 **P0（workflow 基础设施/安全稳定化：registry routing、迁移基线、event 可见性、File API、删除一致性）** 同名但范围不同，是**并行两轨**，不要混用。两者有一处依赖：本文档 Phase 0 的盒子端 schema 改动（§17.0.1 `api_key_ciphertext`→`api_key_ref`、§7.9 本地 usage 表）必须走 workflow-P0 建立的迁移机制（`backend/opcrew_backend/db/migrations.py`，见 `docs/opencrew_p0_migration_runbook.md`），不要再用旧的 `ensure_*_columns()`。

- **Phase 0（当前先做：Local Box Trial）**：真实 provider key 存 Mac mini 本地密钥库；保留本机 provider 调用；内置 mihomo 但订阅 URL 用户自填；backend/frontend 只监听 localhost，经 LAN 反向代理访问（交付形态为 LAN-only，不做公网端口转发；公网 nip.io 仅限内部异地测试，见 §3.2）；打包保护 Python；本机 usage 只做诊断/试用评估。
  - **Phase 0 风险上限**：每台设备独立 provider key/子账号，provider 侧硬预算与限速；不放主 key；试用结束轮换/吊销；不以本地 usage 做强计费。
  - **Phase 0 验收**：用户无需登录 Mac mini，只能通过 LAN Web 完成配置与使用；断电重启后服务自恢复；mihomo 不暴露 LAN 代理；backend/OpenCode/PostgreSQL/raw ports 不暴露 LAN；key/订阅 URL 不落日志；发布包不含源码 repo。
- **Phase 1（Managed Gateway 计费地基，可选后续）**：境外网关 + 对话 LLM（现成 OpenAI 兼容）+ 统一 key + 余额 + 价格本 + 台账 + line items + 预付限额（含 §8.1 reserve/settle 状态机）+ mTLS 校验。打通「统一 key → 证书绑定 → 鉴权 → 调用 → 计量 → 扣费」闭环。
- **Phase 2（境内 + 媒体网关）**：境内网关节点；媒体各家适配（境内 Wan/CosyVoice/Qwen/MiniMax/Paraformer 优先）；境外视频转存 job + 境内 OSS 交付。
- **Phase 3（规模化运营）**：证书生命周期完善（自动续签/换机/吊销审计）、key 池轮换、provider 账单对账、运营控制台、低余额告警与停服策略。
- **Phase 4（企业/隐私 BYOK，可选）**：在本机/网关模式稳定后，按 §6.1/§11.2 增加更明确的 Local Direct BYOK 或 Customer-hosted Gateway。该阶段是新商业模式扩展，不阻塞 Phase 0。

## 16. 决策记录与待决问题

已定：

1. **Phase 0 key 来源**：默认由运营方为每台 Mac mini 签发独立 provider key/子账号；客户 BYOK 不作为首版默认路径。
2. **境外视频字节交付**（已定 v0.2，见 §4.4）：网关转存境内 OSS，盒子境内直取，不装 mihomo。
3. **模型白名单**：Phase 0 首批开放所有已接入模型；风险由每设备独立 key、provider 侧预算、RPM/TPM 与模型权限兜底。
4. **mihomo 交付方式**：预装 mihomo binary，默认禁用，不提供订阅 URL；用户在 Web UI 填写订阅后启用。
5. **代码保护级别**：首版采用 Nuitka 或 PyInstaller 打包，不叠加 PyArmor/Cython/商业混淆；仍需保证不交付 git repo、源码目录、测试目录和开发脚本。
6. **LAN Web 鉴权**：默认使用 OpenCrew 应用登录；普通用户完全关闭 Debug Console；单独创建运营方维护账号；维护通道默认关闭。
7. **本地密钥库实现**：默认使用 `${OPENCREW_DATA_DIR}/secrets.enc`，生产安装器可显式设置 `/Library/Application Support/OpenCrew/secrets.enc`；文件权限 0600，由 `opencrew` 运行用户读取，主密钥为本机 0600 主密钥文件（Keychain 仅 opt-in）；数据库只存 `api_key_ref/has_api_key`，旧 `api_key_ciphertext` 只作为一次性迁移输入后清空。

仍待定：

1. **provider 预算上限（阻塞 Phase 0 交付，不阻塞代码先行开发）**：Phase 0 每台设备/每把 key 的日/月硬预算、RPM/TPM、provider 侧模型权限默认值。未定前可以先实现配置与校验，但交付清单必须要求每台设备填入预算并验证 provider 侧限制已生效。
2. **合规姿态（阻塞对外交付）**：内置 mihomo 但用户自填订阅 URL 的合同表述、免责声明与地区策略，需要法务确认。
3. **Phase 1 迁移触发条件（不阻塞 Phase 0）**：客户数、月 provider 成本、试用转正式、或账单风险达到什么阈值时必须迁移 Managed Gateway。
4. **预付 vs 后付（Phase 1+，不阻塞 Phase 0）**：网关模式默认预付（可强制、零坏账）；是否需要后付额度给信任客户。

## 17. 与现有代码的适配与改造

结论：Phase 0 先沿用现有盒子端 provider 调用能力，但把 key 存储、代理出口、LAN 暴露面、发布包保护补齐；不启动网关重构。调用层仍应预留 `provider_mode` / `billing_mode`，避免未来迁移 Managed Gateway 时大面积重写。

### 17.0 Phase 0 本机 Key 改造（当前先做）

1. **本地密钥库化**：把 `tool_media_provider_configs` / `tool_asr_provider_configs` 中的真实 key 从 `api_key_ciphertext` 迁到**盒子本地密钥库（默认 `${OPENCREW_DATA_DIR}/secrets.enc`，0600 加密文件 + 本机 0600 主密钥文件；生产安装器可显式设为 `/Library/Application Support/OpenCrew/secrets.enc`）**；表中仅保留 `api_key_ref`、`has_api_key` 所需状态与模型配置（schema 改动走 §15 所述 `backend/opcrew_backend/db/migrations.py` 迁移机制，不用 `ensure_*_columns()`）。保存/读取 key 的代码改为本地密钥库 service；旧 `api_key_ciphertext` 只作为一次性迁移输入，迁移成功后清空。
2. **本机 provider resolver**：新增 `resolve_endpoint(provider, model, modality) -> {provider_mode, billing_mode, base_url, auth_ref, proxy_policy}`。Phase 0 返回 `provider_mode=local_box`，`auth_ref` 指向本地密钥库，`proxy_policy` 决定是否走 mihomo。
3. **mihomo 管理 UI/API**：mihomo binary 预装但默认禁用；新增订阅 URL 保存、刷新、连接测试、启停状态、当前代理端口展示；订阅 URL 存本地密钥库；mihomo 配置只绑定 `127.0.0.1`。
4. **LAN-only 访问**：backend/frontend/OpenCode/PostgreSQL/mihomo 继续只监听 `127.0.0.1` 或本机 socket；局域网入口由 Caddy/Nginx 暴露；默认使用 OpenCrew 应用登录；普通用户完全关闭 Debug Console，运营方维护账号单独创建。
5. **Web-only 运维闭环**：新增首次初始化、服务健康页、服务重启、脱敏日志摘要、支持包导出、维护通道开关；常见恢复操作不得依赖 SSH/终端。
6. **launchd 自启动**：生成 backend/frontend/OpenCode/mihomo/PostgreSQL 的 launchd plist，开机自启、崩溃重启、日志归档；Web 健康页能读取服务状态。
7. **发布包保护**：定义 release 构建脚本：前端 build，后端用 Nuitka 或 PyInstaller 打包，剔除源码、测试、开发脚本、`.git`、本地 secrets；生成安装/升级/回滚脚本。
8. **本地 usage 记录**：写入 §7.9 `local_usage_log`（provider、model、modality、时间、请求状态、usage 粗略值、错误码、proxy_policy）；不记录 prompt 中敏感数据或 key；支持 Web 导出供试用复盘。
9. **Provider 风险控制**：交付前为每台设备配置独立 provider key/子账号预算、RPM/TPM 与模型权限；写入试用到期吊销/轮换流程。预算默认值仍待业务确认，未确认不得对外交付。

### 17.1 Phase 1+ 网关为新建服务，零冲突

8 张表（§7.1–§7.8）与全部计量/限额逻辑在 Phase 1+ 网关独立库中；盒子现有 PostgreSQL（sessions / session_events / openclip / oc_rebuild …）不动。Phase 0 盒子只新增 §7.9 `local_usage_log` 与 §17.0.1 的 `api_key_ref` 字段改动，不创建上述 8 表。

### 17.2 Phase 0/Phase 1 共用：盒子媒体 / ASR 集中端点 + 凭据解析

现状：provider URL 硬编码散落在 `media_model_config.py`（OpenAI/xAI/Gemini/DashScope 各调用点）与 `asr_config.py`；真实 key 存于盒子表 `tool_media_provider_configs` / `tool_asr_provider_configs`（定义在 `schema.py`，`api_key_ciphertext` **非真加密**）；HTTP 出口为 `post_json/post_binary(url, api_key, payload)`。

改造：新增模式感知解析器 `resolve_endpoint(provider, model, modality) -> {provider_mode, billing_mode, base_url, auth_ref|auth_key, route_path, proxy_policy}`。

- Phase 0 返回 `{provider_mode=local_box, billing_mode=local_usage_only/customer_byok, base_url=provider 原始地址, auth_ref=本地密钥库 ref, proxy_policy=direct|mihomo}`。
- Phase 1+ 返回 `{provider_mode=managed_gateway, billing_mode=managed_resale, base_url=网关地址, auth_key=统一 key}`；盒子继续构造同样形状 payload，改发网关；网关注入真实 key、转发、计量、（境外视频）转存。

契合点：盒子本就从结果 URL 下载落 workspace（`write_bytes`），DashScope 本就返回境内 OSS URL → §4.4 字节交付复用现有下载逻辑。

### 17.3 对话 / OpenCode

Phase 0：OpenCode provider 直接配置为目标 provider 或本机代理层；真实 key 在 Mac mini（优先盒子本地密钥库或 OpenCode 自身安全配置）。OpenCrew→OpenCode 的 base_url 仍为本地链路。

Phase 1+：**配置 OpenCode 的 provider = 网关 OpenAI 兼容端点 + 统一 key**（不改 OpenCrew→OpenCode 的 base_url，本地链路不变）。OpenCode 仍在盒子但只持有统一 key，真实对话 key 移到网关；网关在该端点计量 chat token、强制配额。

实施要点：

- Phase 0 OpenCode 出站可走 mihomo；Phase 1+ OpenCode 指向**境内网关节点**的兼容端点，境外对话由境内节点转发境外节点。
- 网关须注册 OpenCode 所用的 chat model ID（`models` 表）并路由到真实 provider+model。
- **谁写 OpenCode provider 配置**：当前 OpenCrew 只存本地 OpenCode server 的 base_url/账密（`step1_opencode.py`），**不管理 OpenCode 的 provider 凭据**。需新增：由 OpenCrew（或盒子 provisioning）在开通/轮换时，写入/更新 OpenCode 配置里的自定义 OpenAI 兼容 provider（baseURL=网关、apiKey=统一 key）。统一 key 轮换时同步重写此处（与 §9 联动，盒子两处配置同时更新）。
- **防改回直连**：客户可把 OpenCode 改回直连 OpenAI，但**盒子无真实 key**，改回后只能用客户自带 key、自费，不损运营方；故无需强行阻止，但可在 bootstrap 校验 provider 指向并告警。
- 顺带解决「OpenCode 不向 OpenCrew 暴露 token 用量」——网关即兼容端点，token 在网关侧可计量。

### 17.4 配置与 UI 迁移

Phase 0 盒子 UI 仍需录入/管理 provider key，但 key 写入盒子本地密钥库，不进数据库明文字段；同时增加 mihomo 订阅 URL、代理状态、连通性测试。Phase 1+ 才把真实 provider key 录入迁到网关运营控制台，盒子端退化为统一 key + 网关地址 + 盒子绑定 + provider_mode。

### 17.5 异步任务轮询上移

Phase 0 保持盒子自行提交+轮询，但请求按 `proxy_policy` 走 direct/mihomo。Phase 1+ key 移网关后，submit→poll→result 须经网关（网关代理轮询，或网关持任务、盒子轮询网关）。

### 17.6 改造量总览

| 部分 | 性质 | 量 |
| --- | --- | --- |
| 网关（鉴权/限额/key 池/价格本/台账+line items/事务状态机/媒体适配/转存 job/OSS） | 新建 | 大 |
| 盒子媒体/ASR：集中端点+凭据解析 | 机械改造，~2 文件 | 中 |
| 盒子对话：OpenCode provider 指网关 + 写入/轮换机制 | 配置 + 新增机制 | 小~中 |
| 盒子字节交付（§4.4） | 复用现有下载 + 改取短 TTL URL | 微~小 |
| 真实 key cutover（§17.7） | 迁移 + 清理 + 校验 | 中 |
| 调用模式预留（`provider_mode`/`billing_mode` + resolver 形状） | 低风险结构预留，不启用 BYOK | 小 |
| 配置/UI：真实 key 录入迁网关控制台 | 流程迁移 | 中 |

### 17.7 真实 key cutover 方案（迁移边界闭合）

盒子现有 `tool_media_provider_configs` / `tool_asr_provider_configs`（`schema.py`，含**非加密**的 `api_key_ciphertext`），且 save config 仍会写/沿用真实 key（`media_model_config.py` / `asr_config.py`）。切到网关模式必须闭合迁移：

1. **网关模式 feature flag**：盒子加 `gateway_mode` / `provider_mode=managed_gateway` 开关；v1 开启后所有 provider 调用强制走 `resolve_endpoint`→网关，**禁用任何本地直连 provider 的代码路径**。
2. **禁用本地 key 录入**：关闭盒子上录入真实 provider key 的 UI/接口；录入迁到网关运营控制台。
3. **迁移/清除旧 key**：把存量真实 key 从盒子安全导出到网关 key 池后，**清空盒子表中的 key 字段**（置空/删行），并**轮换这些 key**（假定盒子曾持有 = 已暴露）。
4. **备份与日志脱敏**：迁移前备份；迁移与运行日志对 key 脱敏；确认历史日志/备份不残留明文 key。
5. **旁路核查**：静态扫描确认无任何 `https://<provider>` 直连未走 `resolve_endpoint`；运行期 provider API 出站白名单只允许网关地址（媒体结果下载 URL/OSS 数据面除外）。
6. **回滚**：flag 可关，但关后须确认本地不再有可用真实 key（已轮换），避免「回滚 = 重新暴露」。

验收：盒子任意 provider 调用都经网关；盒子库无明文真实 key；无直连 provider 旁路。

### 17.8 未来 Local Direct BYOK 扩展点（非 v1）

若后续进入企业/隐私模式，Local Direct BYOK 必须作为独立模式接入，而不是在 Managed Gateway 里偷偷塞客户 key：

1. **模式选择**：按客户/工具/模型配置 `provider_mode=local_byok`，请求写 `billing_mode=customer_byok`。
2. **key 存储**：客户 key 只存在 Mac mini（优先盒子本地密钥库或客户指定密钥系统），不上传 OpenCrew 托管网关。
3. **调用路径**：resolver 返回 provider 原始 endpoint + 本地 auth；上层 payload 构造复用，计费/余额逻辑跳过 Managed Gateway。
4. **商业边界**：不赚模型差价；只收软件订阅、授权、实施或支持费。
5. **支持边界**：客户 provider 账号额度、封禁、地区可达性、网络代理均由客户负责；OpenCrew 只负责本地调用链与错误展示。
6. **合规边界**：大陆客户直连境外 provider 的网络与合规风险由客户自担；产品不打包 mihomo/代理订阅。

## 18. 盒子↔网关 API 契约（待细化为正式 spec）

为避免盒子端与网关端实现出不同契约，需固化：

- **端点**（带版本前缀 `/v1/`）：
  - 对话：`POST {gateway}/v1/chat/completions`（OpenAI 兼容，供 OpenCode）。
  - 媒体提交：`POST {gateway}/v1/media/{modality}/submit`。
  - 媒体轮询：`GET {gateway}/v1/media/jobs/{job_id}`。
  - 下载 URL 签发：`POST {gateway}/v1/media/jobs/{job_id}/download-url`（按需短 TTL）。
  - 余额查询：`GET {gateway}/v1/account/balance`。
- **认证**：mTLS 客户端证书 + `Authorization: Bearer <统一key>`，两者都校验（见 §3/§9）。
- **request_id**：由盒子生成（uuid），作幂等键；网关按 `request_id` 去重（见 §8.1）。
- **错误体**（统一 JSON）：`{ "error": { "code": "...", "message": "...", "request_id": "..." } }`
  - `402 insufficient_balance`、`429 quota_exceeded / rate_limited`、`403 model_not_in_scope / box_unbound`、`409 idempotency_conflict`、`404 job_not_found`。
- **媒体 job 状态**：`pending|fetching|uploading|ready|failed`（见 §4.4）；`ready` 才返回可签发下载。
- **下载 URL 协议**：返回短 TTL 签名 URL + 过期时间；盒子按需重新请求，不缓存长时效 URL。
- **版本化**：契约变更向后兼容或灰度。
