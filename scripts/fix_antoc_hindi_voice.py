#!/usr/bin/env python3
"""Fix Antoc Hindi agent: real Sarvam Anushka voice + clean up duplicate agents.

Why American-accent Hindi happened
---------------------------------
Org Models were Dograh-managed. Saving workflow ``model_overrides`` with
Sarvam got converted back to full Dograh mode (because LLM still used a
Dograh key), so TTS stayed English/American while the LLM spoke Hindi text.

This script:
  1. Sets org Models → BYOK: Gemini LLM + Sarvam TTS (anushka, hi-IN) + Sarvam STT
  2. Pins the same BYOK config on workflow 3 via model_configuration_v2_override
  3. Archives duplicate agents (default: 1 and 2)
  4. Attaches Antoc credential to all voice tools (fixes 401 on tool test)
  5. Publishes workflow 3

Usage:
  python3 scripts/fix_antoc_hindi_voice.py
  python3 scripts/fix_antoc_hindi_voice.py --keep-workflow 3 --archive 1,2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    CREDENTIAL_NAME,
    DEFAULT_ENV_FILE,
    _opener,
    api_request,
    ensure_credential,
    load_env_file,
    require_env,
)

SARVAM_VOICE = "anushka"
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_STT_MODEL = "saarika:v2.5"
SARVAM_LANG = "hi-IN"
GEMINI_MODEL = "gemini-2.5-flash"


def load_secret_from_antoc(name: str) -> str:
    antoc_env = Path("/Users/devanggandhi/Desktop/passdn/Antoc_Server_Node/.env")
    if not antoc_env.is_file():
        return ""
    m = re.search(rf"^{re.escape(name)}=(.+)$", antoc_env.read_text(), re.M)
    if not m:
        return ""
    return m.group(1).strip().strip('"').strip("'")


def require_any(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value and not value.startswith("REPLACE"):
            return value
        value = load_secret_from_antoc(name)
        if value and not value.startswith("REPLACE"):
            os.environ[name] = value
            return value
    raise SystemExit(f"Need one of {', '.join(names)} in env / antoc_voice.env / Antoc .env")


def byok_v2_config(*, gemini_key: str, sarvam_key: str) -> dict[str, Any]:
    return {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "pipeline",
            "pipeline": {
                "llm": {
                    "provider": "google",
                    "model": GEMINI_MODEL,
                    "api_key": gemini_key,
                },
                "tts": {
                    "provider": "sarvam",
                    "model": SARVAM_TTS_MODEL,
                    "voice": SARVAM_VOICE,
                    "language": SARVAM_LANG,
                    "speed": 1.0,
                    "api_key": sarvam_key,
                },
                "stt": {
                    "provider": "sarvam",
                    "model": SARVAM_STT_MODEL,
                    "language": SARVAM_LANG,
                    "api_key": sarvam_key,
                },
            },
        },
    }


def set_org_models(base: str, api_key: str, v2: dict[str, Any]) -> None:
    """PUT legacy-shaped config; API converts to org v2 BYOK."""
    pipe = v2["byok"]["pipeline"]
    body = {
        "llm": pipe["llm"],
        "tts": pipe["tts"],
        "stt": pipe["stt"],
        "is_realtime": False,
    }
    api_request("PUT", base, "/api/v1/user/configurations/user", api_key, body)
    print(
        "Org Models → BYOK: Google LLM + Sarvam TTS "
        f"({SARVAM_VOICE}/{SARVAM_LANG}) + Sarvam STT"
    )


def pin_workflow_voice(
    base: str, api_key: str, workflow_id: int, v2: dict[str, Any]
) -> None:
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)
    definition = wf.get("workflow_definition") or {}
    existing_cfg = wf.get("workflow_configurations") or {}
    configs = {
        **existing_cfg,
        "model_configuration_v2_override": v2,
    }
    configs.pop("model_overrides", None)
    api_request(
        "PUT",
        base,
        f"/api/v1/workflow/{workflow_id}",
        api_key,
        {
            "name": wf.get("name"),
            "workflow_definition": definition,
            "workflow_configurations": configs,
            "template_context_variables": wf.get("template_context_variables"),
            "call_disposition_codes": wf.get("call_disposition_codes"),
        },
    )
    print(
        f"Pinned Sarvam Anushka (hi-IN) BYOK on workflow {workflow_id} "
        f"({wf.get('name')})"
    )


def publish(base: str, api_key: str, workflow_id: int) -> None:
    api_request(
        "POST", base, f"/api/v1/workflow/{workflow_id}/publish", api_key, {}
    )
    print(f"Published workflow {workflow_id}")


def archive_workflows(base: str, api_key: str, ids: list[int]) -> None:
    for wid in ids:
        try:
            api_request(
                "PUT",
                base,
                f"/api/v1/workflow/{wid}/status",
                api_key,
                {"status": "archived"},
            )
            print(f"Archived workflow {wid}")
        except SystemExit as e:
            print(f"Skip archive {wid}: {e}")


def attach_credential_to_tools(base: str, api_key: str, credential_uuid: str) -> None:
    tools = api_request("GET", base, "/api/v1/tools/?status=active", api_key) or []
    wanted = {
        "get_lead_context",
        "create_lead",
        "update_lead",
        "end_call_summary",
    }
    for tool in tools:
        if tool.get("name") not in wanted:
            continue
        definition = tool.get("definition") or {}
        config = definition.get("config") or {}
        if config.get("credential_uuid") == credential_uuid:
            print(f"Tool {tool['name']}: credential already set")
            continue
        config["credential_uuid"] = credential_uuid
        if not (config.get("url") or "").startswith("http"):
            print(f"Tool {tool['name']}: WARNING missing URL — fix in UI")
        definition["config"] = config
        api_request(
            "PUT",
            base,
            f"/api/v1/tools/{tool['tool_uuid']}",
            api_key,
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "definition": definition,
            },
        )
        print(f"Tool {tool['name']}: attached {CREDENTIAL_NAME}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--keep-workflow", type=int, default=3)
    parser.add_argument(
        "--archive",
        default="1,2",
        help="Comma-separated workflow ids to archive (default: 1,2)",
    )
    parser.add_argument("--skip-org", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file)
    # Pull keys from Antoc .env if missing locally
    for key in ("SARVAM_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTOC_VOICE_SECRET"):
        if not os.environ.get(key):
            val = load_secret_from_antoc(key)
            if val:
                os.environ[key] = val

    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_secret = require_env("ANTOC_VOICE_SECRET")
    sarvam_key = require_any("SARVAM_API_KEY")
    gemini_key = require_any("GEMINI_API_KEY", "GOOGLE_API_KEY")

    with _opener().open(dograh_base.rstrip("/") + "/api/v1/health", timeout=15) as resp:
        health = json.loads(resp.read().decode())
        print(f"Dograh health: {health.get('status')} v{health.get('version')}")

    v2 = byok_v2_config(gemini_key=gemini_key, sarvam_key=sarvam_key)

    if not args.skip_org:
        set_org_models(dograh_base, api_key, v2)

    cred_uuid = ensure_credential(dograh_base, api_key, antoc_secret)
    attach_credential_to_tools(dograh_base, api_key, cred_uuid)

    pin_workflow_voice(dograh_base, api_key, args.keep_workflow, v2)
    if not args.no_publish:
        publish(dograh_base, api_key, args.keep_workflow)

    archive_ids = [
        int(x.strip()) for x in args.archive.split(",") if x.strip().isdigit()
    ]
    archive_ids = [i for i in archive_ids if i != args.keep_workflow]
    if archive_ids:
        archive_workflows(dograh_base, api_key, archive_ids)

    print(
        f"\nUse ONLY: {dograh_base.rstrip('/')}/workflow/{args.keep_workflow}\n"
        f"Voice must be Sarvam {SARVAM_VOICE} ({SARVAM_LANG}) — Indian Hindi, not US accent.\n"
        "Test Audio on that agent. If still American, hard-refresh and confirm Models shows BYOK Sarvam."
    )


if __name__ == "__main__":
    main()
