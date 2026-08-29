"""scheduler fixture: a job dispatcher. Records are lines of jobs.txt ("<id> <url>"); the
cache is cache.json (torn by crash@cache); the effect is the payload file jobs/<id>.job
(missing after crash@dispatch, so the retry appends a duplicate record)."""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fixture import Fixture, HTTPS, make_fix_spec

WORK = os.path.dirname(os.path.abspath(__file__))


def records(fs):
    t = fs.read("jobs.txt")
    return [ln for ln in (t or "").splitlines() if ln.strip()]


def record_valid(r):
    parts = r.split()
    return len(parts) == 2 and bool(HTTPS.match(parts[1]))


def seed_assert(label, fs):
    if label == "crash@cache":
        c = fs.read("cache.json")
        if c is None: return False, "cache.json was never created"
        try:
            json.loads(c)
            return False, f"cache.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    t = fs.read("jobs.txt")
    lines = [ln for ln in (t or "").splitlines() if ln.strip()]
    if len(lines) != 1:
        return False, f"expected exactly 1 jobs.txt line, got {len(lines)}"
    if fs.read("jobs/job_nightly.job") is not None:
        return False, "payload already written, kill landed too late"
    return True, ""


FIXTURE = Fixture(
    name="scheduler",
    workflow=os.path.join(WORK, "workflow.py"),
    template=os.path.join(WORK, "workflow.fixed.py"),
    transports=["ok", "malformed_json", "bad_url"],
    crash_points={"crash@cache": 4, "crash@dispatch": 5},
    files=["cache.json", "jobs.txt", "error.txt", "state.json", "jobs/job_nightly.job"],
    records=records,
    record_valid=record_valid,
    parseable=["cache.json"],
    err_file="error.txt",
    seed_assert=seed_assert,
    fix_spec=make_fix_spec(
        cache="cache.json",
        rec="jobs.txt",
        validate_field="the url field must match ^https://",
        rebuild_desc="parse every line as (id, url), drop malformed lines and lines whose "
                     "url is not ^https://, drop duplicate ids",
        compute_desc="derives the deterministic job_id from the job name",
    ),
    record_label="queued job",
)
