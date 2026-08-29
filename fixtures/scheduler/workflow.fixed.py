"""Corrected fixture -- scheduler, satisfies the generic fix spec:
  1. cache read guarded, quarantined on corrupt, written tmp + os.replace (atomic)
  2. jobs.txt REBUILT at startup from validated records, before fetch
  3. tool output validated (url must match ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr
  CRASH_AT is still honoured: the same kill now leaves a recoverable machine.
"""
import json, os, re, sys

STATE, CACHE, JOBS, ERR = "state.json", "cache.json", "jobs.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
HTTPS = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'job': 'nightly'"
    if FAULT == "bad_url": return {"job": "nightly", "url": "htp:/broken url"}
    return {"job": "nightly", "url": "https://sched.example/hook/nightly"}


def fail(reason):
    """FIX 4: loud. error.txt is the primary channel, stderr the fallback."""
    try:
        with open(ERR, "w") as f: f.write(f"HONEST_FAIL: {reason}\n")
    except OSError:
        pass
    sys.stderr.write(f"HONEST_FAIL: {reason}\n")
    sys.exit(1)


def load_cache():
    """FIX 1a: a corrupt cache is ignored AND quarantined, never consumed as valid."""
    if not os.path.exists(CACHE): return {}
    try:
        with open(CACHE) as f: return json.load(f)
    except Exception:
        os.replace(CACHE, CACHE + ".corrupt")
        return {}


def rebuild_jobs():
    """FIX 2: rebuild jobs.txt from validated records BEFORE fetch. Drops malformed or
    non-https lines and duplicate ids; rewrites atomically. Returns ids already queued."""
    if not os.path.exists(JOBS): return set()
    seen, keep = set(), []
    for line in open(JOBS).read().splitlines():
        parts = line.split()
        if len(parts) != 2: continue
        jid, url = parts
        if not HTTPS.match(url): continue
        if jid in seen: continue
        seen.add(jid); keep.append(f"{jid} {url}\n")
    tmp = JOBS + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(keep); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, JOBS)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                    # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "url" not in r:
        fail("tool response missing url")
    s.update(r)
    s["job_id"] = "job_" + re.sub(r"\W", "", s.get("job", "x"))[:8]


def validate(s):
    if not HTTPS.match(s.get("url", "")):         # FIX 3b: url checked
        fail(f"url not https: {s.get('url','')!r}")
    s["valid"] = True


def compute(s): s["priority"] = 5


def cache(s):
    """FIX 1b: atomic. A kill before the rename leaves cache.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def dispatch(s, queued):
    """FIX 2b: idempotent per id, record appended atomically, payload written atomically."""
    if s["job_id"] in queued:
        print(f"{s['job_id']} already queued, skipping", flush=True); return
    line = f"{s['job_id']} {s['url']}\n"
    cur = open(JOBS).read() if os.path.exists(JOBS) else ""
    tmp = JOBS + ".tmp"
    with open(tmp, "w") as f:
        f.write(cur + line); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, JOBS)
    os.makedirs("jobs", exist_ok=True)
    p = os.path.join("jobs", s["job_id"] + ".job")
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, p)


def mark_done(s):
    s["done"] = True
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STATE)


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def main():
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("done"): print("already done", flush=True); return
        except Exception:
            pass
    queued = rebuild_jobs()          # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        cache(s)
        dispatch(s, queued)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
