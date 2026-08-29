"""Fixture agent workflow: a refund bot. Runs INSIDE a Daytona sandbox. Stdlib only.

Two fault inputs:
  FAULT     transport fault, injected at the tool-function boundary (ok|malformed_json|bad_url|empty)
  CRASH_AT  machine fault, the step number to die at via os._exit(1)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os, time

STATE, CACHE, LEDGER, ERR = "state.json", "cache.json", "ledger.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
# Test hook, 0 in every sweep cell: holds the process open between the two half-writes so an
# external killer (a real sandbox.stop()) can land INSIDE the write. Not used by the matrix.
HOLD = float(os.environ.get("HOLD", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():                                  # the transport-fault injection point
    if FAULT == "malformed_json": return "{'amount': 250"          # unparseable string
    if FAULT == "bad_url":        return {"amount": 250, "receipt": "htp:/broken url"}
    if FAULT == "empty":          return {}
    return {"amount": 250, "receipt": "https://pay.example/r/4471"}


def step(n, name, fn, s):                         # generic kill for steps with no inner kill
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)                 # no atexit, no buffer flush


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard, dies or half-parses
    s.update(r); s["id"] = "4471"


def validate(s): s["valid"] = True                # BUG 0b: never checks the receipt URL
def compute(s):  s["refund"] = s.get("amount", 0)


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()                     # first half is on disk
    if CRASH_AT == 4: os._exit(1)                 # dies INSIDE the write: truncated JSON
    f.write(p[h:]); f.close()


def refund(s):                                    # BUG 2: side effect before commit
    line = f"REFUND {s['id']} {s['refund']} {s.get('receipt','')}\n"
    h = len(line) // 2
    f = open(LEDGER, "a")
    f.write(line[:h]); f.flush()                  # half a record, no trailing newline
    if HOLD: print("HELD mid-write", flush=True); time.sleep(HOLD)
    if CRASH_AT == 5: os._exit(1)                 # genuinely half-written ledger
    f.write(line[h:]); f.close()


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
    for i, (n, f) in enumerate([("fetch", fetch), ("validate", validate), ("compute", compute),
                                ("cache", cache), ("refund", refund), ("mark_done", mark_done)], 1):
        step(i, n, f, s)


main()
