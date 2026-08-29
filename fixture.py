"""Fixture-driven harness core.

sweep.py and fixer.py take a Fixture instead of hard-coding the refund bot. A fixture
declares everything domain-specific -- how durable records are extracted, which artifact
is the parseable cache, where the crash points tear state, and the prose the LLM fixer is
told. The verdicts, gates, sandbox lifecycle and feedback loop are shared, which is what
makes the method general: swap the fixture and the same harness finds and repairs the
same six failure classes in a different domain.

  refund_bot (the built-in default)          workflow.py / workflow.fixed.py at the repo root
  fixtures/<name>/workflow.py                buggy agent for that domain
  fixtures/<name>/workflow.fixed.py          rule-fixer template (the floor)
  fixtures/<name>/spec.py                    exports FIXTURE = Fixture(...)
"""
import importlib, json, os, re
from dataclasses import dataclass
from typing import Callable

RED = {"CORRUPT", "GARBAGE", "LOST"}
GREEN = {"CLEAN", "HONEST_FAIL"}
HONEST_MARKER = "HONEST_FAIL"

# Constructs every acceptable patch must contain. Same list for every fixture -- the
# failure classes are the same, only the artifacts differ.
REQUIRED = ["os.replace", "HONEST_FAIL", "try:", "https://"]

# Strict https check for the NEW fixtures (their records are whole lines / entries).
HTTPS = re.compile(r"^https://\S+$")


class FS:
    """Minimal filesystem interface -- sweep.py wraps a sandbox, localsim/fixer a dir."""
    def read(self, path):            # path -> str or None
        raise NotImplementedError


class LocalFS(FS):
    def __init__(self, d): self.d = d
    def read(self, p):
        path = os.path.join(self.d, p)
        try: return open(path).read()
        except Exception: return None


@dataclass
class Fixture:
    name: str
    workflow: str                   # buggy source path
    template: str                   # fixed template path
    transports: list                # FAULT values, first is "ok"
    crash_points: dict              # label -> CRASH_AT step number
    files: list                     # artifact paths (autopsy + reads)
    records: Callable               # (fs) -> list of raw record strings
    record_valid: Callable          # (raw record) -> bool
    parseable: list                 # artifact paths that must stay valid JSON
    err_file: str                   # the failure marker file
    seed_assert: Callable           # (label, fs) -> (ok, detail)
    fix_spec: str
    record_label: str               # human word for one unit of work

    @property
    def machines(self):
        return ["clean", *list(self.crash_points)]

    @property
    def interaction(self):
        """The cell that proves the cross product for this fixture."""
        return (list(self.crash_points)[-1], self.transports[-1])


def classify(fx, fs, run_output=""):
    """Pure. Returns (verdict, colour). Flags COMPOUND -- collapsing them to one label is
    exactly what hides the interaction cells this project exists to find.

    The six verdict classes are the same for every fixture:
      DUPLICATE  more than one record of the durable effect
      CORRUPT    a parseable state artifact is unparseable on disk
      GARBAGE    a record whose content fails the fixture's validity rule
      CLEAN      exactly one valid record
      HONEST_FAIL  no record, but an error marker -- the good failure
      LOST       no record and no error -- silent"""
    records = fx.records(fs)
    n = len(records)
    bad = [r for r in records if not fx.record_valid(r)]
    corrupt = []
    for p in fx.parseable:
        c = fs.read(p)
        if c is None:
            continue
        try: json.loads(c)
        except Exception: corrupt.append(p)

    flags = []
    if n > 1:                  flags.append("DUPLICATE")
    if corrupt:                flags.append("CORRUPT")
    if n >= 1 and bad:         flags.append("GARBAGE")

    if flags:
        return "+".join(flags), ("red" if RED & set(flags) else "yellow")
    if n == 1: return "CLEAN", "green"
    if fs.read(fx.err_file) or HONEST_MARKER in (run_output or ""):
        return "HONEST_FAIL", "green"
    return "LOST", "red"


# What each verdict implies must change in the patched file. Generic prose -- the same
# four repairs close the loop in every fixture.
REMEDIATION = {
    "CORRUPT":   "a parseable state file is STILL unparseable on disk after the run. "
                 "Detecting it and exiting is not enough -- when it fails to parse you must "
                 "os.replace it to <name>.corrupt and CONTINUE the run with an empty state, "
                 "so the corrupt file is gone and the work still happens.",
    "DUPLICATE": "more than one record ended up in the durable log. Rebuild the log at "
                 "startup and skip ids already present.",
    "GARBAGE":   "a record in the durable log has a field that is not a valid https:// "
                 "URL. Drop such records during the rebuild and never write one.",
    "LOST":      "the run produced no record and no error file -- it failed silently. "
                 "Every exit path must write error.txt.",
}


def make_fix_spec(cache, rec, validate_field, rebuild_desc, compute_desc):
    """Standard fix spec prose with a fixture's artifact names filled in. Items 1-6 are
    checkable in the patched file and re-verified by the re-sweep (see PLAN.md Step 4)."""
    return (
        f"1. The cache ({cache}) must be read inside try/except; a corrupt cache is ignored "
        f"AND quarantined (os.replace to {cache}.corrupt), never consumed. It must be "
        f"written atomically: write {cache}.tmp then os.replace onto {cache}.\n"
        f"2. The durable record log ({rec}) must be REBUILT at startup, BEFORE fetching: "
        f"{rebuild_desc} and rewrite the whole file atomically. Do NOT merely skip the "
        f"append by id -- a torn record left on disk is still garbage.\n"
        f"3. Tool output must be validated before use: {validate_field} or the run fails.\n"
        f"4. Every failure writes error.txt AND prints 'HONEST_FAIL: <reason>' to stderr, "
        f"then exits nonzero. Never continue silently.\n"
        f"5. Keep honouring the CRASH_AT and FAULT environment variables exactly as the "
        f"original does, so the harness can still kill the process at a given step.\n"
        f"6. Keep a working compute step that {compute_desc}. Do not stub it out.\n"
        f"PITFALLS -- these have broken previous rounds:\n"
        f"- The files MAY NOT EXIST. Every read must check os.path.exists first or catch "
        f"FileNotFoundError. Rebuilding a log that is not there must be a no-op, not a "
        f"crash.\n"
        f"- When appending a record, preserve the records already in the log: read the "
        f"current contents, add the new record, write the whole thing to a .tmp and "
        f"os.replace it. Opening the .tmp in append mode loses every earlier record.\n"
        f"- A crash or an uncaught exception that writes nothing is the WORST outcome "
        f"(verdict LOST). Wrap the whole run so that any failure still writes error.txt."
    )


# ---------------------------------------------------------------------------
# the built-in fixture: the refund bot. Its artifacts live at the repo root and
# its verdicts are byte-identical to the committed results.json.
# ---------------------------------------------------------------------------

def _refund_records(fs):
    return (fs.read("ledger.txt") or "").split("REFUND ")[1:]


def _refund_valid(r):
    return re.search(r"https://\S+", r) is not None


def _refund_seed_assert(label, fs):
    if label == "crash4":
        c = fs.read("cache.json")
        if c is None: return False, "cache.json was never created"
        try:
            json.loads(c)
            return False, f"cache.json parses, kill landed too late: {c!r}"
        except json.JSONDecodeError:
            return True, ""
    l = fs.read("ledger.txt")
    if not l: return False, "ledger.txt was never created"
    if l.endswith("\n"):
        return False, "ledger record is complete, kill landed too late"
    return True, ""


def _refund_fix_spec():
    return make_fix_spec(
        cache="cache.json",
        rec="ledger.txt",
        validate_field="the receipt must match ^https://",
        rebuild_desc="parse every record, drop torn records and records whose receipt "
                     "does not match ^https://, drop duplicate ids",
        compute_desc="sets s['refund'] from s['amount']",
    )


def _default():
    return Fixture(
        name="refund_bot",
        workflow="workflow.py",
        template="workflow.fixed.py",
        transports=["ok", "malformed_json", "bad_url"],
        crash_points={"crash4": 4, "crash5": 5},
        files=["cache.json", "ledger.txt", "error.txt", "state.json"],
        records=_refund_records,
        record_valid=_refund_valid,
        parseable=["cache.json"],
        err_file="error.txt",
        seed_assert=_refund_seed_assert,
        fix_spec=_refund_fix_spec(),
        record_label="REFUND",
    )


_DEFAULT = None


def default():
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = _default()
    return _DEFAULT


def get(name):
    """refund_bot (or None/empty) -> the built-in; anything else -> fixtures/<name>/spec.py"""
    if name in (None, "", "refund_bot"):
        return default()
    spec = importlib.import_module(f"fixtures.{name}.spec")
    return spec.FIXTURE


def all():
    """[(name, Fixture)] -- refund_bot first, then the fixtures/ directory in order."""
    here = os.path.dirname(os.path.abspath(__file__))
    fdir = os.path.join(here, "fixtures")
    names = [d for d in sorted(os.listdir(fdir))
             if os.path.isdir(os.path.join(fdir, d)) and os.path.exists(os.path.join(fdir, d, "spec.py"))]
    return [("refund_bot", default())] + [(n, get(n)) for n in names]
