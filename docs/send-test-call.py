"""Run with the project's virtual environment; sends one real test call to local CRM."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
parser = argparse.ArgumentParser()
parser.add_argument("--event-id", required=True)
parser.add_argument("--phone", required=True)
parser.add_argument("--service-id", type=int)
args = parser.parse_args()
token = os.environ.get("TELEPHONY_WEBHOOK_TOKEN", "")
if not token:
    raise SystemExit("Set TELEPHONY_WEBHOOK_TOKEN in .env first.")
payload = {"event_id": args.event_id, "phone": args.phone}
if args.service_id:
    payload["service_id"] = args.service_id
request = Request("http://127.0.0.1:8000/api/telephony/incoming/",
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
try:
    with urlopen(request, timeout=15) as response:
        print(response.status, response.read().decode())
except HTTPError as error:
    raise SystemExit(f"CRM returned HTTP {error.code}; check token and event data.") from None
except URLError:
    raise SystemExit("Cannot connect to CRM. Start runserver.") from None
