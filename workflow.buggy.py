"""Buggy fixture: a crawler that indexes a document into index.ndjson (one JSON object per
line) and flags progress in fetch_cache.json. Stdlib only. Runs INSIDE a Daytona sandbox.

Fault inputs:
  FAULT     ok | malformed_json | bad_url
  CRASH_AT  4 dies inside the cache write (torn fetch_cache.json)
            5 dies after the index line but before the progress flag (cross-artifact)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os

STATE, CACHE, INDEX, ERR = "state.json", "fetch_cache.json", "index.ndjson", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():
    if FAULT == "malformed_json": return "{'doc': 'x'"
    if FAULT == "bad_url": return {"doc": "hello", "url": "htp:/broken url"}
    return {"doc": "hello", "url": "https://crawl.example/d/1"}


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard
    s.update(r); s["doc_id"] = "doc_1"


def validate(s): s["valid"] = True                # BUG 0b: never checks the url


def compute(s): s["words"] = len(s.get("doc", "").split())


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()
    if CRASH_AT == 4: os._exit(1)
    f.write(p[h:]); f.close()


def append_doc(s):                                # BUG 2: side effect before commit
    line = json.dumps({"id": s["doc_id"], "url": s["url"]}) + "\n"
    f = open(INDEX, "a")                          # append, no dedupe by doc id
    f.write(line); f.flush()
    if CRASH_AT == 5: os._exit(1)
    f.close()
    c = {}
    if os.path.exists(CACHE): c = json.load(open(CACHE))
    c["indexed"] = s["doc_id"]
    open(CACHE, "w").write(json.dumps(c))         # non-atomic progress flag


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
                                 ("append_doc", append_doc), ("mark_done", mark_done)], 1):
        step(i, n, fn, s)


main()
