# OpenCrew 内网穿透与 URL 配置避坑指南

本文基于当前这套可工作的链路整理：

- 本地电脑运行 `OpenCrew frontend`、`OpenCrew backend`、`npc`
- 公网服务器运行 `nps`
- 公网域名 `www.goldenstand.cn` 先进入公网服务器 `nginx`
- `nginx` 再把请求转给 `nps` 暴露出来的映射端口

目标访问地址示例：

- `https://www.goldenstand.cn/live-bot/Sessions/`

## 一句话理解整条链路

公网访问不是直接打到你本地，而是按下面这条链路走：

`浏览器 -> 公网服务器 nginx -> 公网服务器 nps 映射端口 -> 本地 npc -> 本地 OpenCrew frontend/backend`

如果中间任何一层路径、端口、Host、反代规则不对，最后都会表现成：

- 502
- 404
- Host not allowed
- 资源路径不对
- 无限 302 重定向

## 当前推荐端口与角色

建议按下面这组端口分工：

- 本地前端：`127.0.0.1:18080`
- 本地 backend：`0.0.0.0:8011`
- NPS 控制端口：`113.125.202.171:8024`
- NPS 映射公网端口：`10000`

这里的关键点是：

- `target_addr` 必须指向本地真正跑页面的端口
- 如果本地前端在 `18080`，`npc.conf` 里就必须写 `127.0.0.1:18080`

## 本地 frontend 配置

文件：`OpenCrew/frontend/vite.config.ts`

推荐配置思路：

- `host: true`
- `port: 18080`
- `allowedHosts` 放行公网域名
- `base` 使用子路径部署前缀

当前这套场景里，核心是：

- 页面入口路径：`/live-bot/Sessions/`
- 页面资源路径：`/live-bot/Sessions/@vite/client`、`/live-bot/Sessions/src/main.tsx`
- API 路径：`/live-bot/Sessions/api/...`

## 本地 frontend 入口文件

文件：`OpenCrew/frontend/index.html`

最容易踩坑的一点：

- 不要把入口脚本写成根路径绝对地址

错误示例：

```html
<script type="module" src="/src/main.tsx"></script>
```

这个写法会导致当页面挂在 `/live-bot/Sessions/` 下时，浏览器去错误地请求：

- `/src/main.tsx`
- `/@vite/client`

而不是：

- `/live-bot/Sessions/src/main.tsx`
- `/live-bot/Sessions/@vite/client`

结果就是页面本体能打开，但脚本 404，整个应用白屏。

## 本地 backend 配置

文件：`OpenCrew/backend/main.py`

不要只监听 `127.0.0.1`。

推荐：

```python
uvicorn.run("main:app", host="0.0.0.0", port=8011, reload=True)
```

原因：

- `npc`、反代、本地 IP 访问场景更稳
- 后面做 NPS 映射时，不容易被监听地址卡住

## npc.conf 参考模板

参考文件：

- `Test/darwin_amd64_client/conf/npc.conf`
- `Test/darwin_amd64_client/conf/multi_account.conf`

参考配置：

```ini
[common]
server_addr=113.125.202.171:8024
conn_type=tcp
vkey=Has1Password01
auto_reconnection=true
max_conn=1000
flow_limit=1000
rate_limit=1000
basic_username=11
basic_password=3
crypt=true
compress=true
disconnect_timeout=60

[tcp]
mode=tcp
target_addr=127.0.0.1:18080
server_port=10000
```

`multi_account.conf` 示例：

```ini
npc=npc.pwd
```

## 运行 NPC 的正确方式

不要只用下面这种裸命令当成最终可用性验证：

```bash
npc -server=113.125.202.171:8024 -vkey=Has1Password01 -type=tcp
```

这个只能说明：

- 控制连接能不能建立

它不能完整说明：

- 配置是否加载成功
- 代理注册是否成功
- 映射规则是否真的被服务端接受

正确方式是：

```bash
npc -config /path/to/npc.conf
```

## Reconnect NPC 的正确含义

`Reconnect NPC` 只负责：

1. Stop 当前 npc 进程
2. 用已有 conf 重新启动
3. 看连接日志是否成功建立

判断标准只看：

- `Loading configuration file ... successfully`
- `Successful connection with server ...`
- 没有立即出现 `EOF`
- 没有出现 `The server returned an error ...`

只要这些成立，状态就应该是：

- `Connected`

`Reconnect NPC` 不应该混入下面这些问题：

- 域名 nginx 是否转发对
- 公网用户能不能访问你的页面
- 云服务器外网端口是否开放

这些属于公网入口层的问题，不属于 `Reconnect NPC` 本身。

## nginx 正确角色

这里说的 `nginx`，是公网服务器上的 `nginx`，不是你本地电脑上的 `nginx`。

如果你访问的是：

- `https://www.goldenstand.cn/live-bot/Sessions/`

那一定是公网服务器上的 `nginx` 先收到请求。

它再把请求转发到：

- 公网服务器本机 `nps` 暴露出来的映射端口

然后才通过隧道回到你的本地。

## nginx 最终成功示意

你这次最终工作的方式，本质是：

```nginx
location /live-bot/Sessions/ {
    proxy_pass http://127.0.0.1:10000/Sessions/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

注意这里的关键点：

- 域名入口先到 nginx
- nginx 再去转 `127.0.0.1:10000`
- `10000` 是 `nps` 暴露出来的映射口

也就是说，NPS 通道本身虽然已经建立，但如果这层 nginx 没配对，公网域名仍然不通。

## 这次踩过的坑

### 1. 误以为 NPS 连接成功就等于公网一定通

不是。

NPS 连接成功只说明：

- `npc` 到 `nps` 控制通道建立好了

还不代表：

- 域名请求一定能被正确转到这个通道里

### 2. Vite Host 未放行

现象：

- `Blocked request. This host (...) is not allowed`

原因：

- `allowedHosts` 没配置公网域名

### 3. 页面脚本资源路径错误

现象：

- `GET /src/main.tsx 404`
- `GET /@vite/client 404`

原因：

- `index.html` 里用了根路径资源

### 4. 本地前端端口和 `target_addr` 不一致

现象：

- `Reconnect NPC` 似乎成功
- 但访问页面还是不通

原因：

- 本地前端跑在 `5188`
- `npc.conf` 却指向 `127.0.0.1:18080`

### 5. nginx 502

现象：

- `502 Bad Gateway`

原因通常是：

- nginx 后面转到的 NPS 暴露端口没有接住
- 或映射端口根本没开放

### 6. nginx 302 重定向死循环

现象：

- `/live-bot/Sessions/` 返回 `302`
- `Location` 还是 `/live-bot/Sessions/`

原因：

- nginx 自己把请求重定向回自己
- 是公网服务器规则问题，不是本地服务问题

## 推荐排查顺序

每次出问题，按下面顺序查最快：

1. 本地前端是否在监听目标端口
2. 本地 backend 是否在监听 `8011`
3. `npc -config npc.conf` 是否成功
4. Step 2 是否显示：
   - `config_loaded`
   - `server_reachable`
   - `handshake_ok`
   - `proxy_registration_ok`
5. 公网服务器本机是否能访问 NPS 映射端口
6. nginx 是否把域名路径正确转给这个映射端口
7. 页面资源路径是否在子路径下正确生成

## 最后一句经验总结

这套链路一定要分成三层理解：

1. 本地服务是否正常
2. NPS 隧道是否正常
3. 公网域名入口是否正常

这三层不能混着判断。

最常见的误判是：

- 本地服务正常
- `npc` 连上了 `nps`
- 就以为公网一定通

实际上，公网域名那一层还要看 `nginx` 的路径转发和资源 URL 是否完全对齐。
