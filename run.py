"""One command to run Copycat locally: API + UI together.

    python run.py                 # UI on http://localhost:8501, API on 127.0.0.1:5000
    python run.py --login         # same, but require login (needs APP_USERS_JSON, see below)
    python run.py --project my-gcp-project   # call Vertex AI in a specific project

Local runs use YOUR gcloud Application Default Credentials and the project from them
(`gcloud auth application-default login` / `gcloud auth application-default set-quota-project`).
GOOGLE_CLOUD_PROJECT / GCP_PROJECT in your shell are ignored on purpose, so a deployment
project exported for Cloud Shell can never be hit by accident. Use --project to pick one.

Ctrl+C stops both. For Cloud Run the container uses start.sh instead (gunicorn).

Optional env: GCP_LOCATION, COMPANY_NAME, COPYCAT_MODEL_FILE,
and for --login: APP_USERS_JSON='{"me@x.com":"a-long-password"}' ADMIN_EMAILS=me@x.com
"""

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent


def main() -> int:
    env = os.environ.copy()
    env["AUTH_MODE"] = "basic" if "--login" in sys.argv else env.get("AUTH_MODE", "none")
    env.setdefault("API_URL", "http://127.0.0.1:5000")

    # Local = your ADC credentials + your ADC project (never an inherited deployment project).
    for var in ("GOOGLE_CLOUD_PROJECT", "GCP_PROJECT", "GCLOUD_PROJECT"):
        env.pop(var, None)
    if "--project" in sys.argv:
        env["GOOGLE_CLOUD_PROJECT"] = sys.argv[sys.argv.index("--project") + 1]

    api = subprocess.Popen([sys.executable, "app.py"], cwd=HERE, env=env)
    ui = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "ui.py",
            "--server.port", env.get("UI_PORT", "8501"),
            "--browser.gatherUsageStats", "false",
        ],
        cwd=HERE,
        env=env,
    )
    procs = [api, ui]
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A process exited; shutting down.")
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
