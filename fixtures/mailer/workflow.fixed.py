"""Corrected fixture -- mailer, satisfies the generic fix spec:
  1. cache read guarded, quarantined on corrupt, written tmp + os.replace (atomic)
  2. outbox.csv REBUILT at startup from validated records, before fetch
  3. tool output validated (recipient has @, receipt matches ^https://) before use
  4. every failure writes error.txt AND prints HONEST_FAIL to stderr
  CRASH_AT is still honoured: the same kill now leaves a recoverable machine.
"""
import json, os, re, sys

STATE, CACHE, OUTBOX, SENT, ERR = ("state.json", "drafts.json", "outbox.csv",
                                   "sent.json", "error.txt")
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")
HTTPS = re.compile(r"^https://\S+$")


def call_tool():
    if FAULT == "malformed_json": return "{'to': 'alice'"
    if FAULT == "bad_url": return {"to": "alice@example.com", "receipt": "htp:/broken url"}
    return {"to": "alice@example.com", "receipt": "https://mail.example/s/1"}


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


def _row_ok(row):
    parts = row.split(",")
    if len(parts) != 3: return False
    mid, to, receipt = parts
    return bool(mid) and "@" in to and bool(HTTPS.match(receipt))


def rebuild_outbox():
    """FIX 2: rebuild outbox.csv from validated rows BEFORE fetch. Drops malformed rows,
    rows with a bad recipient or non-https receipt, and duplicate ids; rewrites atomically.
    Returns ids already sent."""
    if not os.path.exists(OUTBOX): return set()
    seen, keep = set(), []
    for row in open(OUTBOX).read().splitlines():
        if not row.strip(): continue
        if not _row_ok(row): continue
        mid = row.split(",")[0]
        if mid in seen: continue
        seen.add(mid); keep.append(row + "\n")
    tmp = OUTBOX + ".tmp"
    with open(tmp, "w") as f:
        f.writelines(keep); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, OUTBOX)
    return seen


def fetch(s):
    r = call_tool()
    if isinstance(r, str):
        try: r = json.loads(r)                    # FIX 3a: guarded
        except Exception: fail("malformed tool response")
    if not isinstance(r, dict) or "receipt" not in r:
        fail("tool response missing receipt")
    s.update(r); s["id"] = "m_1"


def validate(s):
    if "@" not in s.get("to", ""):                # FIX 3b: recipient checked
        fail(f"bad recipient: {s.get('to','')!r}")
    if not HTTPS.match(s.get("receipt", "")):     # and receipt checked
        fail(f"receipt not https: {s.get('receipt','')!r}")
    s["valid"] = True


def compute(s): s["size"] = len(s.get("to", ""))


def cache(s):
    """FIX 1b: atomic. A kill before the rename leaves drafts.json untouched."""
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp, CACHE)


def send(s, sent):
    """FIX 2b: idempotent per id, row appended atomically, sent flag atomic."""
    if s["id"] in sent:
        print(f"{s['id']} already sent, skipping", flush=True); return
    row = f"{s['id']},{s['to']},{s.get('receipt','')}\n"
    cur = open(OUTBOX).read() if os.path.exists(OUTBOX) else ""
    tmp = OUTBOX + ".tmp"
    with open(tmp, "w") as f:
        f.write(cur + row); f.flush(); os.fsync(f.fileno())
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp, OUTBOX)
    tmp = SENT + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"sent": s["id"]}, f); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, SENT)


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
    sent = rebuild_outbox()          # BEFORE fetch, so the failure path also starts clean
    s = load_cache()
    try:
        fetch(s); validate(s); compute(s)
        cache(s)
        send(s, sent)
        mark_done(s)
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


main()
