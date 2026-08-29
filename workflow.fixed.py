"""Corrected fixture -- the rule-fixer's output and the floor under GATE 3.

Satisfies the fix spec:
  1. cache: read guarded by try/except, written tmp + os.replace (atomic)
  2. ledger: REBUILT at startup from validated records, before fetch
  3. tool output validated (receipt must match ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr

CRASH_AT is still honoured: the seed must still be able to kill this process. The point
is that the same kill now leaves a recoverable machine instead of a corrupt one.
"""
import json, os, re, sys

STATE, CACHE, LEDGER, ERR = "state.json", "cache.json", "ledger.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
RECEIPT_RE = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'amount': 250"
    if FAULT == "bad_url":        return {"amount": 250, "receipt": "htp:/broken url"}
    if FAULT == "empty":          return {}
    return {"amount": 250, "receipt": "https://pay.example/r/4471"}


def fail(reason):
    """FIX 4: loud. error.txt is the primary channel, stderr the fallback."""
    try:
        with open(ERR, "w") as f: f.write(f"HONEST_FAIL: {reason}\n")
    except OSError:
        pass
    sys.stderr.write(f"HONEST_FAIL: {reason}\n")
    sys.exit(1)


def load_cache():
    """FIX 1a: a corrupt cache is ignored, never consumed as valid -- AND quarantined.
    Ignoring alone is not enough: leaving the unparseable file on disk means the next run
    inherits the same landmine, and an autopsy cannot tell "ignored it" from "ate it"."""
    if not os.path.exists(CACHE): return {}
    try:
        with open(CACHE) as f: return json.load(f)
    except Exception:
        os.replace(CACHE, CACHE + ".corrupt")     # quarantined, not deleted: keep the evidence
        return {}


def write_cache(s):
    """FIX 1b: atomic. A kill before the rename leaves cache.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def rebuild_ledger():
    """FIX 2: rebuild from validated records BEFORE fetch. Drops torn records and
    duplicate ids, rewrites atomically. Returns the set of ids already paid.
    Skipping an append by id is not enough -- a torn record left on disk is still
    garbage the classifier will flag."""
    if not os.path.exists(LEDGER): return set()
    raw = open(LEDGER).read()
    seen, keep = set(), []
    for rec in raw.split("REFUND ")[1:]:
        parts = rec.strip().split()
        if len(parts) != 3: continue                      # torn record
        rid, amt, receipt = parts
        if not RECEIPT_RE.match(receipt): continue        # garbage receipt
        if rid in seen: continue                          # duplicate id
        seen.add(rid); keep.append(f"REFUND {rid} {amt} {receipt}\n")
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(keep); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, LEDGER)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                            # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "amount" not in r:
        fail("tool response missing amount")
    s.update(r); s["id"] = "4471"


def validate(s):
    if not RECEIPT_RE.match(s.get("receipt", "")):        # FIX 3b: receipt checked
        fail(f"receipt not https: {s.get('receipt','')!r}")
    s["valid"] = True


def compute(s): s["refund"] = s.get("amount", 0)


def refund(s, paid):
    """FIX 2b: idempotent per id, and the write is atomic so no torn record can land."""
    if s["id"] in paid:
        print(f"refund {s['id']} already paid, skipping", flush=True); return
    line = f"REFUND {s['id']} {s['refund']} {s['receipt']}\n"
    cur = open(LEDGER).read() if os.path.exists(LEDGER) else ""
    tmp = LEDGER + ".tmp"
    with open(tmp, "w") as f:
        f.write(cur + line); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, LEDGER)


def mark_done(s):
    s["done"] = True
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, STATE)


def main():
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("done"): print("already done", flush=True); return
        except Exception:
            pass
    paid = rebuild_ledger()          # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        write_cache(s)
        refund(s, paid)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
