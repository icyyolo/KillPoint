"""The whole demo, one command.

    python demo.py                      the full live run for the refund bot
    python demo.py --fixture mailer     the same, in another domain
    python demo.py --replay             no sandboxes, no GPU: replay the recording
    python demo.py --sweep              also run the BEFORE matrix live, not from file

It does, in order: build the page for the fixture, put it on a public Daytona URL, seed
(or run) the failing matrix, then run the repair loop against Nosana -- every stage
pushed into that same page as it happens. One process, one URL, nothing to sequence by
hand.

Concurrency: a sweep is one sandbox per cell (9) and the page's own box is a tenth, which
is the whole tier allowance. Never run two of these at once -- the second fails on quota,
which looks exactly like an agent defect and is not one.
"""
import argparse, asyncio, json, os, shutil, sys, time

import fixture as fx
import report
import sweep
from fixer import fix_loop
from live import Live
from livesweep import replay_fix

RECORDING = "fix_events.json"


def before_path(f):
    return "results.json" if f.name == "refund_bot" else f"results_{f.name}.json"


def keep(path):
    """Back up a file this run is about to overwrite. The recording the demo replays has
    been lost to a --live run before; it does not happen twice."""
    if not os.path.exists(path):
        return None
    os.makedirs("artifacts", exist_ok=True)
    dst = os.path.join("artifacts", f"{os.path.basename(path)}.{time.strftime('%H%M%S')}")
    shutil.copy(path, dst)
    return dst


def live_sweep(f, lv, out):
    """Run the failing matrix for real, pushing each cell as it lands."""
    lv.start_cells(f.machines, f.transports, "Before — the buggy agent")
    t0 = time.time()

    def landed(r):
        lv.cell_done(r, ms=int((time.time() - t0) * 1000))
        print(f"  {r['machine']:<14} {r['transport']:<15} {r['verdict']}")

    res = asyncio.run(sweep.run_sweep_fx(f, f.workflow, out, on_cell=landed))
    print(f"  {len(f.machines) * len(f.transports)} sandboxes in {time.time() - t0:.1f}s")
    sweep.render(res, f)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="refund_bot")
    ap.add_argument("--sweep", action="store_true",
                    help="run the BEFORE matrix on real sandboxes instead of loading it")
    ap.add_argument("--replay", metavar="FILE", nargs="?", const="artifacts/fix_events.refund_7b.json",
                    help="replay a recorded repair: no sandboxes, no GPU")
    ap.add_argument("--new-box", action="store_true",
                    help="create a fresh report sandbox instead of reusing report_url.json")
    ap.add_argument("--pace", type=float, default=0.9, help="seconds between replayed cells")
    a = ap.parse_args()

    f = fx.get(a.fixture)
    bp = before_path(f)
    if not os.path.exists(bp):
        sys.exit(f"no before-matrix for {f.name}: {bp} missing. "
                 f"Run: python sweep.py --fixture {f.name} --out {bp}")

    # 1. the page, built for THIS fixture's rows, on a public URL
    print(f"fixture: {f.name}   before-matrix: {bp}")
    url, sb = report.deploy(report.build(json.load(open(bp)), f), refresh=not a.new_box)
    print(f"\n  PAGE: {url}\n        (add ?big for projector size; the URL expires in 3600s)\n")

    lv = Live(sb, f=f)

    if a.replay:
        print(f"replaying {a.replay} -- no sandboxes, no GPU")
        replay_fix(lv, a.replay, a.pace, bp)
        lv.finish("replay finished")
        return 0

    # 2. the failing matrix
    if a.sweep:
        print("sweeping the buggy agent on real sandboxes")
        live_sweep(f, lv, bp)
    else:
        print(f"seeding the failing matrix from {bp} (--sweep to run it live)")
        lv.seed_before(json.load(open(bp)))

    # 3. the repair loop -- fix_loop pushes every stage into the same page
    kept = keep(RECORDING)
    if kept:
        print(f"backed up the previous recording -> {kept}")
    ok = False
    try:
        ok = fix_loop(f, lv, bp)
    finally:
        lv.finish("fixer finished")
        json.dump(lv.state, open(RECORDING, "w"), indent=2)
        print(f"\nrecorded {RECORDING} (replay with: python demo.py --replay {RECORDING})")
        print(f"PAGE: {url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
