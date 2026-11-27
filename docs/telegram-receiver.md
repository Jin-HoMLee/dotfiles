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

Automatic restart does not require manual rearming after an ordinary process
interruption or logout/login:

- Vault's exact approval-service-not-running result allows the next attempt
  after the menu-bar app opens; this does not auto-open the app.
- The preserved transport's exhausted transient poll failures allow another
  attempt. It already bounds those failures and send-rate-limit retries.
- An abrupt child death without an application error result, a terminated
  supervisor, or a stale in-progress attempt after a host restart allows the
  next scheduled/login start. The existing journal, not a guessed success,
  determines whether the resumed transport can send.

**Why interrupted sends stay safe:** the preserved transport commits a delivery
as `sending` before making its API call. Its next startup, under the existing
bot lock, changes interrupted `sending` records to `uncertain`. Its existing
sender refuses an `uncertain` or `rejected` record before making another send.
The companion does not clear that state, mark it sent, reset offsets or call
resolve. Successful capture/collection can happen before the sender reaches the
uncertain record; restart does not claim that any prior reply arrived. If the
network is still down, transient retries can precede discovery of that record,
but they cannot resend it. A previously committed `sent` result stays sent;
existing correlations and offsets resume in the same home.

Application-declared safety stops remain persistent across crashes and logins:
authentication, 409 ownership conflicts, webhook presence, local consumer
conflicts, approval changes, uncertain/rejected sending, unknown application
errors, source drift, missing executables, unexpected output/normal return, or
consent taking more than 60 seconds. The child durably records a known safety
stop before returning its classification, so losing the parent after that
point does not discard the decision. A Vault denial is **not** transient; no
repeated approval requests follow a recorded denial/timeout. The transport
classifies API errors other than 401/403/409 as transient during polling,
including malformed/unavailable responses; this companion does not infer more
precise network causes from that result.

`<FM_HOME>/state/telegram-receiver/attempt.json` is an in-progress diagnostic,
not a restart veto. `halt.json` is created only for an actual safety stop and
requires explicit reconciliation. `last-stop.json` contains the latest stop
observation, fixed reason, numeric child exit when available and retry choice.
A stale attempt records that the previous process was interrupted, not that it
succeeded. Files are private 0600 in a 0700 service directory. They contain no
tokens, API URLs, message bodies, forwarded content, raw stderr or remote
descriptions. Raw subprocess output is bounded in memory and discarded. Core
dumps are disabled for the companion and inherited by its children. There is no
growing log. The transport's regular local `status` command remains the source
of capture/delivery counts; the companion does not read or alter that journal.
Do not run this companion under another restart supervisor.

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
and with the Vault approval service available. First merge/copy the reviewed
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

Bootout removes scheduling; stopping a running process by itself does not
create a hard-stop marker. A later bootstrap/login resumes safely through the
existing journal recovery without manual rearm. Wait for the exact job to exit
before running any alternative receiver. A real uncertain send is still a hard
stop requiring phone verification, not permission to replay it.

If `halt.json` exists, inspect it, `last-stop.json`, local transport status and
any relevant phone delivery. Resolve the concrete safety cause first. Only for
that resolved hard stop, while the job and foreground receiver are both
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
private fake state only. It tests automatic crash/login recovery, persistent
safety stops, unknown output redaction, source changes, duplicates, permissions,
bounded startup, process termination and plist rendering. It never invokes
real `av`, launchctl or Telegram. An optional
`TELEGRAM_TRANSPORT_SOURCE=/absolute/path/to/fm-telegram.py` adds source-contract
tests and full companion subprocess restart tests. These retain the verified
preserved implementation with only mocked API/inbox/clock and temporary home
I/O: idle crash then new capture, supervisor death then fresh startup, a ready
reply sent once and not resent on another start, and a real send-path process
kill followed by startup recovery to `uncertain` with exactly one send attempt
and a persistent hard stop. No real secrets or network.
No CI was added and no heavy build or tool upgrade is required. The existing
PR-close workflow is the only existing GitHub workflow; its owner exemption is
unchanged, and any hosted runner use is ephemeral, not a local upgrade.
