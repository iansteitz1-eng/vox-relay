#!/usr/bin/env python3
"""Vox Relay — proof of concept (macOS). Read-only tail of ~/Library/Messages/chat.db.

Usage (on the Mac, after granting Terminal Full Disk Access):
  python3 vox_relay_poc.py list                      # chats, newest first
  python3 vox_relay_poc.py tail "+15550100123"       # tail one handle (or a chat display name / chat_identifier)
  python3 vox_relay_poc.py tail "+15550100123" --once   # dump the last 20 then exit
  python3 vox_relay_poc.py tail ... --push https://voxordo.ai/v1/relay/messages --token $VOX_TOKEN

Guarantees: the database is opened read-only + immutable (never takes a lock, never writes a -wal entry).
Output: JSONL to stdout and ~/Library/Application Support/VoxRelay/relay.jsonl.
"""
import argparse, datetime as dt, json, os, plistlib, sqlite3, sys, time, urllib.request

DB = os.path.expanduser("~/Library/Messages/chat.db")
OUT_DIR = os.path.expanduser("~/Library/Application Support/VoxRelay")
OUT = os.path.join(OUT_DIR, "relay.jsonl")
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds


def connect() -> sqlite3.Connection:
    if not os.path.exists(DB):
        sys.exit(f"chat.db not found at {DB} — is this a Mac with Messages signed in?")
    uri = f"file:{DB}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f"cannot open chat.db read-only ({e}). Grant Full Disk Access to your terminal: "
                 "System Settings → Privacy & Security → Full Disk Access.")
    # hard property: refuse anything that is not read-only
    if con.execute("PRAGMA query_only").fetchone() is None:
        pass
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    return con


def apple_ts(ns: int | None) -> str:
    if not ns:
        return ""
    secs = ns / 1e9 if ns > 1e12 else ns
    return dt.datetime.fromtimestamp(APPLE_EPOCH + secs, dt.timezone.utc).isoformat()


def decode_attributed_body(blob: bytes | None) -> str:
    """macOS 13+ stores rich text in message.attributedBody (typedstream). Pull the NSString payload."""
    if not blob:
        return ""
    try:
        i = blob.find(b"NSString")
        if i < 0:
            return ""
        j = blob.find(b"+", i)
        if j < 0:
            return ""
        j += 1
        length = blob[j]
        if length == 0x81:  # 2-byte length
            length = int.from_bytes(blob[j + 1:j + 3], "little"); j += 3
        else:
            j += 1
        return blob[j:j + length].decode("utf-8", "replace")
    except Exception:
        return ""


def list_chats(con, limit=40):
    rows = con.execute("""
        SELECT c.ROWID AS chat_id, c.chat_identifier, c.display_name,
               (SELECT MAX(m.date) FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID WHERE j.chat_id=c.ROWID) AS last_date,
               (SELECT COUNT(*) FROM chat_message_join j WHERE j.chat_id=c.ROWID) AS n
        FROM chat c ORDER BY last_date DESC LIMIT ?""", (limit,)).fetchall()
    for r in rows:
        print(f"{r['chat_id']:>6}  {apple_ts(r['last_date'])[:19]}  n={r['n']:<5} {r['display_name'] or ''}  {r['chat_identifier']}")


def resolve_chat(con, key: str):
    r = con.execute("SELECT ROWID FROM chat WHERE chat_identifier=? OR display_name=? OR ROWID=? LIMIT 1",
                    (key, key, key if key.isdigit() else -1)).fetchone()
    if not r:
        sys.exit(f"no chat matches {key!r} — run `list`")
    return r["ROWID"]


def fetch(con, chat_id: int, after_rowid: int, limit=200):
    return con.execute("""
        SELECT m.ROWID AS rowid, m.date, m.is_from_me, m.text, m.attributedBody, h.id AS handle,
               (SELECT GROUP_CONCAT(a.filename, '|') FROM message_attachment_join aj JOIN attachment a ON a.ROWID=aj.attachment_id WHERE aj.message_id=m.ROWID) AS attachments
        FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE j.chat_id=? AND m.ROWID>? ORDER BY m.ROWID ASC LIMIT ?""", (chat_id, after_rowid, limit)).fetchall()


def emit(rec: dict, push: str | None, token: str | None):
    line = json.dumps(rec, ensure_ascii=False)
    print(line, flush=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if push:
        req = urllib.request.Request(push, data=line.encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or ''}"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:  # network is best-effort; the local JSONL is the record
            print(f"# push failed: {e}", file=sys.stderr)


def tail(con, chat_id: int, once: bool, push: str | None, token: str | None):
    start = con.execute("SELECT COALESCE(MAX(m.ROWID),0) FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID WHERE j.chat_id=?",
                        (chat_id,)).fetchone()[0]
    cursor = max(0, start - 20) if once else start
    while True:
        for r in fetch(con, chat_id, cursor):
            cursor = r["rowid"]
            text = r["text"] or decode_attributed_body(r["attributedBody"])
            emit({"ts": apple_ts(r["date"]), "chat_id": chat_id, "handle": r["handle"], "is_from_me": bool(r["is_from_me"]),
                  "text": text, "attachments": [a for a in (r["attachments"] or "").split("|") if a], "rowid": r["rowid"]},
                 push, token)
        if once:
            return
        time.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    t = sub.add_parser("tail"); t.add_argument("chat"); t.add_argument("--once", action="store_true")
    t.add_argument("--push"); t.add_argument("--token", default=os.environ.get("VOX_TOKEN"))
    a = ap.parse_args()
    con = connect()
    if a.cmd == "list":
        list_chats(con)
    else:
        tail(con, resolve_chat(con, a.chat), a.once, a.push, a.token)


if __name__ == "__main__":
    main()
