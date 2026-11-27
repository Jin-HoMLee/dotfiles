#!/usr/bin/env python3
"""Offline tests. No real av, Telegram, launchctl, home or transport journal."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "home" / "telegram-receiver.py"
spec = importlib.util.spec_from_file_location("receiver", SOURCE)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)

FIXTURE = '''import os
class BridgeError(Exception): pass
class APIError(BridgeError): pass
def main():
    case = os.environ.get("MOCK_CASE", "network")
    print("PRIVATE_MESSAGE_SHOULD_NOT_ESCAPE")
    errors = {
      "network": "Telegram unavailable after three polls; stopped; saved requests remain queued",
      "auth": "Bot authentication/ownership conflict; stopped; no webhook or consumer reset",
      "owner": "Another local consumer owns this Telegram bot; stop it first",
      "webhook": "Bot has an existing webhook; nothing changed; use a dedicated unused bot",
      "uncertain": "Delivery 1 is uncertain; no automatic resend; resolve locally",
      "rejected": "Delivery 1 is rejected; no automatic resend; resolve locally",
      "approval": "Telegram approval changed; stopped without rebinding",
      "unknown": "PRIVATE_TOKEN_AND_MESSAGE",
    }
    if case == "crash": os.kill(os.getpid(), 9)
    if case == "normal": return
    raise BridgeError(errors[case])
'''
AV_FIXTURE = '''#!PYTHON
import os, sys
from pathlib import Path
path = Path(os.environ["MOCK_CALLS"])
path.write_text(path.read_text() + "call\\n" if path.exists() else "call\\n")
case = os.environ.get("MOCK_VAULT", "ok")
if case != "ok":
    print("av inject: Automic Vault approval service is not running; open the menu bar app"
          if case == "not-ready" else "PRIVATE_TOKEN_AND_MESSAGE", file=sys.stderr)
    sys.exit(1)
assert sys.argv[1:4] == ["inject", "+FM_TELEGRAM_BOT_TOKEN", "--"]
assert "FM_TELEGRAM_BOT_TOKEN" not in os.environ
os.environ["FM_TELEGRAM_BOT_TOKEN"] = "SYNTHETIC_IN_MEMORY_ONLY"
os.execv(sys.argv[4], sys.argv[4:])
'''


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="telegram-receiver-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir(mode=0o700)
        self.transport = self.root / "transport.py"
        self.transport.write_text(FIXTURE)
        self.runner = self.root / "receiver.py"
        self.runner.write_text(SOURCE.read_text().replace(receiver.TRANSPORT_SHA256,
                                hashlib.sha256(FIXTURE.encode()).hexdigest()))
        self.av = self.root / "av"
        self.av.write_text(AV_FIXTURE.replace("PYTHON", sys.executable))
        self.av.chmod(0o700)
        self.calls = self.root / "calls"
        self.folder = self.home / "state" / "telegram-receiver"

    def invoke(self, case="network", vault="ok"):
        env = {**os.environ, "MOCK_CASE": case, "MOCK_VAULT": vault,
               "MOCK_CALLS": str(self.calls), "FM_TELEGRAM_BOT_TOKEN": "MUST_BE_REMOVED"}
        result = subprocess.run([sys.executable, str(self.runner), "run", "--home", str(self.home),
                                 "--transport", str(self.transport), "--av", str(self.av)],
                                env=env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")

    def last(self):
        return json.loads((self.folder / "last-stop.json").read_text())

    def test_network_retries_without_latch(self):
        self.invoke()
        self.assertEqual(self.last()["reason"], "network_unavailable")
        self.assertTrue(self.last()["automatic_retry"])
        self.assertFalse((self.folder / "halt.json").exists())
        self.invoke()
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_vault_not_ready_then_recovery(self):
        self.invoke(vault="not-ready")
        self.assertEqual(self.last()["reason"], "vault_not_ready")
        self.assertTrue(self.last()["automatic_retry"])
        self.invoke()
        self.assertEqual(self.last()["reason"], "network_unavailable")

    def test_hard_stops_survive_scheduled_and_login_restart(self):
        for case, reason in (("auth", "authentication_or_ownership"), ("owner", "local_consumer"),
                             ("webhook", "webhook"), ("uncertain", "delivery_requires_review"),
                             ("rejected", "delivery_requires_review"), ("approval", "approval_changed"),
                             ("unknown", "transport_requires_review"), ("normal", "unexpected_stop"),
                             ("crash", "unexpected_stop")):
            with self.subTest(case=case):
                self.invoke(case)
                self.assertEqual(self.last()["reason"], reason)
                self.assertFalse(self.last()["automatic_retry"])
                before = self.calls.read_text()
                self.invoke()
                self.assertEqual(self.calls.read_text(), before)
                (self.folder / "halt.json").unlink()  # explicit fixture-only rearm

    def test_vault_unknown_failure_stops_without_persisting_output(self):
        self.invoke(vault="denied")
        self.assertEqual(self.last()["reason"], "unexpected_stop")
        self.assertTrue((self.folder / "halt.json").exists())
        self.assertNotIn("PRIVATE", "".join(p.read_text() for p in self.folder.iterdir()))

    def test_changed_source_does_not_invoke_vault(self):
        self.transport.write_text(FIXTURE + "\n# changed\n")
        self.invoke()
        self.assertEqual(self.last()["reason"], "transport_changed")
        self.assertFalse(self.calls.exists())

    def test_crashed_supervisor_marker_never_automatically_rearms(self):
        self.folder.mkdir(parents=True, mode=0o700)
        (self.folder / "halt.json").write_text("incomplete")
        self.invoke()
        self.assertFalse(self.calls.exists())

    def test_duplicate_supervisor_does_not_invoke_vault(self):
        self.folder.mkdir(parents=True, mode=0o700)
        fd = receiver.private_open(self.folder / "supervisor.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.invoke()
        finally:
            os.close(fd)
        self.assertFalse(self.calls.exists())

    def test_private_modes_and_no_journal_or_token_file(self):
        self.invoke()
        self.assertEqual(self.folder.stat().st_mode & 0o777, 0o700)
        for path in self.folder.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.home / "config").exists())
        self.assertFalse((self.home / "state" / "telegram").exists())
        self.assertNotIn("PRIVATE", (self.folder / "last-stop.json").read_text())

    def test_symlink_directory_or_files_refused(self):
        self.folder.mkdir(parents=True, mode=0o700)
        target = self.root / "target"
        target.write_text("untouched")
        (self.folder / "supervisor.lock").symlink_to(target)
        proc = subprocess.run([sys.executable, str(self.runner), "run", "--home", str(self.home),
                               "--transport", str(self.transport)], capture_output=True, timeout=5)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(target.read_text(), "untouched")
        self.assertFalse(self.calls.exists())

    def test_missing_home_cannot_provision_another_home(self):
        missing = self.root / "missing-home"
        result = subprocess.run([sys.executable, str(self.runner), "run", "--home", str(missing),
                                 "--transport", str(self.transport)], capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(missing.exists())
        self.assertFalse(self.calls.exists())

    def test_child_requires_injection_no_file_fallback(self):
        env = os.environ.copy()
        env.pop("FM_TELEGRAM_BOT_TOKEN", None)
        result = subprocess.run([sys.executable, str(self.runner), "_child", "--home", str(self.home),
                                 "--transport", str(self.transport)], capture_output=True, timeout=5, env=env)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, receiver.RESULT + b"transport_requires_review\n")
        self.assertFalse((self.home / "state").exists())

    def test_symlink_service_directory_refused(self):
        self.folder.parent.mkdir(mode=0o700)
        target = self.root / "other-directory"
        target.mkdir(mode=0o700)
        self.folder.symlink_to(target, target_is_directory=True)
        result = subprocess.run([sys.executable, str(self.runner), "run", "--home", str(self.home),
                                 "--transport", str(self.transport)], capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(target.iterdir()), [])
        self.assertFalse(self.calls.exists())

    def test_render_plist_only(self):
        result = subprocess.run([sys.executable, str(self.runner), "render", "--home", str(self.home),
                                 "--transport", str(self.transport), "--av", str(self.av)],
                                capture_output=True, check=True, timeout=5)
        doc = plistlib.loads(result.stdout)
        self.assertEqual(doc["Label"], "local.telegram-receiver")
        self.assertTrue(doc["RunAtLoad"])
        self.assertEqual(doc["StartInterval"], 60)
        self.assertNotIn("KeepAlive", doc)
        self.assertEqual(doc["StandardErrorPath"], "/dev/null")
        self.assertNotIn("TOKEN", result.stdout.decode())
        self.assertFalse((self.home / "state").exists())
        self.assertFalse(self.calls.exists())

    def test_timeout_and_unexpected_output_are_not_retryable(self):
        for code, reason in (("import time; time.sleep(10)", "vault_start_timeout"),
                             ("print('X'*5000)", "unexpected_output")):
            proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, start_new_session=True)
            try:
                found, _ = receiver.collect(proc, startup_timeout=0.2)
                self.assertEqual(found, reason)
            finally:
                receiver.terminate(proc)
                proc.stdout.close()
                proc.stderr.close()

    def test_sigterm_preserves_latch_and_ends_owned_child(self):
        self.av.write_text(f"#!{sys.executable}\nimport time\nfrom pathlib import Path\n"
                           f"Path({str(self.calls)!r}).write_text('started')\ntime.sleep(10)\n")
        proc = subprocess.Popen([sys.executable, str(self.runner), "run", "--home", str(self.home),
                                 "--transport", str(self.transport), "--av", str(self.av)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            import time
            until = time.monotonic() + 3
            while not self.calls.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(self.calls.exists())
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=3)
            self.assertEqual(self.last()["reason"], "operator_stop_requires_review")
            self.assertTrue((self.folder / "halt.json").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=3)


@unittest.skipUnless(os.environ.get("TELEGRAM_TRANSPORT_SOURCE"), "optional preserved-source contract")
class PreservedTransportTests(unittest.TestCase):
    def setUp(self):
        import types
        self.tmp = tempfile.TemporaryDirectory(prefix="telegram-contract-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        path = Path(os.environ["TELEGRAM_TRANSPORT_SOURCE"])
        source = receiver.checked_source(path)  # read only, never execute the CLI
        self.module = types.ModuleType("preserved_test_transport")
        self.module.__file__ = str(path)
        exec(compile(source, str(path), "exec"), self.module.__dict__)
        self.module.LOCK_ROOT = self.root / "locks"
        clock = __import__("time")
        self.module.time = types.SimpleNamespace(sleep=lambda _: None, time=clock.time, monotonic=clock.monotonic)
        self.cfg = dict(enabled=True, home=str(self.root), bot_id=123, user_id=456, chat_id=456, since=1)
        cfg = self.cfg
        root = self.root

        class Home:
            state = root / "state"
            def __init__(self, _): pass
            def approval(self): return cfg
            def token(self): return "123:" + "x" * 30
            def inbox(self, *_, **__): return {"replies": [], "reply_cursor": ""}

        self.home = Home(None)
        self.module.Home = Home
        self.old_argv = sys.argv[:]
        self.addCleanup(lambda: setattr(sys, "argv", self.old_argv))
        sys.argv = [str(path), "--home", str(self.root), "run"]
        self.api_calls = []

    def api(self, failure):
        module, cfg, calls = self.module, self.cfg, self.api_calls

        class API:
            def __init__(self, _): pass
            def call(self, method, **_):
                calls.append(method)
                if failure in (0, 401, 403, 409):
                    raise module.APIError(failure)
                if method == "getMe": return {"id": cfg["bot_id"], "is_bot": True}
                if method == "getWebhookInfo": return {"url": "present" if failure == "webhook" else ""}
                if method == "getChat": return {"id": cfg["chat_id"], "type": "private"}
                if method == "getUpdates": return []
                if method == "sendMessage": raise module.APIError(0)
                raise AssertionError("unexpected mock method")
        return API

    def outcome(self, failure):
        self.module.API = self.api(failure)
        with self.assertRaises(self.module.BridgeError) as error:
            self.module.main()
        return receiver.reason_for(self.module, error.exception)

    def test_actual_transient_budget_and_hard_auth_conflicts(self):
        self.assertEqual(self.outcome(0), "network_unavailable")
        self.assertEqual(len(self.api_calls), 3)
        for code in (401, 403, 409):
            self.api_calls.clear()
            self.assertEqual(self.outcome(code), "authentication_or_ownership")
            self.assertEqual(len(self.api_calls), 1)

    def test_actual_webhook_and_local_lock_stop(self):
        self.assertEqual(self.outcome("webhook"), "webhook")
        with self.module.consumer_lock(self.cfg["bot_id"]):
            self.assertEqual(self.outcome(0), "local_consumer")

    def test_actual_uncertain_send_never_becomes_transient(self):
        bridge = self.module.Transport(self.home, self.cfg, None)
        with bridge.db:
            bridge.db.execute("INSERT INTO inbound VALUES (1,'note','request',1)")
            bridge.db.execute("INSERT INTO delivery (note,kind,body) VALUES ('note','reply','synthetic')")
        bridge.db.close()
        self.assertEqual(self.outcome("send-uncertain"), "delivery_requires_review")
        self.assertEqual(self.api_calls.count("sendMessage"), 1)
        # A subsequent manual attempt still cannot resend it automatically.
        self.assertEqual(self.outcome("send-uncertain"), "delivery_requires_review")
        self.assertEqual(self.api_calls.count("sendMessage"), 1)

    def test_actual_interrupted_send_requires_review_without_resend(self):
        bridge = self.module.Transport(self.home, self.cfg, None)
        with bridge.db:
            bridge.db.execute("INSERT INTO inbound VALUES (1,'note','request',1)")
            bridge.db.execute("INSERT INTO delivery (note,kind,body,state) VALUES ('note','reply','synthetic','sending')")
        bridge.db.close()
        self.assertEqual(self.outcome("send-uncertain"), "delivery_requires_review")
        self.assertNotIn("sendMessage", self.api_calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
