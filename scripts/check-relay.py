#!/usr/bin/env python3
"""Probe WS proxy authentication and verified OpenAI HTTPS without API credentials."""

import argparse
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import time
from pathlib import Path
from urllib.parse import urlsplit


class WebSocket:
    def __init__(self, endpoint, timeout):
        url = urlsplit(endpoint)
        if url.scheme not in ("ws", "wss") or not url.hostname or url.username:
            raise ValueError("Use a ws:// or wss:// endpoint without credentials")
        port = url.port or (443 if url.scheme == "wss" else 80)
        self.sock = socket.create_connection((url.hostname, port), timeout=timeout)
        self.buffer = bytearray()
        self.fragmented = False
        try:
            if url.scheme == "wss":
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=url.hostname
                )
            key = base64.b64encode(os.urandom(16)).decode()
            host = url.hostname if port in (80, 443) else f"{url.hostname}:{port}"
            path = url.path or "/ws"
            if url.query:
                path += "?" + url.query
            self.sock.sendall(
                (f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                 "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {key}\r\n"
                 "Sec-WebSocket-Version: 13\r\n\r\n").encode()
            )
            header = self.read_header()
            lines = header.decode("iso-8859-1").split("\r\n")
            if len(lines[0].split()) < 2 or lines[0].split()[1] != "101":
                status = lines[0].split()[1:2]
                raise RuntimeError("WebSocket upgrade status: " + " ".join(status))
            fields = dict(
                (key.strip().lower(), value.strip())
                for key, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
            )
            expected = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()
            ).digest()).decode()
            if (fields.get("sec-websocket-accept") != expected
                    or fields.get("upgrade", "").lower() != "websocket"
                    or "upgrade" not in fields.get("connection", "").lower()):
                raise RuntimeError("Invalid WebSocket handshake")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.sock.close()

    def read_header(self):
        while b"\r\n\r\n" not in self.buffer:
            data = self.sock.recv(4096)
            if not data:
                raise ConnectionError("EOF during WebSocket handshake")
            self.buffer.extend(data)
            if len(self.buffer) > 65536:
                raise RuntimeError("WebSocket headers exceed limit")
        end = self.buffer.index(b"\r\n\r\n") + 4
        header = bytes(self.buffer[:end])
        del self.buffer[:end]
        return header

    def exact(self, size):
        while len(self.buffer) < size:
            data = self.sock.recv(min(size - len(self.buffer), 65536))
            if not data:
                raise ConnectionError("WebSocket EOF")
            self.buffer.extend(data)
        value = bytes(self.buffer[:size])
        del self.buffer[:size]
        return value

    def send(self, data, opcode=2):
        size = len(data)
        if size < 126:
            header = bytes((0x80 | opcode, 0x80 | size))
        elif size < 65536:
            header = bytes((0x80 | opcode, 0xFE)) + struct.pack("!H", size)
        else:
            header = bytes((0x80 | opcode, 0xFF)) + struct.pack("!Q", size)
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(
            byte ^ mask[index % 4] for index, byte in enumerate(data)
        ))

    def receive(self):
        while True:
            first, second = self.exact(2)
            final, opcode, size = bool(first & 0x80), first & 0x0F, second & 0x7F
            if first & 0x70 or second & 0x80:
                raise RuntimeError("Invalid server WebSocket frame")
            if opcode >= 8 and (not final or size > 125):
                raise RuntimeError("Invalid WebSocket control frame")
            if size == 126:
                size = struct.unpack("!H", self.exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.exact(8))[0]
            if size > 8 * 1024 * 1024:
                raise RuntimeError("WebSocket frame exceeds probe limit")
            data = self.exact(size)
            if opcode == 8:
                raise ConnectionError("WebSocket closed")
            if opcode == 9:
                self.send(data, opcode=10)
                continue
            if opcode == 10:
                continue
            if opcode == 2 and not self.fragmented:
                self.fragmented = not final
            elif opcode == 0 and self.fragmented:
                self.fragmented = not final
            else:
                raise RuntimeError("Unexpected WebSocket data frame")
            if data:
                return data


def probe(endpoint, password_file, timeout):
    password = Path(password_file).read_text().strip()
    if not password:
        raise ValueError("Relay credential file is empty")
    connection = WebSocket(endpoint, timeout)
    try:
        credential = base64.b64encode(("codex:" + password).encode()).decode()
        connection.send(
            ("CONNECT api.openai.com:443 HTTP/1.1\r\n"
             "Host: api.openai.com:443\r\n"
             f"Proxy-Authorization: Basic {credential}\r\n\r\n").encode()
        )
        buffer = bytearray()
        while b"\r\n\r\n" not in buffer:
            buffer.extend(connection.receive())
            if len(buffer) > 65536:
                raise RuntimeError("Proxy headers exceed limit")
        end = buffer.index(b"\r\n\r\n") + 4
        status = bytes(buffer[:end]).split(b"\r\n", 1)[0].split()
        if len(status) < 2 or status[1] != b"200":
            code = status[1].decode("ascii", "replace") if len(status) > 1 else "invalid"
            raise RuntimeError("Authenticated CONNECT status: " + code)
        incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        tls = ssl.create_default_context().wrap_bio(
            incoming, outgoing, server_side=False, server_hostname="api.openai.com"
        )
        if len(buffer) > end:
            incoming.write(buffer[end:])

        def flush():
            while outgoing.pending:
                connection.send(outgoing.read())

        def perform(operation):
            while True:
                try:
                    result = operation()
                    flush()
                    return result
                except ssl.SSLWantReadError:
                    flush()
                    incoming.write(connection.receive())
                except ssl.SSLWantWriteError:
                    flush()

        perform(tls.do_handshake)
        request = memoryview(
            b"GET /v1/models HTTP/1.1\r\nHost: api.openai.com\r\nConnection: close\r\n\r\n"
        )
        while request:
            written = perform(lambda: tls.write(request))
            request = request[written:]
        response = bytearray()
        while b"\r\n" not in response:
            part = perform(lambda: tls.read(4096))
            if not part:
                raise ConnectionError("OpenAI HTTPS EOF")
            response.extend(part)
            if len(response) > 65536:
                raise RuntimeError("OpenAI response headers exceed limit")
        fields = bytes(response).split(b"\r\n", 1)[0].split()
        if len(fields) < 2 or not fields[1].isdigit():
            raise RuntimeError("Invalid HTTPS response")
        return {"websocket_upgrade": 101, "authenticated_connect": 200,
                "openai_tls_verified": True, "openai_http_status": int(fields[1])}
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint", help="ws://127.0.0.1:8080/ws or wss://relay-host/ws")
    parser.add_argument("--password-file", default=str(
        Path.home() / ".config/transcodex/relay-password.txt"
    ))
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    started = time.monotonic()
    try:
        report = probe(args.endpoint, args.password_file, args.timeout)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(json.dumps(report))
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__,
                          "error": str(error)}))
        raise SystemExit(1)
