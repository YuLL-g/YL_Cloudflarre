"""Offline protocol and full local TLS/corporate-proxy integration tests."""
import asyncio
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge", ROOT / "scripts/cfws_bridge.py")
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


def frame(data, opcode=2, final=True):
    length = len(data)
    head = bytes(((0x80 if final else 0) | opcode, length)) if length < 126 else (
        bytes(((0x80 if final else 0) | opcode, 126)) + struct.pack("!H", length))
    return head + data


async def client_frame(reader):
    first, second = await reader.readexactly(2)
    if not second & 0x80:
        raise AssertionError("client frame must be masked")
    length = second & 127
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4)
    data = await reader.readexactly(length)
    return first & 15, bytes(byte ^ mask[i % 4] for i, byte in enumerate(data))


class Writer:
    def __init__(self):
        self.data = bytearray()

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def make_ws(self, data):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return b.WebSocket(reader, Writer(), {"write_timeout": 1})

    async def test_ping_fragmentation_and_empty_frames(self):
        ws = self.make_ws(frame(b"", 2) + frame(b"part1", 2, False)
                          + frame(b"heartbeat", 9) + frame(b"part2", 0))
        self.assertEqual(await ws.receive(), b"part1")
        self.assertEqual(await ws.receive(), b"part2")
        reader = asyncio.StreamReader()
        reader.feed_data(ws.writer.data)
        self.assertEqual(await client_frame(reader), (10, b"heartbeat"))

    async def test_split_connect_response_and_tail(self):
        ws = self.make_ws(frame(b"HTTP/1.1 200 Con", 2, False)
                          + frame(b"nected\r\n\r\nTLS-BYTES", 0))
        tail = await b.authenticate(ws, "example.com:443", {"username": "test", "password": "synthetic-only"})
        self.assertEqual(tail, b"TLS-BYTES")

    async def test_coalesced_http_header_preserves_tail(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 101 Switching Protocols\r\n\r\n" + frame(b"tail"))
        header = await b.read_header(reader)
        self.assertTrue(header.endswith(b"\r\n\r\n"))
        self.assertEqual(await reader.readexactly(len(frame(b"tail"))), frame(b"tail"))

    async def test_reject_bad_frames(self):
        bad = [b"\x82\x80", b"\x09\x00", b"\x82\x7f" + struct.pack("!Q", b.FRAME_LIMIT + 1),
               frame(b"unexpected text", 1), frame(b"continuation", 0), b"\x82\x7e\x00\x01x"]
        for payload in bad:
            with self.subTest(payload_size=len(payload)):
                with self.assertRaises(b.ProtocolError):
                    await self.make_ws(payload).receive()

    async def test_close_is_not_empty_data(self):
        with self.assertRaises(ConnectionError):
            await self.make_ws(frame(b"\x03\xe8", 8)).receive()
        with self.assertRaises(b.ProtocolError):
            await self.make_ws(frame(b"x", 8)).receive()

    async def test_header_limit(self):
        reader = asyncio.StreamReader(limit=b.HEADER_LIMIT)
        reader.feed_data(b"x" * (b.HEADER_LIMIT + 4))
        with self.assertRaises(b.ProtocolError):
            await b.read_header(reader)

    async def test_heartbeat_timeout_and_matched_pong(self):
        ws = self.make_ws(b"")
        ws.config.update(ping_interval=0.01, pong_timeout=0.02)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.heartbeat(), 0.3)
        self.assertIsNotNone(ws.pending_ping)
        ws.reader = asyncio.StreamReader()
        ws.reader.feed_data(frame(b"wrong", 10) + frame(ws.pending_ping, 10) + frame(b"data"))
        self.assertEqual(await ws.receive(), b"data")
        self.assertTrue(ws.pong.is_set())

    async def test_target_allowlist_and_port(self):
        config = {"allowed_ports": [443], "allowed_hosts": [".example.com"]}
        for target in ("example.com:443", "a.example.com:443"):
            self.assertEqual(b.parse_target(("CONNECT " + target + " HTTP/1.1\r\n\r\n").encode(), config), target)
        for target in ("example.com.evil.test:443", "evil.test:443", "example.com:22", "https://example.com:443"):
            with self.assertRaises(b.ProtocolError):
                b.parse_target(("CONNECT " + target + " HTTP/1.1\r\n\r\n").encode(), config)

    async def test_private_config_permissions_and_placeholders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bridge.json"
            config = json.loads((ROOT / "examples/bridge.example.json").read_text())
            config["password"] = "synthetic-only"
            path.write_text(json.dumps(config))
            path.chmod(0o600)
            self.assertEqual(b.load_config(path)["listen_port"], 7890)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                b.load_config(path)
            path.chmod(0o600)
            config["endpoint"] = "ws://relay.example.com/ws"
            path.write_text(json.dumps(config))
            with self.assertRaises(ValueError):
                b.load_config(path)


@unittest.skipUnless(shutil.which("openssl"), "openssl required for local TLS certificate generation")
class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.cert = Path(cls.temp.name) / "test-cert.pem"
        cls.key = Path(cls.temp.name) / "test-key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-keyout", str(cls.key), "-out", str(cls.cert), "-subj", "/CN=localhost",
                        "-addext", "subjectAltName=DNS:localhost"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.key.chmod(0o600)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    async def asyncSetUp(self):
        self.jobs, self.writers = set(), set()
        self.seen_auth, self.pings, self.server_pong = False, 0, False
        self.bad_auth, self.bad_upgrade, self.drop_pongs = False, False, False
        self.expected_tls_reset = False
        # A rejected test certificate intentionally resets the mock TLS server.
        def handle_loop_error(loop, context):
            if self.expected_tls_reset and isinstance(context.get("exception"), ConnectionResetError):
                return
            loop.default_exception_handler(context)
        asyncio.get_running_loop().set_exception_handler(handle_loop_error)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(self.cert), str(self.key))
        self.upstream = await asyncio.start_server(self.ws_server, "127.0.0.1", 0, ssl=context)
        self.ws_port = self.upstream.sockets[0].getsockname()[1]
        self.corporate = await asyncio.start_server(self.proxy_server, "127.0.0.1", 0)
        config = json.loads((ROOT / "examples/bridge.example.json").read_text())
        config.update(endpoint="wss://localhost:{}/ws".format(self.ws_port),
                      corp_proxy="http://127.0.0.1:{}".format(self.corporate.sockets[0].getsockname()[1]),
                      username="test", password="synthetic-only", ca_file=str(self.cert),
                      ping_interval=0.03, pong_timeout=0.09, connect_timeout=1, write_timeout=1)
        self.bridge = b.Bridge(config)
        self.local = await asyncio.start_server(self.bridge.handle, "127.0.0.1", 0)
        self.port = self.local.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        for server in (self.local, self.corporate, self.upstream):
            server.close()
            await server.wait_closed()
        for writer in self.writers:
            writer.close()
        for task in list(self.bridge.tasks) + list(self.jobs):
            task.cancel()
        await asyncio.gather(*list(self.bridge.tasks), *list(self.jobs), return_exceptions=True)

    async def proxy_server(self, reader, writer):
        task = asyncio.current_task()
        self.jobs.add(task)
        self.writers.add(writer)
        jobs = []
        try:
            header = await b.read_header(reader)
            self.assertIn(("CONNECT localhost:{} HTTP/1.1".format(self.ws_port)).encode(), header)
            remote, out = await asyncio.open_connection("127.0.0.1", self.ws_port)
            self.writers.add(out)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()

            async def copy(source, destination):
                while True:
                    data = await source.read(65536)
                    if not data:
                        return
                    destination.write(data)
                    await destination.drain()
            jobs = [asyncio.create_task(copy(reader, out)), asyncio.create_task(copy(remote, writer))]
            await asyncio.wait(jobs, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            writer.close()
            self.jobs.discard(task)

    async def ws_server(self, reader, writer):
        task = asyncio.current_task()
        self.jobs.add(task)
        self.writers.add(writer)
        authenticated = False
        try:
            request = await b.read_header(reader)
            key = next(line.split(b":", 1)[1].strip() for line in request.split(b"\r\n")
                       if line.lower().startswith(b"sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1(key + b.GUID.encode()).digest())
            if self.bad_upgrade:
                accept = b"invalid"
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                         b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n" + frame(b"server-ping", 9))
            await writer.drain()
            while True:
                opcode, data = await client_frame(reader)
                if opcode == 10:
                    self.server_pong = data == b"server-ping"
                elif opcode == 9:
                    self.pings += 1
                    if not self.drop_pongs:
                        writer.write(frame(data, 10))
                elif opcode == 2:
                    if not authenticated:
                        self.assertIn(b"Proxy-Authorization: Basic " + base64.b64encode(b"test:synthetic-only"), data)
                        self.seen_auth = True
                        authenticated = True
                        if self.bad_auth:
                            writer.write(frame(b"HTTP/1.1 407 Authentication Required\r\n\r\n"))
                        else:
                            writer.write(frame(b"HTTP/1.1 200 Con", 2, False)
                                         + frame(b"nected\r\n\r\nwelcome", 0))
                    else:
                        writer.write(frame(data))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError):
            pass
        finally:
            writer.close()
            self.jobs.discard(task)

    async def connect(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.add(writer)
        writer.write(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
        await writer.drain()
        header = await asyncio.wait_for(b.read_header(reader), 3)
        return reader, writer, header

    async def test_complete_tls_proxy_echo_idle_heartbeat_and_cleanup(self):
        reader, writer, header = await self.connect()
        self.assertIn(b"200", header)
        self.assertEqual(await reader.readexactly(7), b"welcome")
        await asyncio.sleep(0.16)
        writer.write(b"after-idle")
        await writer.drain()
        self.assertEqual(await asyncio.wait_for(reader.readexactly(10), 1), b"after-idle")
        self.assertTrue(self.server_pong)
        self.assertGreater(self.pings, 1)
        writer.close()
        for _ in range(30):
            if not self.bridge.active:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.bridge.active, 0)

    async def test_auth_failure_returns_502_not_success(self):
        self.bad_auth = True
        _, _, header = await self.connect()
        self.assertIn(b"502", header)

    async def test_reject_wrong_accept(self):
        self.bad_upgrade = True
        _, _, header = await self.connect()
        self.assertIn(b"502", header)

    async def test_reject_untrusted_tls(self):
        self.expected_tls_reset = True
        self.bridge.config["ca_file"] = None
        _, _, header = await self.connect()
        self.assertIn(b"502", header)

    async def test_missing_pong_closes_stream(self):
        self.drop_pongs = True
        reader, _, header = await self.connect()
        self.assertIn(b"200", header)
        self.assertEqual(await reader.readexactly(7), b"welcome")
        self.assertEqual(await asyncio.wait_for(reader.read(), 1), b"")

    async def test_connection_limit_rejects_without_upstream(self):
        self.bridge.config["max_connections"] = 1
        await self.connect()
        _, _, header = await self.connect()
        self.assertIn(b"503", header)

    async def test_concurrent_connections_release(self):
        clients = await asyncio.gather(*(self.connect() for _ in range(6)))
        for reader, writer, header in clients:
            self.assertIn(b"200", header)
            self.assertEqual(await reader.readexactly(7), b"welcome")
            writer.write(b"parallel")
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.readexactly(8), 1), b"parallel")
            writer.close()
        for _ in range(50):
            if not self.bridge.active:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.bridge.active, 0)


if __name__ == "__main__":
    unittest.main()
