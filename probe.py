"""Step 1b -- verify the four Daytona assumptions the sweep depends on, before building on them."""
import asyncio, time, traceback
from dotenv import load_dotenv
from daytona import Daytona, CreateSandboxFromSnapshotParams, SessionExecuteRequest

load_dotenv()
d = Daytona()
created = []
results = {}


def mk(**kw):
    sb = d.create(CreateSandboxFromSnapshotParams(auto_delete_interval=0, **kw))
    created.append(sb)
    return sb


def check(name, fn):
    t = time.time()
    try:
        out = fn()
        results[name] = ("PASS", f"{time.time()-t:.1f}s", out)
    except Exception as e:
        results[name] = ("FAIL", f"{time.time()-t:.1f}s", f"{type(e).__name__}: {e}")
        traceback.print_exc()


# 1 -- SDK surface: does create() accept auto_delete_interval, does code_run work?
def probe_create():
    sb = mk()
    r = sb.process.code_run("print(1)")
    assert r.result.strip() == "1", r.result
    return f"sandbox {sb.id} code_run -> {r.result.strip()!r}"


# 2 -- does a relative upload land in the dir process.exec runs from?
def probe_cwd():
    sb = created[0]
    sb.fs.upload_file(b"marker\n", "probe.txt")
    pwd = sb.process.exec("pwd", timeout=30).result.strip()
    ls = sb.process.exec("ls -1", timeout=30).result
    found = "probe.txt" in ls
    assert found, f"upload NOT visible from exec cwd {pwd}: {ls!r}"
    return f"exec cwd={pwd}, upload visible: {found}"


# 3 -- preview URL. GATE 4 depends on this and has no fallback.
def probe_preview():
    sb = created[0]
    sb.fs.upload_file(b"<h1>killpoint probe</h1>", "index.html")
    sb.process.create_session("web")
    sb.process.execute_session_command(
        "web", SessionExecuteRequest(command="python3 -m http.server 3000", run_async=True))
    time.sleep(3)
    link = sb.create_signed_preview_url(3000, expires_in_seconds=3600)
    import urllib.request
    body = urllib.request.urlopen(link.url, timeout=25).read().decode()
    assert "killpoint probe" in body, body[:200]
    return f"{link.url} serves the uploaded page"


# 4 -- 12 concurrent create(). This IS the metric-04 claim; if it caps, shrink the matrix NOW.
def probe_concurrency():
    async def run():
        return await asyncio.gather(*[asyncio.to_thread(mk) for _ in range(12)],
                                    return_exceptions=True)
    boxes = asyncio.run(run())
    ok = [b for b in boxes if not isinstance(b, Exception)]
    errs = [f"{type(b).__name__}: {b}" for b in boxes if isinstance(b, Exception)]
    assert len(ok) == 12, f"only {len(ok)}/12 created. errors: {errs[:3]}"
    return f"12/12 concurrent sandboxes created"


for n, f in [("1 create+code_run", probe_create), ("2 upload cwd", probe_cwd),
             ("3 preview url", probe_preview), ("4 concurrency x12", probe_concurrency)]:
    check(n, f)

print("\n" + "=" * 70)
for n, (st, t, out) in results.items():
    print(f"{st:4} {t:>7}  {n:<20} {out}")
print("=" * 70)

print(f"\ncleaning up {len(created)} sandboxes...")
for sb in created:
    try: sb.delete()
    except Exception as e: print("  delete failed:", sb.id, e)
print("done")
