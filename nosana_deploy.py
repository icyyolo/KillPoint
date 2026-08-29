"""Deploy a vLLM OpenAI-compatible endpoint on Nosana, poll until it serves, print the URL.

Schema below is not guessed: it was read back from a live deployment via
GET /api/deployments/<id>/revisions, so gpu/expose/cmd shapes are confirmed.

Usage:
    python nosana_deploy.py            # create + poll
    python nosana_deploy.py --status   # poll the deployment named in .env / DEPLOYMENT_ID
"""
import os, sys, time, json, requests
from dotenv import load_dotenv

load_dotenv()

API    = os.environ["NOSANA_API"].rstrip("/")
KEY    = os.environ["NOSANA_API_KEY"]
MARKET = os.environ["NOSANA_MARKET"]
H      = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# 7B fits a 24GB 4090 at max-model-len 8192. If the cold-start pull is too slow,
# swap to Qwen/Qwen2.5-Coder-3B-Instruct (~6GB) and re-run.
MODEL = os.environ.get("FIXER_HF_MODEL", "Qwen/Qwen2.5-Coder-7B-Instruct")
NAME  = os.environ.get("DEPLOYMENT_NAME", "killpoint-fixer")
# a vault holds the credits the job draws from. Reuse the existing one rather than
# new_vault:true, which creates an unfunded vault.
VAULT = os.environ.get("NOSANA_VAULT", "6zdf3CoLuoWTHfktGnHrBVbvCtr4MsFBuepuLR2XnbXM")

# Field names below come from GET /api/openapi.json (the spec is undocumented on the
# docs site but the server serves it). Required: name, market, replicas, timeout,
# job_definition, a vault, and a strategy matching one of the anyOf branches.
BODY = {
    "name": NAME,
    "market": MARKET,
    "vault": VAULT,
    "timeout": 360,          # minutes; INFINITE branch requires timeout to be present
    "replicas": 1,
    "strategy": "INFINITE",  # survives past timeout; SIMPLE would expire mid-hackathon
    "rotation_time": 20,
    "job_definition": {
        "version": "0.1",
        "type": "container",
        "meta": {"trigger": "api"},
        "ops": [{
            "type": "container/run",
            "id": "vllm",
            "args": {
                # pinned tag from the market's required_images — :latest is NOT pre-cached
                "image": "docker.io/vllm/vllm-openai:v0.10.2",
                "gpu": True,
                "expose": 8000,
                # entrypoint is set explicitly so we do not depend on whether Nosana
                # preserves or overrides the image's own ENTRYPOINT.
                "entrypoint": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
                "cmd": [
                    "--model", MODEL,
                    "--served-model-name", "fixer",
                    "--max-model-len", "8192",
                    "--gpu-memory-utilization", "0.90",
                    "--host", "0.0.0.0",
                    "--port", "8000",
                ],
            },
        }],
    },
}


def create():
    r = requests.post(f"{API}/deployments/create", headers=H, json=BODY, timeout=60)
    print("POST /deployments/create ->", r.status_code)
    print(json.dumps(r.json(), indent=2)[:2000])
    r.raise_for_status()
    return r.json()["id"]


def start(dep_id):
    """A newly created deployment is DRAFT with 0 jobs -- it does not run until started.
    The body must be `{}`; POSTing with no body returns a 500 Internal Server Error."""
    r = requests.post(f"{API}/deployments/{dep_id}/start", headers=H, json={}, timeout=60)
    print("POST /start ->", r.status_code, r.text[:300])
    r.raise_for_status()


def poll(dep_id, minutes=25):
    """Wait for status RUNNING *and* a live /v1/models. RUNNING alone is not enough --
    the container is up long before vLLM finishes loading weights."""
    deadline = time.time() + minutes * 60
    url = None
    while time.time() < deadline:
        d = requests.get(f"{API}/deployments/{dep_id}", headers=H, timeout=30).json()
        eps = d.get("endpoints") or []
        if eps and not url:
            url = eps[0]["url"].rstrip("/")
            print("endpoint:", url)
        print(f"[{int(time.time()-deadline+minutes*60):>4}s] status={d.get('status')} endpoints={len(eps)}")
        if url:
            try:
                m = requests.get(f"{url}/v1/models", timeout=15)
                if m.status_code == 200 and "data" in m.json():
                    print("\nSERVING. /v1/models ->", json.dumps(m.json(), indent=2))
                    print(f"\nFIXER_BASE_URL={url}/v1")
                    print("Paste that line into .env")
                    return url
            except Exception as e:
                print("   /v1/models not up yet:", type(e).__name__)
        time.sleep(20)
    print("\nTIMED OUT. Deployment may still be pulling weights.")
    print("Do NOT claim Nosana was used unless /v1/models answered. Ship the rule fixer.")
    return None


if __name__ == "__main__":
    if "--status" in sys.argv:
        dep = os.environ.get("DEPLOYMENT_ID") or sys.argv[-1]
    else:
        dep = create()
        print("deployment id:", dep)
        start(dep)
    poll(dep)
