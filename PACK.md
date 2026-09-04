# Vox Relay — product pack (v0.1.0, 2026-09-03)

> LAW: every customer-facing sentence about Vox Relay is LIFTED from this file, never composed.
> Every line here is true of the code in `voxrelay/` as tested by `tests/test_core.py` on 2026-09-03.
> If the code changes, this file changes first.

## What it is
Vox Relay is a small Mac menu-bar app that watches the text threads you choose and writes each new
message, as it arrives, to a plain text file on your Mac — one line per message, with the time,
who sent it, and what it said. Turn on Cloud push and those same lines also flow into your Vox Ordo
workspace, so your agent knows what was said without you copying anything. You pick the threads.
Nothing else is touched.

## What it does not do
- It does not send anything anywhere unless you turn Cloud push on and paste your token. Off is the default.
- It never sends attachment files — photos, videos, documents stay on your Mac. Only the file name is recorded.
- It is read-only. It never sends messages, never marks anything read, never changes Messages in any way.
- It does not relay threads you have not ticked, and it does not go back through history — relaying starts the moment you tick a thread.

## Requirements
- macOS 13 or newer.
- Full Disk Access for Vox Relay (System Settings → Privacy & Security → Full Disk Access). Without it the Threads list is empty and the app tells you so.
- For Cloud push: the Vox Relay token from your purchase email.

## Install (5 steps)
1. Download `VoxRelay-v0.1.0.zip` from your purchase link and double-click it to unzip.
2. Drag `Vox Relay.app` into your Applications folder and open it. It appears in the menu bar (⇄) — there is no Dock icon.
3. Click ⇄ → **Grant Full Disk Access…**, switch Vox Relay on in the list that opens, then quit and reopen Vox Relay.
4. Click ⇄ → **Threads…** and tick the threads you want relayed. Relayed messages appear in `~/Library/Application Support/VoxRelay/relay.jsonl` (⇄ → **Open relay.jsonl**).
5. Optional: click ⇄ → **Cloud push: OFF** and paste the token from your purchase email to send those messages to your Vox Ordo workspace.

## Menu
- **Relay: ON / OFF** — pause and resume without losing your thread choices.
- **Threads…** — your 40 most recent chats; ticked = relayed. Choices are remembered.
- **Cloud push: ON / OFF** — off by default; the first time you turn it on you are asked for your token.
- **Open relay.jsonl** — opens the local record.
- **Grant Full Disk Access…** — opens the right System Settings pane.
- **Quit**.

## Record format (one JSON line per message)
`{"ts": "...", "chat_id": 12, "handle": "+1...", "is_from_me": false, "text": "...", "attachments": ["IMG_0001.jpeg"], "rowid": 4567}`

## Price
$10 one-time. (Stamped by Ian 2026-09-03.)

## Support
hello@voxordo.ai — attach `~/Library/Logs/VoxRelay.log` if something relayed that should not have, or did not that should.

## Verify your download
```
shasum -a 256 -c SHA256SUMS
ssh-keygen -Y verify -f allowed_signers -I release@voxordo.io -n voxrelay-app -s RELEASE.sha256.sig < RELEASE.sha256
```
