"""Buggy fixture: an order agent that bumps a sequence counter then commits the order to
journal.json. Stdlib only. Runs INSIDE a Daytona sandbox.

Fault inputs:
  FAULT     ok | malformed_json | bad_url
  CRASH_AT  4 dies inside the cache write (torn pending.json)
            5 dies after the journal commit but before mark_done (sequence drift)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os

STATE, CACHE, JOURNAL, SEQ, ERR = ("state.json", "pending.json", "journal.json",
                                   "seq.txt", "error.txt")
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():
    if FAULT == "malformed_json": return "{'item': 'widget'"
    if FAULT == "bad_url": return {"item": "widget", "amount": 250,
                                   "receipt": "htp:/broken url"}
    return {"item": "widget", "amount": 250, "receipt": "https://pay.example/r/4471"}


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard
    s.update(r); s["order_id"] = "ord_4471"


def validate(s): s["valid"] = True                # BUG 0b: never checks the receipt


def compute(s): s["total"] = s.get("amount", 0)


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()
    if CRASH_AT == 4: os._exit(1)
    f.write(p[h:]); f.close()


def commit(s):                                    # BUG 2: side effect before commit
    seq = 1
    if os.path.exists(SEQ):
        seq = int(open(SEQ).read().strip()) + 1
    j = []
    if os.path.exists(JOURNAL):
        j = json.load(open(JOURNAL))              # no dedupe by order id
    j.append({"order_id": s["order_id"], "seq": seq, "receipt": s.get("receipt", "")})
    open(JOURNAL, "w").write(json.dumps(j))       # non-atomic journal write
    with open(SEQ, "w") as f: f.write(str(seq))   # counter moves before mark_done


def mark_done(s):
    s["done"] = True
    with open(STATE, "w") as f: json.dump(s, f)


def main():
    s = {}
    if os.path.exists(STATE):
        s = json.load(open(STATE))
        if s.get("done"): print("already done"); return
    if os.path.exists(CACHE):
        s.update(json.load(open(CACHE)))          # consumes a corrupt cache blindly
    for i, (n, fn) in enumerate([("fetch", fetch), ("validate", validate),
                                 ("compute", compute), ("cache", cache),
                                 ("commit", commit), ("mark_done", mark_done)], 1):
        step(i, n, fn, s)


main()
