import json, os, re

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
    if isinstance(r, str): r = json.loads(r)
    s.update(r); s["doc_id"] = "doc_1"

def validate(s):
    if not re.match("^https://", s["url"]):
        raise ValueError("Invalid URL")

def compute(s): s["words"] = len(s.get("doc", "").split())

def cache(s):
    p = json.dumps(s); h = len(p) // 2
    tmp_file = CACHE + ".tmp"
    with open(tmp_file, "w") as f:
        f.write(p[:h])
        if CRASH_AT == 4: os._exit(1)
        f.write(p[h:])
    os.replace(tmp_file, CACHE)

def rebuild_index():
    seen_ids = set()
    tmp_file = INDEX + ".tmp"
    with open(tmp_file, "w") as f_out:
        if os.path.exists(INDEX):
            with open(INDEX, "r") as f_in:
                for line in f_in:
                    try:
                        doc = json.loads(line)
                        if re.match("^https://", doc["url"]) and doc["id"] not in seen_ids:
                            f_out.write(line)
                            seen_ids.add(doc["id"])
                    except json.JSONDecodeError:
                        pass
    os.replace(tmp_file, INDEX)

def append_doc(s):
    line = json.dumps({"id": s["doc_id"], "url": s["url"]}) + "\n"
    with open(INDEX, "a") as f:
        f.write(line)
    if CRASH_AT == 5: os._exit(1)

def mark_done(s):
    s["done"] = True
    with open(STATE, "w") as f: json.dump(s, f)

def handle_error(reason):
    with open(ERR, "w") as f: f.write(reason)
    import sys; sys.stderr.write(f'HONEST_FAIL: {reason}\n'); sys.exit(1)

def main():
    s = {}
    if os.path.exists(STATE):
        try:
            s = json.load(open(STATE))
            if s.get("done"):
                print("already done")
                return
        except json.JSONDecodeError:
            handle_error("Corrupt state file")
    
    if os.path.exists(CACHE):
        try:
            s.update(json.load(open(CACHE)))
        except json.JSONDecodeError:
            os.rename(CACHE, CACHE + ".corrupt")
            s = {}

    rebuild_index()

    for i, (n, fn) in enumerate([("fetch", fetch), ("validate", validate),
                                 ("compute", compute), ("cache", cache),
                                 ("append_doc", append_doc), ("mark_done", mark_done)], 1):
        try:
            step(i, n, fn, s)
        except Exception as e:
            handle_error(str(e))

if __name__ == "__main__":
    main()