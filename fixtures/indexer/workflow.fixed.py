"""Corrected fixture -- indexer, satisfies the generic fix spec:
  1. cache read guarded, quarantined on corrupt, written tmp + os.replace (atomic)
  2. index.ndjson REBUILT at startup from validated records, before fetch
  3. tool output validated (url must match ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr
  CRASH_AT is still honoured: the same kill now leaves a recoverable machine.
"""
import json, os, re, sys

STATE, CACHE, INDEX, ERR = "state.json", "fetch_cache.json", "index.ndjson", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
HTTPS = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'doc': 'x'"
    if FAULT == "bad_url": return {"doc": "hello", "url": "htp:/broken url"}
    return {"doc": "hello", "url": "https://crawl.example/d/1"}


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


def rebuild_index():
    """FIX 2: rebuild index.ndjson from validated records BEFORE fetch. Drops unparseable
    lines and lines whose url is not https, and duplicate ids; rewrites atomically."""
    if not os.path.exists(INDEX): return set()
    seen, keep = set(), []
    for ln in open(INDEX).read().splitlines():
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if not isinstance(d, dict) or not HTTPS.match(d.get("url", "")): continue
        if d.get("id") in seen: continue
        seen.add(d["id"]); keep.append(ln + "\n")
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(keep); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, INDEX)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                    # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "url" not in r:
        fail("tool response missing url")
    s.update(r); s["doc_id"] = "doc_1"


def validate(s):
    if not HTTPS.match(s.get("url", "")):         # FIX 3b: url checked
        fail(f"url not https: {s.get('url','')!r}")
    s["valid"] = True


def compute(s): s["words"] = len(s.get("doc", "").split())


def cache(s):
    """FIX 1b: atomic. A kill before the rename leaves fetch_cache.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def append_doc(s, indexed):
    """FIX 2b: idempotent per id, line appended atomically, progress flag atomic."""
    if s["doc_id"] in indexed:
        print(f"{s['doc_id']} already indexed, skipping", flush=True); return
    line = json.dumps({"id": s["doc_id"], "url": s["url"]}) + "\n"
    cur = open(INDEX).read() if os.path.exists(INDEX) else ""
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        f.write(cur + line); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, INDEX)
    c = {}
    if os.path.exists(CACHE):
        try: c = json.load(open(CACHE))
        except Exception: c = {}
    c["indexed"] = s["doc_id"]
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(c, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, CACHE)


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
    indexed = rebuild_index()          # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        cache(s)
        append_doc(s, indexed)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
