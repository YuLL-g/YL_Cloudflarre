"""Private configuration consistency, permissions and non-overwrite behavior."""
import importlib.util
import json
from pathlib import Path
import plistlib
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("prepare", ROOT / "scripts/prepare-config.py")
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class ConfigTests(unittest.TestCase):
    def test_consistent_private_files_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "private"
            binary = Path(directory) / "gost"
            names = p.prepare(ROOT, output, "relay.example.com", "http://proxy.example.net:80", binary, 7897)
            gost = json.loads((output / "gost.json").read_text())
            bridge = json.loads((output / "bridge.json").read_text())
            password = (output / "relay-password.txt").read_text().strip()
            self.assertEqual(gost["services"][0]["handler"]["auth"]["password"], password)
            self.assertEqual(bridge["password"], password)
            self.assertGreaterEqual(len(password), 32)
            self.assertEqual(bridge["endpoint"], "wss://relay.example.com/ws")
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for name in names:
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)
            agent = plistlib.loads((output / "com.transcodex.gost.plist").read_bytes())
            self.assertEqual(agent["ProgramArguments"], [str(binary), "-C", str(output / "gost.json")])
            before = {name: (output / name).read_bytes() for name in names}
            with self.assertRaises(ValueError):
                p.prepare(ROOT, output, "relay.example.com", "", binary, 7897)
            self.assertEqual(before, {name: (output / name).read_bytes() for name in names})

    def test_invalid_inputs_create_no_private_files(self):
        with tempfile.TemporaryDirectory() as directory:
            for hostname, proxy in (("https://relay.example.com", ""),
                                    ("relay.example.com", "http://user:pass@proxy.example.net:80"),
                                    ("relay.example.com", "socks5://proxy.example.net:80")):
                output = Path(directory) / "private"
                with self.assertRaises(ValueError):
                    p.prepare(ROOT, output, hostname, proxy, Path("/tmp/gost"), 7897)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
