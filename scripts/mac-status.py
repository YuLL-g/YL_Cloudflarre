#!/usr/bin/env python3
"""Read-only local service and Cloudflare connector checks; never display credentials."""

import json
import os
import re
import socket
import subprocess
import urllib.error
import urllib.request


def service(label, target):
    result = subprocess.run(
        ["/bin/launchctl", "print", target], capture_output=True, text=True
    )
    state = re.search(r"^\s*state = (.+)$", result.stdout, re.M)
    pid = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.M)
    value = state.group(1).strip() if state else "未加载或无法查询"
    print(label + ": " + value + (" (PID " + pid.group(1) + ")" if pid else ""))
    return result.returncode == 0 and value == "running" and bool(pid)


def listening(label, port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
        print(label + " 127.0.0.1:" + str(port) + ": 正在监听")
        return True
    except OSError:
        print(label + " 127.0.0.1:" + str(port) + ": 未监听")
        return False


def connector():
    # Bypass any shell proxy when querying local metrics. No tunnel token is required.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for port in range(20241, 20246):
        base = "http://127.0.0.1:" + str(port)
        try:
            with opener.open(base + "/diag/tunnel", timeout=1) as response:
                data = json.load(response)
            if not isinstance(data, dict) or not data.get("tunnelID"):
                continue
            connections = data.get("connections", [])
            connected = sum(bool(item.get("isConnected")) for item in connections)
            try:
                with opener.open(base + "/ready", timeout=2) as response:
                    status = response.status
            except urllib.error.HTTPError as error:
                status = error.code
            print("cloudflared 就绪接口: HTTP " + str(status) + " (本机端口 " + str(port) + ")")
            print("Cloudflare 边缘连接: " + str(connected) + "/" + str(len(connections)))
            return status == 200 and connected > 0
        except (OSError, urllib.error.URLError, ValueError, TypeError):
            continue
    print("cloudflared 就绪接口: 未在默认 20241–20245 端口找到，需核对指标端口")
    return False


if __name__ == "__main__":
    checks = [
        service("gost", "gui/" + str(os.getuid()) + "/com.transcodex.gost"),
        service("cloudflared", "system/com.cloudflare.cloudflared"),
        listening("Clash", 7897),
        listening("gost", 8080),
        connector(),
    ]
    print("以上仅检查本机服务及 connector，不能替代 Linux 端到端验证。")
    raise SystemExit(0 if all(checks) else 1)
