"""file_backup fixture: a backup agent. Records are JSON entries in manifest.json; the
cache is cache.json (torn by crash@cache); the effect is the backup/ file (missing after
crash@backup, which is what makes the retry append a duplicate entry)."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fixture import Fixture, HTTPS, make_fix_spec

WORK = os.path.dirname(os.path.abspath(__file__))


def records(fs):
    m = fs.read("manifest.json")
    if not m:
        return []
    try:
        return [json.dumps(e, sort_keys=True) for e in json.loads(m)]
    except Exception:
        return []


def record_valid(r):
    try:
        d = json.loads(r)
    except Exception:
        return False
    return isinstance(d, dict) and bool(d.get("file")) and bool(HTTPS.match(d.get("url", "")))


def seed_assert(label, fs):
    if label == "crash@cache":
        c = fs.read("cache.json")
        if c is None: return False, "cache.json was never created"
        try:
            json.loads(c)
            return False, f"cache.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    m = fs.read("manifest.json")
    if not m: return False, "manifest.json was never created"
    try:
        entries = json.loads(m)
    except Exception:
        return False, "manifest.json unparseable -- crash@backup should leave it complete"
    if len(entries) != 1:
        return False, f"expected exactly 1 manifest entry, got {len(entries)}"
    if fs.read("backup/a.txt") is not None:
        return False, "backup file already written, kill landed too late"
    return True, ""


FIXTURE = Fixture(
    name="file_backup",
    workflow=os.path.join(WORK, "workflow.py"),
    template=os.path.join(WORK, "workflow.fixed.py"),
    transports=["ok", "malformed_json", "bad_url"],
    crash_points={"crash@cache": 4, "crash@backup": 5},
    files=["cache.json", "manifest.json", "error.txt", "state.json", "backup/a.txt"],
    records=records,
    record_valid=record_valid,
    parseable=["cache.json"],
    err_file="error.txt",
    seed_assert=seed_assert,
    fix_spec=make_fix_spec(
        cache="cache.json",
        rec="manifest.json",
        validate_field="the url field must match ^https://",
        rebuild_desc="parse every manifest entry, drop entries with no file or a non-https "
                     "url, drop duplicate filenames",
        compute_desc="sets s['checksum'] from the content length",
    ),
    record_label="backed-up file",
)
