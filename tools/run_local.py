"""Run on loopback with fresh in-memory login credentials and explicit Azure CLI identity."""

import argparse
import os
import sys
from pathlib import Path

from make_credentials import new_credentials

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--voice-live-endpoint", required=True)
    parser.add_argument("--gpt-live-endpoint", required=True)
    parser.add_argument("--gpt-live-deployment", default="gpt-live-1")
    parser.add_argument("--username", default="developer")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    values = new_credentials(args.username)
    os.environ.update({
        "AZURE_AUTH_MODE": "cli", "AZURE_SUBSCRIPTION_ID": args.subscription,
        "VOICE_LIVE_ENDPOINT": args.voice_live_endpoint, "GPT_LIVE_ENDPOINT": args.gpt_live_endpoint,
        "GPT_LIVE_DEPLOYMENT": args.gpt_live_deployment, "PORTAL_USERNAME": values["portalUsername"],
        "PORTAL_PASSWORD_HASH": values["portalPasswordHash"],
        "PORTAL_SESSION_SECRET": values["portalSessionSecret"], "ALLOW_INSECURE_LOCALHOST": "true",
    })
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    import uvicorn
    print(f"Open http://localhost:{args.port}/recognition/uat and sign in with the credentials you just chose.")
    uvicorn.run("app:app", host="127.0.0.1", port=args.port, ws_max_size=131072, proxy_headers=False)


if __name__ == "__main__":
    main()
