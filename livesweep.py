"""Drive the report page's live feed.

  --replay          push the RECORDED sweep back through the feed, one cell at a time.
                    No sandboxes, no network beyond the report box. Safe on stage.
  (default)         run a real 9-sandbox sweep and push each cell as it lands.
"""
import argparse, asyncio, json, os, random, sys, time
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")
from sweep import MACHINE, TRANSPORT, cell, SEED_SRC
from live import Live


def report_sandbox():
    """Attach to the box already serving the page. None -> write state.json locally only."""
    if not os.path.exists("report_url.json"):
        print("no report_url.json; writing state.json locally only")
        return None
    from daytona import Daytona
    info = json.load(open("report_url.json"))
    sb = Daytona().get(info["sandbox_id"])
    print("feeding:", info["url"])
    return sb


def replay_fix(lv, path, pace, before_path="results.json"):
    """Play a recorded fixer run (fix_events.json) back through the feed, round by round,
    so the repair loop can be shown without calling the model on stage.

    The failing sweep is seeded first: it is what the BEFORE board and the findings list
    show, and it is the thing the patch has to answer for."""
    rec = json.load(open(path))
    fx = rec.get("fix", {})
    fail = json.load(open(before_path))
    lv.seed_before(fail)
    time.sleep(pace)

    lv.fix_start(fx.get("author"), fx.get("endpoint"))
    meta = json.load(open("fix_meta.json")) if os.path.exists("fix_meta.json") else {}
    # the recording predates the model field; fix_meta.json is written by the same run
    lv.state["fix"]["model"] = fx.get("model") or meta.get("model")
    full = {(c["machine"], c["transport"]): c for c in rec.get("cells", [])}
    for r in fx.get("rounds", []):
        n = r["n"]
        lv.round_asking(n, r.get("evidence")); print(f"  round {n}: asking"); time.sleep(pace)
        lv.round_patch(n, r.get("patch"), fx.get("author"), r.get("latency"), r.get("tokens"))
        print(f"  round {n}: patched"); time.sleep(pace)
        lv.round_smoke(n, r.get("smoke") == "passed", r.get("smoke_evidence") or "")
        print(f"  round {n}: smoke {r.get('smoke')}"); time.sleep(pace)
        lv.round_sweeping(n, MACHINE, TRANSPORT); time.sleep(pace)
        cells = sorted(r.get("cells", []), key=lambda _: random.random())
        for c in cells:                       # fill the AFTER board cell by cell
            lv.round_cell(n, full.get((c["machine"], c["transport"]), c))
            print(f"  round {n}: {c['machine']:<7} {c['transport']:<15} {c['verdict']}")
            time.sleep(pace / 2)
        lv.round_result(n, r.get("checks"), r.get("fixed"))
        time.sleep(pace)
    lv.fix_done(fx.get("fixed", False), fx.get("total_rounds", len(fx.get("rounds", []))))


def replay(lv, path, phase, pace):
    results = json.load(open(path))
    lv.start_cells(MACHINE, TRANSPORT, phase)
    time.sleep(pace)
    for r in sorted(results, key=lambda _: random.random()):   # land out of order, like real parallel
        lv.cell_done(r, ms=int(pace * 1000))
        print(f"  {r['machine']:<7} {r['transport']:<15} {r['verdict']}")
        time.sleep(pace)


async def real(lv, workflow, phase):
    run_src = open(workflow, "rb").read()
    seed_src = open(SEED_SRC, "rb").read()
    lv.start_cells(MACHINE, TRANSPORT, phase)
    t0 = time.time()

    async def one(m, t):
        r = await cell(m, t, run_src, seed_src)
        lv.cell_done(r, ms=int((time.time() - t0) * 1000))
        print(f"  {m:<7} {t:<15} {r['verdict']}")
        return r

    return await asyncio.gather(*[one(m, t) for m in MACHINE for t in TRANSPORT])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay", action="store_true")
    ap.add_argument("--replay-fix", metavar="FILE", nargs="?", const="fix_events.json",
                    help="replay a recorded fixer run instead of a sweep")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--workflow", default="workflow.py")
    ap.add_argument("--phase", default="Before -- the buggy agent")
    ap.add_argument("--pace", type=float, default=0.9, help="seconds between replayed cells")
    a = ap.parse_args()

    lv = Live(report_sandbox())
    if a.replay_fix:
        replay_fix(lv, a.replay_fix, a.pace, a.results)
    elif a.replay:
        replay(lv, a.results, a.phase, a.pace)
    else:
        asyncio.run(real(lv, a.workflow, a.phase))
    lv.finish()
    print("done; state.json pushed")
