"""Buggy fixture: an email agent that appends a CSV row to outbox.csv and marks the send
in sent.json. Stdlib only. Runs INSIDE a Daytona sandbox.

Fault inputs:
  FAULT     ok | malformed_json | bad_url
  CRASH_AT  4 dies inside the cache write (torn drafts.json)
            5 dies after the CSV row but before the sent flag (cross-artifact)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os

STATE, CACHE, OUTBOX, SENT, ERR = ("state.json", "drafts.json", "outbox.csv",
                                   "sent.json", "error.txt")
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():
    if FAULT == "malformed_json": return "{'to': 'alice'"
    if FAULT == "bad_url": return {"to": "alice@example.com", "receipt": "htp:/broken url"}
    return {"to": "alice@example.com", "receipt": "https://mail.example/s/1"}


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard
    s.update(r); s["id"] = "m_1"


def validate(s): s["valid"] = True                # BUG 0b: never checks recipient/receipt


def compute(s): s["size"] = len(s.get("to", ""))


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()
    if CRASH_AT == 4: os._exit(1)
    f.write(p[h:]); f.close()


def send(s):                                      # BUG 2: side effect before commit
    row = f"{s['id']},{s['to']},{s.get('receipt','')}\n"
    f = open(OUTBOX, "a")                         # append, no dedupe by id
    f.write(row); f.flush()
    if CRASH_AT == 5: os._exit(1)
    f.close()
    with open(SENT, "w") as g: json.dump({"sent": s["id"]}, g)


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
                                 ("send", send), ("mark_done", mark_done)], 1):
        step(i, n, fn, s)


main()
