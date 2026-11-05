# TransCodex 项目背景与目标

> 公开版说明：本文中的域名、内部代理、设备与用户路径均已脱敏，不能将示例当作真实运行配置。原始含秘密的外部调试记录只保留在本地并被 Git 忽略；公开证据见[历史实测摘要](docs/历史实测摘要.md)。从零部署以[完整配置与使用手册](docs/完整配置与使用手册.md)为准。历史章节描述的是各自记录时点。

## 信息来源与状态

- 以用户确认的信息和本地保留的外部设备调试记录为环境依据；该记录描述 2026-10-27 的构建、调试和成功运行情况。公开版以[历史实测摘要](docs/历史实测摘要.md)替代原始材料。
- 用户于 2026-11-02 确认：当前会话所在设备就是调试记录中的家中 MacBook；外地 Linux 是另一台设备。
- 区分“外部记录中的历史实测”“本项目当前机器上的验证”“待实施方案”；读取记录不等于重新验证外地设备或修改了远端服务。
- 2026-11-02 根据该记录修订：主路线由待评估的 ECS 双 SSH 隧道，改为现有 Cloudflare/WebSocket 链路升级至 Named Tunnel。详细方案见 [README.md](README.md)。

## 已知环境

### 外地 Linux

- RHEL 8 兼容 Linux，RHEL8/el8 兼容，内核 4.19，systemd；记录中具备免密 sudo。
- 只能通过公司 HTTP 代理 `proxy.example.net:80` 出网，存在目标域名过滤，UDP 全禁，直接连接公网基本不可用。此前“一般国内 IP 可访问”的粗略描述不能作为直连 ECS 的依据。
- 记录中 `trycloudflare.com` 等域名可用，ChatGPT/OpenAI 域名被拒；其他域名的表现不能证明 `codex.example.com` 会被允许。
- 历史 Codex CLI 为 `0.151.0`；2026-11-04 用户的实际调用日志已显示 `0.164.0`，调用路径沿用 `/usr/local/node24131/bin/codex`。采用 ChatGPT 订阅认证，不以 API Key 计费作为默认目标。
- 本地桥 `~/.local/bin/cfws_bridge.py` 监听 `127.0.0.1:7890`，通过 `cfws-bridge.service` 托管；记录中的脚本位于 `/home/yourname/.local/bin/cfws_bridge.py`。
- `codex()` shell 函数为 Codex 设置本地 HTTP/HTTPS 代理；非交互 shell、绝对路径调用和 systemd 不一定经过该函数。

### 家中 MacBook（当前设备）

- Mac 侧的 gost、cloudflared、Clash、launchd 配置与运行状态可在当前设备直接核查；历史记录中的状态仍需以实际检查更新，不因设备身份确认而自动视为当前运行状态。
- Apple Silicon；Clash 本地混合代理为 `127.0.0.1:7897`。
- 外部记录使用 gost `3.3.1`，位于 `~/gost-3.3.1_darwin_all/gost`；cloudflared `2026.10.3`，通过 Homebrew 安装。
- gost 在 `127.0.0.1:8080` 提供带认证的 WebSocket `/ws` 入口，出站必须显式经过 Clash `http://127.0.0.1:7897`。
- 记录中的 gost、Quick Tunnel 依赖两个前台窗口；Named Tunnel 和 Mac 服务托管尚未记录为完成。
- 用户购买了阿里云域名 `example.com`，没有确认购买 ECS 或 EIP。当前路线不以购买公网服务器为前提。

## 核心目标与数据边界

1. 外地 Linux 上运行 Codex CLI，工作区、文件操作、命令、构建与测试留在该设备，不将项目仓库同步到 Mac 或中转节点。
2. 沿用“本地桥 → 公司 HTTP 代理 → Cloudflare WSS → Mac cloudflared → gost → Clash → OpenAI”的已验证路径。
3. 将随机 Quick Tunnel 地址升级为稳定的 `codex.example.com`，减少人工更换域名及重启。
4. 补齐 Mac/Linux 服务托管、桥接协议处理、连接超时、心跳和分层诊断；在基础网络与出口恢复后争取 1–2 分钟内恢复新请求，此指标尚待验收。
5. 保持 ChatGPT 订阅认证；API 探测成功仅作为网络证据，不能替代订阅版 Codex 的完整响应验证。
6. 使用云端模型仍会把提示词、选中代码上下文和工具输出发送至 OpenAI。“代码留在本地”仅指存储和执行位置，不表示代码内容完全不出内网。
7. 保持内层 OpenAI HTTPS 的证书与主机名校验。外层 WSS 在 Cloudflare 终止，Cloudflare 可以接触隧道认证和 CONNECT 元数据；不得描述为整条链路完全不解密。
8. Mac 和转发组件不主动记录项目内容或认证正文；不在仓库中保存任何真实凭据。

## 已验证结果及不能外推的结论

- 外部记录：2026-10-27 的 Quick Tunnel 路线完成真实 `codex exec`，返回正常回复并报告 13,634 tokens；`codex login status` 显示 ChatGPT 登录。
- 外部记录：带凭据访问 API 模型列表返回 200；订阅响应端点 GET 返回 405。完整 Codex 调用是更强的成功证据，不因探测 API 而改用 API Key 模式。
- 外部记录：gost 原生客户端在该路径遇到 ALPN/HTTP/2 与经典 WebSocket Upgrade 不兼容；Python 桥采用 HTTP/1.1 路径成功。不能推广成所有 HTTP/2 都不支持 WebSocket，也不能混同 Mac cloudflared 的传输协议。
- 外部记录：Tailscale 控制面曾通过代理登录，但 UDP 与 DERP 可达性/配置尝试受限，外地 tailscaled 已停用。此前原因不明的判断已由这些具体障碍补充；仍不将其归因于缺少固定公网 IP。
- 外部记录：出现过隔夜 Quick Tunnel 不可用、公司代理返回 `302 dns_faile` 的情况。没有完整日志证明统一的“闲置自动回收”机制或每次瞬时断线都更换域名。
- 本项目先前在 Mac 验证：本地 CLI 为 `0.164.0`；经 `7897` 无认证访问 API 返回 401。这不等于外地 CLI 的版本或 Named Tunnel 已验证。

## 当前实施方向

- 优先按外部记录的远程管理 Named Tunnel 路线推进：Cloudflare 接管 DNS、公共主机名映射至 `http://127.0.0.1:8080`、Mac connector 与 gost 托管、Linux 桥切换固定域名。
- 注册商可以保留阿里云；标准 DNS 接入需要按 Cloudflare 分配的 NS 完成委派，并核对已有 DNS 记录。不是增加一条指向 ECS 的 A 记录。
- 新域名能否通过公司代理是迁移门槛；必须在外地测试公司 CONNECT、TLS、`/ws` Upgrade、gost 认证及真实 Codex 请求后才切换。未通过则保留临时回退并确认可用的获准入口，不假设更换 IP 或端口即可解决。
- 保留桥的“每个 CONNECT 新建一条 WebSocket”设计。历史旧桥的 Ping/Pong、30 秒超时、握手缓冲、跨帧 CONNECT 和连接释放存在待修复问题。2026-11-05 仓库已新增独立参考桥及本地测试，但没有部署到当前外地 Linux；不能宣称远端旧代码已修复。
- Mac cloudflared 到 Cloudflare 的连接与 Linux 到边缘的 WSS 是不同链路；前者使用 7844（QUIC/UDP 或 HTTP/2/TCP），后者经公司代理 CONNECT 到 443。
- Mac 用 launchd 管理 gost/cloudflared；区分登录启动的 LaunchAgent 和系统启动的 LaunchDaemon，结合 Clash GUI 的登录依赖验收。
- 不重新默认部署双 SSH、Tailscale、自建 DERP 或 ECS。只有实际出口限制变化或新证据支持时才重新评估。

## 稳定性与运维边界

- Named Tunnel 提供稳定入口，不保证连接永不中断；Cloudflare 边缘更新、代理空闲超时、Mac 断网等仍可能终止 WebSocket。
- “每请求新建 WebSocket”不等于请求很短；一个 CONNECT 内可能包含长期复用的 HTTPS 连接或流式请求。
- 已断开的 TCP/流式会话不能由桥透明无损重放；桥只对尚未交付业务流量的连接建立阶段做有限重试，业务重试交给 Codex。
- 不能仅用 `systemctl is-active`、HTTP 403/405、普通页面返回或 cloudflared Connected 判定全链路健康。
- 302/DNS 错误、Upgrade 失败和上游 CONNECT 失败均需分层定位，不能固定映射为唯一根因或无条件重启全部组件。
- Mac 必须保持在线、唤醒，Clash 上游可用；退出登录、合盖、重启和 FileVault 解锁依赖应单独验收。

## 后续待确认

- Mac 侧检查在当前设备进行；外地桥、systemd、公司代理可达性和订阅请求结果需来自外地 Linux 的实测，不能用 Mac 的联网结果替代。
- Cloudflare 站点接入、NS、Named Tunnel、公共路由与 Mac 服务已在本日实际部署并验证，详见下方最新执行结果；后续待验收的是外地公司代理、Linux 桥与订阅请求，以及两端重启/长时间运行。
- 公司代理对固定域名的 CONNECT、TLS 与 WebSocket Upgrade 已由用户在外地验证通过；固定时长、空闲断连和长期运行仍待验证。
- 外地 Python/OpenSSL 版本、实际桥代码是否与记录附录一致、凭据存储、服务用户和当前端点。
- 一次真实订阅请求已成功，后续仍待登录刷新、模型目录刷新超时定位、长时间空闲、流式响应、并发和断网恢复验证。

## 2026-11-02 重建连接检查

- 用户表示已关闭本机配置，并要求指导重新建立连接。当前以指导和本机只读核查为范围；操作步骤见 `RECONNECT.md`。
- Mac 的 Clash `7897` 仍在监听，无凭据访问 OpenAI API 返回 401；cloudflared 为 `/opt/homebrew/bin/cloudflared`，版本 `2026.10.3`。
- gost 不在 PATH，记录中的安装路径不存在，检查的 Downloads/Codex/Homebrew 位置也未找到；`8080` 无监听。不能据此断言整机所有目录都没有 gost。
- 检查的用户 LaunchAgents 和系统 LaunchDaemons 未发现名称含 cloudflare/gost/transcodex 的 plist；`~/.cloudflared` 不存在，不据此断言云端没有隧道。
- Google 与 Cloudflare DoH 查询根域名 NS 均返回 NXDOMAIN。系统解析的 `198.18.0.x` 不能作为公共 DNS 已配置成功的证据；需先确认实际注册域名、注册状态与 NS 委派。
- 用户确认 Cloudflare 域名尚未接入或状态不确定。下一步先在注册商核对域名，再按 Cloudflare 分配的 NS 完成接入。
- 本次准备了无凭据的 `examples/gost.macos.example.json`；没有安装软件、生成真实密码、启用服务或修改 DNS。

## 2026-11-03 域名复测

- 用户确认 `example.com` 已完成实名认证，阿里云提供的 NS 为 `dns17.hichina.com`、`dns18.hichina.com`。
- Google 与 Cloudflare DoH 的根域名 NS 查询均返回 NOERROR，记录一致指向这两台阿里云 NS；昨日根域名 NXDOMAIN 的现象本次已不再出现。
- 分别直接向这两台 NS 查询 SOA/NS 均得到 NOERROR 和权威应答。根域名 A 查询为 NOERROR、无 A 答案；`codex.example.com` 的 A 查询仍为 NXDOMAIN。
- 结论：根域名的公共 DNS 委派已可查询，当前仍由阿里云 DNS 托管，尚未切换为 Cloudflare NS；根域名没有 A 记录不妨碍继续接入 Cloudflare。
- 下一步是在 Cloudflare 添加站点、获取该站点实际分配的两台 NS，再到阿里云注册商处替换当前 NS，等待 Cloudflare Active。不要把阿里云 NS 当作 Cloudflare 要求的 NS。
- 本次仅复测 DNS，没有修改 NS、创建 Tunnel 或启动服务；Mac 系统仍返回 `198.18.0.x`，不以它代替公共 DNS 检查。

## 2026-11-03 Cloudflare 接入完成

- 用户已将阿里云注册商处 NS 改为 `assigned-ns1.ns.cloudflare.com`、`assigned-ns2.ns.cloudflare.com`，并提供 Cloudflare Overview 的接入成功提示。
- 本机通过 Google 与 Cloudflare DoH 复测，根域名 NS 均已返回上述 Cloudflare 地址；此前仍使用阿里云 NS 的结论已被此最新结果替代。
- `codex.example.com` 仍为 NXDOMAIN。域名接入与 Named Tunnel/公共应用路由是不同步骤；尚无后两项完成的证据。
- 最近复查 Mac：Clash `7897` 在监听，gost `8080` 无监听，检查的 gost 二进制位置及 `~/.config/transcodex/gost.json` 均不存在。
- 下一步按 `RECONNECT.md` 恢复 gost，并在 Cloudflare 创建远程管理 Tunnel、连接 Mac connector、发布 `codex` 到 HTTP `127.0.0.1:8080`。token 只由用户在本机执行或保管，不回传聊天。

## 2026-11-03 用户授权实际部署及执行结果

- 用户最新明确要求“帮我完成相应操作”，已授权本机安装、私有配置、服务启动和对应 Cloudflare 隧道/路由配置。此前仅指导/只读的任务范围已被这次授权更新；仍不购买资源，不读取或输出用户的 Cloudflare token。
- 已从 go-gost 官方 GitHub 发布资产下载 gost `3.3.1`，与已核对的 SHA-256 一致，安装至 `/Users/yourname/.local/bin/gost`。
- 已生成新的随机代理密码，存于项目外 `/Users/yourname/.config/transcodex/relay-password.txt`；gost 配置为同目录 `gost.json`。目录为 0700，秘密文件为 0600；不得输出这些文件的内容。
- 已安装并启动用户 LaunchAgent：`/Users/yourname/Library/LaunchAgents/com.transcodex.gost.plist`，标签 `com.transcodex.gost`；日志位于 `/Users/yourname/Library/Logs/TransCodex`，日志级别 warn。
- gost 监听 `127.0.0.1:8080`，WebSocket `/ws`、用户名 `codex`、认证启用，出站链为 Clash `127.0.0.1:7897`。
- 已验证 WebSocket 101、认证 CONNECT 200、内层 OpenAI TLS 证书校验通过、无 API 凭据请求返回 401；错误代理密码被拒绝（407）。向本次创建的 gost 进程发送 SIGTERM 后，launchd 自动恢复，TLS 探测再次通过。
- 网络探测脚本为 `scripts/check-relay.py`；它仅在本机进程内使用代理凭据，不输出秘密，不调用模型推理。不要把它当作外地订阅会话的验收。
- 已通过用户已登录的 Chrome 在 Cloudflare 创建远程管理隧道 `transcodex-home`；macOS 安装页等待 connector 连接。含 token 的命令需用户本人在 Mac 终端执行，已请求此项操作。
- 公共应用路由尚未配置，公网 WSS 与外地 Linux 尚未验通。浏览器扩展控制超时后改用 Chrome 原生可访问性界面继续；不通过隐藏浏览器 API 操作 Cloudflare。
- 外地 Linux 后续需要更新固定 hostname 和新代理密码；不沿用历史记录中出现过的旧密码，不复制 Codex 登录缓存。

## 2026-11-03 Mac 与 Cloudflare 部署完成

- 用户已在 Mac 执行页面提供的 connector 命令并确认连接成功。Cloudflare 页面实测显示 `transcodex-home` Healthy，1 个 darwin_arm64 replica。
- cloudflared 当前版本已变为 `2026.10.7`；用户安装后存在系统 LaunchDaemon `/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`。不读取或输出其 token 配置内容。
- 已通过 Cloudflare UI 新建公共应用路由 `codex.example.com` → `http://127.0.0.1:8080`，可选路径条件留空，桥实际 WebSocket 路径仍是 `/ws`；页面确认 DNS CNAME 已创建。
- 公共 DNS 对 `codex.example.com` 已返回 Cloudflare 边缘地址，不再是此前的 NXDOMAIN。
- 已从当前 Mac 通过公网 `wss://codex.example.com/ws` 完成 WebSocket 101、认证 CONNECT 200、OpenAI TLS 证书校验及 HTTP 401 探测。这是 API 网络验证，未调用模型或读取 Codex 登录态。
- 已在项目外生成私有 Linux 设置 `~/.config/transcodex/linux-relay-settings.json`，权限 0600；不得输出。无秘密的更新工具为 `scripts/configure-linux-bridge.py`，已用合成凭据验证常量替换、备份、无效输入不修改文件。
- 外地设备操作见 `NEXT_LINUX.md`。尚未从外地公司代理验证固定域名，也未修改外地桥或运行真实订阅任务；其长连接代码加固仍未完成。

## 2026-11-03 外地 Linux 固定域名初步验证

- 用户在外地 Linux 经 `proxy.example.net:80` 使用 curl HTTP/1.1、默认 TLS 校验，对 `https://codex.example.com/` 发起 HEAD。
- 结果为公司代理 `200 Connection established`，随后收到含 Cloudflare 响应头的 HTTP 404。说明该次公司 CONNECT 与 HTTPS 请求可完成；根路径没有提供普通页面，与本服务只在 `/ws` 提供 WebSocket 入口相符。
- 此结果不能外推为 WebSocket Upgrade、gost 认证、Linux 桥或 Codex 订阅请求已经通过。下一步在同一公司代理路径测试 GET `/ws` + Upgrade，预期 101；再同步新凭据、重启桥并验证本地代理。
- 不因根路径的 404 重建隧道、修改 DNS 或关闭 TLS 校验。
- 后续用户已在同一外地设备和公司代理上对 `/ws` 发起 Upgrade：CONNECT 200、HTTP 101，`Sec-WebSocket-Accept` 与给定 nonce 匹配；约 8 秒的 curl (28) 是本次 max-time 主动结束，不是握手失败。
- 最新进度：外地到固定入口的 WebSocket 路径已验证；下一步同步 `linux-relay-settings.json` 和无秘密的更新工具，更新历史桥、重启服务，验证代理认证及 OpenAI HTTPS。尚无 Linux 内层 CONNECT 或真实订阅调用的成功证据。

## 2026-11-04 外地 Linux API 网络验证通过

- 用户报告，按上一步通过 Linux 本地代理 `127.0.0.1:7890` 探测 `https://api.openai.com/v1/models` 已返回 `status=401`。
- 该无 OpenAI 凭据的请求符合预期，作为用户远端实测，确认 Linux 桥经公司代理、Cloudflare、Mac gost/Clash 到 API 端点的网络路径可用。没有据此读取或核验外地私密设置文件，也不将其等同于订阅鉴权成功。
- 下一步在外地 CLI 检查活动认证方式应为 ChatGPT，并在空临时目录以显式代理环境运行简单 `codex exec`。不复制登录缓存、不改用 API Key、不修改用户现有模型或服务地址配置。
- 真实订阅响应、刷新、长时间空闲、流式响应及断网恢复仍待验收；历史桥代码的稳定性缺陷没有因 API 401 自动解决。

## 2026-11-04 真实 ChatGPT 订阅调用通过

- 用户在空临时目录按显式本地代理环境执行测试，登录方式显示 ChatGPT，最终得到 `CONNECTION_OK`，日志报告 2,390 tokens。
- 外地实际 CLI 为 `0.164.0`，provider 为 `openai`，模型为 `gpt-6-sol`，reasoning effort 为 high，sandbox 为 read-only。不把它改写成历史 0.151.0，也不擅自切换模型或认证方式。
- 日志 UTC 时间为 2026-11-03 16:06–16:07，对应 Asia/Shanghai 2026-11-04 00:06–00:07。
- 结论：新固定域名方案已在外地 Linux 完成一次真实订阅请求，基础端到端使用路径验收通过；不代表长期稳定性已达标。
- 同次日志出现多次 `failed to refresh available models: request timed out`，但实际推理成功。模型目录刷新是独立待排查项，不判定为登录失败或整条隧道故障，也不宣布该错误已修复。
- 官方模型目录文档区分 Codex 模型元数据端点与公共 `/v1/models`。排查应区分目录请求时限、端点可达性、桥的超时/缓冲/心跳、并发与 CLI 行为；目前没有足够证据指定单一根因。
- 已准备无凭据 Linux 启动器 `scripts/codex-relay`，仅为 Codex 进程设置已验证代理环境，不改用户 shell 配置、登录缓存、模型或 provider；尚未在外地安装。
- 下一阶段：处理模型目录刷新超时并加固历史 Linux 桥，再验证空闲、过夜、重启及断网恢复；不能把功能已通等同于永不断连。

## 2026-11-04 本机状态检查

- 用户询问本地检查方法。只读复查中，gost 和 cloudflared 的 launchd 状态均为 running，Clash `7897` 和 gost `8080` 都在监听。
- cloudflared 本地指标服务位于 `127.0.0.1:20241`；`/ready` 返回 200，`/diag/tunnel` 报告 4 条连接均为 isConnected。
- 本地 `ws://127.0.0.1:8080/ws` 的完整探测通过 101、认证 CONNECT 200、OpenAI TLS 校验及 HTTP 401。
- 同次从 Mac 访问公网 `wss://codex.example.com/ws` 的探测发生外层 TLS 握手超时；显式经 Clash 的公网 Upgrade 探测也出现 TLS 错误。没有因此重启服务，不据此宣称 Linux 端已断连，也不能因进程 running 或 connector ready 就宣称公网入口完全正常。该时点的 Mac 公网访问路径问题仍待定位。
- 准备了只读本机检查工具 `scripts/mac-status.py`，只输出服务状态、PID、监听状态及 connector 就绪/连接计数，不显示参数、token 或密码。

## 工作约定

- 2026-11-05 用户授权整理详细复现文档、建立 Git 版本控制并发布到公开 GitHub 仓库；指定远程为 `https://github.com/YuLL-g/YL_Cloudflarre`，不另建仓库、不强推或覆盖远程他人提交。公开内容须去除内部环境标识与真实凭据。
- 当前发布仅新增文档、模板和参考工具，不自动替换正在运行的 Mac/Linux 服务。新参考桥 `scripts/cfws_bridge.py` 使用项目外的 `bridge.json`；旧常量更新器和 `linux-relay-settings.json` 不能当作新桥配置。

- 以用户最新授权为准：已允许实际安装和部署本机转发与 Cloudflare 隧道/路由；不得超出该范围购买资源或变更无关系统设置。Cloudflare token 的本机输入/执行仍交由用户处理。
- 原始调试记录保留为历史材料；其中含真实曾用密码，新文档不得复制。正式部署前轮换相关凭据，并将密码/隧道 token 放入权限受限的配置或凭据文件。
- 不读取、输出或提交 Codex `auth.json`、其备份、SSH 私钥、Cloudflare token、Clash 订阅等秘密；不沿用手工把认证 JSON 粘贴到聊天或日志的做法。
- TLS 默认校验保持开启；必要企业 CA 通过正规信任配置安装，不能以关闭校验作为修复。
- 修改桥协议处理时针对分段头、跨帧响应、Ping/Pong、空闲、关闭及泄漏进行有意义的测试，再在真实路径回归；不要只测试 API 模型列表。
- 涉及 Codex、cloudflared、gost 的版本相关选项时核对对应官方资料。不要未经验证升级全部组件或重走记录中已经失败的路线。
