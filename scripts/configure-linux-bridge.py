#!/usr/bin/env python3
"""Update the historical Linux bridge from a locally supplied private settings file."""

import argparse
import ast
import base64
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def render_update(source, settings):
    host = settings["WS_HOST"]
    credential = settings["PROXY_AUTH"]
    if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise ValueError("WS_HOST must be a hostname without scheme or path")
    decoded = base64.b64decode(credential, validate=True)
    if not decoded.startswith(b"codex:") or len(decoded) <= len(b"codex:"):
        raise ValueError("PROXY_AUTH must encode the proxy username and password")
    tree = ast.parse(source)
    for name in ("WS_HOST", "PROXY_AUTH"):
        nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                 and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                 and node.targets[0].id == name]
        if len(nodes) != 1:
            raise ValueError("Expected one top-level assignment for " + name)
        # Support only the one-line constants used by the historical bridge.
        original_line = source.splitlines()[nodes[0].lineno - 1]
        if not re.match(r"^" + name + r"[ \t]*=", original_line):
            raise ValueError("Unsupported bridge constant layout for " + name)
        pattern = r"(?m)^" + name + r"[ \t]*=[^\n]*$"
        replacement = name + " = " + repr(settings[name])
        source, count = re.subn(pattern, lambda match: replacement, source)
        if count != 1:
            raise ValueError("Expected exactly one replaceable constant for " + name)
    compile(source, "cfws_bridge.py", "exec")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--bridge", type=Path,
                        default=Path.home() / ".local/bin/cfws_bridge.py")
    args = parser.parse_args()
    settings_path = args.settings.expanduser()
    bridge = args.bridge.expanduser()
    if bridge.is_symlink() or not bridge.is_file():
        raise ValueError("Bridge must be an existing regular file")
    if stat.S_IMODE(settings_path.stat().st_mode) & 0o077:
        raise ValueError("Set private settings permissions to 600 before use")
    settings = json.loads(settings_path.read_text())
    original = bridge.read_text()
    updated = render_update(original, settings)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = bridge.with_name(bridge.name + ".bak." + stamp)
    fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(original)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=str(bridge.parent),
                                         prefix=".transcodex-", delete=False) as output:
            staged = Path(output.name)
            output.write(updated)
        staged.chmod(0o600)
        os.replace(str(staged), str(bridge))
    finally:
        if staged is not None and staged.exists():
            staged.unlink()
    print("Updated bridge hostname and proxy authentication; values not displayed.")
    print("Private backup:", backup)
    print("Restart cfws-bridge, then verify from the Linux device.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not print malformed private input or source text through exception messages.
        raise SystemExit("Bridge update failed: " + type(error).__name__
                         + ". Check the local files and permissions; no secrets displayed.")
