# 阿里云轻量应用服务器部署

当前生产实例使用 Alibaba Cloud Linux 3，应用由独立的 `optionedge` 用户和 systemd
运行，只监听 `127.0.0.1:8790`。Nginx 对外监听 80/443、强制 HTTPS，并把请求转发
给应用。登录名和密码仅保存在服务器的 `/etc/option-edge.env` 中。

公网入口使用 Let's Encrypt 的短期 IP 地址证书，因此不依赖域名。证书续期由
`option-edge-cert-renew.timer` 每天检查两次。添加正式域名后，应将 Nginx 的
`server_name` 和证书路径切换为已备案域名。

常用维护命令：

```bash
systemctl status option-edge nginx option-edge-cert-renew.timer
journalctl -u option-edge -n 100 --no-pager
curl http://127.0.0.1:8790/health
systemctl restart option-edge
```
