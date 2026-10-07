# TradingView + Jin10 AI Commodities

面向原油、黄金、白银的 TradingView 策略与金十 MCP 风险分析系统。Pine 脚本负责确定性的技术入场、回测和风控；本地 Python 服务负责读取金十行情/快讯/财经日历，并可调用 OpenAI Responses API 生成结构化风险闸门。

> 默认只做研究、回测和提醒，不连接券商、不自动下单。先用模拟盘和样本外数据验证。

## 当前线上版本

本仓库对应阿里云轻量应用服务器上的 Option Edge 决策台版本。线上运行采用
Python 3.11 + systemd + Nginx，应用本身只监听服务器的 `127.0.0.1:8790`，公网
入口由 Nginx 提供 HTTPS 和登录保护。服务器上的账号、密码、金十令牌和 OpenAI
Key 均通过环境变量或独立配置文件注入，不在仓库中保存。

详细的服务器部署、证书续期、健康检查和故障排查见
[deploy/aliyun/README.md](deploy/aliyun/README.md)。如果迁移到 Docker 或其他云平台，
可参考根目录的 `Dockerfile`、`compose.istoreos.yml` 和 `render.yaml`。

## 已实现

1. **12小时研究报告**：按 `cursor` 分页读取快讯和资讯，获取 WTI/布伦特报价。
2. **K线与策略分析**：事件评分、分钟K线结构、条件式策略卡和数据过期保护。
3. **历史持久化**：SQLite 保存运行、事件、报价、K线和策略24小时后表现。
4. **验证与提醒**：历史趋势回测、最小样本/胜率门槛和条件提醒日志。
5. **TradingView 策略**：同一份 Pine v6 脚本自动适配 WTI/布伦特、黄金和白银。
6. **AI 风险闸门**：输出允许方向、风险倍数、有效期、阻断事件和证据；缺少 API Key 时自动降级为规则闸门。
7. **异步 webhook**：TradingView 信号在 3 秒内返回 `202`，金十和 AI 分析在后台完成。
8. **国内商品期权买方决策台**：实时扫描原油、黄金、白银全部在市期权链，计算RV5/RV20、ATM IV、25Delta偏斜、期限相对价值、Theta负担与五日情景回报，并通过硬闸门和六项独立评分筛选推荐/观察候选。

项目始终优先解析 MCP 的 `result.structuredContent`。`result.content` 不作为机器解析来源。

## 认证

令牌按以下优先级读取：

1. `JIN10_BEARER_TOKEN`
2. `JIN10_API_TOKEN`
3. 本机 `~/.codex/config.toml` 中的 `[mcp_servers.jin10]` 静态 Bearer 请求头

令牌不会写入数据库、报告或日志。

## 使用

```bash
# 检查 MCP 握手、工具和资源
python3 -m jin10_research doctor --json

# 生成并保存12小时研究报告
python3 -m jin10_research report --hours 12

# 查看历史统计
python3 -m jin10_research stats --json

# 对本地累计的WTI分钟K线做趋势回测
python3 -m jin10_research backtest --code USOIL --json

# 检查最近4小时是否出现满足条件的研究提醒
python3 -m jin10_research monitor --hours 4 --json

# 生成黄金风险简报；未设置 OPENAI_API_KEY 时使用规则降级
python3 -m jin10_research ai-brief --asset gold --hours 12 --json

# 明确不调用 OpenAI，只测试金十数据和规则闸门
python3 -m jin10_research ai-brief --asset XAGUSD --no-ai --json

# 本机接收 TradingView 告警
python3 -m jin10_research webhook --host 127.0.0.1 --port 8787

# 启动国内原油/黄金/白银期权实时雷达
python3 -m jin10_research option-radar

# 只抓取一次并在终端显示排名前20的合约
python3 -m jin10_research option-radar --once
```

浏览器打开 `http://127.0.0.1:8790`。决策台默认每5秒刷新一次，可用
`--refresh 10` 调整；期货日线每30分钟更新一次，公开期权接口不提供逐条交易所
时间戳，因此页面显示的是本地抓取时间。买方分由方向、波动价值、波动启动、
期限结构、合约效率和流动性组成；任何报价、DTE、Delta、Theta或价差硬闸门失败
都会取消推荐资格。五日情景回报是模型压力测试，不是收益预测或自动交易指令。

### 手机远程访问

最省维护的云端方式是把仓库连接到 Render，并用根目录的 `render.yaml` 创建
Blueprint。平台会按 `Dockerfile` 构建服务、检查 `/live`，并在首次部署时要求
填写下面两个变量：

```text
OPTION_RADAR_USERNAME=你的登录名
OPTION_RADAR_PASSWORD=一个独立的强密码
```

部署完成后，手机直接打开 Render 分配的 HTTPS 地址并登录即可。本地没有设置这
两个变量时，不启用登录保护；两个变量必须同时设置。免费 Web Service 长时间无人
访问会休眠，适合按需打开决策台，不适合全天候后台扫描；全天运行时应改用常驻实例。

如果家里的 Mac 可以一直开机，则无需云服务器：在 Mac 和手机登录同一个
Tailscale 账户，再用 Tailscale Serve 将 `http://127.0.0.1:8790` 仅分享给自己的
私网设备。这种方式不会把决策台公开到互联网。

## iStoreOS / Docker 部署

项目包含面向 iStoreOS 的多架构 Docker 镜像和 Compose 配置，支持三种运行模式：

- 仅 iStoreOS 本机或家庭局域网访问
- Tailscale 私网 HTTPS 访问（推荐）
- Cloudflare Tunnel + Access 浏览器登录

完整操作见 [deploy/istoreos/README.md](deploy/istoreos/README.md)。默认端口只绑定
`127.0.0.1`，不会直接暴露给局域网或公网。

如需 AI，将 API Key 只放在服务端环境变量中：

```bash
export OPENAI_API_KEY="..."
# 可选；默认使用兼顾质量与成本的 gpt-5.6-terra
export OPENAI_MODEL="gpt-5.6-terra"
```

## 安装到 TradingView

1. 打开 [tradingview/commodities_ai_strategy.pine](tradingview/commodities_ai_strategy.pine)，复制全部内容到 TradingView 的 Pine Editor。
2. 保存并点击「添加到图表」。建议先分别在 `USOIL`/`XAUUSD`/`XAGUSD` 的 15 分钟或 30 分钟图测试；若券商代码不同，在脚本设置中手动选择 Asset profile。
3. 在 Strategy Tester 中加入真实手续费、滑点和合约参数，再做训练期/样本外分段验证。
4. 先运行 `ai-brief`，把 `allowed_direction`、`risk_multiplier`、`valid_until` 映射到脚本的 External gate 输入：

   - `both` → Both
   - `long_only` → Long only
   - `short_only` → Short only
   - `none` → Pause

5. 创建告警时选择该策略和 `alert() function calls only`，频率使用 `Once Per Bar Close`。不要同时选择订单成交和 `alert()`，否则可能产生重复消息。

Pine 不能直接访问任意 HTTP API 或 MCP，因此 AI 结果不能自动“回写”到已运行的 Pine 脚本。手动交易时，把最新简报复制到 External gate；未来如连接券商，应由 webhook 服务在真正下单前执行 AI 闸门，而不是让 Pine 直接自动成交。

## TradingView webhook

本地测试：

```bash
python3 -m jin10_research webhook --no-ai
curl http://127.0.0.1:8787/health
curl -X POST http://127.0.0.1:8787/webhook/tradingview \
  -H 'Content-Type: application/json' \
  -d '{"asset":"gold","symbol":"OANDA:XAUUSD","side":"long","price":4300}'
curl 'http://127.0.0.1:8787/latest?asset=gold'
```

TradingView 云端无法访问你的 `127.0.0.1`。真实 webhook 需要公网 HTTPS 端点，且只接受 80/443 端口。建议在反向代理层校验 TradingView 的客户端证书或来源 IP，并做限流；不要把 API Key、金十令牌或券商密码放进告警 JSON。

输出文件：

- `data/tradingview_signals.jsonl`：收到的原始技术信号
- `data/ai_decisions.jsonl`：金十上下文、AI/规则决策和错误记录

报告写入 `reports/`，SQLite 数据库和提醒日志写入 `data/`。两个目录默认不进入 Git。

## 策略启用门槛

默认只有同时满足以下条件才会把候选策略写入 `data/alerts.jsonl`：

- 实时报价和K线没有过期
- 策略不是 `observe`
- 置信度至少为 `medium`
- 已评价模拟策略不少于20条
- 历史回测信号不少于30条
- 模拟与回测胜率均不低于52%
- 模拟与回测平均方向收益为正

开发时可用 `--allow-unvalidated` 查看未达到样本门槛的候选，但这不会改变数据库中的验证状态。

## 定时执行

`scheduler/` 包含两个未安装的 macOS `launchd` 模板：

- `com.jin10.research-reports.plist`：每天 08:00、16:00、22:00 生成报告
- `com.jin10.research-monitor.plist`：每10分钟运行一次条件监控

安装前先确认时间安排和绝对路径。确认后可复制到 `~/Library/LaunchAgents/` 并使用 `launchctl bootstrap` 启用。项目不会自动安装或启用系统任务。

## 数据局限

- 金十 `get_kline` 提供的是短周期分钟数据；周末或休市时可能返回空数组。
- 长期回测必须依靠定时任务从现在开始持续积累本地K线。
- 新闻与K线按时间关联只能说明相关性，不能证明因果关系。
- 当前系统只生成研究和模拟策略，不连接交易账户，也不自动下单。
- 金十 MCP 当前只提供短窗口分钟 K 线，TradingView 的长期回测结果应以图表数据为准。
- AI 只适合做事件与数据质量过滤；它不会让技术策略天然具有正期望，也不应绕过止损和每日风险上限。
- 修改 Pine 输入后，TradingView 已创建的告警仍可能保留旧参数，应删除并重新创建告警。
