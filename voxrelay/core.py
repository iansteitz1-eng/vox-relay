"""Vox Relay core — pure Python, no macOS dependency, unit-tested on Linux.

Read-only access to the Messages database (``~/Library/Messages/chat.db``), persisted
relay state, and the ``Relay`` loop object the menu-bar app drives every 5 seconds.

Hard properties (each asserted by tests/test_core.py):
  * the database is opened ``?mode=ro&immutable=1`` + ``PRAGMA query_only=1`` — any write
    statement raises; the relay never takes a lock and never writes a ``-wal`` entry;
  * the JSON record emitted per message is EXACTLY the proof-of-concept's record
    ``{ts, chat_id, handle, is_from_me, text, attachments[], rowid}`` — attachment
    *filenames* only, never attachment bytes;
  * cloud push is OFF unless the user turns it on; when off nothing leaves the Mac.

The connect / decode / list / fetch SQL and the timestamp + attributedBody decoders are the
proof-of-concept's (vox_relay_poc.py, run by Ian on his Mac 2026-09-03) carried over
verbatim.  One deliberate change: ``sys.exit`` became ``RelayError`` (a menu-bar app must
never exit on a bad poll), and ``Relay.poll_once`` opens a FRESH connection per poll —
``immutable=1`` tells SQLite the file never changes, so a connection held open across
polls can serve a stale page cache.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterable

DB = os.path.expanduser("~/Library/Messages/chat.db")
OUT_DIR = os.path.expanduser("~/Library/Application Support/VoxRelay")
OUT = os.path.join(OUT_DIR, "relay.jsonl")
STATE = os.path.join(OUT_DIR, "state.json")
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds
DEFAULT_PUSH_URL = "https://voxordo.ai/v1/relay/messages"
FULL_DISK_ACCESS_HINT = ("Grant Full Disk Access to Vox Relay: System Settings → Privacy & Security → "
                         "Full Disk Access.")


class RelayError(Exception):
    """A recoverable relay problem (shown once as a notification, logged, never a crash)."""


# ----------------------------------------------------------------------------- database

def connect(db_path: str = DB) -> sqlite3.Connection:
    """Open chat.db read-only + immutable. Raises RelayError (never exits)."""
    if not os.path.exists(db_path):
        raise RelayError(f"chat.db not found at {db_path} — is this a Mac with Messages signed in? "
                         + FULL_DISK_ACCESS_HINT)
    uri = f"file:{db_path}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as e:
        raise RelayError(f"cannot open chat.db read-only ({e}). " + FULL_DISK_ACCESS_HINT) from e
    # hard property: refuse anything that is not read-only
    con.execute("PRAGMA query_only = 1")
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM chat LIMIT 1")
    except sqlite3.OperationalError as e:
        con.close()
        raise RelayError(f"chat.db could not be read ({e}). " + FULL_DISK_ACCESS_HINT) from e
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


def list_chats(con: sqlite3.Connection, limit: int = 40) -> list[dict]:
    """Chats newest-first: [{chat_id, chat_identifier, display_name, last_date, last_ts, n}]."""
    rows = con.execute("""
        SELECT c.ROWID AS chat_id, c.chat_identifier, c.display_name,
               (SELECT MAX(m.date) FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID WHERE j.chat_id=c.ROWID) AS last_date,
               (SELECT COUNT(*) FROM chat_message_join j WHERE j.chat_id=c.ROWID) AS n
        FROM chat c ORDER BY last_date DESC LIMIT ?""", (limit,)).fetchall()
    return [{"chat_id": r["chat_id"], "chat_identifier": r["chat_identifier"],
             "display_name": r["display_name"] or "", "last_date": r["last_date"],
             "last_ts": apple_ts(r["last_date"]), "n": r["n"]} for r in rows]


def chat_label(chat: dict) -> str:
    """Menu label for a chat: display name if any, else the handle / chat identifier."""
    return chat["display_name"] or chat["chat_identifier"] or f"chat {chat['chat_id']}"


def resolve_chat(con: sqlite3.Connection, key: str) -> int | None:
    r = con.execute("SELECT ROWID FROM chat WHERE chat_identifier=? OR display_name=? OR ROWID=? LIMIT 1",
                    (key, key, key if key.isdigit() else -1)).fetchone()
    return None if not r else r["ROWID"]


def fetch(con: sqlite3.Connection, chat_id: int, after_rowid: int, limit: int = 200):
    return con.execute("""
        SELECT m.ROWID AS rowid, m.date, m.is_from_me, m.text, m.attributedBody, h.id AS handle,
               (SELECT GROUP_CONCAT(a.filename, '|') FROM message_attachment_join aj JOIN attachment a ON a.ROWID=aj.attachment_id WHERE aj.message_id=m.ROWID) AS attachments
        FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE j.chat_id=? AND m.ROWID>? ORDER BY m.ROWID ASC LIMIT ?""", (chat_id, after_rowid, limit)).fetchall()


def latest_rowid(con: sqlite3.Connection, chat_id: int) -> int:
    """The newest message ROWID in a chat (0 if empty) — where a freshly ticked thread starts."""
    return con.execute("SELECT COALESCE(MAX(m.ROWID),0) FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID WHERE j.chat_id=?",
                       (chat_id,)).fetchone()[0]


def record_from_row(chat_id: int, r: sqlite3.Row) -> dict:
    """The POC's JSON record, field for field. Attachments are filenames only — never bytes."""
    text = r["text"] or decode_attributed_body(r["attributedBody"])
    return {"ts": apple_ts(r["date"]), "chat_id": chat_id, "handle": r["handle"], "is_from_me": bool(r["is_from_me"]),
            "text": text, "attachments": [a for a in (r["attachments"] or "").split("|") if a], "rowid": r["rowid"]}


# ----------------------------------------------------------------------------- state

@dataclass
class RelayState:
    """Everything the app remembers between launches. Persisted as JSON."""
    chats: list[int] = field(default_factory=list)          # ticked chat ROWIDs (default: none)
    cursors: dict[str, int] = field(default_factory=dict)   # chat_id (str) -> last relayed message ROWID
    push_url: str = DEFAULT_PUSH_URL
    push_token: str = ""
    local_only: bool = True                                 # cloud push OFF until the user turns it on
    relay_on: bool = True

    def to_dict(self) -> dict:
        return {"chats": list(self.chats), "cursors": dict(self.cursors), "push_url": self.push_url,
                "push_token": self.push_token, "local_only": self.local_only, "relay_on": self.relay_on}

    @classmethod
    def from_dict(cls, d: dict) -> "RelayState":
        s = cls()
        s.chats = [int(c) for c in d.get("chats", [])]
        s.cursors = {str(k): int(v) for k, v in d.get("cursors", {}).items()}
        s.push_url = str(d.get("push_url", DEFAULT_PUSH_URL) or DEFAULT_PUSH_URL)
        s.push_token = str(d.get("push_token", "") or "")
        s.local_only = bool(d.get("local_only", True))
        s.relay_on = bool(d.get("relay_on", True))
        return s

    @classmethod
    def load(cls, path: str = STATE) -> "RelayState":
        try:
            with open(path, encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (OSError, ValueError):
            return cls()

    def save(self, path: str = STATE) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=1, ensure_ascii=False)
        os.replace(tmp, path)

    def cursor(self, chat_id: int) -> int | None:
        return self.cursors.get(str(chat_id))

    def set_cursor(self, chat_id: int, rowid: int) -> None:
        self.cursors[str(chat_id)] = int(rowid)

    @property
    def push_enabled(self) -> bool:
        return (not self.local_only) and bool(self.push_url) and bool(self.push_token)


# ----------------------------------------------------------------------------- push

def push_record(rec: dict, url: str, token: str, urlopen: Callable = urllib.request.urlopen, timeout: int = 10) -> None:
    """POST one record as JSON with ``Authorization: Bearer <token>``. Raises on failure."""
    line = json.dumps(rec, ensure_ascii=False)
    req = urllib.request.Request(url, data=line.encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token or ''}"})
    urlopen(req, timeout=timeout)


# ----------------------------------------------------------------------------- relay loop

class Relay:
    """Drives one poll at a time. ``poll_once()`` never raises for push failures (the local
    JSONL is the record); it raises RelayError only when the database cannot be read."""

    def __init__(self, state: RelayState, db_path: str = DB, out_path: str = OUT, state_path: str = STATE,
                 urlopen: Callable = urllib.request.urlopen, log: Callable[[str], None] | None = None):
        self.state = state
        self.db_path = db_path
        self.out_path = out_path
        self.state_path = state_path
        self.urlopen = urlopen
        self.log = log or (lambda msg: None)
        self.push_failures = 0

    # -- thread selection
    def tick(self, chat_id: int, con: sqlite3.Connection | None = None) -> None:
        """Start relaying a chat from NOW (cursor = its newest message; history is not replayed)."""
        chat_id = int(chat_id)
        if chat_id not in self.state.chats:
            self.state.chats.append(chat_id)
        if self.state.cursor(chat_id) is None:
            own = con is None
            con = con or connect(self.db_path)
            try:
                self.state.set_cursor(chat_id, latest_rowid(con, chat_id))
            finally:
                if own:
                    con.close()
        self.state.save(self.state_path)

    def untick(self, chat_id: int) -> None:
        chat_id = int(chat_id)
        if chat_id in self.state.chats:
            self.state.chats.remove(chat_id)
        self.state.cursors.pop(str(chat_id), None)
        self.state.save(self.state_path)

    # -- one poll
    def poll_once(self) -> list[dict]:
        """Fetch rows newer than each ticked chat's cursor, append them to relay.jsonl, push if on,
        advance the cursors, persist state. Returns the new records (POC shape)."""
        if not self.state.chats:
            return []
        new: list[dict] = []
        con = connect(self.db_path)
        try:
            for chat_id in list(self.state.chats):
                cursor = self.state.cursor(chat_id)
                if cursor is None:  # ticked before a cursor existed: start from now
                    self.state.set_cursor(chat_id, latest_rowid(con, chat_id))
                    continue
                for r in fetch(con, chat_id, cursor):
                    rec = record_from_row(chat_id, r)
                    self._emit(rec)
                    cursor = r["rowid"]
                    self.state.set_cursor(chat_id, cursor)
                    new.append(rec)
        finally:
            con.close()
        if new or not os.path.exists(self.state_path):
            self.state.save(self.state_path)
        return new

    def _emit(self, rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        os.makedirs(os.path.dirname(self.out_path), exist_ok=True)
        with open(self.out_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if self.state.push_enabled:
            try:
                push_record(rec, self.state.push_url, self.state.push_token, urlopen=self.urlopen)
            except Exception as e:  # network is best-effort; the local JSONL is the record
                self.push_failures += 1
                self.log(f"push failed: {e}")


def write_jsonl(records: Iterable[dict], path: str) -> int:
    """Utility (tests / CLI): append records, return count."""
    n = 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n"); n += 1
    return n
