# 已部署环境的检查与恢复

首次部署请按[完整配置与使用手册](docs/完整配置与使用手册.md)操作。以下用于检查已有环境，先检查状态再按需恢复。

## 1. Mac检查

在项目根目录执行：

```sh
python3 scripts/mac-status.py
python3 scripts/check-relay.py ws://127.0.0.1:8080/ws
```

第一条检查gost/cloudflared状态、Clash7897和gost8080监听、connector就绪/边缘连接；第二条读取项目外密码并检查WS101、认证CONNECT200、内层OpenAI TLS和无凭据401。均不调用模型。

如要测公网入口，使用自己的域名：

```sh
python3 scripts/check-relay.py wss://relay.example.com/ws
```

公网自测和Linux公司代理访问是不同路径。历史曾出现本地成功、connector正常但Mac公网TLS失败；应分别定位，不自动判定Linux断连。

## 2. Linux检查

```sh
systemctl is-active cfws-bridge.service
curl --proxy http://127.0.0.1:7890 --noproxy '' \
  --connect-timeout 10 --max-time 30 --silent --show-error \
  --output /dev/null --write-out 'status=%{http_code}\n' \
  https://api.openai.com/v1/models
```

401是该API端点的网络证据，完整订阅或网页还需各自验证。日志只提供脱敏的阶段与异常，不发送配置全文。

## 3. 恢复顺序

1. 确认Mac已唤醒、联网、登录，Clash出口正常。
2. 检查gost本地链路，再查cloudflared与公共路由。
3. Linux按手册第11节独立测试公司CONNECT/TLS和WS。
4. 检查本地桥并探测目标HTTPS。
5. 仅对已定位的组件重启；命令与状态码解释见手册第16节。

关闭Mac的Codex App或终端不影响已托管的转发；睡眠、退出用户登录、Clash不可用可能影响。服务状态不能代替实际端到端验证。
