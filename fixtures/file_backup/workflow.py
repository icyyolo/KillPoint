"""Buggy fixture: a backup agent that mirrors a source file and records the copy in a
manifest. Stdlib only. Runs INSIDE a Daytona sandbox.

Fault inputs (same contract as every fixture):
  FAULT     ok | malformed_json | bad_url      injected at the tool-function boundary
  CRASH_AT  4 dies inside the cache write (torn cache.json)
            5 dies after the manifest entry but before the file lands (cross-artifact)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os

STATE, CACHE, MAN, ERR = "state.json", "cache.json", "manifest.json", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():
    if FAULT == "malformed_json": return "{'file': 'a.txt'"
    if FAULT == "bad_url": return {"file": "a.txt", "content": "hello",
                                   "url": "htp:/broken url"}
    return {"file": "a.txt", "content": "hello", "url": "https://src.example/f/a.txt"}


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard, dies on malformed
    s.update(r); s["id"] = s.get("file", "?")


def validate(s): s["valid"] = True                # BUG 0b: never checks the url


def compute(s): s["checksum"] = str(len(s.get("content", "")))


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()
    if CRASH_AT == 4: os._exit(1)
    f.write(p[h:]); f.close()


def backup(s):                                    # BUG 2: side effect before commit
    man = []
    if os.path.exists(MAN):
        man = json.load(open(MAN))                # no dedupe by filename
    man.append({"file": s["id"], "checksum": s["checksum"], "url": s["url"]})
    open(MAN, "w").write(json.dumps(man))         # non-atomic, no idempotency guard
    if CRASH_AT == 5: os._exit(1)
    os.makedirs("backup", exist_ok=True)
    with open(os.path.join("backup", s["id"]), "w") as f:
        f.write(s.get("content", ""))


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
                                 ("backup", backup), ("mark_done", mark_done)], 1):
        step(i, n, fn, s)


main()
