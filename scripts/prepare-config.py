#!/usr/bin/env python3
"""Create new private deployment files; never overwrite an existing deployment."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
from urllib.parse import urlsplit


def prepare(root, output, hostname, corp_proxy, gost_bin, clash_port):
    if not re.fullmatch(r"[A-Za-z0-9.-]+", hostname) or "." not in hostname:
        raise ValueError("hostname must be a real hostname without scheme or path")
    if not 1 <= clash_port <= 65535:
        raise ValueError("invalid Clash port")
    if corp_proxy:
        proxy = urlsplit(corp_proxy)
        if (proxy.scheme != "http" or not proxy.hostname or proxy.username or proxy.password
                or proxy.path not in ("", "/") or proxy.query or proxy.fragment):
            raise ValueError("corporate proxy must be http://host:port without credentials")
        proxy.port
    names = ("gost.json", "bridge.json", "relay-password.txt", "com.transcodex.gost.plist")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    if any((output / name).exists() or (output / name).is_symlink() for name in names):
        raise ValueError("deployment files already exist; use a new staging directory")
    password = secrets.token_urlsafe(32)
    gost = json.loads((root / "examples/gost.macos.example.json").read_text())
    gost["services"][0]["handler"]["auth"]["password"] = password
    gost["chains"][0]["hops"][0]["nodes"][0]["addr"] = "127.0.0.1:" + str(clash_port)
    gost["log"] = {"level": "warn"}
    bridge = json.loads((root / "examples/bridge.example.json").read_text())
    bridge.update(endpoint="wss://" + hostname + "/ws", corp_proxy=corp_proxy, password=password)
    logs = Path.home() / "Library/Logs/TransCodex"
    agent = {"Label": "com.transcodex.gost", "ProgramArguments": [str(gost_bin), "-C", str(output / "gost.json")],
             "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
             "ProcessType": "Background", "StandardOutPath": str(logs / "gost.out.log"),
             "StandardErrorPath": str(logs / "gost.err.log")}
    payloads = {"gost.json": json.dumps(gost, indent=2).encode() + b"\n",
                "bridge.json": json.dumps(bridge, indent=2).encode() + b"\n",
                "relay-password.txt": password.encode() + b"\n",
                "com.transcodex.gost.plist": plistlib.dumps(agent)}
    for name, payload in payloads.items():
        fd = os.open(str(output / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--corp-proxy", required=True, help="http://host:port or empty string for direct egress")
    parser.add_argument("--clash-port", type=int, default=7897)
    parser.add_argument("--gost-bin", type=Path, default=Path.home() / ".local/bin/gost")
    parser.add_argument("--output-dir", type=Path, default=Path.home() / ".config/transcodex")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    output = args.output_dir.expanduser().resolve()
    # A public working tree must never hold generated credentials, even if ignored.
    if output == root or root in output.parents:
        raise ValueError("output directory must be outside this repository")
    names = prepare(root, output, args.hostname, args.corp_proxy,
                    args.gost_bin.expanduser().resolve(), args.clash_port)
    print("Created private files outside the repository (values not displayed):")
    for name in names:
        print(output / name)
    print("No service was started or changed. Transfer bridge.json privately to Linux.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit("Configuration preparation failed: " + type(error).__name__
                         + "; no private values displayed; existing files are not overwritten.")
