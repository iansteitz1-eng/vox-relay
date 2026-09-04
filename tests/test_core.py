"""Vox Relay core tests — run on Linux against a synthetic chat.db built with the real
Messages schema subset (chat, handle, message, chat_message_join, attachment,
message_attachment_join).  `python3 -m pytest tests -q`
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from voxrelay import core  # noqa: E402

APPLE_NS = 10 ** 9
BASE = 780_000_000  # seconds since 2001-01-01 (~2025-09) — Messages stores ns since Apple epoch

SCHEMA = """
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, style INTEGER,
  state INTEGER, account_id TEXT, chat_identifier TEXT, service_name TEXT, display_name TEXT);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL, country TEXT,
  service TEXT NOT NULL, uncanonicalized_id TEXT, UNIQUE (id, service));
CREATE TABLE message (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, text TEXT,
  handle_id INTEGER DEFAULT 0, service TEXT, date INTEGER, is_from_me INTEGER DEFAULT 0,
  attributedBody BLOB, cache_has_attachments INTEGER DEFAULT 0);
CREATE TABLE chat_message_join (chat_id INTEGER REFERENCES chat (ROWID), message_id INTEGER REFERENCES message (ROWID),
  message_date INTEGER DEFAULT 0, PRIMARY KEY (chat_id, message_id));
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY AUTOINCREMENT, guid TEXT UNIQUE NOT NULL, created_date INTEGER,
  filename TEXT, mime_type TEXT, total_bytes INTEGER DEFAULT 0);
CREATE TABLE message_attachment_join (message_id INTEGER REFERENCES message (ROWID),
  attachment_id INTEGER REFERENCES attachment (ROWID), PRIMARY KEY (message_id, attachment_id));
"""


def typedstream(text: str) -> bytes:
    """A crafted attributedBody: typedstream header … NSString … '+' <len> <utf8>. Lengths >=128 use 0x81 + 2-byte LE."""
    raw = text.encode("utf-8")
    ln = bytes([len(raw)]) if len(raw) < 0x81 else b"\x81" + len(raw).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00\x84\x84\x08NSObject"
            b"\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + ln + raw + b"\x86\x84\x02iI\x01\x01\x92")


class Fixture:
    """A synthetic chat.db with a writer connection (to simulate Messages adding rows)."""

    def __init__(self, path: str):
        self.path = path
        self.w = sqlite3.connect(path)
        self.w.executescript(SCHEMA)
        self.w.commit()
        self.n = 0

    def handle(self, hid: str) -> int:
        cur = self.w.execute("INSERT INTO handle (id, service) VALUES (?, 'iMessage')", (hid,))
        self.w.commit()
        return cur.lastrowid

    def chat(self, identifier: str, display_name: str | None = None) -> int:
        cur = self.w.execute("INSERT INTO chat (guid, chat_identifier, service_name, display_name) VALUES (?, ?, 'iMessage', ?)",
                             (f"iMessage;-;{identifier}", identifier, display_name))
        self.w.commit()
        return cur.lastrowid

    def message(self, chat_id: int, handle_id: int, text: str | None, when: int, is_from_me=0,
                attributed: bytes | None = None, attachments: list[str] | None = None) -> int:
        self.n += 1
        cur = self.w.execute("INSERT INTO message (guid, text, handle_id, service, date, is_from_me, attributedBody) VALUES (?,?,?,?,?,?,?)",
                             (f"m-{self.n}", text, handle_id, "iMessage", when * APPLE_NS, is_from_me, attributed))
        mid = cur.lastrowid
        self.w.execute("INSERT INTO chat_message_join (chat_id, message_id, message_date) VALUES (?,?,?)", (chat_id, mid, when * APPLE_NS))
        for fn in attachments or []:
            a = self.w.execute("INSERT INTO attachment (guid, filename, mime_type, total_bytes) VALUES (?,?,?,?)",
                               (f"a-{self.n}-{fn}", fn, "image/jpeg", 12345)).lastrowid
            self.w.execute("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?,?)", (mid, a))
        self.w.commit()
        return mid


@pytest.fixture
def db(tmp_path):
    fx = Fixture(str(tmp_path / "chat.db"))
    h_alex = fx.handle("+15550100123")
    h_annie = fx.handle("annie@example.com")
    c_old = fx.chat("annie@example.com")
    c_new = fx.chat("+15550100123")
    c_group = fx.chat("chat123456", "Sunday Crew")
    fx.message(c_old, h_annie, "hey", BASE + 10)
    fx.message(c_group, h_alex, "group hello", BASE + 20)
    fx.message(c_new, h_alex, "Alex here", BASE + 30)
    fx.message(c_new, h_alex, None, BASE + 31, attributed=typedstream("rich text only"))
    fx.message(c_new, 0, "me replying", BASE + 32, is_from_me=1)
    fx.chats = {"old": c_old, "new": c_new, "group": c_group}
    fx.handles = {"alex": h_alex, "annie": h_annie}
    return fx


# ----------------------------------------------------------------------------- read-only

def test_readonly_open_refuses_writes(db):
    con = core.connect(db.path)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO handle (id, service) VALUES ('x', 'SMS')")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("UPDATE message SET text='pwned'")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("DELETE FROM chat")
    with pytest.raises(sqlite3.OperationalError):
        con.execute("CREATE TABLE evil (x)")
    assert con.execute("PRAGMA query_only").fetchone()[0] == 1
    con.close()
    assert not os.path.exists(db.path + "-wal"), "read-only open must never create a -wal"
    assert not os.path.exists(db.path + "-journal")


def test_connect_missing_db_raises_relayerror(tmp_path):
    with pytest.raises(core.RelayError) as ei:
        core.connect(str(tmp_path / "nope.db"))
    assert "Full Disk Access" in str(ei.value)


# ----------------------------------------------------------------------------- listing

def test_list_chats_newest_first(db):
    con = core.connect(db.path)
    chats = core.list_chats(con, 40)
    con.close()
    assert [c["chat_id"] for c in chats] == [db.chats["new"], db.chats["group"], db.chats["old"]]
    by_id = {c["chat_id"]: c for c in chats}
    assert by_id[db.chats["group"]]["display_name"] == "Sunday Crew"
    assert core.chat_label(by_id[db.chats["group"]]) == "Sunday Crew"
    assert core.chat_label(by_id[db.chats["new"]]) == "+15550100123"
    assert by_id[db.chats["new"]]["n"] == 3
    assert by_id[db.chats["new"]]["last_ts"].startswith("20")


def test_list_chats_limit(db):
    con = core.connect(db.path)
    assert len(core.list_chats(con, 2)) == 2
    con.close()


def test_resolve_chat(db):
    con = core.connect(db.path)
    assert core.resolve_chat(con, "+15550100123") == db.chats["new"]
    assert core.resolve_chat(con, "Sunday Crew") == db.chats["group"]
    assert core.resolve_chat(con, str(db.chats["old"])) == db.chats["old"]
    assert core.resolve_chat(con, "nobody") is None
    con.close()


# ----------------------------------------------------------------------------- tail / fetch

def test_fetch_returns_only_rows_after_cursor(db):
    con = core.connect(db.path)
    cid = db.chats["new"]
    all_rows = core.fetch(con, cid, 0)
    assert [r["rowid"] for r in all_rows] == [3, 4, 5]
    after = core.fetch(con, cid, 4)
    assert [r["rowid"] for r in after] == [5]
    assert core.fetch(con, cid, 5) == []
    assert core.latest_rowid(con, cid) == 5
    assert core.latest_rowid(con, 999) == 0
    con.close()


def test_record_shape_matches_poc(db):
    con = core.connect(db.path)
    cid = db.chats["new"]
    recs = [core.record_from_row(cid, r) for r in core.fetch(con, cid, 0)]
    con.close()
    assert list(recs[0].keys()) == ["ts", "chat_id", "handle", "is_from_me", "text", "attachments", "rowid"]
    assert recs[0]["handle"] == "+15550100123" and recs[0]["text"] == "Alex here" and recs[0]["is_from_me"] is False
    assert recs[1]["text"] == "rich text only"  # NULL text → attributedBody decode
    assert recs[2]["is_from_me"] is True and recs[2]["handle"] is None
    assert recs[0]["ts"] == core.apple_ts((BASE + 30) * APPLE_NS)
    assert recs[0]["ts"].endswith("+00:00")


# ----------------------------------------------------------------------------- decode

def test_apple_ts():
    assert core.apple_ts(None) == "" and core.apple_ts(0) == ""
    assert core.apple_ts(0 + 1) == "2001-01-01T00:00:01+00:00"          # seconds form (<= 1e12)
    # nanoseconds form (> 1e12, what Messages writes on macOS 13+): 780,000,001 s after 2001-01-01
    assert core.apple_ts((BASE + 1) * APPLE_NS) == "2025-09-19T18:40:01+00:00"
    assert core.apple_ts((BASE + 1) * APPLE_NS) == core.apple_ts(BASE + 1)


def test_decode_attributed_body_short_and_long():
    assert core.decode_attributed_body(typedstream("hello")) == "hello"
    assert core.decode_attributed_body(typedstream("héllo wörld ✓")) == "héllo wörld ✓"
    long = "x" * 300
    assert core.decode_attributed_body(typedstream(long)) == long
    assert core.decode_attributed_body(None) == ""
    assert core.decode_attributed_body(b"") == ""
    assert core.decode_attributed_body(b"garbage without marker") == ""
    assert core.decode_attributed_body(b"NSString no plus") == ""


# ----------------------------------------------------------------------------- state

def test_relaystate_roundtrip(tmp_path):
    p = str(tmp_path / "AppSupport" / "VoxRelay" / "state.json")
    s = core.RelayState()
    assert s.chats == [] and s.local_only is True and s.relay_on is True and s.push_token == ""
    assert s.push_url == core.DEFAULT_PUSH_URL and s.push_enabled is False
    s.chats = [7, 9]
    s.set_cursor(7, 120)
    s.set_cursor(9, 5)
    s.push_token = "tok-abc"
    s.local_only = False
    s.relay_on = False
    s.save(p)
    assert os.path.exists(p) and not os.path.exists(p + ".tmp")
    t = core.RelayState.load(p)
    assert t.to_dict() == s.to_dict()
    assert t.cursor(7) == 120 and t.cursor(9) == 5 and t.cursor(8) is None
    assert t.push_enabled is True
    assert core.RelayState.load(str(tmp_path / "missing.json")).to_dict() == core.RelayState().to_dict()
    (tmp_path / "bad.json").write_text("{not json")
    assert core.RelayState.load(str(tmp_path / "bad.json")).chats == []


# ----------------------------------------------------------------------------- relay loop

def make_relay(db, tmp_path, urlopen=None, **state_kw):
    st = core.RelayState(**state_kw)
    r = core.Relay(st, db_path=db.path, out_path=str(tmp_path / "out" / "relay.jsonl"),
                   state_path=str(tmp_path / "out" / "state.json"), urlopen=urlopen or (lambda *a, **k: None))
    return r


def read_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_poll_once_appends_exactly_n_and_advances_cursor(db, tmp_path):
    r = make_relay(db, tmp_path)
    cid = db.chats["new"]
    r.tick(cid)                                  # cursor = newest existing row → history not replayed
    assert r.state.cursor(cid) == 5
    assert r.poll_once() == []
    assert read_jsonl(r.out_path) == []
    # Messages writes 3 new rows
    db.message(cid, db.handles["alex"], "one", BASE + 40)
    db.message(cid, db.handles["alex"], None, BASE + 41, attributed=typedstream("two (rich)"), attachments=["IMG_0001.jpeg"])
    db.message(cid, 0, "three", BASE + 42, is_from_me=1)
    new = r.poll_once()
    assert [x["text"] for x in new] == ["one", "two (rich)", "three"]
    lines = read_jsonl(r.out_path)
    assert len(lines) == 3 and lines == new
    assert lines[1]["attachments"] == ["IMG_0001.jpeg"]      # filename only, never bytes
    assert r.state.cursor(cid) == 8
    # persisted
    assert core.RelayState.load(r.state_path).cursor(cid) == 8
    # nothing new → nothing appended
    assert r.poll_once() == []
    assert len(read_jsonl(r.out_path)) == 3
    # one more
    db.message(cid, db.handles["alex"], "four", BASE + 50)
    assert [x["text"] for x in r.poll_once()] == ["four"]
    assert len(read_jsonl(r.out_path)) == 4 and r.state.cursor(cid) == 9


def test_poll_once_ignores_unticked_chats(db, tmp_path):
    r = make_relay(db, tmp_path)
    r.tick(db.chats["new"])
    db.message(db.chats["old"], db.handles["annie"], "private", BASE + 60)
    db.message(db.chats["group"], db.handles["alex"], "group private", BASE + 61)
    assert r.poll_once() == []
    assert read_jsonl(r.out_path) == []
    # untick clears cursor and stops relaying
    r.untick(db.chats["new"])
    assert r.state.chats == [] and r.state.cursor(db.chats["new"]) is None
    db.message(db.chats["new"], db.handles["alex"], "after untick", BASE + 62)
    assert r.poll_once() == []


def test_poll_once_with_no_cursor_starts_from_now(db, tmp_path):
    r = make_relay(db, tmp_path, chats=[db.chats["new"]])   # ticked in state but no cursor (e.g. old state file)
    assert r.poll_once() == []
    assert r.state.cursor(db.chats["new"]) == 5
    db.message(db.chats["new"], db.handles["alex"], "fresh", BASE + 70)
    assert [x["text"] for x in r.poll_once()] == ["fresh"]


def test_poll_once_missing_db_raises_relayerror_not_crash(tmp_path):
    st = core.RelayState(chats=[1], cursors={"1": 0})
    r = core.Relay(st, db_path=str(tmp_path / "gone.db"), out_path=str(tmp_path / "relay.jsonl"), state_path=str(tmp_path / "state.json"))
    with pytest.raises(core.RelayError):
        r.poll_once()


# ----------------------------------------------------------------------------- push

class FakeOpener:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, req, timeout=None):
        self.calls.append((req, timeout))
        if self.fail:
            raise OSError("network down")
        return None


def test_push_noop_when_off(db, tmp_path):
    op = FakeOpener()
    r = make_relay(db, tmp_path, urlopen=op, push_token="tok", local_only=True)   # token present but cloud push OFF
    cid = db.chats["new"]
    r.tick(cid)
    db.message(cid, db.handles["alex"], "local only", BASE + 80)
    assert len(r.poll_once()) == 1
    assert op.calls == []
    assert len(read_jsonl(r.out_path)) == 1


def test_push_noop_when_on_but_no_token(db, tmp_path):
    op = FakeOpener()
    r = make_relay(db, tmp_path, urlopen=op, push_token="", local_only=False)
    cid = db.chats["new"]
    r.tick(cid)
    db.message(cid, db.handles["alex"], "no token", BASE + 81)
    assert len(r.poll_once()) == 1
    assert op.calls == []


def test_push_posts_each_record_when_on(db, tmp_path):
    op = FakeOpener()
    r = make_relay(db, tmp_path, urlopen=op, push_token="tok-123", local_only=False, push_url="https://voxordo.ai/v1/relay/messages")
    cid = db.chats["new"]
    r.tick(cid)
    db.message(cid, db.handles["alex"], "p1", BASE + 90)
    db.message(cid, db.handles["alex"], "p2", BASE + 91, attachments=["photo.heic"])
    new = r.poll_once()
    assert len(new) == 2 and len(op.calls) == 2
    for (req, timeout), rec in zip(op.calls, new):
        assert req.full_url == "https://voxordo.ai/v1/relay/messages"
        assert req.get_method() == "POST"
        assert req.get_header("Authorization") == "Bearer tok-123"
        assert req.get_header("Content-type") == "application/json"
        assert json.loads(req.data.decode("utf-8")) == rec              # payload == the POC record, exactly
        assert timeout == 10
    assert json.loads(op.calls[1][0].data)["attachments"] == ["photo.heic"]
    assert b"\xff\xd8" not in op.calls[1][0].data                        # no image bytes ride the push
    assert r.push_failures == 0


def test_push_failure_is_logged_not_raised(db, tmp_path):
    logs = []
    op = FakeOpener(fail=True)
    st = core.RelayState(push_token="tok", local_only=False)
    r = core.Relay(st, db_path=db.path, out_path=str(tmp_path / "relay.jsonl"), state_path=str(tmp_path / "state.json"),
                   urlopen=op, log=logs.append)
    cid = db.chats["new"]
    r.tick(cid)
    db.message(cid, db.handles["alex"], "will fail to push", BASE + 100)
    new = r.poll_once()                       # must not raise
    assert len(new) == 1 and len(read_jsonl(r.out_path)) == 1   # local JSONL is still the record
    assert r.push_failures == 1 and logs and "push failed" in logs[0]
    assert r.state.cursor(cid) == 6           # cursor still advances


def test_push_record_direct(monkeypatch):
    op = FakeOpener()
    rec = {"ts": "t", "chat_id": 1, "handle": "h", "is_from_me": False, "text": "x", "attachments": [], "rowid": 1}
    core.push_record(rec, "https://example.test/ingest", "abc", urlopen=op)
    req = op.calls[0][0]
    assert req.get_header("Authorization") == "Bearer abc"
    assert json.loads(req.data) == rec
