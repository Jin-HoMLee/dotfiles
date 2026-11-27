#!/usr/bin/env python3
"""Opt-in launchd companion for the preserved Firstmate Telegram transport.

render prints a plist; it does not install or start anything. run is launchd's
entry point. _child is private to run and receives the named Vault injection.
No transport source, approval, token file or journal is written by this file.
"""

import argparse
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import resource
import selectors
import signal
import stat
import subprocess
import sys
import time
import types

LABEL = "local.telegram-receiver"
# This boundary depends on this implementation's exact error semantics.
TRANSPORT_SHA256 = "d3275d5668636df51c04ac82f9a93ffe4b768669cc2590f4e3c892f2bdd50a9d"
TRANSIENT = "Telegram unavailable after three polls; stopped; saved requests remain queued"
VAULT_NOT_READY = b"av inject: Automic Vault approval service is not running; open the menu bar app\n"
STARTED = b"DOTFILES_TELEGRAM_STARTED\n"
RESULT = b"DOTFILES_TELEGRAM_RESULT:"
REASONS = frozenset(("network_unavailable", "authentication_or_ownership", "local_consumer",
                     "webhook", "delivery_requires_review", "approval_changed",
                     "transport_requires_review", "transport_changed", "unexpected_stop"))


class StopRequested(Exception):
    pass


def checked_source(path):
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != TRANSPORT_SHA256:
        raise ValueError("transport_changed")
    return source


def reason_for(module, error):
    # The generic CLI exit code 1 is NOT permission to retry. Only the exact
    # transient BridgeError from the reviewed implementation is retryable.
    if type(error) is not module.BridgeError:
        return "transport_requires_review"
    text = str(error)
    if text == TRANSIENT:
        return "network_unavailable"
    if text == "Bot authentication/ownership conflict; stopped; no webhook or consumer reset":
        return "authentication_or_ownership"
    if text == "Another local consumer owns this Telegram bot; stop it first":
        return "local_consumer"
    if text == "Bot has an existing webhook; nothing changed; use a dedicated unused bot":
        return "webhook"
    if text.startswith("Delivery ") or "delivery uncertain" in text:
        return "delivery_requires_review"
    if text.startswith("Telegram approval changed"):
        return "approval_changed"
    return "transport_requires_review"


def child(home, transport):
    reason = "transport_requires_review"
    try:
        source = checked_source(transport)
        # Require injection. Never allow Home.token() to fall back to a file.
        if not os.environ.get("FM_TELEGRAM_BOT_TOKEN", "").strip():
            raise ValueError("missing injection")
        module = types.ModuleType("dotfiles_preserved_telegram")
        module.__file__ = str(transport)
        exec(compile(source, str(transport), "exec"), module.__dict__)
        sys.argv = [str(transport), "--home", str(home), "run"]
        print(STARTED.decode().strip(), flush=True)
        # Neither unexpected library output nor exceptions cross this boundary.
        with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                module.main()
                reason = "unexpected_stop"
            except Exception as error:
                reason = reason_for(module, error)
    except ValueError as error:
        if str(error) == "transport_changed":
            reason = "transport_changed"
    except BaseException:
        reason = "unexpected_stop"
    print((RESULT + reason.encode()).decode(), file=sys.stderr, flush=True)
    return 0


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("unsafe service directory")


def private_open(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        os.close(fd)
        raise ValueError("unsafe service file")
    return fd


def record(path, reason, **fields):
    # Fixed vocabulary only; never write raw child output, paths or exceptions.
    doc = {"time_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "reason": reason, **fields}
    fd = private_open(path)
    with os.fdopen(fd, "w") as stream:
        stream.write(json.dumps(doc, sort_keys=True) + "\n")
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def terminate(proc):
    # Only this invocation's process group, including av and its child.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)


def collect(proc, startup_timeout=60):
    """Bound raw output in memory; consent must finish within 60 seconds."""
    output = {"out": bytearray(), "err": bytearray()}
    started, deadline = False, time.monotonic() + startup_timeout
    with selectors.DefaultSelector() as selector:
        selector.register(proc.stdout, selectors.EVENT_READ, "out")
        selector.register(proc.stderr, selectors.EVENT_READ, "err")
        while selector.get_map():
            if not started and time.monotonic() >= deadline:
                terminate(proc)
                return "vault_start_timeout", proc.returncode
            for key, _ in selector.select(timeout=0.5):
                data = os.read(key.fd, 4096)
                if not data:
                    selector.unregister(key.fileobj)
                    continue
                buf = output[key.data]
                if len(buf) + len(data) > 4096:
                    terminate(proc)
                    return "unexpected_output", proc.returncode
                buf.extend(data)
            started = output["out"] == STARTED
    code = proc.wait(timeout=5)
    out, err = bytes(output["out"]), bytes(output["err"])
    if not out and err == VAULT_NOT_READY and code != 0:
        return "vault_not_ready", code
    for reason in REASONS:
        if out == STARTED and err == RESULT + reason.encode() + b"\n" and code == 0:
            return reason, code
    return "unexpected_stop", code


def run(home, transport, av):
    # A typo must not provision a different home or a fresh transport journal.
    home = home.resolve(strict=True)
    if not home.is_dir():
        raise ValueError("existing home required")
    folder = home / "state" / "telegram-receiver"
    private_directory(folder)
    lock = private_open(folder / "supervisor.lock")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        halt = folder / "halt.json"
        # A crash or stop during an attempt leaves this durable marker. Login
        # and scheduled starts never silently clear a hard stop.
        if halt.exists() or halt.is_symlink():
            return 0
        record(halt, "interrupted_attempt_requires_review")
        proc = None
        reason, code = "startup_requires_review", None
        try:
            checked_source(transport)
            env = os.environ.copy()
            env.pop("FM_TELEGRAM_BOT_TOKEN", None)
            proc = subprocess.Popen(
                [str(av), "inject", "+FM_TELEGRAM_BOT_TOKEN", "--", sys.executable,
                 str(Path(__file__).resolve()), "_child", "--home", str(home),
                 "--transport", str(transport)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=env, start_new_session=True)
            reason, code = collect(proc)
        except StopRequested:
            reason = "operator_stop_requires_review"
        except ValueError as error:
            if str(error) == "transport_changed":
                reason = "transport_changed"
        except Exception:
            reason = "startup_requires_review"
        finally:
            if proc is not None:
                terminate(proc)
                proc.stdout.close()
                proc.stderr.close()
        retry = reason in ("vault_not_ready", "network_unavailable")
        record(folder / "last-stop.json", reason, child_exit=code, automatic_retry=retry)
        if retry:
            halt.unlink()
        else:
            record(halt, reason, child_exit=code, automatic_retry=False)
        return 0
    finally:
        os.close(lock)


def render(home, transport, av):
    user_home = Path.home()
    return plistlib.dumps({
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(Path(__file__).resolve()), "run",
                             "--home", str(home), "--transport", str(transport), "--av", str(av)],
        "RunAtLoad": True,
        "StartInterval": 60,
        "ProcessType": "Background",
        "ExitTimeOut": 10,
        "WorkingDirectory": str(home),
        "Umask": 0o077,
        "EnvironmentVariables": {"PATH": f"{user_home}/.nix-profile/bin:/run/current-system/sw/bin:"
                                 "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
        # Raw Vault/transport output is never a launchd log.
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    })


def absolute(value):
    path = Path(value)
    if not path.is_absolute():
        raise argparse.ArgumentTypeError("absolute path required")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("render", "run", "_child"))
    parser.add_argument("--home", type=absolute, required=True)
    parser.add_argument("--transport", type=absolute, required=True)
    parser.add_argument("--av", type=absolute, default=Path("/usr/local/bin/av"))
    args = parser.parse_args()
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args.command == "render":
        sys.stdout.buffer.write(render(args.home, args.transport, args.av))
        return 0
    if args.command == "_child":
        return child(args.home, args.transport)
    def stop(*_):
        raise StopRequested()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    return run(args.home, args.transport, args.av)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Private setup errors may contain paths. Never emit traceback/details.
        print("telegram-receiver: private service setup failed; check directory permissions", file=sys.stderr)
        sys.exit(1)
