# 在 iStoreOS 上部署 Option Edge

推荐把项目放在 iStoreOS 的数据盘，而不是系统盘。例如：

```text
/mnt/data/docker/option-edge
```

## 1. 准备配置

进入项目目录并复制环境变量模板：

```bash
cp .env.istoreos.example .env
```

默认仅把 `8790` 映射到 iStoreOS 的 `127.0.0.1`，不会暴露给家庭局域网或公网。

## 2. 仅在 iStoreOS 本机运行

```bash
docker compose -f compose.istoreos.yml up -d --build
docker compose -f compose.istoreos.yml ps
docker compose -f compose.istoreos.yml logs -f option-radar
```

健康检查：

```bash
curl http://127.0.0.1:8790/health
```

如果希望家庭局域网设备直接访问，把 `.env` 中的 `OPTION_RADAR_BIND` 改为
`0.0.0.0`，然后重新运行 `docker compose up -d`。此时访问：

```text
http://<iStoreOS局域网IP>:8790
```

不要在 iStoreOS 防火墙中把 `8790` 直接转发到公网。

## 3. 推荐：通过 Tailscale 私网远程访问

在 Tailscale 管理后台生成 Auth Key，写入 `.env` 的 `TS_AUTHKEY`，然后执行：

```bash
docker compose -f compose.istoreos.yml --profile tailscale up -d --build
docker compose -f compose.istoreos.yml logs -f tailscale
```

首次启动后，Tailscale 后台会出现名为 `option-edge` 的设备。启用 tailnet HTTPS 后，
通过日志或 Tailscale 管理后台查看它的 `*.ts.net` 地址。只有同一 tailnet 中被授权的
设备可以访问该地址。

Tailscale 状态诊断：

```bash
docker compose -f compose.istoreos.yml exec tailscale tailscale status
docker compose -f compose.istoreos.yml exec tailscale tailscale serve status
```

Tailscale 状态保存在 Docker volume `option-edge_tailscale-state` 中。首次成功加入后，
可以从 `.env` 删除 Auth Key，再执行一次 `docker compose up -d`，避免长期保留密钥。

## 4. 可选：Cloudflare Tunnel + 浏览器登录

如果不想在外部手机或电脑安装 Tailscale，可在 Cloudflare 创建 remotely-managed
Tunnel，将 Public Hostname 的服务地址设为 `http://127.0.0.1:8790`，并在 Access
中限制允许登录的邮箱。把 Tunnel Token 写入 `.env` 后执行：

```bash
docker compose -f compose.istoreos.yml --profile cloudflare up -d --build
docker compose -f compose.istoreos.yml logs -f cloudflared
```

不要只创建 Public Hostname 而不配置 Access 策略，否则任何知道域名的人都能打开页面。

## 5. 更新和回滚

更新源码后重新构建：

```bash
docker compose -f compose.istoreos.yml build --pull option-radar
docker compose -f compose.istoreos.yml up -d
docker compose -f compose.istoreos.yml ps
```

查看最近日志：

```bash
docker compose -f compose.istoreos.yml logs --tail=200 option-radar
```

停止但保留 Tailscale 身份：

```bash
docker compose -f compose.istoreos.yml down
```

不要使用 `down -v`，除非确定要删除 Tailscale 节点状态并重新注册。
