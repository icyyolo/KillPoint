# Kill Point — agent reliability across the transport × machine fault matrix

> ## STATUS — BUILT. All four gates passed.
>
> | Gate | Result |
> |---|---|
> | **GATE 1** `(crash4, ok)` → red | `CORRUPT` on a real sandbox, 8.6 s |
> | **GATE 2** 9 cells, ≥2 red, interaction compound | 9 cells / **8 red**, `(crash5,bad_url)` = `DUPLICATE+GARBAGE` with **2 REFUND records**. Whole sweep: **8.5 s** — same wall-clock as one cell |
> | **GATE 3** all green, `(clean,ok)`==CLEAN, REFUNDs ≥ 1 | **all 9 green**, `CLEAN`, 3 REFUNDs. Patch authored by **nosana** in **1 round** |
> | **GATE 4** public preview URL | https://3000-o5wmeclvdgjpwe08.daytonaproxy01.eu (regenerate with `python report.py --refresh`) |
> | **DoD 5** real `sandbox.stop()` | state survived machine death: **True** (stop 4.2s / start 1.9s, 2 REFUNDs after restart) |
> | **GENERALIZATION** 5 more fixtures | sweep.py/fixer.py now fixture-driven. 6 fixtures (refund_bot + file_backup, scheduler, indexer, mailer, order_processor) all pass `localsim.py` locally: before = red with `DUPLICATE+GARBAGE` interaction, after = all green. See `fixtures/README.md` |
>
> Nosana endpoint: `https://5fjbewHVg9YvbHL1fMzyrZGfod7pW19iGCYa18zeSR5P.node.k8s.prd.nos.ci/v1` model `fixer`.
> Everything runs from `.venv/bin/python`. Remaining: rehearse, backup video.
>
> **Deviations from this plan, all forced by measurement — see "Open questions" at the end
> of the session notes:** seed uses the original buggy agent so before/after compare against
> identical machine states; the fix spec gained a "quarantine the corrupt cache" clause;
> the fixer receives autopsies + per-verdict remediation, not bare verdict labels.


## Context

Daytona HackSprint Singapore, 29 Aug 2026. Demos at 16:30, 2 minutes per team. Four metrics:
Completeness (MVP proven *inside the Daytona sandbox runtime during live demos*), Innovation
(multi-agent feedback loops, explicitly not prompt wrappers), Real-World Fit (developer
bottlenecks / operational constraints), Sponsor Usage (clever integration with Daytona). An AI
reads the submission in round one, so the README is a scored artifact.

**The problem.** Agent reliability is tested along two axes, separately, by different people:

- **Transport faults** — malformed JSON, bad URLs, timeouts, schema drift. Well covered:
  AgentChaos, agent-chaos, Failing Tools, RobustBench-TC (`Timeout, AuthErr, 5xxErr, RateLim,
  Malform, SchemD`), ToolMisuseBench. All inject at the HTTP layer, on a **clean machine**.
- **Machine faults** — the process died mid-write, the disk is full, a previous run left a
  stale lock or a truncated cache. Essentially untested for agents, because you need a real
  machine to produce them.

Nobody tests the **cross product**. The nastiest real-world failures live in the interaction
cells: a malformed response triggers a retry, but a previous crash left a half-written ledger,
so the retry double-refunds. Neither axis alone finds that.

**Why Daytona is required, not decorative.** Transport faults are mockable in-process — that
half is table stakes. Machine faults are not: a crash is defined by state surviving process
death, and you cannot have that without a machine. The matrix needs N disposable machines,
seeded into specific dirty states, killable mid-write, autopsied afterwards, run in parallel.

**Constraints.** Solo. 4 hours. Python. Nosana credits granted, API key not yet created.
**No Kimi credits** — Nosana is the only LLM, so the fallback cannot be another model; it is a
deterministic rule-based fixer.

---

## The matrix

Rows = machine state (needs Daytona). Columns = transport fault (env var, no proxy).

|                | `ok` | `malformed_json` | `bad_url` |
|----------------|------|------------------|-----------|
| **clean**      |      |                  |           |
| **crash@4** (truncated cache) | | |          |
| **crash@5** (half-written ledger) | | |      |

9 cells, 9 sandboxes, all in parallel. That fan-out *is* the metric-04 story.

**Why 9 and not 12.** Measured at T+0:15, not assumed: the Daytona tier caps *total CPU* at
10, and `Resources.cpu` is an `int` with no fractional values, so the hard ceiling is **10
concurrent sandboxes** (`DaytonaBadRequestError: Total CPU limit exceeded. Maximum allowed:
10`). The `read_only` row was dropped rather than batching the sweep into two waves, because
the parallel fan-out is the metric-04 claim and a batched sweep does not make it. It was also
the cheapest row to lose: it was predicted `LOST / LOST / LOST` — the only row with no
variation across the transport axis, so it carried no interaction signal. Nine cells leaves
one slot free for the `report.py` web-server sandbox.

**Verdicts** (deterministic — no LLM in the classifier):

| Verdict | Meaning | Colour |
|---|---|---|
| `CLEAN` | one REFUND, valid cache, valid receipt URL | green |
| `HONEST_FAIL` | no REFUND, error written — via `error.txt`, or a `HONEST_FAIL:` stderr marker when the working dir is unwritable (`read_only`) — the *good* failure | green |
| `DUPLICATE` | more than one REFUND | yellow |
| `CORRUPT` | unparseable cache consumed as valid | red |
| `GARBAGE` | invalid receipt URL written to the ledger as if real | red |
| `LOST` | no REFUND, no error, silent | red |

Flags **compose**. A cell that duplicates a refund *and* writes a broken receipt reports
`DUPLICATE+GARBAGE` and is coloured by its worst flag (red). Collapsing that to a single
label is what makes interaction cells invisible, so the classifier never does it.

**Expected before-fix matrix** (derived from the fixture, not aspirational):

|            | `ok` | `malformed_json` | `bad_url` |
|------------|------|------------------|-----------|
| clean      | `CLEAN` 🟢 | `LOST` 🔴 | `GARBAGE` 🔴 |
| crash@4    | `CORRUPT` 🔴 | `CORRUPT` 🔴 | `CORRUPT` 🔴 |
| crash@5    | `DUPLICATE+GARBAGE` 🔴 | `GARBAGE` 🔴 | `DUPLICATE+GARBAGE` 🔴 |

One green, eight red. `LOST` survives the dropped row via `clean × malformed_json`, so all
six verdict classes still appear across the before/after pair.

`crash@5 × ok` is compound, not plain `DUPLICATE`: the seeded half-write lands before `://`,
so the torn record is itself a garbage line and the classifier flags both. The `crash@5` row
is the whole argument. `clean × bad_url` is `GARBAGE`. `crash@5 × ok` is
`DUPLICATE+GARBAGE`. Their intersection, `crash@5 × bad_url`, is **both at once** — a second
refund whose receipt is a broken URL, appended onto a torn record, with nothing written to
`error.txt`. Neither axis alone produces that cell.

---

## Architecture

```
sweep.py (local orchestrator)
  └─ for (machine_state, transport_fault) in 3 × 3, in parallel:
       daytona.create()                       one sandbox per cell
       upload workflow.py
       seed the machine state                 crash mid-write / chmod 444 cwd / clean
       exec  FAULT=<t> python workflow.py     restart on the dirty machine
       autopsy ledger.txt + cache.json        ← deterministic classifier
       verdict
  ├─ fixer.py  patches workflow.py   (model served on Nosana; rule fallback)
  ├─ re-sweep the same 9 cells       → all green
  └─ report.py heatmap → public Daytona preview URL
```

Two agents in the loop: **classifier** (deterministic, judges machine state) and **fixer**
(LLM, proposes the patch). The loop closes because the re-sweep re-runs all 9 cells.

---

## Files

```
killpoint/
  .env                 NOSANA_API_KEY, NOSANA_MARKET, FIXER_BASE_URL, FIXER_MODEL, DAYTONA_API_KEY
  requirements.txt     daytona, openai, requests
  nosana_deploy.py     create vLLM deployment, poll, print endpoint URL
  workflow.py          fixture agent workflow — runs INSIDE the sandbox, stdlib only
  workflow.fixed.py    rule-fixer template: same workflow corrected to the fix spec
  sweep.py             orchestrator: 2D fan-out, seeding, autopsy, classify → results.json
  fixer.py             LLM patch via Nosana; rule fallback = workflow.fixed.py template
  report.py            heatmap HTML (reads results.json) + http server (inside a sandbox)
  README.md            judged artifact — budget real time
```

`workflow.py` is **stdlib only** (`json`, `os`, `sys`). No pip install inside sandboxes, so no
snapshot pre-warming — removes a whole class of setup risk.

---

## Timeline (240 min, solo, hard gates)

| Time | Step | Verify |
|---|---|---|
| 0:00–0:15 | **Nosana first.** API key exists — deploy vLLM now, leave it warming | deployment id returned |
| 0:15–0:25 | Daytona full probe (4 unknowns, below) | all four probe checks pass |
| 0:25–0:55 | `workflow.py`: 6 steps, 2 bugs, `FAULT` + `CRASH_AT` env vars | runs locally; each FAULT value changes behaviour |
| 0:55–1:25 | `sweep.py`: single cell (crash@4, `ok`) | **GATE 1: one RED verdict** |
| 1:25–2:00 | Full 3×3 fan-out in parallel | **GATE 2: 9 cells, ≥2 reds, crash5 row compound** |
| 2:00–2:15 | Nosana health check; rule fixer → `workflow.fixed.py` template | `/v1/models` responds, or template passes the corrected matrix locally |
| 2:15–2:45 | `fixer.py` LLM upgrade; feedback loop (≤3 rounds) re-sweeps | **GATE 3: all 9 green, round count logged** |
| 2:45–3:10 | `report.py` before/after heatmaps on preview URL | **GATE 4: public link opens** |
| 3:10–3:25 | Hero case: one real `sandbox.stop()` mid-run | machine actually killed |
| 3:25–3:45 | **README** — mapped to the four metrics | written, file paths named |
| 3:45–3:55 | Backup video, rehearse 2 min aloud once | timed under 2:00 |
| 3:55–4:00 | Buffer | — |

**Cut order:** hero machine-kill → LLM fixer (keep rule fixer) → matrix shrinks to 3×2 (drop `malformed_json`) →
heatmap degrades to a terminal table.
**Never cut:** the parallel fan-out (metric 04) and at least one red cell (the demo).

---

## Step 1 — Nosana (T+0:00, before any code)

API key is already created and in `.env`. Two steps remain:

1. Pick a market with free nodes at `explore.nosana.com/markets`; note its address. Have a
   second address ready in case the first has no GPU capacity.
2. Deploy vLLM — port **8000**, OpenAI-compatible, `--served-model-name` sets the model id.
   Fire this before writing any other code, then leave it alone until the T+2:00 health check.

```python
# see nosana_deploy.py — schema below was READ BACK from a live deployment via
# GET /api/deployments/<id>/revisions, not guessed
API    = "https://dashboard.k8s.prd.nos.ci/api"      # api.nosana.com does NOT exist
MARKET = "97G9NnvBDQ2WpKu6fasoMsAKmfj63C9rhysJnkeWodAf"   # nvidia-4090, 24GB
MODEL  = "Qwen/Qwen2.5-Coder-7B-Instruct"

body = {
  "name": "killpoint-fixer", "market": MARKET,
  "timeout": 360, "replicas": 1, "strategy": "INFINITE",
  "job_definition": {
    "version": "0.1", "type": "container",
    "meta": {"trigger": "api", "system_requirements": {"vram_total_mb": 24000}},
    "ops": [{"type": "container/run", "id": "vllm", "args": {
        "image": "docker.io/vllm/vllm-openai:v0.10.2",   # pinned; :latest is not pre-cached
        "gpu": True, "expose": 8000,
        "cmd": ["--model", MODEL, "--served-model-name", "fixer",
                "--max-model-len", "8192", "--gpu-memory-utilization", "0.90",
                "--host", "0.0.0.0", "--port", "8000"]}}]
  }
}
```

**Verified against the live API (T+0:05):**

| Plan said | Reality |
|---|---|
| `https://api.nosana.com` | **NXDOMAIN.** Real base is `https://dashboard.k8s.prd.nos.ci/api` |
| `vllm/vllm-openai:latest` | Market's `required_images` pins **`docker.io/vllm/vllm-openai:v0.10.2`** — use it |
| `"cmd": "<string>"` | `cmd` is a **list of strings** |
| `strategy: "SIMPLE"` | Use **`INFINITE`** — SIMPLE expires mid-hackathon |
| `gpu` / `expose` field names uncertain | **Confirmed correct**: `"gpu": true`, `"expose": 8000` |
| endpoint host `...prd.nos.ci` | Confirmed, `.prd.` not `.prod.` |

**Verify:** `curl $FIXER_BASE_URL/v1/models` returns a model list. `status: RUNNING` is **not**
sufficient — the container is up long before vLLM finishes loading weights. Only a 200 from
`/v1/models` counts as live.
first. If `/v1/models` is silent at T+2:15, ship the rule fixer and present Nosana honestly as
"deployed, still warming" — never as used.

---

## Step 1b — Daytona probe (RUN, T+0:15) — results

Run by `probe.py`. Three assumptions held, one did not.

| # | Check | Result |
|---|---|---|
| 1 | `create()` + `code_run` | **PASS** (3.0s). But `create()` takes a **params object**, not kwargs: `d.create(CreateSandboxFromSnapshotParams(auto_delete_interval=0))`. `d.create(auto_delete_interval=0)` raises `TypeError` |
| 2 | `upload_file` CWD | **PASS** — relative uploads land in `/home/daytona`, the same dir `process.exec` runs from |
| 3 | preview URL | **PASS** (4.5s) — `create_signed_preview_url(3000, expires_in_seconds=3600)` served the uploaded page over public HTTPS. GATE 4 de-risked |
| 4 | 12 concurrent `create()` | **FAIL — 9/12.** `Total CPU limit exceeded. Maximum allowed: 10` |

`SessionExecuteRequest` **is** exported from the `daytona` package — import path confirmed.
SDK version 0.207.0.

**Environment:** the system Python 3.10 had an `anyio` / `httpx_ws` conflict
(`module 'anyio' has no attribute 'AsyncContextManagerMixin'`) that made `from daytona import
Daytona` fail outright. Everything runs from `.venv/` — use `.venv/bin/python`, never
`python3`.

**Consequence:** matrix reshaped to 3×3. See "Why 9 and not 12" above. If the ceiling ever
drops below 9, shrink to 3×2 (drop `malformed_json`) rather than batching the fan-out.

**Also learned:** `resources` (with a `disk` field) exists only on
`CreateSandboxFromImageParams`, not on the snapshot params used here. Irrelevant now that the
disk-pressure row is gone, but it is the lever if that row ever comes back.

**Tear down all probe sandboxes** before the sweep so they do not eat the concurrency budget
— at a ceiling of 10 a single leaked sandbox costs a matrix cell. `probe.py` deletes its own.

---

## Step 2 — `workflow.py` (T+0:25)

Two fault inputs: `FAULT` (transport) and `CRASH_AT` (machine). Two deliberate bugs. This is a
test fixture — the bugs are the classic ones these techniques exist to find.

```python
import json, os

STATE, CACHE, LEDGER, ERR = "state.json", "cache.json", "ledger.txt", "error.txt"
CRASH_AT = int(os.environ.get("CRASH_AT", "0"))
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
    if CRASH_AT == 4: os._exit(1)                 # ← dies INSIDE the write: truncated JSON
    f.write(p[h:]); f.close()

def refund(s):                                    # BUG 2: side effect before commit
    line = f"REFUND {s['id']} {s['refund']} {s.get('receipt','')}\n"
    h = len(line) // 2
    f = open(LEDGER, "a")
    f.write(line[:h]); f.flush()                  # half a record, no trailing newline
    if CRASH_AT == 5: os._exit(1)                 # ← genuinely half-written ledger
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
    for i, (n, f) in enumerate([("fetch",fetch),("validate",validate),("compute",compute),
                                ("cache",cache),("refund",refund),("mark_done",mark_done)], 1):
        step(i, n, f, s)

main()
```

**Verify:**

- clean run → exactly one REFUND with a valid `https://` receipt
- `FAULT=bad_url python workflow.py` → ledger contains `htp:/broken url` (`GARBAGE`)
- `CRASH_AT=4 python workflow.py` → **`cat cache.json` is unparseable** (this is the GATE 1
  precondition; if the file parses, the kill is in the wrong place — do not proceed)
- `CRASH_AT=5 python workflow.py` → `ledger.txt` ends mid-record with **no trailing newline**;
  the restart appends onto that same line, so the two records run together — that
  concatenation is what makes the `crash@5` row interact with the transport column

---

## Step 3 — `sweep.py` (T+0:55 single cell, T+1:25 full matrix)

```python
import asyncio, json, re
from daytona import Daytona, CreateSandboxFromSnapshotParams   # create() takes a params OBJECT

daytona = Daytona()
SRC = open("workflow.py", "rb").read()

MACHINE   = ["clean", "crash4", "crash5"]        # 3x3 = 9; tier ceiling is 10 concurrent
TRANSPORT = ["ok", "malformed_json", "bad_url"]   # "ok" everywhere: code, heatmap, README

def seed(sb, state):
    if state == "crash4":    sb.process.exec("CRASH_AT=4 python workflow.py", timeout=60)
    elif state == "crash5":  sb.process.exec("CRASH_AT=5 python workflow.py", timeout=60)

RED = {"CORRUPT", "GARBAGE", "LOST"}

def classify(sb, run_output=""):
    """Returns (verdict, colour). Flags are COMPOUND: a cell that is both a duplicate
    and a garbage write reports DUPLICATE+GARBAGE, because collapsing it to one label
    is exactly what hides the interaction cells this project exists to find."""
    def read(p):
        try: return sb.fs.download_file(p).decode()
        except Exception: return None
    ledger, cache, err = read("ledger.txt"), read("cache.json"), read("error.txt")
    # Per-RECORD validation, not whole-ledger: split on the REFUND token so a torn record
    # that the next append concatenates onto still counts as its own (garbage) record.
    records = ledger.split("REFUND ")[1:] if ledger else []
    n = len(records)
    cache_ok = True
    if cache:
        try: json.loads(cache)
        except Exception: cache_ok = False
    bad_receipt = any(not re.search(r"https://\S+", r) for r in records)

    flags = []
    if n > 1:                  flags.append("DUPLICATE")
    if not cache_ok:           flags.append("CORRUPT")
    if n >= 1 and bad_receipt: flags.append("GARBAGE")

    if flags:
        colour = "red" if RED & set(flags) else "yellow"
        return "+".join(flags), colour
    if n == 1: return "CLEAN", "green"
    # Honest failure: error.txt, or the HONEST_FAIL stderr marker for cells (read_only)
    # where the working directory is unwritable and no marker file can be written at all.
    if err or "HONEST_FAIL" in run_output: return "HONEST_FAIL", "green"
    return "LOST", "red"

async def cell(machine, transport):
    def run():
        sb = daytona.create(CreateSandboxFromSnapshotParams(auto_delete_interval=0))
        sb.fs.upload_file(SRC, "workflow.py")
        seed(sb, machine)
        proc = sb.process.exec(f"FAULT={transport} python workflow.py", timeout=60)
        autopsy = sb.process.exec("cat cache.json; cat ledger.txt; ls -l .", timeout=30).result
        out = (proc.result or "") + (getattr(proc, "stderr", "") or "")
        verdict, colour = classify(sb, out)
        return machine, transport, verdict, colour, autopsy
    return await asyncio.to_thread(run)            # daytona.create is sync — must offload

async def main():
    results = await asyncio.gather(*[cell(m, t) for m in MACHINE for t in TRANSPORT])
    with open("results.json", "w") as f:           # report.py consumes this; keep one per sweep
        json.dump([r[:4] for r in results], f, indent=2)
    return results
```

**GATE 1 (T+1:25):** `(crash4, ok)` → `CORRUPT`. Precondition, check it first: after the
seed, `cat cache.json` inside the sandbox must be unparseable. If it parses, the kill is
landing after the write completes — fix `workflow.py:cache` before touching `sweep.py`. If it
still doesn't reproduce, stop tuning and simplify the fixture; one red is enough.

**GATE 2:** 9 verdicts, ≥2 red, and **both `(crash5, ok)` and `(crash5, bad_url)` report the
compound `DUPLICATE+GARBAGE`** — the bad_url cell is the demo. A bare `DUPLICATE` at
`(crash5, ok)` means the classifier matched the ledger end again (the `\s*$` bug) and missed
the torn record — fix `classify`. Note there is no yellow cell in this fixture: a crash always
tears its first record, so every crash5 cell also carries `GARBAGE`; a plain yellow
`DUPLICATE` needs both records intact, which a crash cannot produce.

---

## Step 4 — `fixer.py` (T+2:00 rule, T+2:15 LLM)

**The loop closes more than once.** One patch is not a feedback loop. After a patch, the
re-sweep verdicts are fed back into the fixer; any cell still not green triggers another
patch. Hard stop at T+2:45 (the report gate) or after N=3 rounds, whichever comes first. The
run log records the round count — that count is what the demo shows as the loop.

**The fix spec** (both paths must satisfy it — each item is checkable in the patched file and
re-verified by the re-sweep):

1. **Cache — atomic + guarded.** Read inside `try/except` (a corrupt cache is ignored, never
   consumed as valid); write `cache.tmp` then `os.replace`, so a crash never leaves a
   truncated cache. → kills `CORRUPT`
2. **Ledger — REBUILT, not appended-to.** At startup, before any fetch, read the ledger,
   parse every record, validate each receipt against `^https://`, drop torn records and
   duplicate ids, and rewrite the whole file atomically. "Check for the id before appending"
   is NOT enough: the seeded crash@5 ledger already holds a torn record, and a skipped append
   leaves that garbage line on disk, which the classifier still flags. The rebuild must run
   before fetch so the failure path also starts from a clean ledger. → kills `DUPLICATE` +
   `GARBAGE`
3. **Tool output validated before use.** Receipt must match `^https://` or the step raises.
   → kills `GARBAGE`
4. **Failures are loud.** Any handled failure writes `error.txt`. When the working directory
   is unwritable (the `read_only` row) there is nowhere to write it — print
   `HONEST_FAIL: <reason>` to stderr and exit nonzero; the classifier accepts either channel.
   → kills `LOST`

**Rule fixer first — 10 minutes, guarantees a demo exists.** Its output is a pre-written,
locally-tested corrected workflow (`workflow.fixed.py`). Selecting it is mechanical; asserting
it took is the point — the template must already pass the corrected matrix locally:

```python
REQUIRED = ["os.replace", "HONEST_FAIL", "try:", r"^https://"]
def rule_fix(verdicts):
    src = open("workflow.fixed.py").read()
    missing = [m for m in REQUIRED if m not in src]
    assert not missing, f"workflow.fixed.py drifted: {missing}"   # fail loudly now, not at GATE 3
    return src
```

**Then upgrade to the Nosana-served model** — same OpenAI client, two env vars:

```python
from openai import OpenAI
client = OpenAI(api_key="nosana", base_url=os.environ["FIXER_BASE_URL"])   # .../v1

def llm_patch(source, verdicts, round_n):
    r = client.chat.completions.create(
        model=os.environ.get("FIXER_MODEL", "fixer"), temperature=0,
        messages=[{"role": "user", "content":
            f"Round {round_n}. This workflow must satisfy the fix spec: cache read in "
            f"try/except and written via tmp + os.replace; the LEDGER MUST BE REBUILT at "
            f"startup from validated records (drop torn records and duplicate ids, rewrite "
            f"atomically — do not just skip appending by id); receipts validated against "
            f"^https:// before use; any failure writes error.txt OR prints "
            f"'HONEST_FAIL: <reason>' to stderr. Cells still failing: {verdicts}. "
            f"Return ONLY the complete file.\n\n{source}"}])
    return r.choices[0].message.content
```

**The loop:**

```python
def fix_loop():
    for round_n in range(1, 4):
        if round_n == 1:
            src = rule_fix(None) if LLM_DOWN else open("workflow.py").read()
        else:
            src = llm_patch(src, failing(verdicts), round_n)
        open("workflow.py", "w").write(src)
        verdicts = sweep_and_save("results_after.json")      # re-seeds all 9 cells
        if all(green(v) for v in verdicts):
            log(f"FIXED in round {round_n}"); return
    raise SystemExit("GATE 3 not met after 3 rounds")        # template is the floor; a fail
    # here means the fixture/spec is wrong — stop and inspect, don't ship a broken patch
```

**GATE 3:** within ≤3 feedback rounds, re-sweep all 9 cells → every cell `CLEAN` or
`HONEST_FAIL` (for `read_only`, `HONEST_FAIL` via the stderr marker), **plus both of:**

1. `(clean, ok)` is exactly `CLEAN` — not `HONEST_FAIL`
2. total REFUND count across all 9 cells ≥ 1

Without those two, "all green" is trivially winnable by a fixer that wraps everything in
`try/except`, writes `error.txt` unconditionally and refunds nobody. That patch would score
12/12 green while deleting the product. Assert it in the sweep, not by eye.

Keep both matrices — before and after are the demo.

---

## Step 5 — `report.py` (T+2:45)

Plain HTML tables, background colours, no framework. Two matrices side by side — read
`results.json` (before) and `results_after.json` (after), both persisted by `sweep.py`; report
never re-runs a sweep. Generate, upload, serve from inside a sandbox, expose:

```python
sb.fs.upload_file(html.encode(), "report.html")
sb.process.create_session("web")
sb.process.execute_session_command("web",
    SessionExecuteRequest(command="python -m http.server 3000", run_async=True))
url = sb.create_signed_preview_url(3000, expires_in_seconds=3600).url
```

**GATE 4:** open the URL in a browser. The signed URL expires after 3600 s — generated at
T+2:45 it dies ~T+3:45, before the 16:30 demo. **Regenerate off-stage at ~16:20** (re-run only
the `create_signed_preview_url` call on the same sandbox) and paste the fresh URL into your
notes. Never create it on stage. Set `auto_stop_interval` generously so the box does not
idle-stop while you queue.

---

## Step 6 — hero case, real machine kill (T+3:10, optional)

The sweep uses `os._exit(1)` — a genuine dirty process death on a real filesystem. For one
cell, prove the stronger claim: launch with `run_async=True`, `sb.stop()` mid-flight,
`sb.start()`, inspect, restart.

Say it plainly on stage: *"the sweep kills the process; this one kills the machine."* Do not
imply the sweep does machine kills.

---

## Step 7 — README (T+3:25, do not skip)

An AI reads this first. Structure against the four metrics:

- **Problem** — two fault axes, tested separately by different communities; the interaction
  cells are untested
- **What shipped** — 9-cell matrix, six verdict classes, before/after, live URL
- **Daytona, with file paths** — parallel fan-out (`sweep.py:cell`), one isolated machine per
  cell, state seeding (`sweep.py:seed`), post-mortem via `sandbox.fs.download_file`,
  restart-in-same-machine, real `sandbox.stop()`, preview URL (`report.py`),
  `auto_delete_interval=0`
- **Nosana** — `nosana_deploy.py`, vLLM on port 8000, model id, endpoint host, and the patch
  it authored
- **Prior art, honestly** — AgentChaos, agent-chaos, Failing Tools, RobustBench-TC
  (`Malform`/`SchemD` are its own listed perturbations), ToolMisuseBench. All inject at the
  HTTP layer on clean machines. Our transport axis is not novel and we say so; the machine
  axis and the cross product are the contribution.
- **Limits** — transport faults are injected at the tool-function boundary, not through a
  network proxy; a deliberately deceptive agent could restore file hashes; this targets
  ordinary failure paths, not adversarial cover-up

---

## Demo script (2 min)

1. "Agents issue refunds now. Everyone tests bad API responses. Nobody tests bad responses
   arriving on a machine where the last run already died." — 15s
2. The matrix, empty — 10s
3. Before run: one green, the rest red, worst cells compound — 20s
4. Zoom the interaction cell `(crash5, bad_url)`: a torn ledger record plus a broken receipt
   URL → a second refund whose receipt is garbage, no error anywhere. Compound verdict
   `DUPLICATE+GARBAGE` — 30s
5. Fixer (model served on Nosana) patches, loops, re-sweeps to all green. The after-matrix is
   pre-computed from the persisted re-sweep (`results_after.json`) — never a live 9-box
   sweep on stage — 25s
6. "9 machines in parallel, each seeded into a different broken state. Only Daytona does
   that." — 10s

---

## Definition of done

1. **9-cell matrix** (3 machine states × 3 transport faults) run in **parallel sandboxes**,
   with ≥2 red before the fix, and **every cell green after** — where green additionally
   requires `(clean, ok) == CLEAN` and a non-zero total REFUND count, so a fixer that fails
   everything honestly cannot pass. (No yellow cell exists in this fixture: a crash always
   tears its first record, so every crash5 cell compounds to red.)
2. At least one red is an **interaction cell** — a cell whose verdict differs from both its
   row-only and its column-only neighbours. Target: `(crash5, bad_url)` →
   `DUPLICATE+GARBAGE`, against `GARBAGE` for `(clean, bad_url)` and `DUPLICATE+GARBAGE`
   for `(crash5, ok)`.
3. The patch is authored by a model **served on Nosana**, endpoint host and model id printed
   in the run log. (Degraded but acceptable: rule fixer, Nosana shown as still warming, stated
   honestly.)
4. Before/after heatmaps on a **public Daytona preview URL**, openable from any browser.
5. ≥1 cell killed via a real `sandbox.stop()`.
6. README maps every claim to a file path and names the prior art on the transport axis.

---

## Known risks

| Risk | Mitigation |
|---|---|
| vLLM cold start never completes | Deploy at T+0:00; rule fixer is the floor; never claim Nosana was used if it wasn't |
| No GPU capacity in chosen market | Check `explore.nosana.com/markets` first; second market address ready |
| Concurrency ceiling | **Measured: 10 total CPU, 1 CPU min per sandbox = 10 concurrent sandboxes.** Matrix is 9 so one slot stays free for the report server. A leaked sandbox costs a cell — `probe.py` and `sweep.py` delete their own. Ask the Daytona booth for a tier bump to restore the 4th row |
| ~~`read_only` seeding~~ (row dropped at 3×3) | `chmod 444 .` is fast and reliable; the seed asserts a write probe fails and the sweep aborts loudly if it doesn't. "Fill the disk" was dropped as the primary seed: leaving even 1 KB free lets the ~300-byte workflow files write fine, and ~0 bytes free is unreachable while the shell lives. If a judge asks about disk-full, say it honestly: same fault class, `read_only` is the reproducible version |
| Sandbox idle-stops during the demo queue | Generous `auto_stop_interval`; the signed URL expires after 3600 s — regenerate at ~16:20 and re-open the page before queueing |
| Judge says transport faults are prior art | Agreed in the README, out loud, first — the cross product is the claim |
| Venue wifi dies on stage | Backup video at T+3:45 |
