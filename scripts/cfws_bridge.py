#!/usr/bin/env python3
"""Loopback CONNECT -> corporate CONNECT -> verified WSS -> authenticated gost.

Python 3.8+, standard library only. No HTTP forwarding, SOCKS, UDP or replay.
This reference implementation is separate from the historical deployed bridge.
"""
import argparse
import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import struct
import time
from urllib.parse import urlsplit

HEADER_LIMIT = 16384
FRAME_LIMIT = 8 * 1024 * 1024
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class ProtocolError(Exception):
    pass


def load_config(path):
    path = Path(path).expanduser()
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError("configuration must be a regular private file (chmod 600)")
    if info.st_uid != os.geteuid():
        raise ValueError("configuration must belong to the service user")
    config = json.loads(path.read_text())
    url = urlsplit(config["endpoint"])
    if (url.scheme != "wss" or not url.hostname or url.username or url.password
            or url.fragment or url.query or not re.fullmatch(r"[A-Za-z0-9.-]+", url.hostname)
            or not re.fullmatch(r"/[A-Za-z0-9/_-]*", url.path)):
        raise ValueError("endpoint must be wss://hostname/path without credentials")
    if not config.get("username") or not config.get("password"):
        raise ValueError("missing relay credentials")
    if ":" in config["username"] or config["password"] == "REPLACE_WITH_NEW_PASSWORD":
        raise ValueError("set real relay credentials outside the repository")
    proxy = config.get("corp_proxy", "")
    if proxy:
        parsed = urlsplit(proxy)
        if (parsed.scheme != "http" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in ("", "/") or parsed.query
                or parsed.fragment):
            raise ValueError("corp_proxy must be http://host:port without credentials")
        parsed.port
    url.port
    defaults = dict(listen_host="127.0.0.1", listen_port=7890, connect_timeout=20,
                    write_timeout=30, ping_interval=20, pong_timeout=60,
                    max_connections=64, allowed_ports=[443], allowed_hosts=[], ca_file=None)
    for key, value in defaults.items():
        config.setdefault(key, value)
    if config["listen_host"] not in ("127.0.0.1", "::1"):
        raise ValueError("listen_host must be a loopback literal")
    for key in ("connect_timeout", "write_timeout", "ping_interval", "pong_timeout"):
        if not isinstance(config[key], (int, float)) or not 0 < config[key] <= 3600:
            raise ValueError("invalid timeout setting")
    if not isinstance(config["max_connections"], int) or not 1 <= config["max_connections"] <= 256:
        raise ValueError("max_connections must be 1..256")
    for port in [config["listen_port"]] + config["allowed_ports"]:
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("invalid port")
    if not config["allowed_ports"]:
        raise ValueError("allowed_ports must not be empty")
    if (not isinstance(config["allowed_hosts"], list) or any(
            not isinstance(host, str) or not re.fullmatch(r"\.?[A-Za-z0-9.-]+", host)
            for host in config["allowed_hosts"])):
        raise ValueError("invalid allowed_hosts")
    return config


def response_fields(header, expected):
    lines = header.decode("iso-8859-1").split("\r\n")
    fields = lines[0].split()
    if len(fields) < 2 or fields[0] not in ("HTTP/1.0", "HTTP/1.1") or fields[1] != str(expected):
        # Status only: no headers, credentials or response body in errors.
        status = fields[1] if len(fields) > 1 and fields[1].isdigit() else "invalid"
        raise ProtocolError("unexpected HTTP status " + status)
    result = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise ProtocolError("malformed header")
        key, value = line.split(":", 1)
        key = key.strip().lower()
        if key in result:
            raise ProtocolError("duplicate header")
        result[key] = value.strip()
    return result


async def read_header(reader):
    try:
        header = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise ProtocolError("headers exceed limit") from None
    if len(header) > HEADER_LIMIT:
        raise ProtocolError("headers exceed limit")
    return header


def raw_connection(config):
    """Blocking TCP/corporate handshake only; executed in the bounded thread pool."""
    url = urlsplit(config["endpoint"])
    proxy = urlsplit(config.get("corp_proxy", ""))
    destination = (proxy.hostname, proxy.port or 80) if proxy.hostname else (url.hostname, url.port or 443)
    sock = socket.create_connection(destination, config["connect_timeout"])
    try:
        deadline = time.monotonic() + config["connect_timeout"]
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        if proxy.hostname:
            target = "{}:{}".format(url.hostname, url.port or 443)
            sock.sendall(("CONNECT {0} HTTP/1.1\r\nHost: {0}\r\n\r\n".format(target)).encode("ascii"))
            header = bytearray()
            # Do not over-read bytes belonging to the upcoming TLS exchange.
            while not header.endswith(b"\r\n\r\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("corporate CONNECT deadline")
                sock.settimeout(remaining)
                byte = sock.recv(1)
                if not byte:
                    raise ConnectionError("corporate CONNECT EOF")
                header.extend(byte)
                if len(header) > HEADER_LIMIT:
                    raise ProtocolError("corporate headers exceed limit")
            response_fields(header, 200)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


class WebSocket:
    def __init__(self, reader, writer, config):
        self.reader, self.writer, self.config = reader, writer, config
        self.lock = asyncio.Lock()
        self.fragmented = False
        self.pending_ping = None
        self.pong = asyncio.Event()

    @classmethod
    async def connect(cls, config):
        future = asyncio.get_running_loop().run_in_executor(None, raw_connection, config)
        try:
            sock = await asyncio.shield(future)
        except asyncio.CancelledError:
            def discard(done):
                try:
                    done.result().close()
                except Exception:
                    pass
            future.add_done_callback(discard)
            raise
        url = urlsplit(config["endpoint"])
        try:
            context = ssl.create_default_context(cafile=config.get("ca_file"))
            context.set_alpn_protocols(["http/1.1"])
            reader, writer = await asyncio.open_connection(
                sock=sock, ssl=context, server_hostname=url.hostname, limit=HEADER_LIMIT)
        except BaseException:
            sock.close()
            raise
        ws = cls(reader, writer, config)
        try:
            key = base64.b64encode(os.urandom(16)).decode("ascii")
            host = url.hostname if (url.port or 443) == 443 else "{}:{}".format(url.hostname, url.port)
            request = ("GET {} HTTP/1.1\r\nHost: {}\r\nUpgrade: websocket\r\n"
                       "Connection: Upgrade\r\nSec-WebSocket-Version: 13\r\n"
                       "Sec-WebSocket-Key: {}\r\n\r\n").format(url.path, host, key)
            writer.write(request.encode("ascii"))
            await writer.drain()
            fields = response_fields(await read_header(reader), 101)
            expected = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
            tokens = [item.strip().lower() for item in fields.get("connection", "").split(",")]
            if (fields.get("sec-websocket-accept") != expected
                    or fields.get("upgrade", "").lower() != "websocket" or "upgrade" not in tokens
                    or "sec-websocket-extensions" in fields):
                raise ProtocolError("invalid WebSocket upgrade")
            return ws
        except BaseException:
            await ws.close()
            raise

    async def send(self, data, opcode=2):
        if len(data) > FRAME_LIMIT or (opcode >= 8 and len(data) > 125):
            raise ProtocolError("outgoing frame too large")
        length = len(data)
        header = bytes((0x80 | opcode, 0x80 | length)) if length < 126 else (
            bytes((0x80 | opcode, 0xFE)) + struct.pack("!H", length) if length <= 65535 else
            bytes((0x80 | opcode, 0xFF)) + struct.pack("!Q", length))
        mask = os.urandom(4)
        payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))
        async with self.lock:
            self.writer.write(header + mask + payload)
            await asyncio.wait_for(self.writer.drain(), self.config["write_timeout"])

    async def receive(self):
        while True:
            first, second = await self.reader.readexactly(2)
            final, opcode, length = bool(first & 0x80), first & 15, second & 127
            if first & 0x70 or second & 0x80:
                raise ProtocolError("invalid server frame flags")
            if opcode >= 8 and (not final or length > 125):
                raise ProtocolError("invalid control frame")
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
                if length < 126:
                    raise ProtocolError("noncanonical length")
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
                if length < 65536:
                    raise ProtocolError("noncanonical length")
            if length > FRAME_LIMIT:
                raise ProtocolError("frame exceeds limit")
            data = await self.reader.readexactly(length)
            if opcode == 8:
                if len(data) == 1:
                    raise ProtocolError("invalid close payload")
                await self.send(data, 8)
                raise ConnectionError("WebSocket close")
            if opcode == 9:
                await self.send(data, 10)
                continue
            if opcode == 10:
                if self.pending_ping is not None and data == self.pending_ping:
                    self.pong.set()
                continue
            if opcode == 2 and not self.fragmented:
                self.fragmented = not final
            elif opcode == 0 and self.fragmented:
                self.fragmented = not final
            else:
                raise ProtocolError("unexpected data opcode")
            # Empty data frames are legal; they are not TCP EOF.
            if data:
                return data

    async def heartbeat(self):
        while True:
            await asyncio.sleep(self.config["ping_interval"])
            self.pending_ping = os.urandom(8)
            self.pong.clear()
            await self.send(self.pending_ping, 9)
            await asyncio.wait_for(self.pong.wait(), self.config["pong_timeout"])
            self.pending_ping = None

    async def close(self):
        self.writer.close()
        try:
            await asyncio.wait_for(self.writer.wait_closed(), 2)
        except Exception:
            pass


def parse_target(header, config):
    first = header.split(b"\r\n", 1)[0].decode("ascii").split()
    if len(first) != 3 or first[0] != "CONNECT" or first[2] not in ("HTTP/1.0", "HTTP/1.1"):
        raise ProtocolError("only CONNECT supported")
    target = first[1]
    if target.startswith("["):
        match = re.fullmatch(r"\[([0-9a-fA-F:]+)\]:([0-9]+)", target)
        if not match:
            raise ProtocolError("invalid target")
        host, port = match.groups()
        ipaddress.IPv6Address(host)
    else:
        match = re.fullmatch(r"([A-Za-z0-9.-]+):([0-9]+)", target)
        if not match:
            raise ProtocolError("invalid target")
        host, port = match.groups()
    if int(port) not in config["allowed_ports"]:
        raise ProtocolError("target port denied")
    host = host.lower().rstrip(".")
    allowed = config["allowed_hosts"]
    if allowed and not any(host == item.lower().lstrip(".") or (
            item.startswith(".") and host.endswith(item.lower())) for item in allowed):
        raise ProtocolError("target host denied")
    return target


async def authenticate(ws, target, config):
    auth = base64.b64encode((config["username"] + ":" + config["password"]).encode()).decode("ascii")
    await ws.send(("CONNECT {0} HTTP/1.1\r\nHost: {0}\r\n"
                   "Proxy-Authorization: Basic {1}\r\n\r\n").format(target, auth).encode("ascii"))
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        buffer.extend(await ws.receive())
        if len(buffer) > HEADER_LIMIT + FRAME_LIMIT:
            raise ProtocolError("CONNECT response exceeds limit")
    end = buffer.index(b"\r\n\r\n") + 4
    if end > HEADER_LIMIT:
        raise ProtocolError("CONNECT headers exceed limit")
    response_fields(bytes(buffer[:end]), 200)
    return bytes(buffer[end:])


class Bridge:
    def __init__(self, config):
        self.config, self.active, self.tasks = config, 0, set()

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        if self.active >= self.config["max_connections"]:
            writer.write(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\n\r\n")
            writer.close()
            self.tasks.discard(task)
            return
        self.active += 1
        ws, jobs, delivered, stage = None, [], False, "local-connect"
        try:
            header = await asyncio.wait_for(read_header(reader), self.config["connect_timeout"])
            target = parse_target(header, self.config)
            stage = "outer-connect-tls-upgrade"
            ws = await asyncio.wait_for(WebSocket.connect(self.config), self.config["connect_timeout"])
            stage = "relay-auth-connect"
            tail = await asyncio.wait_for(authenticate(ws, target, self.config), self.config["connect_timeout"])
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n" + tail)
            await asyncio.wait_for(writer.drain(), self.config["write_timeout"])
            delivered, stage = True, "stream"

            async def upload():
                while True:
                    data = await reader.read(65536)
                    if not data:
                        return
                    await ws.send(data)

            async def download():
                while True:
                    writer.write(await ws.receive())
                    await asyncio.wait_for(writer.drain(), self.config["write_timeout"])

            jobs = [asyncio.create_task(job()) for job in (upload, download, ws.heartbeat)]
            done, _ = await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                finished.result()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Log fixed stages and types only, never exception text or HTTP headers.
            logging.warning("stage=%s error=%s", stage, type(error).__name__)
            if not delivered:
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
                try:
                    await asyncio.wait_for(writer.drain(), self.config["write_timeout"])
                except Exception:
                    pass
        finally:
            for job in jobs:
                job.cancel()
            if jobs:
                await asyncio.gather(*jobs, return_exceptions=True)
            if ws:
                await ws.close()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 2)
            except Exception:
                pass
            self.active -= 1
            self.tasks.discard(task)


async def serve(config):
    bridge, stopped = Bridge(config), asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopped.set)
    server = await asyncio.start_server(bridge.handle, config["listen_host"], config["listen_port"], limit=HEADER_LIMIT)
    logging.warning("bridge ready on loopback port %d", config["listen_port"])
    try:
        await stopped.wait()
    finally:
        server.close()
        await server.wait_closed()
        for task in list(bridge.tasks):
            task.cancel()
        await asyncio.gather(*list(bridge.tasks), return_exceptions=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(Path.home() / ".config/transcodex/bridge.json"))
    parser.add_argument("--check", action="store_true", help="validate configuration without connecting")
    args = parser.parse_args()
    logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.WARNING)
    try:
        settings = load_config(args.config)
        if args.check:
            print("Configuration valid; private values not displayed.")
        else:
            asyncio.run(serve(settings))
    except Exception as error:
        raise SystemExit("Bridge stopped: " + type(error).__name__ + "; check configuration, permissions and network.")
