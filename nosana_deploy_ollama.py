"""Deploy an Ollama-served model on Nosana as a SECOND job.

killpoint-fixer (vLLM, Qwen2.5-Coder-7B) is left running as the fallback -- this creates a
new deployment, it does not replace one. Ollama exposes an OpenAI-compatible API at /v1,
so fixer.py needs no code change, only FIXER_BASE_URL and FIXER_MODEL.

Job shape copied from the official Nosana template (GET /api/templates, id=qwen3-6-27b),
not guessed: image, port 11434, and the Ollama `resources` entry are the template's own.

Usage:
    python nosana_deploy_ollama.py                  # qwen3.6:27b on the 4090 market
    python nosana_deploy_ollama.py --model qwen3.5:27b --market <addr>
    python nosana_deploy_ollama.py --status <id>
"""
import argparse, json, os, sys, time, requests
from dotenv import load_dotenv

load_dotenv("/home/mx/daytona_hacksprint/.env")

API = os.environ["NOSANA_API"].rstrip("/")
KEY = os.environ["NOSANA_API_KEY"]
H   = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
VAULT = os.environ.get("NOSANA_VAULT", "6zdf3CoLuoWTHfktGnHrBVbvCtr4MsFBuepuLR2XnbXM")


def body(name, market, model, minutes):
    return {
        "name": name,
        "market": market,
        "vault": VAULT,
        "timeout": minutes,      # reserve-upfront; the unused portion is refunded at end
        "replicas": 1,
        "strategy": "INFINITE",
        "rotation_time": 20,
        "job_definition": {
            "version": "0.1",
            "type": "container",
            "meta": {"trigger": "api", "system_requirements": {"vram_total_mb": 23552}},
            "ops": [{
                "type": "container/run",
                "id": "server",
                "args": {
                    "image": "docker.io/ollama/ollama:0.32.6",   # pinned; from the market's required_images
                    "gpu": True,
                    # Ollama pulls the weights itself; this resource entry is what the
                    # official template uses to declare the model to the node.
                    "resources": [{"type": "Ollama", "model": model}],
                    "expose": [{"port": 11434, "health_checks": [
                        {"path": "/api/tags", "type": "http", "method": "GET",
                         "continuous": False, "expected_status": 200}]}],
                },
            }],
        },
    }


def create(b):
    r = requests.post(f"{API}/deployments/create", headers=H, json=b, timeout=60)
    print("POST /deployments/create ->", r.status_code)
    if r.status_code >= 300:
        print(r.text[:1500])
    r.raise_for_status()
    return r.json()["id"]


def start(dep):
    # body must be {}; an empty body returns 500
    r = requests.post(f"{API}/deployments/{dep}/start", headers=H, json={}, timeout=60)
    print("POST /start ->", r.status_code, r.text[:200])
    r.raise_for_status()


def poll(dep, model, minutes=25):
    """RUNNING is not enough -- the container is up long before Ollama has pulled the
    weights. Only a 200 from /v1/models listing the tag counts as serving."""
    t0 = time.time(); deadline = t0 + minutes * 60
    url = None
    while time.time() < deadline:
        try:
            d = requests.get(f"{API}/deployments/{dep}", headers=H, timeout=30).json()
        except Exception as e:
            print(f"[{int(time.time()-t0):>4}s] api error {type(e).__name__}"); time.sleep(20); continue
        eps = d.get("endpoints") or []
        if eps and not url:
            url = eps[0]["url"].rstrip("/"); print("endpoint:", url)
        print(f"[{int(time.time()-t0):>4}s] status={d.get('status')} endpoints={len(eps)}")
        if url:
            try:
                m = requests.get(f"{url}/v1/models", timeout=15)
                if m.status_code == 200 and "data" in m.json():
                    tags = [x["id"] for x in m.json()["data"]]
                    print("  /v1/models ->", tags)
                    if any(model.split(":")[0] in t for t in tags):
                        print(f"\nSERVING after {int(time.time()-t0)}s")
                        print(f"FIXER_BASE_URL={url}/v1")
                        print(f"FIXER_MODEL={model}")
                        return url
            except Exception as e:
                print("   /v1/models not up yet:", type(e).__name__)
        time.sleep(20)
    print("\nTIMED OUT. Still pulling weights, most likely. killpoint-fixer is untouched.")
    return None


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.6:27b")
    ap.add_argument("--market", default=os.environ["NOSANA_MARKET"])
    ap.add_argument("--name", default="killpoint-fixer-qwen36")
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--status", metavar="ID")
    ap.add_argument("--wait", type=int, default=25)
    a = ap.parse_args()

    if a.status:
        poll(a.status, a.model, a.wait); sys.exit(0)

    dep = create(body(a.name, a.market, a.model, a.minutes))
    print("deployment id:", dep)
    open(".deployment_id_qwen36", "w").write(dep)
    start(dep)
    poll(dep, a.model, a.wait)
