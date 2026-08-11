#!/usr/bin/env python3
"""Create a dedicated Antoc OUTBOUND sales agent on Dograh.

Does NOT modify inbound workflow 3 (receptionist). Pins the same Hindi
BYOK stack (Gemini + Sarvam TTS/STT) with real keys — copying inbound's
override is impossible because GET masks api_key. Adds an API Trigger so
Antoc CRM can dial selected leads with a per-campaign script.

Usage:
  python3 scripts/create_antoc_outbound_agent.py --publish
  python3 scripts/create_antoc_outbound_agent.py --workflow-id N --publish
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    DEFAULT_ENV_FILE,
    _opener,
    api_request,
    ensure_credential,
    ensure_tools,
    load_env_file,
    require_env,
)

AGENT_NAME = "Arushi Antoc Outbound Sales"

# Do not interpolate empty CRM fields — Test Agent has no lead/business context
# and previously rendered as "Namaste ji, main Arushi bol rahi hoon se".
GREETING = (
    "Namaste ji, main Arushi bol rahi hoon Antoc se. "
    "Kya aap ek minute baat kar sakte hain?"
)

SARVAM_VOICE = "anushka"
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_STT_MODEL = "saarika:v2.5"
SARVAM_LANG = "hi-IN"
GEMINI_MODEL = "gemini-3.5-flash"

GLOBAL_PROMPT = """You are Arushi placing an OUTBOUND sales call for Antoc CRM (real estate).

OUTPUT: ONLY the final Hindi/Hinglish line to the person. Never English thinking/planning.
Never invent inventory, prices, or availability. Never say tool names out loud.
If lead_name is set: NEVER ask their name. Never ask for their phone number.

This is an outbound call YOU placed. Do not greet as if they called you.
Follow OUTBOUND SCRIPT below. Ask one question at a time. Max 2 short sentences.

If they are busy: offer a callback, then end_call_summary (disposition=callback_requested).
If voicemail / no person: end politely, disposition=no_answer.
Before goodbye: call end_call_summary ONCE with disposition + Hindi summary + fields.
""".strip()

START_PROMPT = """You handle the FULL outbound call after the greeting.

OUTBOUND SCRIPT (follow this; do not ignore it):
{{outbound_script}}

CONTEXT:
lead_name={{lead_name}} lead_found={{lead_found}} phone={{phone_number}}
lead_summary={{lead_summary}}
prior_requirements={{prior_requirements}}
known_city={{known_city}} known_area={{known_area}} known_bhk={{known_bhk}}
known_budget={{known_budget}}

RULES:
- If lead_name is set, address them by name. Never ask their name.
- If outbound_script is empty: qualify buy/rent, city, area, BHK, budget one question at a time.
- If they already have prior_requirements, confirm in one short line, then follow the script.
- Do NOT call move_to_main_agenda (there is no agenda hop).
- Call end_call_summary once before goodbye.
""".strip()

END_PROMPT = (
    "Thank them politely in Hindi/Hinglish in one short sentence and end the call."
)


def build_definition(
    *,
    tool_uuids: list[str],
    credential_uuid: str,
    antoc_base: str,
    trigger_path: str = "",
) -> dict[str, Any]:
    start_id = "start"
    end_id = "end"
    global_id = "global"
    webhook_id = "webhook"
    trigger_id = "api_trigger"

    return {
        "nodes": [
            {
                "id": global_id,
                "type": "globalNode",
                "position": {"x": -200, "y": 80},
                "data": {
                    "name": "Global Node",
                    "prompt": GLOBAL_PROMPT,
                    "allow_interrupt": True,
                },
            },
            {
                "id": trigger_id,
                "type": "trigger",
                "position": {"x": 120, "y": -80},
                "data": {
                    "name": "Antoc CRM outbound",
                    "enabled": True,
                    **({"trigger_path": trigger_path} if trigger_path else {}),
                },
            },
            {
                "id": start_id,
                "type": "startCall",
                "position": {"x": 120, "y": 80},
                "data": {
                    "name": "start call",
                    "greeting_type": "text",
                    "greeting": GREETING,
                    "prompt": START_PROMPT,
                    "is_start": True,
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "delayed_start": True,
                    "delayed_start_duration": 0.8,
                    "tool_uuids": tool_uuids,
                },
            },
            {
                "id": end_id,
                "type": "endCall",
                "position": {"x": 520, "y": 80},
                "data": {
                    "name": "End Call",
                    "prompt": END_PROMPT,
                    "is_end": True,
                    "allow_interrupt": False,
                    "add_global_prompt": True,
                },
            },
            {
                "id": webhook_id,
                "type": "webhook",
                "position": {"x": 760, "y": 80},
                "data": {
                    "name": "Antoc call-end",
                    "enabled": True,
                    "http_method": "POST",
                    "endpoint_url": antoc_base.rstrip("/") + "/api/voice/call-end",
                    "credential_uuid": credential_uuid,
                    "payload_template": {
                        "phone": "{{initial_context.phone_number}}",
                        "lead_id": "{{initial_context.lead_id}}",
                        "disposition": "{{gathered_context.call_disposition}}",
                        "call_disposition": "{{gathered_context.call_disposition}}",
                        "summary": "{{gathered_context.summary}}",
                        "duration_seconds": "{{cost_info.call_duration_seconds}}",
                        "recording_url": "{{recording_url}}",
                        "transcript_url": "{{transcript_url}}",
                        "language": "hi",
                        "workflow_run_id": "{{workflow_run_id}}",
                        "direction": "outgoing",
                        "placeId": "{{initial_context.placeId}}",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": str(uuid.uuid4()),
                "source": start_id,
                "target": end_id,
                "data": {
                    "label": "End call",
                    "condition": (
                        "Caller wants to hang up, is busy, wrong person, "
                        "or inquiry finished after end_call_summary."
                    ),
                },
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def byok_hindi_stack(*, gemini_key: str, sarvam_key: str) -> dict[str, Any]:
    """Same Hindi BYOK as inbound: Gemini + Sarvam TTS/STT.

    Cannot copy workflow 3's override — GET masks api_key and saving that
    makes DograhSTTService connect with a junk key (WebSocket HTTP 400).
    """
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


def copy_inbound_voice_config(
    base: str,
    api_key: str,
    inbound_id: int,
    *,
    gemini_key: str,
    sarvam_key: str,
) -> dict[str, Any]:
    """Copy VAD/call limits from inbound; pin real BYOK keys (not masked)."""
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{inbound_id}", api_key)
    cfg = dict(wf.get("workflow_configurations") or {})
    cfg.pop("model_overrides", None)
    cfg["model_configuration_v2_override"] = byok_hindi_stack(
        gemini_key=gemini_key, sarvam_key=sarvam_key
    )
    cfg["smart_turn_stop_secs"] = 0.9
    cfg["provisional_vad_pause_secs"] = 0.6
    cfg["turn_start_strategy"] = "provisional_vad"
    cfg["max_call_duration"] = min(int(cfg.get("max_call_duration") or 300), 240)
    return cfg


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--name", default=AGENT_NAME)
    parser.add_argument("--publish", action="store_true", default=True)
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--workflow-id", type=int, default=0)
    parser.add_argument(
        "--inbound-workflow-id",
        type=int,
        default=3,
        help="Copy Arushi voice config from this inbound agent (not modified).",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_base = require_env("ANTOC_BASE_URL")
    antoc_secret = require_env("ANTOC_VOICE_SECRET")
    sarvam_key = require_env("SARVAM_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip() or require_env(
        "GOOGLE_API_KEY"
    )

    cred_uuid = ensure_credential(dograh_base, api_key, antoc_secret)
    tool_uuids = ensure_tools(dograh_base, api_key, antoc_base, cred_uuid)

    existing_trigger = os.environ.get("DOGRAH_OUTBOUND_TRIGGER_UUID", "").strip()
    if args.workflow_id:
        existing_wf = api_request(
            "GET", dograh_base, f"/api/v1/workflow/fetch/{args.workflow_id}", api_key
        )
        for node in (existing_wf.get("workflow_definition") or {}).get("nodes") or []:
            if node.get("type") == "trigger":
                existing_trigger = (
                    (node.get("data") or {}).get("trigger_path") or existing_trigger
                )
                break
        # Prefer the originally published trigger so Antoc env does not rot.
        env_trigger = os.environ.get("DOGRAH_OUTBOUND_TRIGGER_UUID", "").strip()
        if env_trigger:
            existing_trigger = env_trigger

    definition = build_definition(
        tool_uuids=[
            tool_uuids[n]
            for n in ("get_lead_context", "end_call_summary")
            if n in tool_uuids
        ]
        or list(tool_uuids.values()),
        credential_uuid=cred_uuid,
        antoc_base=antoc_base,
        trigger_path=existing_trigger,
    )
    voice_cfg = copy_inbound_voice_config(
        dograh_base,
        api_key,
        args.inbound_workflow_id,
        gemini_key=gemini_key,
        sarvam_key=sarvam_key,
    )

    workflow_id = args.workflow_id
    if workflow_id:
        api_request(
            "PUT",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}",
            api_key,
            {
                "name": args.name,
                "workflow_definition": definition,
                "workflow_configurations": voice_cfg,
            },
        )
        print(f"Updated outbound workflow id={workflow_id}")
    else:
        created = api_request(
            "POST",
            dograh_base,
            "/api/v1/workflow/create/definition",
            api_key,
            {"name": args.name, "workflow_definition": definition},
        )
        workflow_id = created["id"]
        print(f"Created outbound workflow id={workflow_id}")
        api_request(
            "PUT",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}",
            api_key,
            {
                "name": args.name,
                "workflow_definition": definition,
                "workflow_configurations": voice_cfg,
            },
        )

    if args.publish and not args.no_publish:
        api_request(
            "POST",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}/publish",
            api_key,
            {},
        )
        print(f"Published outbound workflow {workflow_id}")

    wf = api_request(
        "GET", dograh_base, f"/api/v1/workflow/fetch/{workflow_id}", api_key
    )
    trigger_uuid = ""
    for node in (wf.get("workflow_definition") or {}).get("nodes") or []:
        if node.get("type") == "trigger":
            trigger_uuid = (node.get("data") or {}).get("trigger_path") or ""
    workflow_uuid = wf.get("workflow_uuid") or wf.get("uuid") or ""

    print("\nInbound workflow 3 was NOT modified.")
    print(f"Outbound agent: {dograh_base}/workflow/{workflow_id}")
    print("Set these on Antoc (.env) — outbound only:")
    print(f"  DOGRAH_OUTBOUND_WORKFLOW_ID={workflow_id}")
    if workflow_uuid:
        print(f"  DOGRAH_OUTBOUND_WORKFLOW_UUID={workflow_uuid}")
    if trigger_uuid:
        print(f"  DOGRAH_OUTBOUND_TRIGGER_UUID={trigger_uuid}")


if __name__ == "__main__":
    main()
