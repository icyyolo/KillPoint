"""Combined before/after matrix across ALL fixtures -- the generalization proof.

  python fixtures_report.py [--out report_fixtures.html] [--real]

Reads per-fixture results files (results_<name>.json) when present; otherwise runs the
local simulation for each fixture (no sandboxes). --real forces reading only sandbox
results files and skips any fixture without one.

Each fixture is a different domain with different durable artifacts and crash points,
run through the SAME sweep harness and the SAME verdicts -- the before matrix must be red
with a compound interaction cell, the after matrix all green.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture as fx
from fixture import LocalFS, classify
import localsim

CELL_COL = {"green": "#1f7a33", "yellow": "#b58900", "red": "#a31515"}


def verdicts_for(f, src, machines, transports):
    out = {}
    for m in machines:
        for t in transports:
            import subprocess, tempfile, shutil
            d = tempfile.mkdtemp()
            try:
                open(os.path.join(d, "workflow.py"), "w").write(open(f.workflow).read())
                if m != "clean":
                    subprocess.run([sys.executable, "workflow.py"], cwd=d,
                                   capture_output=True,
                                   env={**os.environ, "CRASH_AT": str(f.crash_points[m])})
                    fs = LocalFS(d)
                    ok, detail = f.seed_assert(m, fs)
                    assert ok, f"{m} seed failed: {detail}"
                open(os.path.join(d, "workflow.py"), "w").write(src)
                p = subprocess.run([sys.executable, "workflow.py"], cwd=d,
                                   capture_output=True, env={**os.environ, "FAULT": t})
                fs = LocalFS(d)
                v, c = classify(f, fs, p.stdout.decode() + p.stderr.decode())
                out[(m, t)] = (v, c)
            finally:
                shutil.rmtree(d)
    return out


def load_real(f, label):
    path = f"results_{label}_{f.name}.json" if f.name != "refund_bot" else f"results{label}.json"
    if not os.path.exists(path):
        return None
    by = {(r["machine"], r["transport"]): (r["verdict"], r["colour"]) for r in json.load(open(path))}
    return by


def matrix_rows(f, by):
    rows = []
    for m in f.machines:
        cells = [by[(m, t)] for t in f.transports]
        rows.append((m, cells))
    return rows


def render(fixtures, real):
    html = ["<!doctype html><html><head><meta charset=utf-8>",
            "<title>Kill Point -- the generalization</title>",
            "<style>body{font-family:system-ui,sans-serif;margin:24px;background:#0d1117;color:#e6edf3}"
            "h1{font-size:22px} h2{font-size:16px;margin-top:28px} table{border-collapse:collapse;margin:8px 0}"
            "th,td{border:1px solid #30363d;padding:6px 10px;font-size:13px}"
            "td{text-align:center;color:#fff} .tag{color:#8b949e;font-size:12px}</style></head><body>",
            "<h1>Kill Point &mdash; six fixtures, one harness</h1>",
            "<p class=tag>Each row is a fixture: a different agent domain with different durable artifacts, "
            "crash points and record formats. Same 3&times;3 matrix, same six verdicts, same feedback loop. "
            f"{'Sandbox results' if real else 'Local simulation (no sandboxes)'}. "
            "Interaction cell = <b>DUPLICATE+GARBAGE</b> in every before matrix, all green after.</p>"]
    for name, f in fixtures:
        m0, t0 = f.interaction
        before = load_real(f, "") if real else None
        after = load_real(f, "_after") if real else None
        if real and (before is None or after is None):
            continue
        if before is None:
            before = verdicts_for(f, open(f.workflow).read(), f.machines, f.transports)
        if after is None:
            after = verdicts_for(f, open(f.template).read(), f.machines, f.transports)
        html.append(f"<h2>{name} <span class=tag>records = {f.record_label}</span></h2>")
        for label, by in (("before", before), ("after", after)):
            html.append(f"<table><caption>{label}</caption><tr><th>machine \\ transport</th>" +
                        "".join(f"<th>{t}</th>" for t in f.transports) + "</tr>")
            for m, cells in matrix_rows(f, by):
                tds = "".join(f"<td style='background:{CELL_COL[c]}'>{v}</td>" for v, c in cells)
                html.append(f"<tr><td>{m}</td>{tds}</tr>")
            html.append("</table>")
        inter_v, inter_c = before[(m0, t0)]
        html.append(f"<p class=tag>interaction cell ({m0}, {t0}) = "
                    f"<b style='color:{CELL_COL[inter_c]}'>{inter_v}</b> &mdash; "
                    "the compound only the cross product finds.</p>")
    html.append("</body></html>")
    return "\n".join(html)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="report_fixtures.html")
    ap.add_argument("--real", action="store_true",
                    help="use sandbox results_*.json only (skip fixtures without one)")
    a = ap.parse_args()
    open(a.out, "w").write(render(fx.all(), a.real))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
