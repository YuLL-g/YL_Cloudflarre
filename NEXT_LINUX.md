# Linux 安装与使用入口

完整步骤已统一到[完整配置与使用手册](docs/完整配置与使用手册.md)。

- **全新部署：**先完成 Mac 和 Cloudflare，再按手册第 11～13 节测试公司出口、安装参考桥并使用 Codex。
- **已有历史桥：**先阅读手册第 18.1 节；历史脚本内嵌常量，新参考桥读取 `bridge.json`，不能直接混用。旁路端口验收后再切换，保留回滚。
- **日常使用：**运行 `~/.local/bin/codex-relay`，默认调用 PATH 中的 Codex；必要时用 `CODEX_RELAY_BIN` 指定绝对路径。
- **浏览器：**见手册第 14 节。将专用浏览器的 HTTP/HTTPS 代理指向127.0.0.1:7890，尚需实际登录和对话验收。

历史远端已完成API401和一次ChatGPT订阅请求（CONNECTION_OK，2,390tokens）；这是历史旧桥结果，不证明新参考桥已部署或长期稳定。模型目录刷新超时仍待处理。

不要通过公开GitHub传递生成的 `bridge.json`、旧 `linux-relay-settings.json`、密码或任何登录缓存。
