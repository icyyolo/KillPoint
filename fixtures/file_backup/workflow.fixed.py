"""Corrected fixture -- file_backup, satisfies the generic fix spec:
  1. cache read guarded, quarantined on corrupt, written tmp + os.replace (atomic)
  2. manifest REBUILT at startup from validated entries, before fetch
  3. tool output validated (url must match ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr
  CRASH_AT is still honoured: the same kill now leaves a recoverable machine.
"""
import json, os, re, sys

STATE, CACHE, MAN, ERR = "state.json", "cache.json", "manifest.json", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
HTTPS = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'file': 'a.txt'"
    if FAULT == "bad_url": return {"file": "a.txt", "content": "hello",
                                   "url": "htp:/broken url"}
    return {"file": "a.txt", "content": "hello", "url": "https://src.example/f/a.txt"}


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


def rebuild_manifest():
    """FIX 2: rebuild from validated entries BEFORE fetch. Drops entries with no file,
    non-https urls and duplicate filenames; rewrites atomically. Returns files already
    backed up so the effect can be skipped idempotently."""
    if not os.path.exists(MAN): return set()
    try:
        entries = json.load(open(MAN))
    except Exception:
        os.replace(MAN, MAN + ".corrupt"); entries = []
    seen, keep = set(), []
    for e in entries:
        if not isinstance(e, dict) or not e.get("file"): continue
        if not HTTPS.match(e.get("url", "")): continue
        if e["file"] in seen: continue
        seen.add(e["file"]); keep.append(e)
    tmp = MAN + ".tmp"
    with open(tmp, "w") as f:
        json.dump(keep, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, MAN)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                    # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "url" not in r:
        fail("tool response missing url")
    s.update(r); s["id"] = s.get("file", "?")


def validate(s):
    if not HTTPS.match(s.get("url", "")):         # FIX 3b: url checked
        fail(f"url not https: {s.get('url','')!r}")
    s["valid"] = True


def compute(s): s["checksum"] = str(len(s.get("content", "")))


def cache(s):
    """FIX 1b: atomic. A kill before the rename leaves cache.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def backup(s, backed):
    """FIX 2b: idempotent per filename, file written atomically, entry appended atomically."""
    if s["id"] in backed:
        print(f"{s['id']} already backed up, skipping", flush=True); return
    os.makedirs("backup", exist_ok=True)
    fp = os.path.join("backup", s["id"])
    tmp = fp + ".tmp"
    with open(tmp, "w") as f:
        f.write(s.get("content", "")); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, fp)
    cur = []
    if os.path.exists(MAN):
        try: cur = json.load(open(MAN))
        except Exception: cur = []
    cur.append({"file": s["id"], "checksum": s["checksum"], "url": s["url"]})
    tmp = MAN + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, MAN)


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
    backed = rebuild_manifest()          # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        cache(s)
        backup(s, backed)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
