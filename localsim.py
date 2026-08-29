"""Local dry-run of the matrix on the real filesystem for EVERY fixture. Same classify()
and seed_assert() as the sandboxed sweep, no sandboxes: proves each fixture and its fix
before spending the concurrency budget.

  python localsim.py [--fixture <name>]   (default: all fixtures)

For each fixture it prints the buggy BEFORE matrix (must show LOST/GARBAGE/CORRUPT and a
compound interaction cell) and the fixed AFTER matrix (must be all green), then the gate
checks.
"""
import os, subprocess, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fixture as fx
from fixture import LocalFS, classify

DOT = {"green": "\033[32m", "yellow": "\033[33m", "red": "\033[31m"}


def local_run(f, src, label):
    print(f"\n=== {f.name} -- {label} ===")
    print(f"{'':<16}" + "".join(f"{t:<22}" for t in f.transports))
    results = []
    for m in f.machines:
        row = f"{m:<16}"
        for t in f.transports:
            d = tempfile.mkdtemp()
            try:
                # seed: always the ORIGINAL buggy agent, with the fixture's crash step
                open(os.path.join(d, "workflow.py"), "w").write(open(f.workflow).read())
                if m != "clean":
                    subprocess.run([sys.executable, "workflow.py"], cwd=d,
                                   capture_output=True,
                                   env={**os.environ, "CRASH_AT": str(f.crash_points[m])})
                    fs = LocalFS(d)
                    ok, detail = f.seed_assert(m, fs)
                    assert ok, f"{m} seed failed: {detail}"
                # under test: the workflow src, only FAULT set (CRASH_AT defaults to 0)
                open(os.path.join(d, "workflow.py"), "w").write(src)
                p = subprocess.run([sys.executable, "workflow.py"], cwd=d,
                                   capture_output=True, env={**os.environ, "FAULT": t})
                fs = LocalFS(d)
                v, c = classify(f, fs, p.stdout.decode() + p.stderr.decode())
                row += DOT[c] + f"{v:<22}" + "\033[0m"
                results.append(dict(machine=m, transport=t, verdict=v, colour=c,
                                    refunds=len(f.records(fs))))
            finally:
                shutil.rmtree(d)
        print(row)
    return results


def gates(f, before, after):
    b = {(r["machine"], r["transport"]): r for r in before}
    a = {(r["machine"], r["transport"]): r for r in after}
    m0, t0 = f.interaction
    print("  --- gate checks ---")
    print(f"  GATE 2  reds before        : {sum(1 for r in before if r['colour']=='red')} (need >=2)")
    print(f"  GATE 2  interaction        : ({m0}, {t0}) = {b[(m0,t0)]['verdict']} (need compound)")
    print(f"  GATE 3  all green after    : {all(r['colour']=='green' for r in after)}")
    print(f"  GATE 3  (clean,ok)==CLEAN  : {a[('clean','ok')]['verdict']=='CLEAN'}")
    rec = sum(r["refunds"] for r in after)
    print(f"  GATE 3  total records >= 1 : {rec} (>= 1)  [{rec} total]")
    return (sum(1 for r in before if r['colour'] == 'red') >= 2
            and "+" in b[(m0, t0)]["verdict"]
            and all(r['colour'] == 'green' for r in after)
            and a[('clean', 'ok')]['verdict'] == 'CLEAN' and rec >= 1)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default=None, help="only one fixture")
    a = ap.parse_args()

    fixtures = [(n, fx.get(n)) for n, _ in fx.all()] if not a.fixture else [(a.fixture, fx.get(a.fixture))]
    allok = True
    for name, f in fixtures:
        before = local_run(f, open(f.workflow).read(), "BEFORE (buggy)")
        after = local_run(f, open(f.template).read(), "AFTER (workflow.fixed.py)")
        ok = gates(f, before, after)
        allok &= ok
        print(f"  => {'PASS' if ok else 'FAIL'}")
    print(f"\nALL FIXTURES: {'PASS' if allok else 'FAIL'}")
    sys.exit(0 if allok else 1)


if __name__ == "__main__":
    main()
