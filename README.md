# Kill Point

**Agent reliability across the transport × machine fault matrix.**

Built for Daytona HackSprint Singapore. One sentence: *agent reliability is tested along two
axes separately, and nobody tests the cross product — so we built the cross product, ran it on
real disposable machines, and had an LLM repair what it found.*

The live report is a Daytona sandbox serving a signed preview URL. The URL expires after
3600 s; the current one is always in [`report_url.json`](report_url.json), and
`python report.py --refresh` mints a new one on the same box.

---

## Table of contents

- [The problem](#the-problem)
- [What shipped](#what-shipped)
- [The interaction cell](#the-interaction-cell)
- [The six verdicts](#the-six-verdicts-deterministic--no-llm-in-the-classifier)
- [Daytona — what it does here](#daytona--what-it-does-here-with-file-paths)
- [Nosana — the fixer](#nosana--the-fixer)
- [The feedback loop](#the-feedback-loop-in-detail)
- [The live report page](#the-live-report-page)
- [It generalizes: six fixtures](#it-generalizes-six-fixtures-one-harness)
- [Writing a new fixture](#writing-a-new-fixture)
- [Repo map](#repo-map)
- [Install and run](#install-and-run)
- [Demo runbook](#demo-runbook)
- [Evidence files](#evidence-files--what-is-committed-and-why)
- [What is measured vs. what is architectural](#what-is-measured-vs-what-is-architectural)
- [Limits, stated honestly](#limits-stated-honestly)

---

## The problem

Agent reliability is tested along two axes, separately, by different people.

**Transport faults** — malformed JSON, bad URLs, timeouts, schema drift — are well covered.
AgentChaos, agent-chaos, Failing Tools, RobustBench-TC (whose own listed perturbations include
`Malform` and `SchemD`) and ToolMisuseBench all inject at the HTTP layer. **Our transport axis
is not novel and we do not claim it is.**

**Machine faults** — the process died mid-write, a previous run left a truncated cache or a
half-written ledger — are essentially untested for agents, because you need a real machine to
produce them. A crash is *defined* by state surviving process death. You cannot mock that
in-process: the thing under test is precisely what is left on disk after the process that was
supposed to clean up is gone.

**Nobody tests the cross product.** The nastiest real-world failures live in the interaction
cells: a malformed response triggers a retry, but a previous crash left a torn ledger record,
so the retry appends onto it and double-refunds with a receipt that is not a URL. Neither axis
alone finds that. **The machine axis and the cross product are the contribution.**

---

## What shipped

A 3×3 matrix — 3 machine states × 3 transport faults — run as **9 isolated Daytona sandboxes
in parallel**, autopsied by a deterministic classifier, then repaired by an LLM served on
Nosana in a feedback loop and re-swept until the whole matrix is green.

### Before — the buggy agent ([`workflow.py`](workflow.py))

|          | ok | malformed_json | bad_url |
|----------|----|----|----|
| clean    | `CLEAN` | `LOST` | `GARBAGE` |
| crash@4 <br><sub>truncated cache</sub> | `CORRUPT` | `CORRUPT` | `CORRUPT` |
| crash@5 <br><sub>half-written ledger</sub> | `DUPLICATE+GARBAGE` | `GARBAGE` | **`DUPLICATE+GARBAGE`** |

8 of 9 cells broken. Verbatim from [`results.json`](results.json).

### After — patched by a model served on Nosana

All 9 green: `CLEAN` on the `ok` column, `HONEST_FAIL` elsewhere — the *good* failure, where
the agent refuses to act and says so instead of quietly producing garbage.

```
clean    ok               CLEAN         green
clean    malformed_json   HONEST_FAIL   green
clean    bad_url          HONEST_FAIL   green
crash4   ok               CLEAN         green
crash4   malformed_json   HONEST_FAIL   green
crash4   bad_url          HONEST_FAIL   green
crash5   ok               CLEAN         green
crash5   malformed_json   HONEST_FAIL   green
crash5   bad_url          HONEST_FAIL   green
```

Took **2 feedback rounds**. Round 1's patch passed the local smoke test, was swept on 9 fresh
sandboxes, and still failed `all cells green`; the failures were fed back with their autopsies
and round 2 closed it. Recorded in
[`artifacts/fix_events.refund_7b.json`](artifacts/fix_events.refund_7b.json) — round 1:
17.3 s / 893 completion tokens, round 2: 16.9 s / 895 tokens.

### The fan-out is real

Nine parallel sandboxes complete the whole sweep in **8.5 s** — the same wall-clock as a
single cell (**8.6 s**, measured at GATE 1). That is the fan-out, measured, not asserted.

---

## The interaction cell

`crash@5 × bad_url` is the whole argument.

The seeded crash kills the process mid-`write()`, leaving a torn ledger record with no
trailing newline. On restart, the retry opens the ledger in append mode and writes onto that
same unterminated line. Result: the ledger holds **2 REFUND records**, the last receipt is
`htp:/broken url`, and **nothing is written to `error.txt`**.

Neither axis alone finds it:

| run | verdict | why |
|---|---|---|
| `clean × bad_url` | `GARBAGE` | one bad receipt, no duplication — the retry path never runs |
| `crash@5 × ok` | `DUPLICATE+GARBAGE` | duplication, but a valid receipt would not be caught by a URL check alone |
| **`crash@5 × bad_url`** | **`DUPLICATE+GARBAGE`** | both failures at once, **silently** — no error marker |

Verdicts **compose** (`DUPLICATE+GARBAGE`) precisely because collapsing them to one label is
what makes interaction cells invisible. A harness that reports a single worst-case label per
cell literally cannot see this row.

### And the machine kill is real

The sweep kills the *process* (`os._exit(1)` inside an unflushed write).
[`hero_kill.py`](hero_kill.py) kills the *machine* — a real `sandbox.stop()` landing while the
workflow holds the file open — and produces identical wreckage, which is what validates the
cheaper process kill used by the sweep:

```
mid-flight ledger:                    'REFUND 4471 250 https'
>>> sandbox.stop()                    stopped in 4.2s   (state: SandboxState.STOPPED)
>>> sandbox.start()                   started in 1.9s
ledger after machine kill + restart:  'REFUND 4471 250 https'   <- state survived machine death
after restarting the agent:           'REFUND 4471 250 httpsREFUND 4471 250 https://pay.example/r/4471'
REFUND records: 2                     <- double refund
```

Verbatim from [`hero_kill.json`](hero_kill.json). We say this plainly on stage:
**the sweep kills the process; the hero case kills the machine.**

---

## The six verdicts (deterministic — no LLM in the classifier)

`fixture.classify()` in [`fixture.py`](fixture.py) is a pure function of the files on disk.
No model, no judgment, no flakiness — the same wreckage always yields the same verdict.

| Verdict | Colour | Meaning |
|---|---|---|
| `CLEAN` | green | exactly one valid record |
| `HONEST_FAIL` | green | no record, but an error marker written — the good failure |
| `DUPLICATE` | yellow | more than one record of the durable effect |
| `CORRUPT` | red | a parseable state artifact left unparseable on disk |
| `GARBAGE` | red | a record whose content fails the fixture's validity rule |
| `LOST` | red | no record and no error — silent failure, the worst outcome |

`DUPLICATE` is yellow rather than red because it is a real defect that does not by itself
prove the run lied about what it did; it turns red the moment it compounds
(`DUPLICATE+GARBAGE`).

### GATE 3 — why "all green" is not the bar

A fixer that wraps everything in `try/except` and does no work at all scores nine green cells
while deleting the product. So [`sweep.gate3_ok()`](sweep.py) requires three things at once:

```python
"all cells green":     all(r["colour"] == "green" for r in results)
"(clean,ok) == CLEAN": by[("clean", "ok")]["verdict"] == "CLEAN"   # happy path still works
"total records >= 1":  sum(r.get("refunds", 0) for r in results) >= 1  # work still happens
```

The second and third checks are what stop the trivial cheat.

---

## Daytona — what it does here, with file paths

Machine faults are not mockable in-process. Every one of these needs a real, disposable
machine:

| Capability | Where |
|---|---|
| **Parallel fan-out**, 9 sandboxes at once | [`sweep.py`](sweep.py) → `cell()`, `run_sweep_fx()` |
| **One isolated machine per cell** | `Daytona().create(CreateSandboxFromSnapshotParams(...))` |
| **State seeding** — crash the agent mid-write, keep the disk | [`sweep.py`](sweep.py) → `seed()`, with assertions that the seed actually took |
| **Restart on the dirty machine** | `sweep.py:cell()` — seed, then re-upload and re-run on the same box |
| **Post-mortem** | `sandbox.fs.download_file()` for `ledger.txt`, `cache.json`, `error.txt` |
| **Real machine kill** | [`hero_kill.py`](hero_kill.py) — `sandbox.stop()` mid-write, then `start()` |
| **Public preview URL** | [`report.py`](report.py) — `create_signed_preview_url(3000, expires_in_seconds=3600)` |
| **A box that outlives the demo** | `auto_stop_interval=0` on the report sandbox — never idle-stop |

### The seed assertion matters

A seeded crash that lands one instruction too late produces a *clean* machine, and the whole
row silently becomes meaningless. Every fixture therefore declares a `seed_assert(label, fs)`
that must prove the damage is really there before the cell counts — for `crash4`, that
`cache.json` exists and **fails** to parse; for `crash5`, that `ledger.txt` exists and does
**not** end in a newline. A seed that does not take is an infrastructure error, not a verdict.

### Two API notes worth stealing

- `auto_stop_interval=0` means *never idle-stop*. It is not `auto_delete_interval=0`, which
  means *delete immediately on stop* — an easy and expensive confusion.
- `python -m http.server` serves a directory listing unless the file is named `index.html`,
  and it cannot upgrade a connection, which is why the live page polls rather than using a
  WebSocket (see [below](#the-live-report-page)).

---

## Nosana — the fixer

```
deploy    nosana_deploy.py     vLLM on port 8000, OpenAI-compatible
image     docker.io/vllm/vllm-openai:v0.10.2   (pinned from the market's required_images)
market    nvidia-4090 (24 GB)
model     Qwen/Qwen2.5-Coder-7B-Instruct, served as "fixer"
```

A second, larger model was also deployed via [`nosana_deploy_ollama.py`](nosana_deploy_ollama.py)
— `qwen3.6:27b` behind Ollama's OpenAI-compatible `/v1`. Because both speak the same API,
switching costs two environment variables and no code:

```bash
FIXER_BASE_URL=<ollama node>/v1 FIXER_MODEL=qwen3.6:27b FIXER_MAX_TOKENS=16384 \
  .venv/bin/python fixer.py
```

`FIXER_MAX_TOKENS` has to be raised for a reasoning model: it spends its budget thinking
before it writes the file, and a 4096-token cap truncates the patch mid-function.
[`fixer.extract_code()`](fixer.py) strips `<think>…</think>` before looking for a code fence,
because the reasoning block contains draft fences of its own and the first fence found would
otherwise be a draft rather than the answer.

---

## The feedback loop, in detail

Two agents, and only one of them is a model:

- the **classifier** is deterministic. It judges the machine state from files on disk.
- the **fixer** is the LLM. It receives the failing cells' autopsies **plus the classifier's
  remediation for each verdict**, patches the file, and the matrix is re-swept.

One patch is not a feedback loop. The loop in [`fixer.py`](fixer.py) is:

```
1. sweep the buggy agent            -> 9 verdicts + 9 autopsies
2. failing_detail()                 -> per-cell evidence: exit code, files on disk,
                                       and REMEDIATION[verdict] — what must change
3. llm_patch()                      -> Nosana returns a complete rewritten file
4. ast.parse() + REQUIRED check     -> refuse a patch that does not parse or is missing
                                       os.replace / HONEST_FAIL / try: / https://
5. smoke()                          -> run it locally on a clean machine, FAULT=ok.
                                       Must classify CLEAN or the sweep is skipped.
6. re-sweep 9 fresh sandboxes       -> new verdicts
7. gate3_ok()                       -> pass: done. fail: goto 2 with the NEW failures.
```

Hard stop at 3 rounds (`MAX_ROUNDS`).

**Feeding back verdict labels alone was not enough.** The first attempt produced a plausible
but broken rewrite — it crashed on a missing `ledger.txt`, taking the whole `clean` row to
`LOST`. That failure is preserved in
[`artifacts/fix_events.failed.json`](artifacts/fix_events.failed.json): three rounds, three
patches, `all cells green: False` every time. Feeding back the *evidence* — exit codes, the
actual bytes on disk, and what each verdict requires — is what closed the loop. The pitfalls
that broke those rounds were then written into `fixture.make_fix_spec()` so the prompt states
them explicitly.

Step 5 is a real cost control: a candidate that cannot do the happy path on a clean machine is
not worth nine sandboxes.

`workflow.fixed.py` is a pre-tested deterministic template kept as the **floor**, so a demo
exists even if the GPU is unreachable. `rule_fix()` asserts it still contains every construct
in `REQUIRED` before shipping it, so the floor cannot silently rot. It was not needed for the
refund bot: the run in [`fix_meta.json`](fix_meta.json) is Nosana's.

---

## The live report page

The page is not a static artifact. During a run it shows the sweep filling in cell by cell,
the evidence being sent to the model, the patch coming back, and the re-swept matrix turning
green beside the broken one.

| piece | file | what it does |
|---|---|---|
| page builder | [`report.py`](report.py) | renders the HTML/CSS/JS, uploads it to a Daytona box, serves it, returns a signed URL |
| feed writer | [`live.py`](live.py) | pushes `state.json` into that box as the run progresses |
| driver | [`livesweep.py`](livesweep.py) | runs a real sweep, or replays a recorded one, pushing each event |

**Why polling, not WebSocket.** The sandbox serves the page with `python -m http.server`,
which cannot upgrade a connection. The page fetches `state.json` every 700 ms instead, and
smoothness comes from *diffing* at three levels: the raw response text is compared before
anything is parsed, each round card carries a signature so an unchanged card is never touched,
and each `<pre>` remembers its own scroll position (and keeps following the tail if it was
already at the bottom). Two identical polls repaint exactly nothing.

**Before | after boards.** Once the repair starts, the failing matrix is frozen as BEFORE and
the re-sweeps fill AFTER beside it, with a tally under each (`8 broken` → `9 green`) and a
delta strip: *same machine states, same transport faults → broken cells 8 before, 0 after*.

**Findings are clickable and hoverable.** Every red cell produces a finding card. Hovering
shows a preview — the first output (the files on disk after the run) and the function the fix
changed. Clicking pins a three-part pane: **1.** the first output, **2.** the code that did
it, **3.** what the fix changed. The code panes come from `live.code_focus()`, which `ast`-
parses both the buggy and patched sources, extracts top-level functions by name (with prefix
globs, so `rebuild_ledger` / `rebuild_outbox` / `rebuild_journal` all match `rebuild*`), and
falls back to the best keyword-anchor match if the model renamed something.

**If nothing ever pushes, the page falls back to the recorded matrices** — so the demo never
depends on a live run coming up on venue wifi.

---

## It generalizes: six fixtures, one harness

The refund bot is the default **fixture**, not the whole method. The classifier, the gates,
the sandbox lifecycle and the feedback loop are fixture-agnostic. A fixture only declares its
domain: how a "unit of work" is extracted from its files, which artifact is its cache, and
where the two crash points tear state.

| fixture | the agent | records from | cache (torn → `CORRUPT`) | crash@effect (→ `DUPLICATE`) |
|---|---|---|---|---|
| `refund_bot` (default, repo root) | refund bot | `ledger.txt` split on `REFUND ` | `cache.json` | torn record, retry appends onto it |
| `file_backup` | backup agent | `manifest.json` array | `cache.json` | entry committed, backup file never landed |
| `scheduler` | job dispatcher | `jobs.txt` lines | `cache.json` | record written, payload file never landed |
| `indexer` | web crawler | `index.ndjson` lines | `fetch_cache.json` | index line written, progress flag never set |
| `mailer` | email sender | `outbox.csv` rows | `drafts.json` | row written, sent flag never set |
| `order_processor` | order/charging agent | `journal.json` array | `pending.json` | order committed, `mark_done` never reached |

All six were swept on **real Daytona sandboxes** — 10 sweeps, 90 sandboxes, 0 infrastructure
errors — and every one reproduces the same structure:

| fixture | before, broken cells | interaction cell | after, green cells |
|---|---|---|---|
| `refund_bot` | 8 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |
| `file_backup` | 7 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |
| `indexer` | 7 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |
| `mailer` | 7 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |
| `order_processor` | 7 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |
| `scheduler` | 7 / 9 | `DUPLICATE+GARBAGE` | 9 / 9 |

Six different domains, six different file formats, six different crash mechanisms — the same
six verdicts, and the same compound interaction cell in every single one. That is the claim
this table exists to support. **Read
[What is measured vs. what is architectural](#what-is-measured-vs-what-is-architectural)
before quoting the "after" column** — five of those six were repaired by the deterministic
template, not by the model.

`python localsim.py` proves all six locally with no sandboxes at all;
`python fixtures_report.py` renders their matrices side by side.

---

## Writing a new fixture

Three files, one of which is the only fixture-specific code:

```
fixtures/<name>/
  workflow.py         buggy agent under test — stdlib only, honours CRASH_AT and FAULT
  workflow.fixed.py   deterministic corrected version (the floor under GATE 3)
  spec.py             exports FIXTURE = Fixture(...)
```

`Fixture` ([`fixture.py`](fixture.py)) declares:

| field | meaning |
|---|---|
| `records(fs)` | how the classifier extracts units of work from the durable log |
| `record_valid(r)` | whether one record's content is acceptable (drives `GARBAGE`) |
| `parseable` | artifacts that must stay valid JSON (drives `CORRUPT`) |
| `crash_points` | label → `CRASH_AT` step number; these become the rows beyond `clean` |
| `transports` | `FAULT` values; the first must be `"ok"`, the last is used for the interaction cell |
| `err_file` | the failure marker whose presence means `HONEST_FAIL` rather than `LOST` |
| `seed_assert(label, fs)` | proves the seeded dirty state actually took |
| `fix_spec` | the prose the LLM fixer is told — build it with `make_fix_spec()` |
| `record_label` | the human word for one unit of work (`REFUND`, `EMAIL`, `JOB`…) |

`machines` and `interaction` are derived, not declared: `machines == ["clean", *crash_points]`
and `interaction == (last crash point, last transport)`.

Then:

```bash
.venv/bin/python localsim.py --fixture <name>     # must show reds before, all green after
.venv/bin/python sweep.py --fixture <name>        # the same, on real sandboxes
```

---

## Repo map

```
fixture.py                the Fixture dataclass, classify(), REMEDIATION, make_fix_spec()
sweep.py                  the fan-out: one sandbox per cell, seed, run, autopsy, gate3_ok()
fixer.py                  the two-agent feedback loop (classifier <-> Nosana model)
localsim.py               the whole matrix on the local filesystem, no sandboxes
hero_kill.py              real sandbox.stop() mid-write — the machine-kill proof
probe.py                  verifies the four Daytona assumptions the sweep depends on

report.py                 builds the report page, deploys it, mints the signed preview URL
live.py                   pushes state.json into the report box; code_focus() AST attribution
livesweep.py              drives the live feed: real sweep, --replay, or --replay-fix
fixtures_report.py        all fixtures' matrices in one page

nosana_deploy.py          vLLM deployment (Qwen2.5-Coder-7B, served as "fixer")
nosana_deploy_ollama.py   second deployment: Ollama qwen3.6:27b, same OpenAI API

workflow.py               the buggy refund bot — the default fixture's agent
workflow.fixed.py         its deterministic corrected template (the floor)
workflow.buggy.py         copy of workflow.py kept by fixer.py for before/after attribution
workflow_under_test.py    the patch the model most recently authored

fixtures/<name>/          the other five domains (workflow.py, workflow.fixed.py, spec.py)
artifacts/                local scratch, backups, recordings — gitignored
```

---

## Install and run

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env       # fill in NOSANA_API_KEY, DAYTONA_API_KEY, FIXER_BASE_URL, ...
```

Dependencies are four packages: `daytona`, `openai`, `requests`, `python-dotenv`. The
workflows themselves are stdlib-only, because they have to run inside a bare sandbox.

```bash
# no sandboxes, no network — proves all six fixtures locally
.venv/bin/python localsim.py
.venv/bin/python localsim.py --fixture mailer

# real Daytona sandboxes
.venv/bin/python probe.py                       # verify the Daytona assumptions first
.venv/bin/python sweep.py --cell crash4 ok      # a single cell
.venv/bin/python sweep.py                       # the 9-cell parallel sweep -> results.json
.venv/bin/python sweep.py --fixture indexer     # the same sweep, another domain
.venv/bin/python hero_kill.py                   # real sandbox.stop() mid-write

# the feedback loop
.venv/bin/python fixer.py                             # refund bot -> results_after.json
.venv/bin/python fixer.py --fixture mailer --before results_mailer.json

# the page
.venv/bin/python report.py                      # deploy -> public preview URL
.venv/bin/python report.py --local              # write report.html only, no sandbox
.venv/bin/python report.py --refresh            # new signed URL on the SAME box
.venv/bin/python fixtures_report.py             # all fixtures -> report_fixtures.html
```

### Environment

| variable | purpose |
|---|---|
| `DAYTONA_API_KEY` | sandbox creation |
| `NOSANA_API_KEY`, `NOSANA_API`, `NOSANA_MARKET`, `NOSANA_VAULT` | deploying the model |
| `FIXER_BASE_URL` | OpenAI-compatible endpoint the fixer calls (`…/v1`) |
| `FIXER_MODEL` | model name at that endpoint (`fixer`, `qwen3.6:27b`, …) |
| `FIXER_MAX_TOKENS` | default 4096; raise to ~16384 for a reasoning model |

`.env` holds live credentials and is gitignored. Never commit it.

---

## Demo runbook

```bash
# 1. put the page online, get the URL (do this well before, not on stage)
.venv/bin/python report.py
# ... signed URL expires after 3600s; regenerate on the same box shortly before demoing:
.venv/bin/python report.py --refresh

# 2. safest option — replay the recorded repair into the live page. No sandboxes,
#    no GPU, no venue wifi dependency beyond reaching the report box.
.venv/bin/python livesweep.py --replay-fix artifacts/fix_events.refund_7b.json

# 3. live option — run the real thing against Nosana, streaming into the page
.venv/bin/python report.py --fixture mailer --refresh
.venv/bin/python fixer.py --fixture mailer --before results_mailer.json --live
```

**`fixer.py --live` overwrites `fix_events.json`, `results_after.json` and `state.json`.**
Back up any recording you intend to demo before running it. The refund-bot repair is
preserved at `artifacts/fix_events.refund_7b.json` for exactly this reason.

---

## Evidence files — what is committed, and why

Generated files split into two groups, and [`.gitignore`](.gitignore) draws the line
deliberately:

**Committed — this is the evidence the claims above rest on:**

| file | what it proves |
|---|---|
| `results.json` | the before matrix, 8/9 broken, compound interaction cell |
| `results_<fixture>.json`, `results_<fixture>_after.json` | the same structure in five more domains, from real sandboxes |
| `fix_meta.json` | the repair was authored by a model on Nosana, in 2 rounds |
| `hero_kill.json` | state survived a real `sandbox.stop()` / `start()` |
| `workflow_under_test.py` | the patch the model actually wrote |

**Ignored — rebuilt by a script every run, or expiring:**
`report.html`, `state.json`, `report_url.json` (a signed URL, dead after 3600 s),
`fix_events.json` (overwritten by every `--live` run), `.deployment_id*`, and the workflow's
own leftovers (`cache.json`, `ledger.txt`, `error.txt`) if a local run drops them in the root.

`artifacts/` is ignored too, which means the recordings inside it — including the
refund-bot repair the demo replays — do not survive a fresh clone. If you want the replay to
work from a clone, move one recording to a tracked path.

---

## What is measured vs. what is architectural

Kill Point's own thesis is that silent success claims are the failure mode, so:

- **The model repaired `refund_bot`.** Measured, 2 rounds, all 9 cells green, recorded with
  per-round latency and token counts.
- **The other five fixtures' "after" matrices are the hand-written `workflow.fixed.py`
  template**, verified on real sandboxes. They prove the *harness* generalizes — same sweep,
  same six verdicts, same gates, same compound interaction cell in every domain. They do
  **not** prove the model repairs every domain.
- **The 7B has failed on other domains.** A `mailer` run went the full 3 rounds without
  reaching `all cells green` (each round's patch passed the local smoke test and still lost
  cells on the sweep). That recording is not hidden; it is the current `fix_events.json`.

So: the generalization of the *method* is measured across six domains. The generalization of
the *repair* is measured on one. Both statements belong in the same paragraph.

---

## Limits, stated honestly

- Transport faults are injected at the **tool-function boundary**, not through a network
  proxy. Real HTTP semantics — retries, connection resets, partial bodies, TLS errors — are
  not exercised.
- The matrix is 3×3, not 4×3. A `read_only` / disk-pressure row was designed and dropped: the
  Daytona tier caps total CPU at 10 and `Resources.cpu` is an integer, so 10 concurrent
  sandboxes is a hard ceiling. Batching the sweep into two waves would have preserved the cell
  count at the cost of the parallelism, and the parallelism is the point.
- A deliberately deceptive agent could restore file hashes to hide what it did. This targets
  **ordinary failure paths, not adversarial cover-up**.
- Each fixture is a single 6-step workflow with known planted bugs, not a survey of real
  agents. It demonstrates the method; it does not benchmark the field.
- The repair loop caps at 3 rounds. "Not fixed" means *not fixed within 3 rounds by this
  model*, not *unfixable*.
