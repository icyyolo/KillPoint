"""Kill Point orchestrator: the (machine_state, transport_fault) fan-out.

One disposable Daytona sandbox per cell, all in parallel. Each sandbox is seeded into a
specific dirty state by the fixture's ORIGINAL buggy workflow, then the workflow under
test is run on top of that dirty machine and the wreckage is classified deterministically.

Everything here is fixture-agnostic: pass --fixture <name> and the same harness sweeps a
different domain. `python sweep.py` with no args is the refund bot, unchanged.
"""
import argparse, asyncio, json, sys, time
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")
from daytona import Daytona, CreateSandboxFromSnapshotParams   # create() takes a params OBJECT

import fixture as fx
from fixture import Fixture

# Default-fixture aliases kept for the refund-specific tools (livesweep.py, live.py):
# they import these names and expect the 3x3 refund bot.
MACHINE   = fx.default().machines
TRANSPORT = fx.default().transports
SEED_SRC  = fx.default().workflow


class SandboxFS(fx.FS):
    def __init__(self, sb): self.sb = sb
    def read(self, p):
        try: return self.sb.fs.download_file(p).decode()
        except Exception: return None


def read(sb, p):
    return SandboxFS(sb).read(p)


def classify(ledger, cache, err, run_output=""):
    """Refund-bot default, kept for localsim.py and any caller that passes raw strings."""
    d = {"ledger.txt": ledger, "cache.json": cache, "error.txt": err}
    class _DictFS(fx.FS):
        def read(self, p): return d.get(p)
    return fx.classify(fx.default(), _DictFS(), run_output)


def seed(sb, f, state):
    """Put the machine into a specific dirty state, then ASSERT the seed actually took.
    A silently-failed seed is indistinguishable from a clean machine and would quietly
    collapse two rows of the matrix into one. The seed is always the ORIGINAL buggy
    agent: it represents "the previous run died here"."""
    if state == "clean":
        return
    n = f.crash_points[state]
    sb.fs.upload_file(open(f.workflow, "rb").read(), "workflow.py")
    sb.process.exec(f"CRASH_AT={n} python workflow.py", timeout=60)
    ok, detail = f.seed_assert(state, SandboxFS(sb))
    assert ok, f"{state} seed failed: {detail}"


def _autopsy_cmd(f):
    return "; ".join(f"echo '--- {p}'; cat {p}" for p in f.files)


async def cell_fx(machine, transport, f, run_src):
    def go():
        sb = None
        try:
            # auto_stop_interval=0 -> never idle-stop. NOT auto_delete_interval=0, which
            # means "delete immediately upon stopping" and would destroy the evidence.
            # Transient network failures are expected on venue wifi. Retry the create so a
            # dropped packet does not masquerade as an agent defect.
            last = None
            for attempt in range(3):
                try:
                    sb = Daytona().create(CreateSandboxFromSnapshotParams(auto_stop_interval=0))
                    break
                except Exception as e:
                    last = e; time.sleep(2 * (attempt + 1))
            if sb is None:
                raise RuntimeError(f"sandbox create failed after 3 attempts: {last}")
            seed(sb, f, machine)
            sb.fs.upload_file(run_src, "workflow.py")       # the agent under test
            proc = sb.process.exec(f"FAULT={transport} python workflow.py", timeout=60)
            out = proc.result or ""                         # stderr is merged into result
            autopsy = sb.process.exec(_autopsy_cmd(f), timeout=30).result
            fs = SandboxFS(sb)
            verdict, colour = fx.classify(f, fs, out)
            return dict(machine=machine, transport=transport, verdict=verdict,
                        colour=colour, autopsy=autopsy, exit_code=proc.exit_code,
                        refunds=len(f.records(fs)))
        except Exception as e:
            return dict(machine=machine, transport=transport, verdict=f"SWEEP_ERROR",
                        colour="red", autopsy=f"{type(e).__name__}: {e}", exit_code=None,
                        refunds=0)
        finally:
            if sb:
                try: sb.delete()          # free the concurrency slot immediately
                except Exception: pass
    return await asyncio.to_thread(go)


async def cell(machine, transport, run_src, seed_src):
    """Refund-bot default, kept for livesweep.py."""
    return await cell_fx(machine, transport, fx.default(), run_src)


async def run_sweep_fx(f, workflow_path, out_path, on_cell=None):
    """Sweep a fixture: 9 (or n) cells, all parallel, seed = the fixture's buggy agent."""
    run_src = open(workflow_path, "rb").read()   # re-read EVERY sweep: fixer rewrites this

    async def one(m, t):
        r = await cell_fx(m, t, f, run_src)
        if on_cell:                               # report each cell the moment it lands
            try: on_cell(r)
            except Exception: pass                # a broken feed must never fail a sweep
        return r

    results = await asyncio.gather(*[one(m, t) for m in f.machines for t in f.transports])
    infra = [r for r in results if r["verdict"] == "SWEEP_ERROR"]
    if infra:
        # Infrastructure failure is NOT an agent defect. Never let it reach the fixer as
        # feedback -- it would patch code to chase a network outage.
        raise RuntimeError(
            f"{len(infra)}/{len(results)} cells failed for infrastructure reasons, not agent "
            f"behaviour. Sweep aborted, results NOT written.\n  first: {infra[0]['autopsy'][:300]}")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)
    return results


async def run_sweep(workflow_path, out_path, on_cell=None):
    """Refund-bot default."""
    return await run_sweep_fx(fx.default(), workflow_path, out_path, on_cell)


def render(results, f=None):
    f = f or fx.default()
    by = {(r["machine"], r["transport"]): r for r in results}
    w = max(len(r["verdict"]) for r in results) + 2
    print(f"{'':<10}" + "".join(f"{t:<{w}}" for t in f.transports))
    for m in f.machines:
        row = f"{m:<10}"
        for t in f.transports:
            r = by[(m, t)]
            dot = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m"}[r["colour"]]
            row += dot + f"{r['verdict']:<{w}}" + "\033[0m"
        print(row)


def gate_report(results, f=None):
    f = f or fx.default()
    reds = [r for r in results if r["colour"] == "red"]
    greens = [r for r in results if r["colour"] == "green"]
    by = {(r["machine"], r["transport"]): r["verdict"] for r in results}
    m0, t0 = f.interaction
    print(f"\ncells={len(results)} green={len(greens)} red={len(reds)}")
    print(f"  (clean, ok)        = {by.get(('clean','ok'))}")
    print(f"  ({m0}, ok)         = {by.get((m0,'ok'))}")
    print(f"  ({m0}, {t0})   <- interaction cell = {by.get((m0,t0))}")
    print(f"  total records      = {sum(r.get('refunds',0) for r in results)}")
    return reds, greens


def gate3_ok(results, f=None):
    """All green is NOT enough: a fixer that wraps everything in try/except and does no
    work would score all-green while deleting the product."""
    f = f or fx.default()
    by = {(r["machine"], r["transport"]): r for r in results}
    checks = {
        "all cells green":      all(r["colour"] == "green" for r in results),
        "(clean,ok) == CLEAN":  by[("clean", "ok")]["verdict"] == "CLEAN",
        "total records >= 1":   sum(r.get("refunds", 0) for r in results) >= 1,
    }
    return all(checks.values()), checks


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="refund_bot",
                    help="fixture name (refund_bot or a fixtures/<name> dir)")
    ap.add_argument("--workflow", default=None,
                    help="workflow under test (default: the fixture's buggy source)")
    ap.add_argument("--out", default="results.json")
    ap.add_argument("--cell", nargs=2, metavar=("MACHINE", "TRANSPORT"),
                    help="run a single cell (GATE 1)")
    a = ap.parse_args()

    f = fx.get(a.fixture)
    wpath = a.workflow or f.workflow

    if a.cell:
        m, t = a.cell
        src = open(wpath, "rb").read()
        r = asyncio.run(cell_fx(m, t, f, src))
        print(json.dumps(r, indent=2))
        sys.exit(0)

    res = asyncio.run(run_sweep_fx(f, wpath, a.out))
    render(res, f); gate_report(res, f)
    print(f"\nwrote {a.out}")
