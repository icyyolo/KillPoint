"""Two-agent feedback loop.

  classifier (deterministic, fixture.py) judges the machine state from files on disk
  fixer      (LLM served on Nosana)       proposes the patch

One patch is not a feedback loop. After each patch the matrix is re-swept and the failing
cells are fed back to the fixer for another round. Hard stop at 3 rounds.

The loop is fixture-agnostic: every fixture's fix spec and template come from its Fixture
object, and the classifier/gates are shared. `python fixer.py` fixes the refund bot;
`python fixer.py --fixture <name>` fixes another domain the same way.

Rule fallback: the fixture's workflow.fixed.py, a pre-tested corrected workflow. It is
the floor -- if Nosana is unreachable the demo still exists, and we say so honestly
rather than claiming the model authored the patch.
"""
import ast, asyncio, json, os, re, shutil, sys, time
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")
import fixture as fx
import sweep
from fixture import Fixture

MAX_ROUNDS = 3

# The deterministic classifier does not just label the wreckage -- it says what the label
# implies must change. This is the classifier -> fixer half of the feedback loop.
REMEDIATION = fx.REMEDIATION


def failing_detail(results):
    """Feed back the EVIDENCE, not just the label. A verdict name alone tells the fixer
    nothing about why -- the autopsy is the whole point of the classifier."""
    out = []
    for r in results:
        if r["colour"] == "green":
            continue
        hints = [REMEDIATION[f] for f in r["verdict"].split("+") if f in REMEDIATION]
        out.append(
            f"cell (machine={r['machine']}, transport={r['transport']}) -> {r['verdict']}\n"
            f"  process exit code: {r.get('exit_code')}\n"
            f"  files on disk after the run:\n"
            + "\n".join("    " + ln for ln in (r.get("autopsy") or "").strip().splitlines()[:12])
            + ("\n  REQUIRED CHANGE: " + " ".join(hints) if hints else "")
        )
    return "\n".join(out)


def smoke(src, f):
    """Cheap local pre-flight: a candidate that cannot do the happy path on a clean machine
    is not worth nine sandboxes. Returns (ok, evidence)."""
    import subprocess, tempfile
    d = tempfile.mkdtemp()
    try:
        open(os.path.join(d, "w.py"), "w").write(src)
        p = subprocess.run([sys.executable, "w.py"], cwd=d, capture_output=True,
                           env={**os.environ, "FAULT": "ok", "CRASH_AT": "0"}, timeout=60)
        fs = fx.LocalFS(d)
        v, c = fx.classify(f, fs, p.stdout.decode() + p.stderr.decode())
        if c == "green" and v == "CLEAN":
            return True, ""
        return False, (f"LOCAL SMOKE TEST FAILED on a clean machine with FAULT=ok.\n"
                       f"  exit code: {p.returncode}  verdict: {v} ({c})  (expected CLEAN)\n"
                       f"  stderr: {p.stderr.decode()[:600]}")
    except Exception as e:
        return False, f"LOCAL SMOKE TEST raised {type(e).__name__}: {e}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def extract_code(text):
    """A 7B model will wrap the file in markdown more often than not.

    A reasoning model (Qwen3.x, gpt-oss) emits a <think> block first, and it may contain
    code fences of its own -- strip the thinking before looking for the answer, or the
    first fence found is a draft rather than the patch."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S)   # unclosed/omitted opening tag
    m = re.findall(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m[0] if m else text).strip()


def rule_fix(f, _verdicts=None, _round=None):
    src = open(f.template).read()
    missing = [m for m in fx.REQUIRED if m not in src]
    assert not missing, f"{f.template} drifted, missing {missing}"
    return src, "rule", {}


def llm_patch(source, f, failing, round_n):
    from openai import OpenAI
    base = os.environ["FIXER_BASE_URL"]
    client = OpenAI(api_key="nosana", base_url=base)
    model = os.environ.get("FIXER_MODEL", "fixer")
    prompt = (
        f"Round {round_n}. Rewrite this Python workflow so it satisfies the fix spec.\n\n"
        f"FIX SPEC:\n{f.fix_spec}\n\n"
        f"Cells still failing (machine_state, transport_fault) -> verdict:\n{failing}\n\n"
        f"Return ONLY the complete corrected file, no explanation.\n\n{source}"
    )
    t = time.time()
    # a reasoning model spends tokens thinking before it writes the file, so the cap has
    # to cover both or the patch is truncated mid-function
    max_tok = int(os.environ.get("FIXER_MAX_TOKENS", "4096"))
    r = client.chat.completions.create(model=model, temperature=0, max_tokens=max_tok,
                                       messages=[{"role": "user", "content": prompt}])
    msg = r.choices[0].message
    think = getattr(msg, "reasoning_content", None) or ""
    code = extract_code(msg.content or "")
    print(f"    [nosana] {base}  model={model}  {time.time()-t:.1f}s  "
          f"{r.usage.completion_tokens} completion tokens"
          + (f"  ({len(think)} chars of reasoning)" if think else ""))
    ast.parse(code)                       # refuse to ship a file that does not parse
    missing = [m for m in fx.REQUIRED if m not in code]
    if missing:
        raise ValueError(f"LLM patch missing required constructs: {missing}")
    return code, "nosana", {"latency": round(time.time() - t, 1),
                            "tokens": r.usage.completion_tokens,
                            "thinking": think, "prompt": prompt}


def llm_available():
    try:
        import requests
        base = os.environ.get("FIXER_BASE_URL", "")
        if not base: return False
        return requests.get(f"{base}/models", timeout=10).status_code == 200
    except Exception:
        return False


def fix_loop(f=None, lv=None, before_path="results.json"):
    f = f or fx.default()
    shutil.copy(f.workflow, "workflow.buggy.py")     # keep the original for seeding
    use_llm = llm_available()
    print(f"fixer source: {'NOSANA (' + os.environ['FIXER_BASE_URL'] + ')' if use_llm else 'RULE TEMPLATE (Nosana unreachable)'}\n")

    src = open(f.workflow).read()
    before = json.load(open(before_path))
    failing = failing_detail(before)
    author = None
    if lv:
        lv.seed_before(before)     # BEFORE = the sweep being repaired, not whatever the
                                   # resumed page happens to be showing
        lv.fix_start("nosana" if use_llm else "rule",
                     os.environ.get("FIXER_BASE_URL") if use_llm else None)

    for round_n in range(1, MAX_ROUNDS + 1):
        print(f"--- round {round_n} ---")
        if lv: lv.round_asking(round_n, failing)
        if use_llm:
            try:
                src, author, meta = llm_patch(src, f, failing, round_n)
            except Exception as e:
                print(f"    [nosana] patch rejected ({type(e).__name__}: {e}); "
                      f"falling back to rule template for this round")
                src, author, meta = rule_fix(f)
        else:
            src, author, meta = rule_fix(f)
        if lv: lv.round_patch(round_n, src, author, meta.get("latency"), meta.get("tokens"))

        open("workflow_under_test.py", "w").write(src)

        # pre-flight before spending nine sandboxes
        ok_smoke, evidence = smoke(src, f)
        if lv: lv.round_smoke(round_n, ok_smoke, evidence)
        if not ok_smoke:
            print(f"    smoke test FAILED, skipping the sweep this round")
            print("      " + evidence.replace("\n", "\n      "))
            failing = evidence
            continue
        print("    smoke test passed, sweeping")

        if lv: lv.round_sweeping(round_n, f.machines, f.transports)
        try:
            res = asyncio.run(sweep.run_sweep_fx(
                f, "workflow_under_test.py", "results_after.json",
                on_cell=(lambda r: lv.round_cell(round_n, r)) if lv else None))
        except RuntimeError as e:
            print(f"    INFRASTRUCTURE FAILURE, not an agent defect -- aborting the loop.")
            print(f"    {e}")
            print("    Re-run fixer.py once connectivity is back; nothing was fed to the model.")
            if lv: lv.fix_done(False, round_n)
            return False
        sweep.render(res, f)
        ok, checks = sweep.gate3_ok(res, f)
        for k, v in checks.items():
            print(f"    {'PASS' if v else 'FAIL'}  {k}")
        if lv: lv.round_result(round_n, checks, ok)
        if ok:
            print(f"\nFIXED in round {round_n}. patch authored by: {author}")
            json.dump({"fixture": f.name, "rounds": round_n, "author": author,
                       "endpoint": os.environ.get("FIXER_BASE_URL") if author == "nosana" else None,
                       "model": os.environ.get("FIXER_MODEL") if author == "nosana" else None},
                      open("fix_meta.json", "w"), indent=2)
            if lv: lv.fix_done(True, round_n)
            return True
        failing = failing_detail(res)
        print(f"    still failing:\n      " + failing.replace("\n", "\n      ") + "\n")

    print(f"GATE 3 NOT MET after {MAX_ROUNDS} rounds", file=sys.stderr)
    if lv: lv.fix_done(False, MAX_ROUNDS)
    return False


if __name__ == "__main__":
    ap = __import__("argparse").ArgumentParser()
    ap.add_argument("--fixture", default="refund_bot")
    ap.add_argument("--before", default="results.json", help="before-matrix results file")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()

    lv = None
    if a.live:
        from live import Live
        from livesweep import report_sandbox
        lv = Live(report_sandbox(), resume=True)
    ok = False
    try:
        ok = fix_loop(fx.get(a.fixture), lv, a.before)
    finally:
        if lv:
            lv.finish("fixer finished")
            json.dump(lv.state, open("fix_events.json", "w"), indent=2)
            print("recorded fix_events.json for replay")
    sys.exit(0 if ok else 1)
