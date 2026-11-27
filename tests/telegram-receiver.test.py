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


# Appended ONLY to a temporary copy of the byte-verified preserved source.
# No transport method is replaced: Home/API/lock location are the I/O seams.
PRESERVED_IO_FIXTURE = '''
_fixture_root = Path(os.environ["MOCK_ROOT"])
LOCK_ROOT = _fixture_root / "bot-locks"
_fixture_sleep = time.sleep
_fixture_time = time
class _FixtureClock:
    time = staticmethod(_fixture_time.time)
    monotonic = staticmethod(_fixture_time.monotonic)
    sleep = staticmethod(lambda _: None)
time = _FixtureClock()
class _FixtureHome(Home):
    def approval(self):
        return dict(enabled=True, home=str(self.path), bot_id=123, user_id=456, chat_id=456, since=1)
    def token(self):
        return "123:" + "x" * 30
    def inbox(self, *args, text=None):
        if args[0] == "receipts":
            return {"replies": [], "reply_cursor": ""}
        assert args[0] == "note"
        request = args[args.index("--request-id") + 1]
        with (_fixture_root / "notes").open("a") as stream:
            stream.write("synthetic-note\\n")
        return {"id": "fixture-note", "request_id": request, "saved": True, "announced": True}
Home = _FixtureHome
class _FixtureAPI:
    def __init__(self, _): self.polls = 0
    def call(self, method, **payload):
        with (_fixture_root / "api-calls").open("a") as stream:
            stream.write(method + "\\n")
        case = os.environ["MOCK_CASE"]
        if method == "getMe": return {"id": 123, "is_bot": True}
        if method == "getWebhookInfo": return {"url": ""}
        if method == "getChat": return {"id": 456, "type": "private"}
        if method == "getUpdates":
            if case == "idle_crash": os.kill(os.getpid(), 9)
            if case == "idle_wait":
                (_fixture_root / "child-pid").write_text(str(os.getpid()))
                _fixture_sleep(10)
            self.polls += 1
            if case == "ready_send" and self.polls > 1: raise APIError(0)
            if case == "receive":
                if self.polls > 1: raise APIError(0)
                if payload.get("offset", 0) > 2: return []
                return [{"update_id": 2, "message": {
                    "from": {"id": 456, "is_bot": False},
                    "chat": {"id": 456, "type": "private"},
                    "message_id": 2, "date": int(time.time()), "text": "synthetic lead"}}]
            return []
        if method == "sendMessage":
            if case == "send_crash": os.kill(os.getpid(), 9)
            if case == "ready_send":
                return {"chat": {"id": 456, "type": "private"},
                        "from": {"id": 123, "is_bot": True}, "message_id": 77}
            raise AssertionError("unexpected send after recovery")
        raise AssertionError("unexpected mock method")
API = _FixtureAPI
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
               "MOCK_ROOT": str(self.root), "MOCK_CALLS": str(self.calls),
               "FM_TELEGRAM_BOT_TOKEN": "MUST_BE_REMOVED"}
        result = subprocess.run([sys.executable, str(self.runner), "run", "--home", str(self.home),
                                 "--transport", str(self.transport), "--av", str(self.av)],
                                env=env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, b"")

    def last(self):
        return json.loads((self.folder / "last-stop.json").read_text())

    def prepare_preserved_source(self):
        # Retain the original source bytes as the prefix; append only fake I/O.
        source = receiver.checked_source(Path(os.environ["TELEGRAM_TRANSPORT_SOURCE"]))
        fixture = source + PRESERVED_IO_FIXTURE.encode()
        self.transport.write_bytes(fixture)
        self.runner.write_text(SOURCE.read_text().replace(receiver.TRANSPORT_SHA256,
                               hashlib.sha256(fixture).hexdigest()))

    @unittest.skipUnless(os.environ.get("TELEGRAM_TRANSPORT_SOURCE"), "optional preserved-source restart")
    def test_source_idle_crash_restarts_and_captures_without_manual_rearm(self):
        import sqlite3
        self.prepare_preserved_source()
        self.invoke("idle_crash")
        self.assertEqual(self.last()["reason"], "process_interrupted")
        self.assertTrue(self.last()["automatic_retry"])
        self.assertFalse((self.folder / "halt.json").exists())
        self.invoke("receive")
        self.assertEqual(self.last()["reason"], "network_unavailable")
        self.invoke("receive")
        with sqlite3.connect(self.home / "state" / "telegram" / "transport.sqlite3") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM inbound").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()[0], "3")
        self.assertEqual((self.root / "notes").read_text().splitlines(), ["synthetic-note"])
        self.assertFalse((self.folder / "halt.json").exists())

    @unittest.skipUnless(os.environ.get("TELEGRAM_TRANSPORT_SOURCE"), "optional preserved-source restart")
    def test_source_send_crash_recovery_halts_without_second_send(self):
        import sqlite3
        self.prepare_preserved_source()
        self.invoke("idle_crash")  # initialize only a synthetic journal, then die before sending
        journal = self.home / "state" / "telegram" / "transport.sqlite3"
        with sqlite3.connect(journal) as db:
            db.execute("INSERT INTO inbound VALUES (1,'original-note','original-request',1)")
            db.execute("INSERT INTO delivery (note,kind,body) VALUES ('original-note','reply','synthetic')")
        self.invoke("send_crash")
        self.assertEqual(self.last()["reason"], "process_interrupted")
        self.assertTrue(self.last()["automatic_retry"])
        with sqlite3.connect(journal) as db:
            self.assertEqual(db.execute("SELECT state FROM delivery").fetchone()[0], "sending")
        self.invoke("recover_send")
        self.assertEqual(self.last()["reason"], "delivery_requires_review")
        self.assertFalse(self.last()["automatic_retry"])
        with sqlite3.connect(journal) as db:
            self.assertEqual(db.execute("SELECT state FROM delivery").fetchone()[0], "uncertain")
        self.assertEqual((self.root / "api-calls").read_text().splitlines().count("sendMessage"), 1)
        before = self.calls.read_text()
        self.invoke("receive")
        self.assertEqual(self.calls.read_text(), before)
        self.assertTrue((self.folder / "halt.json").exists())

    @unittest.skipUnless(os.environ.get("TELEGRAM_TRANSPORT_SOURCE"), "optional preserved-source restart")
    def test_source_ready_reply_resumes_and_confirmed_sent_never_resends(self):
        import sqlite3
        self.prepare_preserved_source()
        self.invoke("idle_crash")
        journal = self.home / "state" / "telegram" / "transport.sqlite3"
        with sqlite3.connect(journal) as db:
            db.execute("INSERT INTO inbound VALUES (1,'original-note','original-request',1)")
            db.execute("INSERT INTO delivery (note,kind,body) VALUES ('original-note','reply','synthetic')")
        self.invoke("ready_send")
        self.assertTrue(self.last()["automatic_retry"])
        self.assertFalse((self.folder / "halt.json").exists())
        with sqlite3.connect(journal) as db:
            self.assertEqual(db.execute("SELECT state,body FROM delivery").fetchone(), ("sent", ""))
        self.invoke("ready_send")
        self.assertEqual((self.root / "api-calls").read_text().splitlines().count("sendMessage"), 1)
        self.assertFalse((self.folder / "halt.json").exists())

    @unittest.skipUnless(os.environ.get("TELEGRAM_TRANSPORT_SOURCE"), "optional preserved-source restart")
    def test_source_supervisor_crash_then_fresh_login_resumes_safe_journal(self):
        import time
        self.prepare_preserved_source()
        env = {**os.environ, "MOCK_CASE": "idle_wait", "MOCK_ROOT": str(self.root),
               "MOCK_CALLS": str(self.calls)}
        proc = subprocess.Popen([sys.executable, str(self.runner), "run", "--home", str(self.home),
                                 "--transport", str(self.transport), "--av", str(self.av)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        child_pid = None
        try:
            until = time.monotonic() + 3
            child_record = self.root / "child-pid"
            while not child_record.exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertTrue(child_record.exists())
            child_pid = int(child_record.read_text())
            proc.kill()
            # Simulate launchd's owned-process-group cleanup, not a live service action.
            os.killpg(child_pid, signal.SIGKILL)
            proc.communicate(timeout=3)
            self.assertTrue((self.folder / "attempt.json").exists())
            self.assertFalse((self.folder / "halt.json").exists())
            self.invoke("receive")
            self.assertEqual((self.root / "notes").read_text().splitlines(), ["synthetic-note"])
            self.assertFalse((self.folder / "attempt.json").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.communicate(timeout=3)

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
                             ("unknown", "transport_requires_review"), ("normal", "unexpected_stop")):
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

    def test_existing_hard_stop_never_automatically_rearms(self):
        self.folder.mkdir(parents=True, mode=0o700)
        (self.folder / "halt.json").write_text("incomplete")
        self.invoke()
        self.assertFalse(self.calls.exists())

    def test_abrupt_child_death_restarts_without_manual_rearm(self):
        self.invoke("crash")
        self.assertEqual(self.last()["reason"], "process_interrupted")
        self.assertTrue(self.last()["automatic_retry"])
        self.assertFalse((self.folder / "halt.json").exists())
        self.invoke()
        self.assertEqual(self.last()["reason"], "network_unavailable")
        self.assertEqual(len(self.calls.read_text().splitlines()), 2)

    def test_stale_attempt_after_supervisor_death_does_not_block_login(self):
        self.folder.mkdir(parents=True, mode=0o700)
        (self.folder / "attempt.json").write_text("incomplete attempt")
        self.invoke()
        self.assertEqual(self.last()["reason"], "network_unavailable")
        self.assertFalse((self.folder / "halt.json").exists())
        self.assertFalse((self.folder / "attempt.json").exists())

    def test_child_persists_hard_stop_without_parent_reporting(self):
        self.folder.mkdir(parents=True, mode=0o700)
        env = {**os.environ, "FM_TELEGRAM_BOT_TOKEN": "SYNTHETIC", "MOCK_CASE": "auth"}
        result = subprocess.run([sys.executable, str(self.runner), "_child", "--home", str(self.home),
                                 "--transport", str(self.transport)], capture_output=True, timeout=5, env=env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.folder / "halt.json").read_text())["reason"],
                         "authentication_or_ownership")
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

    def test_logout_sigterm_ends_owned_child_and_allows_next_login(self):
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
            self.assertEqual(self.last()["reason"], "process_interrupted")
            self.assertTrue(self.last()["automatic_retry"])
            self.assertFalse((self.folder / "halt.json").exists())
            self.av.write_text(AV_FIXTURE.replace("PYTHON", sys.executable))
            self.invoke()
            self.assertEqual(self.last()["reason"], "network_unavailable")
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
