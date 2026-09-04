# Vox Relay

Vox Relay is a small Mac menu-bar app that watches the text threads you choose and writes each new message, as it arrives, to a plain text file on your Mac. One line per message, with the time, who sent it, and what it said. Turn on Cloud push and those same lines also flow into your Vox Ordo workspace, so your agent knows what was said without you copying anything. You pick the threads. Nothing else is touched.


## What it does not do

- It does not send anything anywhere unless you turn Cloud push on and paste your token. Off is the default.
- It never sends attachment files. Photos, videos, and documents stay on your Mac. Only the file name is recorded.
- It is read-only. It never sends messages, never marks anything read, never changes Messages in any way.
- It does not relay threads you have not ticked, and it does not go back through history. Relaying starts the moment you tick a thread.

## Install

### Run it from source (about 60 seconds)

Requires macOS 13 or newer and Python 3.

```
git clone https://github.com/iansteitz1-eng/vox-relay.git
cd vox-relay
python3 -m venv .venv && source .venv/bin/activate
pip install rumps
python3 -m voxrelay
```

The ⇄ item appears in the menu bar. There is no Dock icon. The terminal you run it from needs Full Disk Access (System Settings → Privacy & Security → Full Disk Access) or the Threads list is empty and the app tells you so.

Then: click ⇄ → **Threads…** and tick the threads you want relayed. Relayed messages appear in `~/Library/Application Support/VoxRelay/relay.jsonl` (⇄ → **Open relay.jsonl**).

Tests: `pip install pytest && python3 -m pytest tests -q` (19 tests; they build a synthetic Messages database and prove the read-only, tick-only, and push-off claims above).

### Get the signed build

The notarized, Developer ID-signed `Vox Relay.app` is at [voxordo.io/voxrelay](https://voxordo.io/voxrelay?utm_source=github&utm_medium=readme&utm_campaign=voxrelay). It opens like any Mac app, no right-click needed, and comes with a purchase token for Cloud push.

Install: unzip, drag `Vox Relay.app` into Applications, open it, click ⇄ → **Grant Full Disk Access…**, switch Vox Relay on in the list that opens, then quit and reopen Vox Relay.

## Menu

- **Relay: ON / OFF** — pause and resume without losing your thread choices.
- **Threads…** — your 40 most recent chats; ticked = relayed. Choices are remembered.
- **Cloud push: ON / OFF** — off by default; the first time you turn it on you are asked for your token.
- **Open relay.jsonl** — opens the local record.
- **Grant Full Disk Access…** — opens the right System Settings pane.
- **Quit**.

## Record format

One JSON line per message:

```
{"ts": "...", "chat_id": 12, "handle": "+1...", "is_from_me": false, "text": "...", "attachments": ["IMG_0001.jpeg"], "rowid": 4567}
```

## Verify your download

Every release publishes `SHA256SUMS`, `RELEASE.sha256`, `RELEASE.sha256.sig`, and `allowed_signers` on the [Releases](https://github.com/iansteitz1-eng/vox-relay/releases) page. From the folder holding your downloaded zip:

```
shasum -a 256 -c SHA256SUMS
ssh-keygen -Y verify -f allowed_signers -I release@voxordo.io -n voxrelay-app -s RELEASE.sha256.sig < RELEASE.sha256
```

## Updates

Use Watch → Custom → Releases on this repository to be told when a new signed build ships. Email updates arrive with the next release.

## Support

hello@voxordo.ai. Attach `~/Library/Logs/VoxRelay.log` if something relayed that should not have, or did not that should.

## License

Apache-2.0. "Vox Relay", "voxordo", and "InsyncTech" are trademarks of InsyncTech; see `TRADEMARKS.md`. Vox Relay is made by InsyncTech, the company behind voxordo.
