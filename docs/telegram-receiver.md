# Optional Mac Telegram receiver

`home/telegram-receiver.py` adds an opt-in **user LaunchAgent**, independent of an
assistant terminal. It uses the existing approved bot, home, inbox and journal.
It does not create a bot, enroll a chat, change Vault policy, migrate data or
install a root daemon. Nothing is activated by checking out this change, and no
Nix switch, build, flake update, package install or version change is needed.

## Recovery and safety

The agent starts at GUI login and checks every 60 seconds while logged in.
launchd does not overlap a running job. A companion lock excludes duplicate
companion invocations; the transport's existing user/bot lock still excludes
other local receivers, including the old foreground receiver. A remote poller
cannot be ruled out locally: establish exclusive bot ownership before starting.

Only two exact, reviewed outcomes allow another automatic attempt:

- Vault's approval service reports that its menu-bar app is not running. The
  next attempt can connect after the app opens; this does not auto-open the app.
- The preserved transport reports exhaustion of its three transient poll
  failures. It already bounds those failures and send-rate-limit retries.

All other outcomes stop until explicit local reconciliation: authentication,
409 ownership conflicts, webhook presence, local consumer conflicts, approval
changes, uncertain/rejected sending, unexpected exits, unknown errors, missing
executables, or consent taking more than 60 seconds. A Vault denial is **not** a
transient condition. No repeated approval requests follow a denial/timeout.
The transport classifies API errors other than 401/403/409 as transient during
polling, including malformed/unavailable responses; this companion does not
claim to infer more precise network causes from that result.

Before attempting Vault injection, the companion durably creates
`<FM_HOME>/state/telegram-receiver/halt.json`. Only a recognized safe retry clears
it. Process crashes, host restarts and login do not clear it. This deliberately
sacrifices unattended crash recovery when delivery outcome is unknown rather
than potentially resending a reply. An interrupted send remains governed by the
transport's journal recovery and explicit phone verification, never a replay or
offset reset. Do not run this companion under another restart supervisor.

`last-stop.json` contains the latest completed stop time, fixed reason, numeric
child exit and retry choice. `halt.json` contains the hard-stop reason (or the
pre-attempt marker after a crash). Both are private 0600 files in a 0700 service
directory. They contain no tokens, API URLs, message bodies, forwarded content,
raw stderr or remote descriptions. Raw subprocess output is bounded in memory
and discarded. Core dumps are disabled for the companion and inherited by its
children. There is no growing log. The transport's regular local `status`
command remains the source of capture/delivery counts; the companion does not
read or alter that journal.

### Temporary dependency, not source extraction

The existing Firstmate `bin/fm-telegram.py` and adjacent inbox scripts remain a
read-only runtime dependency at the supplied absolute installation path.
No transport source is vendored here. Because its CLI uses exit 1 for both
network and safety stops, `_child` loads the exact reviewed source and calls its
existing `main()` to distinguish the one transient exception. It runs no
replacement polling or sending loop. An SHA-256 guard stops before secret
injection if the transport source changes. A later Firstmate update therefore
requires a fresh review of error semantics and an intentional companion hash
update; do not blindly replace the hash to make it start. Paths cannot point at
a temporary task copy that will be removed.

## Requirements and availability limits

- This Mac must be awake, connected to the network and logged into the intended
  user's GUI session. Logout stops the user job; sleep/network loss interrupts
  availability. Nothing here prevents sleep or promises continuous online
  operation. Telegram retains unreceived updates for **no more than 24 hours**.
- The already installed `/usr/bin/python3`, `/usr/local/bin/av`, and retained
  Firstmate transport/inbox dependencies must remain available. The plist uses
  absolute paths, not interactive shell initialization. Its PATH includes the
  user Nix profile, current system profile, Homebrew and system tool paths.
- The existing home approval, same bot/chat, permissions and journal must be
  preserved. Firstmate may be offline while notes queue, but must run to answer.
- The Vault menu-bar approval service must be running and the **exact new
  launcher/script chain** must work with the existing named-key consent route.
  Launchd has no interactive terminal. GUI consent/policy for this chain has
  **not** been live verified; if it needs a new security approval or unsupported
  unattended access, stop for explicit approval rather than weakening policy,
  blessing a launcher automatically or persisting a token.
- The original receiver's earlier stop cause is unknown. A later Vault startup
  refusal is not evidence for why the earlier session ended.

## Reviewed activation procedure (not run by tests)

The supervisor performs these only after explicit persistent-service approval
and after the current Vault blocker is cleared. First merge/copy the reviewed
files to the stable Dotfiles location. Preserve unrelated local edits. Substitute
actual stable absolute paths; the sample `FM_HOME` is the existing home, never a
new home or a task directory. No token is passed in an argument.

```sh
FM_HOME=/absolute/existing/firstmate-home
TRANSPORT=/absolute/preserved/firstmate/bin/fm-telegram.py
RECEIVER="$HOME/.dotfiles/home/telegram-receiver.py"
PLIST="$HOME/Library/LaunchAgents/local.telegram-receiver.plist"
```

1. Verify the Vault app is open and the installed tools and approved identity
   are unchanged. Stop only the known foreground receiver, if one exists.
   Do not start a second poller, modify approval or reset any offsets.
2. Inspect the existing transport's local status with no secret or network:

   ```sh
   /usr/bin/python3 "$TRANSPORT" --home "$FM_HOME" status
   shasum -a 256 "$TRANSPORT"
   ```

   The hash must match `TRANSPORT_SHA256` in the reviewed companion. Reconcile
   any `sending`, `uncertain` or `rejected` delivery against the phone while all
   receivers are stopped, using the transport's documented resolve procedure.
   Never infer successful delivery from an exit code or a last-poll timestamp.
3. Render the plist, inspect it and lint it. `render` only prints XML and does
   not access Vault, the home, the network or launchctl. Do not overwrite an
   existing service definition: inspect it and stop for reconciliation first.

   ```sh
   umask 077
   mkdir -p "$HOME/Library/LaunchAgents"
   (set -C; /usr/bin/python3 "$RECEIVER" render --home "$FM_HOME" \
     --transport "$TRANSPORT" --av /usr/local/bin/av > "$PLIST")
   /usr/bin/plutil -lint "$PLIST"
   /usr/bin/plutil -p "$PLIST"
   ```

   Review the source path, interpreter, home, transport and PATH before the next
   command. Keep the stable script and transport installed at these paths.
4. Start **only this user agent**. This is the first secret/network/service
   action and requires the explicit activation approval:

   ```sh
   launchctl bootstrap "gui/$(id -u)" "$PLIST"
   launchctl print "gui/$(id -u)/local.telegram-receiver"
   ```

   Bootstrap starts it via `RunAtLoad`; do not also run the foreground command.
   Monitor the private companion stop records and local transport status.
   If consent cannot complete, inspect the fixed reason, stop the job, and
   resolve the specific Vault requirement locally with explicit authority.
   Do not use `kickstart -k` to force restarts through an unknown sending state.
5. MAIN owns the attended new phone message/forward and authored reply check.
   Confirm a new local captured note and actual phone delivery, rather than
   assuming launchd's running/exit status proves receipt. No such live check is
   claimed by this change.

## Stop, rearm and rollback

To stop automatic attempts, remove **only this job** from the user's domain:

```sh
launchctl bootout "gui/$(id -u)" "$PLIST"
```

If it is running, stopping leaves a hard-stop marker. Wait for the exact job to
exit; inspect `halt.json`, `last-stop.json`, local transport status and any
relevant phone delivery before restarting. If it crashed before recording a
stop, the pre-attempt marker means the outcome is unknown, not successful.
For a resolved hard stop only, while the job and foreground receiver are both
stopped, explicitly remove the companion marker, **not** transport state:

```sh
rm "$FM_HOME/state/telegram-receiver/halt.json"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
```

Do not clear it merely because the network/Vault is back: authentication,
ownership, source changes and uncertain deliveries need their own resolution.

For rollback, boot out the job first as above, then remove only its plist:

```sh
rm "$PLIST"
```

Keep the private stop evidence, approval, token route, inbox and transport
journal intact. The prior attended `av inject +FM_TELEGRAM_BOT_TOKEN --
/usr/bin/python3 "$TRANSPORT" --home "$FM_HOME" run` route remains available
only after ownership and delivery reconciliation and with the agent unloaded.
No rebuild, tool downgrade, journal deletion or offset reset is required.

## Offline verification

```sh
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 tests/telegram-receiver.test.py
```

The suite uses synthetic transport and Vault fixtures, temporary homes and
private fake state only. It tests retry/recovery, persistent safety stops,
unknown output redaction, source changes, duplicates, permissions, bounded
startup, process termination and plist rendering. It never invokes real `av`,
launchctl or Telegram. An optional `TELEGRAM_TRANSPORT_SOURCE=/absolute/path/to/
fm-telegram.py` adds source-contract tests against the preserved implementation
with mocked API/inbox and temporary journals, still no real secrets or network.
No CI was added and no heavy build or tool upgrade is required. The existing
PR-close workflow is the only existing GitHub workflow; its owner exemption is
unchanged, and any hosted runner use is ephemeral, not a local upgrade.
