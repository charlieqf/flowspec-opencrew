# OpenCrew Mac mini nip.io 部署计划

日期：2026-05-27

## 目标

在这台 Mac mini 的当前 checkout：

```text
/Users/macmini-1/work/code/OpenCrew
```

把 OpenCrew 跑成“本机服务 + localhost 反向代理”的形态，并准备通过 `nip.io` 域名对外访问。

## 关键边界

- `nip.io` 只把 `<ip>.nip.io` 解析到对应 IP，不提供内网穿透。
- 如果 Mac mini 没有公网 IPv4 入站能力，需要路由器端口转发、云服务器隧道、Tailscale Funnel、NPS/npc、Cloudflare Tunnel 等额外入口。
- 当前系统没有完整鉴权，不能裸露 Debug Console、主 UI 或 backend raw port。
- 公网入口必须先加 Basic Auth、IP allowlist、VPN，或只暴露 share 路由。
- PostgreSQL、OpenCode、backend 原始端口不能暴露到公网。
- backend/frontend 默认只监听 `127.0.0.1`，公网入口只经 Caddy/Nginx 反向代理。

## 本机服务布局

| 服务 | 默认地址 | 公网暴露 |
| --- | --- | --- |
| PostgreSQL | `127.0.0.1:5433` | 不暴露 |
| OpenCrew backend | `127.0.0.1:8011` | 不暴露 |
| OpenCrew frontend | `127.0.0.1:18080` | 只经反代 |
| Caddy reverse proxy | 本地验证 `http://127.0.0.1.nip.io:18081` | 公网部署时改为 `https://<public-ip>.nip.io` |
| OpenCode | `127.0.0.1:<port>` | 不暴露 |

## 已实现的本机准备

- `scripts/opencrew_local_stack.sh`
  - 移除 `/Users/duheng` 硬编码。
  - 自动定位当前 repo。
  - 默认使用 Python 3.12 venv：`backend/.venv`。
  - 默认管理 standalone PostgreSQL：`~/.opencrew/postgres` on `5433`。
  - backend/frontend 默认绑定 `127.0.0.1`。
  - frontend 依赖缺失时自动 `npm install`。

- `scripts/opencrew_postgres.py`
  - 自动发现 Homebrew PostgreSQL bin 目录。
  - 不再依赖旧机器的 embedded PostgreSQL 路径。

- `scripts/opencrew_nipio_caddy.sh`
  - 生成 Basic Auth 保护的 Caddyfile。
  - 默认本地验证入口：`http://127.0.0.1.nip.io:18081`。
  - 反代到 `127.0.0.1:18080`，不暴露 backend raw port。

- `backend/main.py`
  - 支持 `OPENCREW_BACKEND_HOST` / `OPENCREW_BACKEND_PORT`。
  - 默认监听 `127.0.0.1:8011`。

- `frontend/vite.config.ts`
  - 支持 `OPENCREW_FRONTEND_HOST`。
  - 默认监听 `127.0.0.1:18080`。

## 本机启动与验证

安装依赖已经在本机完成：

- Homebrew PostgreSQL 16
- Homebrew Caddy
- backend Python 3.12 venv
- backend requirements

启动 OpenCrew：

```bash
cd /Users/macmini-1/work/code/OpenCrew
OPENCREW_DATA_DIR="$HOME/.opencrew" \
DATABASE_URL='postgresql+psycopg://opencrew:opencrew@127.0.0.1:5433/opencrew' \
scripts/opencrew_local_stack.sh restart
scripts/opencrew_local_stack.sh status
```

不要从 Phase 0 测试环境启动这个本地栈。特别是不要让
`OPENCREW_DATA_DIR=/private/tmp/opencrew-phase0-data`、
`OPENCREW_SECRET_STORE_PATH=/private/tmp/opencrew-phase0-secrets.enc` 或
`OPENCREW_SECRET_STORE_KEY=phase0-test-key` 进入生产本地服务；否则模型/API Key
会从空的测试 secret store 读取，表现为 ASR/TTS/Image 配置存在但 `has_api_key=false`。
`scripts/opencrew_local_stack.sh` 会在 start/restart 时拒绝这类 Phase 0 环境。

本机验证：

```bash
curl -fsS http://127.0.0.1:8011/api/health
curl -fsS http://127.0.0.1:18080/
curl -fsS http://127.0.0.1:18080/api/setup/summary
```

启动 Basic Auth 反代做本机 nip.io 验证：

```bash
export OPENCREW_BASIC_AUTH_USER="opencrew"
export OPENCREW_BASIC_AUTH_PASSWORD="<strong-password>"
export OPENCREW_CADDY_SITE="http://127.0.0.1.nip.io:18081"
scripts/opencrew_nipio_caddy.sh restart
curl -fsS -u "opencrew:<strong-password>" http://127.0.0.1.nip.io:18081/api/setup/summary
```

未带 Basic Auth 的请求必须失败：

```bash
curl -i http://127.0.0.1.nip.io:18081/api/setup/summary
```

预期返回 `401 Unauthorized`。

## 公网 nip.io 部署步骤

1. 确认 Mac mini 是否有公网 IPv4 入站能力。

```bash
curl -fsS https://api.ipify.org
```

得到的公网 IP 假设为 `<public-ip>`，公网域名就是：

```text
https://<public-ip>.nip.io
```

2. 如果 Mac mini 在路由器后面：

- 在路由器上把公网 `80/tcp` 和 `443/tcp` 转发到 Mac mini。
- 不要转发 `5433`、`8011`、`18080`、OpenCode 端口。

3. 启动 Caddy 公网入口：

```bash
export OPENCREW_BASIC_AUTH_USER="opencrew"
export OPENCREW_BASIC_AUTH_PASSWORD="<strong-password>"
export OPENCREW_CADDY_SITE="https://<public-ip>.nip.io"
scripts/opencrew_nipio_caddy.sh restart
```

4. 验证：

```bash
curl -I https://<public-ip>.nip.io
curl -fsS -u "opencrew:<strong-password>" https://<public-ip>.nip.io/api/setup/summary
```

## 安全规则

- 不使用 `OPENCREW_BACKEND_HOST=0.0.0.0` 做公网部署。
- 不使用 `OPENCREW_FRONTEND_HOST=0.0.0.0` 直接面向公网。
- 不在路由器/NPS/隧道中转发 PostgreSQL、OpenCode 或 backend raw port。
- Caddy/Nginx 外层必须有 Basic Auth、IP allowlist 或 VPN。
- 在 P0 事件/file 安全完成前，公网入口应优先限内部使用；匿名 share 也要等 share/file/event 过滤验证后再开放。

## 回滚

停止入口：

```bash
scripts/opencrew_nipio_caddy.sh stop
```

停止 OpenCrew frontend/backend：

```bash
scripts/opencrew_local_stack.sh stop
```

停止 standalone PostgreSQL：

```bash
backend/.venv/bin/python scripts/opencrew_postgres.py stop
```
