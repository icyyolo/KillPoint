"""Buggy fixture: a job scheduler that records a dispatch in jobs.txt then writes the
payload file. Stdlib only. Runs INSIDE a Daytona sandbox.

Fault inputs:
  FAULT     ok | malformed_json | bad_url
  CRASH_AT  4 dies inside the cache write (torn cache.json)
            5 dies after the jobs.txt record but before the payload file (cross-artifact)

Deliberate bugs -- this is the thing under test, not production code.
"""
import json, os, re

STATE, CACHE, JOBS, ERR = "state.json", "cache.json", "jobs.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
FAULT = os.environ.get("FAULT", "ok")


def call_tool():
    if FAULT == "malformed_json": return "{'job': 'nightly'"
    if FAULT == "bad_url": return {"job": "nightly", "url": "htp:/broken url"}
    return {"job": "nightly", "url": "https://sched.example/hook/nightly"}


def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)


def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)      # BUG 0: no guard
    s.update(r); s["job_id"] = "job_" + re.sub(r"\W", "", s.get("job", "x"))[:8]


def validate(s): s["valid"] = True                # BUG 0b: never checks the url


def compute(s): s["priority"] = 5


def cache(s):                                     # BUG 1: no atomic rename
    p = json.dumps(s); h = len(p) // 2
    f = open(CACHE, "w")
    f.write(p[:h]); f.flush()
    if CRASH_AT == 4: os._exit(1)
    f.write(p[h:]); f.close()


def dispatch(s):                                  # BUG 2: side effect before commit
    line = f"{s['job_id']} {s['url']}\n"
    f = open(JOBS, "a")                           # append, no dedupe by id
    f.write(line); f.flush()
    if CRASH_AT == 5: os._exit(1)
    f.close()
    os.makedirs("jobs", exist_ok=True)
    with open(os.path.join("jobs", s["job_id"] + ".job"), "w") as g:
        json.dump(s, g)


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
                                 ("dispatch", dispatch), ("mark_done", mark_done)], 1):
        step(i, n, fn, s)


main()
