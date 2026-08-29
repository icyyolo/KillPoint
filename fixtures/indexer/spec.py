"""indexer fixture: a web crawler. Records are lines of index.ndjson (one JSON object per
line); the cache is fetch_cache.json (torn by crash@cache); the progress flag in the cache
is what is missing after crash@append, so the retry appends a duplicate index line."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fixture import Fixture, HTTPS, make_fix_spec

WORK = os.path.dirname(os.path.abspath(__file__))


def records(fs):
    t = fs.read("index.ndjson")
    return [ln for ln in (t or "").splitlines() if ln.strip()]


def record_valid(r):
    try:
        d = json.loads(r)
    except Exception:
        return False
    return isinstance(d, dict) and bool(d.get("id")) and bool(HTTPS.match(d.get("url", "")))


def seed_assert(label, fs):
    if label == "crash@cache":
        c = fs.read("fetch_cache.json")
        if c is None: return False, "fetch_cache.json was never created"
        try:
            json.loads(c)
            return False, f"fetch_cache.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    t = fs.read("index.ndjson")
    lines = [ln for ln in (t or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return False, f"expected exactly 1 index line, got {len(lines)}"
    c = fs.read("fetch_cache.json")
    if c:
        try:
            if json.loads(c).get("indexed") == "doc_1":
                return False, "progress flag already set, kill landed too late"
        except Exception:
            return False, "fetch_cache.json unparseable -- crash@append should leave it valid"
    return True, ""


FIXTURE = Fixture(
    name="indexer",
    workflow=os.path.join(WORK, "workflow.py"),
    template=os.path.join(WORK, "workflow.fixed.py"),
    transports=["ok", "malformed_json", "bad_url"],
    crash_points={"crash@cache": 4, "crash@append": 5},
    files=["fetch_cache.json", "index.ndjson", "error.txt", "state.json"],
    records=records,
    record_valid=record_valid,
    parseable=["fetch_cache.json"],
    err_file="error.txt",
    seed_assert=seed_assert,
    fix_spec=make_fix_spec(
        cache="fetch_cache.json",
        rec="index.ndjson",
        validate_field="each document's url field must match ^https://",
        rebuild_desc="parse every line as JSON, drop unparseable lines and lines whose url "
                     "is not ^https://, drop duplicate document ids",
        compute_desc="derives the deterministic doc_id",
    ),
    record_label="indexed document",
)
