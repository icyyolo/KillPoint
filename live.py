"""Live feed for the report page.

Pushes a state.json into the running report sandbox as a sweep progresses. The page
polls it. If nothing ever pushes, the page falls back to the recorded matrices, so the
demo never depends on a live run coming up on venue wifi.
"""
import ast, difflib, json, os, queue, threading, time

STATE_PATH = "state.json"
BUGGY_SRC = "workflow.buggy.py"

# What each verdict flag means in plain English, for the findings feed. Same defects the
# fixer is told about in REMEDIATION -- phrased for a human reading a projector.
# {label}/{cache}/{err} are filled from the fixture, so a mailer run does not describe
# its defects in the refund bot's vocabulary.
FINDING = {
    "CORRUPT":   ("truncated cache consumed",
                  "{cache} is unparseable on disk after the run -- the previous crash "
                  "tore it mid-write and this run did not quarantine it."),
    "DUPLICATE": ("{label} written twice",
                  "more than one {label} record in the durable log: the retry did not "
                  "notice the id was already there."),
    "GARBAGE":   ("invalid {label} written",
                  "a {label} record has a field that is not an https:// URL -- a malformed "
                  "tool response was committed to the log as if it were real."),
    "LOST":      ("failed silently",
                  "no {label} record and no {err}: the run gave up without doing the work "
                  "and without telling anyone."),
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


def diff_lines(prev, new, context=3, cap=400):
    """Unified diff of the previous file against the patch, plus what it did to the
    functions. A whole-file dump does not show what the model changed; this does."""
    d = list(difflib.unified_diff(prev.splitlines(), new.splitlines(),
                                  lineterm="", n=context))[2:]   # drop the ---/+++ header
    add = sum(1 for l in d if l[:1] == "+")
    rem = sum(1 for l in d if l[:1] == "-")
    if len(d) > cap:
        d = d[:cap] + [f"@@ ... {len(d) - cap} more diff lines @@"]
    b, a = _top_defs(prev), _top_defs(new)
    return dict(text="\n".join(d), added=add, removed=rem,
                fn_added=[k for k in a if k not in b],
                fn_changed=[k for k in a if k in b and a[k] != b[k]],
                fn_removed=[k for k in b if k not in a])


def findings_for(r, f=None):
    """Every defect the classifier found in one cell, with its on-disk evidence."""
    import fixture as fxmod
    f = f or fxmod.default()
    words = dict(label=f.record_label, err=f.err_file,
                 cache=(f.parseable[0] if f.parseable else "the cache"))
    out = []
    for flag in r["verdict"].split("+"):
        if flag not in FINDING:
            continue
        title, why = (t.format(**words) for t in FINDING[flag])
        out.append(dict(machine=r["machine"], transport=r["transport"], flag=flag,
                        title=title, why=why, refunds=r.get("refunds", 0),
                        exit_code=r.get("exit_code"),
                        evidence=(r.get("autopsy") or "").strip()))
    return out


class Live:
    """Coalescing background pusher: the sweep never blocks on an upload, and a slow link
    drops stale frames instead of queueing them."""

    def __init__(self, sandbox=None, resume=False, f=None):
        self.sb = sandbox
        import fixture as fxmod
        self.f = f or fxmod.default()
        # what the next patch is diffed against: the buggy original, then each patch in turn
        self.prev_src = None
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
        self.state["findings"] = [x for r in results for x in findings_for(r, self.f)]
        self.push(phase=phase, done=False)

    def cell_done(self, r, ms):
        for c in self.state["cells"]:
            if c["machine"] == r["machine"] and c["transport"] == r["transport"]:
                c.update(status="done", verdict=r["verdict"], colour=r["colour"],
                         refunds=r.get("refunds", 0), exit_code=r.get("exit_code"),
                         autopsy=r.get("autopsy"), ms=ms)
        if not self.frozen:
            self.state["findings"].extend(findings_for(r, self.f))
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

    def buggy_src(self):
        """The fixture's own workflow is the buggy original -- fixer.py never overwrites it.
        workflow.buggy.py is a copy left by whichever fixture ran last, so it is only the
        fallback: diffing a refund patch against the mailer's source is nonsense."""
        for path in (self.f.workflow, BUGGY_SRC):
            try: return open(path).read()
            except Exception: pass
        return ""

    def round_patch(self, n, patch, author, latency=None, tokens=None):
        if self.prev_src is None:
            self.prev_src = self.buggy_src()
        d = diff_lines(self.prev_src, patch) if patch else None
        self._round(n).update(status="patched", patch=patch, latency=latency,
                              tokens=tokens, diff=d)
        if patch:
            self.prev_src = patch
        self.state["fix"]["author"] = author
        self.attach_code(patch)
        self.push()

    def attach_code(self, patch):
        """Hang the buggy lines and the patched lines off each finding, so hovering a
        defect shows what it did AND what changed to stop it doing that."""
        try:
            focus = code_focus(self.buggy_src(), patch)
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
