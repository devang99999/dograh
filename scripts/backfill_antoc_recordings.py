#!/usr/bin/env python3
"""Backfill Dograh call recordings + transcripts into Antoc call_log remarks.

For each completed workflow run with a recording:
  POST /api/voice/call-end  { phone, recording_url, transcript_url, ... force_new }

Creates one Antoc Call Log per Dograh run (player + bilingual transcript),
same shape the CRM Remarks UI already renders.

Usage:
  python3 scripts/backfill_antoc_recordings.py
  python3 scripts/backfill_antoc_recordings.py --phone 7016896136 --workflow-id 3
  python3 scripts/backfill_antoc_recordings.py --dry-run
  python3 scripts/backfill_antoc_recordings.py --run-ids 36,37,38,39,40
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENV_FILE = Path(__file__).resolve().parent / "antoc_voice.env"


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}")
    return value


def opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def dograh_api(method: str, base: str, path: str, api_key: str, body: dict | None = None) -> Any:
    url = base.rstrip("/") + path
    data = None
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener().open(req, timeout=90) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def antoc_call_end(base: str, secret: str, payload: dict) -> dict:
    url = base.rstrip("/") + "/api/voice/call-end"
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Voice-Secret": secret,
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with opener().open(req, timeout=120) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {"success": False, "http": e.code, "error": detail[:500]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--workflow-id", type=int, default=0)
    parser.add_argument("--phone", default="", help="Fallback phone if run has none")
    parser.add_argument("--run-ids", default="", help="Comma list, e.g. 36,37,40")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--min-duration", type=int, default=5, help="Skip tiny test rings")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args()

    load_env_file(args.env_file)
    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_base = require_env("ANTOC_BASE_URL")
    secret = require_env("ANTOC_VOICE_SECRET")
    workflow_id = args.workflow_id or int(os.environ.get("DOGRAH_WORKFLOW_ID") or "3")
    fallback_phone = (args.phone or os.environ.get("ANTOC_BACKFILL_PHONE") or "7016896136").strip()
    only_ids = {int(x) for x in args.run_ids.split(",") if x.strip().isdigit()}

    print(f"Dograh={dograh_base} workflow={workflow_id}")
    print(f"Antoc={antoc_base} fallback_phone={fallback_phone}")

    listed = dograh_api(
        "GET", dograh_base, f"/api/v1/organizations/usage/runs?limit={args.limit}", api_key
    )["runs"]

    ok = fail = skip = 0
    for row in listed:
        rid = int(row["id"])
        if only_ids and rid not in only_ids:
            continue
        wf = int(row.get("workflow_id") or workflow_id)
        if wf != workflow_id:
            continue

        run = dograh_api("GET", dograh_base, f"/api/v1/workflow/{wf}/runs/{rid}", api_key)
        if not run.get("is_completed"):
            skip += 1
            print(f"skip {rid}: not completed")
            continue

        ic = run.get("initial_context") or {}
        gc = run.get("gathered_context") or {}
        usage = run.get("usage_info") or {}
        duration = int(
            usage.get("call_duration_seconds")
            or (run.get("cost_info") or {}).get("call_duration_seconds")
            or 0
        )
        if duration < args.min_duration:
            skip += 1
            print(f"skip {rid}: duration={duration}s")
            continue

        rec = run.get("recording_public_url") or run.get("recording_url")
        tr = run.get("transcript_public_url") or run.get("transcript_url")
        if not rec:
            skip += 1
            print(f"skip {rid}: no recording")
            continue

        phone = str(ic.get("phone_number") or fallback_phone).strip()
        lead_id = ic.get("lead_id") or ""
        place_id = ic.get("placeId") or ""
        disposition = (
            gc.get("call_disposition")
            or gc.get("disposition")
            or gc.get("mapped_call_disposition")
            or "completed"
        )
        summary = gc.get("summary") or ""

        payload = {
            "phone": phone,
            "lead_id": lead_id or None,
            "placeId": place_id or None,
            "disposition": disposition,
            "summary": summary or None,
            "duration_seconds": duration,
            "recording_url": rec,
            "transcript_url": tr,
            "language": "hi",
            "workflow_run_id": rid,
            "direction": "incoming",
            "force_new": True,
        }
        # Drop nulls
        payload = {k: v for k, v in payload.items() if v is not None and v != ""}

        print(
            f"run {rid}: phone={phone} dur={duration}s "
            f"rec={'yes' if rec else 'no'} tr={'yes' if tr else 'no'}"
        )
        if args.dry_run:
            ok += 1
            continue

        result = antoc_call_end(antoc_base, secret, payload)
        success = bool(result.get("success"))
        if success:
            ok += 1
            print(
                f"  OK remark={result.get('remark_id')} "
                f"recording={result.get('has_recording')} "
                f"transcript={result.get('has_transcript')} "
                f"updated_existing={result.get('updated_existing')}"
            )
        else:
            fail += 1
            print(f"  FAIL {json.dumps(result, ensure_ascii=False)[:300]}")
        time.sleep(args.sleep)

    print(f"\nDone. ok={ok} fail={fail} skip={skip}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
