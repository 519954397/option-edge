# 安全说明

## 不能提交到 Git

以下内容只能通过服务器环境变量、秘密管理器或本机私有配置注入：

- `JIN10_BEARER_TOKEN`、`JIN10_API_TOKEN`
- `OPENAI_API_KEY`
- `OPTION_RADAR_USERNAME`、`OPTION_RADAR_PASSWORD`
- `TS_AUTHKEY`、`CLOUDFLARE_TUNNEL_TOKEN`
- SSH 私钥、服务器密码、GitHub Personal Access Token

项目自带的 `.gitignore` 会忽略 `.env`、虚拟环境、运行数据和报告。提交前仍应使用
密码扫描工具检查一次，并确认 `git diff --cached` 中没有密钥。

## 线上访问

应用不应直接暴露 8790 端口。生产环境应让 Nginx、Caddy 或云负载均衡处理 HTTPS，
并在应用层启用 `OPTION_RADAR_USERNAME` 和 `OPTION_RADAR_PASSWORD`。修改凭据后重启
`option-edge` 服务，并从外部网络验证旧凭据已经失效。
