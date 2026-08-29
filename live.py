"""Live feed for the report page.

Pushes a state.json into the running report sandbox as a sweep progresses. The page
polls it. If nothing ever pushes, the page falls back to the recorded matrices, so the
demo never depends on a live run coming up on venue wifi.
"""
import ast, json, os, queue, threading, time

STATE_PATH = "state.json"
BUGGY_SRC = "workflow.buggy.py"

# What each verdict flag means in plain English, for the findings feed. Same defects the
# fixer is told about in REMEDIATION -- phrased for a human reading a projector.
FINDING = {
    "CORRUPT":   ("truncated cache consumed",
                  "cache.json is unparseable on disk after the run -- the previous crash "
                  "tore it mid-write and this run did not quarantine it."),
    "DUPLICATE": ("paid twice",
                  "more than one REFUND record in ledger.txt: the retry did not notice the "
                  "id was already there."),
    "GARBAGE":   ("bad receipt written",
                  "a REFUND record has a receipt that is not an https:// URL -- a malformed "
                  "tool response was committed to the ledger as if it were real."),
    "LOST":      ("failed silently",
                  "no REFUND and no error.txt: the run gave up without paying and without "
                  "telling anyone."),
}

# Which code a verdict is actually about, so the page can show the defect next to the
# lines that repaired it. Named functions first; anchors are the fallback for when the
# model renames things (it writes the whole file from scratch, so it often does).
# The durable-effect function is named for its domain: refund, send, backup, dispatch...
EFFECT = ["refund", "send", "backup", "append_doc", "commit", "dispatch"]

FIX_FOCUS = {
    "CORRUPT":   dict(bug=["cache", "main"], fix=["load_cache", "cache", "main"],
                      anchors=[".corrupt", "os.replace", "json.load"]),
    "DUPLICATE": dict(bug=EFFECT, fix=["rebuild*"] + EFFECT,
                      anchors=["seen", "dup", "id"]),
    "GARBAGE":   dict(bug=["validate"] + EFFECT, fix=["validate", "rebuild*"],
                      anchors=["https://", "receipt", "url"]),
    "LOST":      dict(bug=["main", "fetch"], fix=["fail", "main"],
                      anchors=["ERR", "error.txt", "HONEST_FAIL"]),
}


def _top_defs(src):
    """name -> source text, for every top-level function. Unparseable source yields {}."""
    try:
        tree = ast.parse(src)
    except Exception:
        return {}
    lines = src.splitlines()
    return {n.name: "\n".join(lines[n.lineno - 1:n.end_lineno])
            for n in tree.body if isinstance(n, ast.FunctionDef)}


def _clip(code, n=44):
    ls = code.splitlines()
    return code if len(ls) <= n else "\n".join(ls[:n] + [f"# ... {len(ls) - n} more lines"])


def _expand(defs, names):
    """A name, or a prefix glob ('rebuild*') -- the rebuild function is called
    rebuild_ledger, rebuild_outbox, rebuild_journal... one per domain."""
    out = []
    for p in names:
        hits = [n for n in defs if n.startswith(p[:-1])] if p.endswith("*") else \
               ([p] if p in defs else [])
        out += [h for h in hits if h not in out]
    return out


def _pick(defs, names, anchors, limit=2):
    got = [(n, defs[n]) for n in _expand(defs, names)]
    if not got and defs:                       # renamed: fall back to the best anchor match
        best = max(defs.items(), key=lambda kv: sum(a in kv[1] for a in anchors))
        if sum(a in best[1] for a in anchors):
            got = [best]
    return [dict(name=n, code=_clip(c)) for n, c in got[:limit]]


def code_focus(buggy_src, fixed_src):
    """{flag: {bug:[{name,code}], fix:[{name,code}]}} -- the lines behind each verdict."""
    b, f = _top_defs(buggy_src), _top_defs(fixed_src)
    out = {}
    for flag, spec in FIX_FOCUS.items():
        out[flag] = dict(bug=_pick(b, spec["bug"], spec["anchors"]),
                         fix=_pick(f, spec["fix"], spec["anchors"]))
    return out


def findings_for(r):
    """Every defect the classifier found in one cell, with its on-disk evidence."""
    out = []
    for flag in r["verdict"].split("+"):
        if flag not in FINDING:
            continue
        title, why = FINDING[flag]
        out.append(dict(machine=r["machine"], transport=r["transport"], flag=flag,
                        title=title, why=why, refunds=r.get("refunds", 0),
                        exit_code=r.get("exit_code"),
                        evidence=(r.get("autopsy") or "").strip()))
    return out


class Live:
    """Coalescing background pusher: the sweep never blocks on an upload, and a slow link
    drops stale frames instead of queueing them."""

    def __init__(self, sandbox=None, resume=False):
        self.sb = sandbox
        # Once the repair starts, the findings list is frozen: it is the list of defects
        # the patch has to answer for. Re-sweep cells fill the AFTER grid instead.
        self.frozen = False
        # resume: keep the matrix already on screen (e.g. the failing sweep) while the
        # repair loop runs underneath it, instead of blanking the page.
        if resume and os.path.exists(STATE_PATH):
            self.state = json.load(open(STATE_PATH))
            self.state.setdefault("fix", dict(active=False, author=None, endpoint=None,
                                              rounds=[], fixed=False))
        else:
            self.state = dict(phase="idle", started=time.time(), cells=[], findings=[],
                          fix=dict(active=False, author=None, endpoint=None, rounds=[],
                                   fixed=False), done=False)
        self._q = queue.Queue()
        self._t = threading.Thread(target=self._pump, daemon=True)
        self._t.start()

    def _pump(self):
        while True:
            body = self._q.get()
            if body is None:
                return
            while not self._q.empty():          # keep only the newest frame
                nxt = self._q.get()
                if nxt is None:
                    return
                body = nxt
            try:
                open(STATE_PATH, "wb").write(body)
                if self.sb:
                    self.sb.fs.upload_file(body, STATE_PATH)
            except Exception as e:
                print(f"  [live] push failed (ignored): {type(e).__name__}: {e}")

    def push(self, **patch):
        self.state.update(patch)
        self.state["t"] = time.time()
        self._q.put(json.dumps(self.state).encode())

    def start_cells(self, machines, transports, phase):
        self.state["cells"] = [dict(machine=m, transport=t, status="running")
                               for m in machines for t in transports]
        if not self.frozen:
            self.state["findings"] = []
        self.push(phase=phase, started=time.time(), done=False)

    def seed_before(self, results, phase="Before \u2014 the buggy agent"):
        """Plant the failing sweep as the BEFORE board.

        A resumed state.json holds whatever the LAST run left on the page -- often the
        green AFTER grid of a previous repair. Snapshotting that as BEFORE shows nine
        green cells next to a list of defects nothing on screen produced. BEFORE is
        always the sweep being answered for, so it is seeded from those results, never
        inherited."""
        self.frozen = False
        self.state.pop("before", None)
        self.state["cells"] = [
            dict(machine=r["machine"], transport=r["transport"], status="done",
                 verdict=r["verdict"], colour=r["colour"], refunds=r.get("refunds", 0),
                 exit_code=r.get("exit_code"), autopsy=r.get("autopsy")) for r in results]
        self.state["findings"] = [f for r in results for f in findings_for(r)]
        self.push(phase=phase, done=False)

    def cell_done(self, r, ms):
        for c in self.state["cells"]:
            if c["machine"] == r["machine"] and c["transport"] == r["transport"]:
                c.update(status="done", verdict=r["verdict"], colour=r["colour"],
                         refunds=r.get("refunds", 0), exit_code=r.get("exit_code"),
                         autopsy=r.get("autopsy"), ms=ms)
        if not self.frozen:
            self.state["findings"].extend(findings_for(r))
        self.push()

    # ---- fixer loop ----------------------------------------------------------
    # The page renders these as they land, so the feedback loop is visible while it
    # runs rather than summarised after it finishes.

    def fix_start(self, author, endpoint):
        # Freeze the failing matrix as BEFORE; the re-sweeps render as AFTER beside it.
        self.state["before"] = json.loads(json.dumps(self.state.get("cells", [])))
        self.frozen = True
        self.state["fix"] = dict(active=True, author=author, endpoint=endpoint,
                                 model=os.environ.get("FIXER_MODEL"), rounds=[],
                                 fixed=False)
        self.push()

    def _round(self, n):
        for r in self.state["fix"]["rounds"]:
            if r["n"] == n:
                return r
        r = dict(n=n, status="asking", evidence=None, patch=None, latency=None,
                 tokens=None, smoke=None, cells=[], checks=None, fixed=False)
        self.state["fix"]["rounds"].append(r)
        return r

    def round_asking(self, n, evidence):
        self._round(n).update(status="asking", evidence=evidence)
        self.push()

    def round_patch(self, n, patch, author, latency=None, tokens=None):
        self._round(n).update(status="patched", patch=patch, latency=latency,
                              tokens=tokens)
        self.state["fix"]["author"] = author
        self.attach_code(patch)
        self.push()

    def attach_code(self, patch):
        """Hang the buggy lines and the patched lines off each finding, so hovering a
        defect shows what it did AND what changed to stop it doing that."""
        try:
            focus = code_focus(open(BUGGY_SRC).read(), patch)
        except Exception:
            return
        for f in self.state.get("findings", []):
            f["code"] = focus.get(f["flag"])

    def round_smoke(self, n, ok, evidence=""):
        self._round(n).update(status="smoke", smoke=("passed" if ok else "FAILED"),
                              smoke_evidence=evidence)
        self.push()

    def round_sweeping(self, n, machines, transports):
        self._round(n).update(status="sweeping")
        self.start_cells(machines, transports, f"Round {n} — re-sweeping the patched agent")

    def round_cell(self, n, r, ms=0):
        self._round(n)["cells"].append(dict(machine=r["machine"], transport=r["transport"],
                                            verdict=r["verdict"], colour=r["colour"]))
        self.cell_done(r, ms)

    def round_result(self, n, checks, fixed):
        self._round(n).update(status="done", checks=checks, fixed=fixed)
        if fixed:
            self.state["fix"]["fixed"] = True
        self.push()

    def fix_done(self, fixed, rounds):
        self.state["fix"].update(active=False, fixed=fixed, total_rounds=rounds)
        self.push()

    def finish(self, phase="done"):
        self.push(phase=phase, done=True)
        time.sleep(1.5)                          # let the last frame land before exit
