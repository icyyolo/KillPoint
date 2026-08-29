"""mailer fixture: an email dispatcher. Records are CSV rows of outbox.csv
("<id>,<to>,<receipt>"); the cache is drafts.json (torn by crash@draft); the sent flag in
sent.json is what is missing after crash@send, so the retry appends a duplicate row."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fixture import Fixture, HTTPS, make_fix_spec

WORK = os.path.dirname(os.path.abspath(__file__))


def records(fs):
    t = fs.read("outbox.csv")
    return [ln for ln in (t or "").splitlines() if ln.strip()]


def record_valid(r):
    parts = r.split(",")
    if len(parts) != 3: return False
    mid, to, receipt = parts
    return bool(mid) and "@" in to and bool(HTTPS.match(receipt))


def seed_assert(label, fs):
    if label == "crash@draft":
        c = fs.read("drafts.json")
        if c is None: return False, "drafts.json was never created"
        try:
            json.loads(c)
            return False, f"drafts.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    t = fs.read("outbox.csv")
    rows = [ln for ln in (t or "").splitlines() if ln.strip()]
    if len(rows) != 1:
        return False, f"expected exactly 1 outbox row, got {len(rows)}"
    s = fs.read("sent.json")
    if s:
        try:
            if json.loads(s).get("sent") == "m_1":
                return False, "sent flag already set, kill landed too late"
        except Exception:
            return False, "sent.json unparseable -- crash@send should leave it valid"
    return True, ""


FIXTURE = Fixture(
    name="mailer",
    workflow=os.path.join(WORK, "workflow.py"),
    template=os.path.join(WORK, "workflow.fixed.py"),
    transports=["ok", "malformed_json", "bad_url"],
    crash_points={"crash@draft": 4, "crash@send": 5},
    files=["drafts.json", "outbox.csv", "sent.json", "error.txt", "state.json"],
    records=records,
    record_valid=record_valid,
    parseable=["drafts.json"],
    err_file="error.txt",
    seed_assert=seed_assert,
    fix_spec=make_fix_spec(
        cache="drafts.json",
        rec="outbox.csv",
        validate_field="the recipient must contain @ and the receipt must match ^https://",
        rebuild_desc="parse every CSV row as (id, to, receipt), drop rows with a bad "
                     "recipient or non-https receipt, drop duplicate ids",
        compute_desc="sets the deterministic message id",
    ),
    record_label="sent email",
)
