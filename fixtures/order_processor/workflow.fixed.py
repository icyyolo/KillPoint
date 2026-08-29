"""Corrected fixture -- order_processor, satisfies the generic fix spec:
  1. cache read guarded, quarantined on corrupt, written tmp + os.replace (atomic)
  2. journal.json REBUILT at startup from validated records, before fetch
  3. tool output validated (receipt must match ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr
  CRASH_AT is still honoured: the same kill now leaves a recoverable machine.
"""
import json, os, re, sys

STATE, CACHE, JOURNAL, SEQ, ERR = ("state.json", "pending.json", "journal.json",
                                   "seq.txt", "error.txt")
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
HTTPS = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'item': 'widget'"
    if FAULT == "bad_url": return {"item": "widget", "amount": 250,
                                   "receipt": "htp:/broken url"}
    return {"item": "widget", "amount": 250, "receipt": "https://pay.example/r/4471"}


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


def rebuild_journal():
    """FIX 2: rebuild journal.json from validated entries BEFORE fetch. Drops entries with
    a non-https receipt and duplicate order ids; rewrites atomically. Returns ids already
    committed so the effect can be skipped idempotently."""
    if not os.path.exists(JOURNAL): return set()
    try:
        entries = json.load(open(JOURNAL))
    except Exception:
        os.replace(JOURNAL, JOURNAL + ".corrupt"); entries = []
    seen, keep = set(), []
    for e in entries:
        if not isinstance(e, dict) or not e.get("order_id"): continue
        if not HTTPS.match(e.get("receipt", "")): continue
        if e["order_id"] in seen: continue
        seen.add(e["order_id"]); keep.append(e)
    tmp = JOURNAL + ".tmp"
    with open(tmp, "w") as f:
        json.dump(keep, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, JOURNAL)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                    # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "receipt" not in r:
        fail("tool response missing receipt")
    s.update(r); s["order_id"] = "ord_4471"


def validate(s):
    if not HTTPS.match(s.get("receipt", "")):     # FIX 3b: receipt checked
        fail(f"receipt not https: {s.get('receipt','')!r}")
    s["valid"] = True


def compute(s): s["total"] = s.get("amount", 0)


def cache(s):
    """FIX 1b: atomic. A kill before the rename leaves pending.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def commit(s, committed):
    """FIX 2b: idempotent per order id, journal appended atomically, seq bumped after."""
    if s["order_id"] in committed:
        print(f"{s['order_id']} already committed, skipping", flush=True); return
    cur = []
    if os.path.exists(JOURNAL):
        try: cur = json.load(open(JOURNAL))
        except Exception: cur = []
    seq = 1
    if os.path.exists(SEQ):
        try: seq = int(open(SEQ).read().strip()) + 1
        except Exception: pass
    cur.append({"order_id": s["order_id"], "seq": seq, "receipt": s.get("receipt", "")})
    tmp = JOURNAL + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cur, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, JOURNAL)
    tmp = SEQ + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(seq)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, SEQ)


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
    committed = rebuild_journal()      # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        cache(s)
        commit(s, committed)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
