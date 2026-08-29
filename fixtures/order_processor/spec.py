"""order_processor fixture: an order/charging agent. Records are JSON entries in
journal.json; the cache is pending.json (torn by crash@pending); crash@seq leaves the
order committed and the sequence counter advanced but mark_done never reached, so the
retry double-commits the same order id."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fixture import Fixture, HTTPS, make_fix_spec

WORK = os.path.dirname(os.path.abspath(__file__))


def records(fs):
    j = fs.read("journal.json")
    if not j:
        return []
    try:
        return [json.dumps(e, sort_keys=True) for e in json.loads(j)]
    except Exception:
        return []


def record_valid(r):
    try:
        d = json.loads(r)
    except Exception:
        return False
    return isinstance(d, dict) and bool(d.get("order_id")) and bool(HTTPS.match(d.get("receipt", "")))


def seed_assert(label, fs):
    if label == "crash@pending":
        c = fs.read("pending.json")
        if c is None: return False, "pending.json was never created"
        try:
            json.loads(c)
            return False, f"pending.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    j = fs.read("journal.json")
    if not j: return False, "journal.json was never created"
    try:
        entries = json.loads(j)
    except Exception:
        return False, "journal.json unparseable -- crash@seq should leave it complete"
    if len(entries) != 1:
        return False, f"expected exactly 1 journal entry, got {len(entries)}"
    seq = fs.read("seq.txt")
    if seq and seq.strip() != "1":
        return False, f"seq.txt should be 1 (counter committed before mark_done), got {seq!r}"
    return True, ""


FIXTURE = Fixture(
    name="order_processor",
    workflow=os.path.join(WORK, "workflow.py"),
    template=os.path.join(WORK, "workflow.fixed.py"),
    transports=["ok", "malformed_json", "bad_url"],
    crash_points={"crash@pending": 4, "crash@seq": 5},
    files=["pending.json", "journal.json", "seq.txt", "error.txt", "state.json"],
    records=records,
    record_valid=record_valid,
    parseable=["pending.json"],
    err_file="error.txt",
    seed_assert=seed_assert,
    fix_spec=make_fix_spec(
        cache="pending.json",
        rec="journal.json",
        validate_field="the receipt must match ^https://",
        rebuild_desc="parse every journal entry, drop entries with a non-https receipt, "
                     "drop duplicate order ids",
        compute_desc="sets the order total from the amount",
    ),
    record_label="committed order",
)
