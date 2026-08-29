import json, os, re, sys, time

STATE, CACHE, LEDGER, ERR = "state.json", "cache.json", "ledger.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
HOLD = float(os.environ.get("HOLD", "0"))
FAULT = os.environ.get("FAULT", "ok")

def call_tool():
    if FAULT == "malformed_json": return "{'amount': 250"
    if FAULT == "bad_url":        return {"amount": 250, "receipt": "htp:/broken url"}
    if FAULT == "empty":          return {}
    return {"amount": 250, "receipt": "https://pay.example/r/4471"}

def step(n, name, fn, s):
    fn(s)
    print(f"step {n} {name} ok", flush=True)
    if CRASH_AT == n: os._exit(1)

def fetch(s):
    r = call_tool()
    if isinstance(r, str): r = json.loads(r)
    s.update(r); s["id"] = "4471"

def validate(s):
    if not re.match("^https://", s.get("receipt", "")):
        raise ValueError("Invalid receipt URL")

def compute(s):  s["refund"] = s.get("amount", 0)

def cache(s):
    p = json.dumps(s)
    tmp_file = CACHE + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(p)
    if CRASH_AT == 4: os._exit(1)
    os.replace(tmp_file, CACHE)

def refund(s):
    line = f"REFUND {s['id']} {s['refund']} {s.get('receipt','')}\n"
    tmp_file = LEDGER + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(line)
    if CRASH_AT == 5: os._exit(1)
    os.replace(tmp_file, LEDGER)

def rebuild_ledger():
    seen_ids = set()
    new_records = []
    if os.path.exists(LEDGER):
        with open(LEDGER, "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) != 4: continue
                _, id_, amount, receipt = parts
                if not re.match("^https://", receipt): continue
                if id_ in seen_ids: continue
                seen_ids.add(id_)
                new_records.append((id_, amount, receipt))
    
    with open(LEDGER, "w") as f:
        for id_, amount, receipt in new_records:
            f.write(f"REFUND {id_} {amount} {receipt}\n")

def mark_done(s):
    s["done"] = True
    with open(STATE, "w") as f: json.dump(s, f)

def main():
    s = {}
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("done"):
                print("already done")
                return
        except Exception as e:
            print(f"HONEST_FAIL: Corrupted state file: {e}", file=sys.stderr)
            os.rename(STATE, STATE + ".corrupt")
            s = {}

    if os.path.exists(CACHE):
        try:
            s.update(json.load(open(CACHE)))
        except Exception as e:
            print(f"HONEST_FAIL: Corrupted cache file: {e}", file=sys.stderr)
            os.rename(CACHE, CACHE + ".corrupt")
            s = {}

    rebuild_ledger()

    for i, (n, f) in enumerate([("fetch", fetch), ("validate", validate), ("compute", compute),
                                ("cache", cache), ("refund", refund), ("mark_done", mark_done)], 1):
        try:
            step(i, n, f, s)
        except Exception as e:
            print(f"HONEST_FAIL: {e}", file=sys.stderr)
            with open(ERR, "w") as f: f.write(str(e))
            os._exit(1)

if __name__ == "__main__":
    main()