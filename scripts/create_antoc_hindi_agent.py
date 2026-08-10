#!/usr/bin/env python3
"""Create an Antoc Hindi receptionist voice agent on self-hosted Dograh.

Voice: Sarvam Bulbul \"Anushka\" (hi-IN) — Dograh has no voice literally named
\"Arushi\"; Anushka is the standard Hindi female Sarvam voice. The agent
introduces herself as Arushi.

Creates:
  - Ensures Antoc credential + 4 HTTP tools (same as seed_antoc_voice_tools)
  - New workflow with Global / Start / Agenda / End / Webhook
  - Attaches tools + call-end webhook
  - Sets Hindi TTS (Anushka) on the workflow when Models allow it

Usage:
  python3 scripts/create_antoc_hindi_agent.py
  python3 scripts/create_antoc_hindi_agent.py --name \"Arushi Antoc Receptionist\"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

# Reuse tool seeding helpers
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

# Sarvam Bulbul v2 — closest to requested \"Arushi\"
SARVAM_VOICE = "anushka"
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_LANG = "hi-IN"

GREETING = "Namaste, aapka swagat hai. Main Arushi bol rahi hoon. Main aapki kaise madad kar sakti hoon?"

GLOBAL_PROMPT = """You are Arushi, a warm phone receptionist for a real-estate business on Antoc CRM.

LANGUAGE (most important):
- Always reply in the same language the caller is using.
- If the caller speaks Hindi or Hinglish, reply in simple Hindi (or matching Hinglish). Do NOT reply in English.
- Only use English if the caller clearly speaks only English.

STYLE:
- 1 short sentence at a time.
- Ask only one question at a time.
- If caller says "hello" after you already greeted, do not greet again. Continue the last question.
- Never invent inventory, rates, or availability.
- Never say tool names out loud.
""".strip()

START_PROMPT = """You just received an inbound call.
After the greeting, ask once for the caller's 10-digit mobile number if they have not already given it.
When you have the number, move to Main Agenda.
Keep it short and natural in Hindi/Hinglish.
""".strip()

AGENDA_PROMPT = """CALL FLOW:
1. Ask once for the caller's 10-digit mobile number if missing.
2. When you have it, call get_lead_context with digits only (example: 7016896136), no spaces or +91.
3. If lead exists: greet by name using the tool data. Help briefly. Do not invent details.
4. If no lead: ask one question at a time for name, buy/rent, city, BHK, budget. Then call create_lead.
5. If an existing lead shares new details, call update_lead with phone/lead_id and a short remark.
6. Before ending, call end_call_summary with disposition and a short summary, then go to End Call.
""".strip()

END_PROMPT = """Thank the caller politely in the same language they used and end the call.
Do not start new topics. Keep it to one short goodbye sentence.
""".strip()


def fix_tool_urls(
    base: str, api_key: str, antoc_base: str, tool_uuids: dict[str, str]
) -> None:
    """Repair tools that were saved without a full https URL."""
    path_by_name = {
        "get_lead_context": "/api/voice/context",
        "create_lead": "/api/voice/create",
        "update_lead": "/api/voice/update",
        "end_call_summary": "/api/voice/end-summary",
    }
    for name, tool_uuid in tool_uuids.items():
        tool = api_request("GET", base, f"/api/v1/tools/{tool_uuid}", api_key)
        definition = tool.get("definition") or {}
        config = definition.get("config") or {}
        url = (config.get("url") or "").strip()
        expected = antoc_base.rstrip("/") + path_by_name[name]
        if url.startswith("http"):
            continue
        config["url"] = expected
        if "timeout_ms" not in config or not config["timeout_ms"]:
            config["timeout_ms"] = 10000
        definition["config"] = config
        api_request(
            "PUT",
            base,
            f"/api/v1/tools/{tool_uuid}",
            api_key,
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "definition": definition,
            },
        )
        print(f"Fixed URL for {name} → {expected}")


def build_definition(
    *,
    tool_uuids: list[str],
    credential_uuid: str,
    antoc_base: str,
) -> dict[str, Any]:
    start_id = "start"
    agenda_id = "agenda"
    end_id = "end"
    global_id = "global"
    webhook_id = "webhook"

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
                },
            },
            {
                "id": agenda_id,
                "type": "agentNode",
                "position": {"x": 420, "y": 80},
                "data": {
                    "name": "Main Agenda and Questions",
                    "prompt": AGENDA_PROMPT,
                    "allow_interrupt": True,
                    "add_global_prompt": True,
                    "tool_uuids": tool_uuids,
                },
            },
            {
                "id": end_id,
                "type": "endCall",
                "position": {"x": 720, "y": 80},
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
                "position": {"x": 960, "y": 80},
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
                        "direction": "incoming",
                        "placeId": "{{initial_context.placeId}}",
                    },
                },
            },
        ],
        "edges": [
            {
                "id": str(uuid.uuid4()),
                "source": start_id,
                "target": agenda_id,
                "data": {
                    "label": "Move to Main Agenda",
                    "condition": "Caller shared their phone number or is ready to continue the inquiry.",
                },
            },
            {
                "id": str(uuid.uuid4()),
                "source": start_id,
                "target": end_id,
                "data": {
                    "label": "End call",
                    "condition": "Caller wants to hang up immediately or wrong number.",
                },
            },
            {
                "id": str(uuid.uuid4()),
                "source": agenda_id,
                "target": end_id,
                "data": {
                    "label": "End call",
                    "condition": "Inquiry handled and end_call_summary has been called, or caller wants to end.",
                },
            },
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def hindi_tts_configurations() -> dict[str, Any]:
    """Full BYOK v2 override — required when org LLM is Dograh-managed.

    Plain model_overrides get converted back to Dograh mode and wipe Sarvam TTS
    (American-accent Hindi). Explicit BYOK pipeline keeps Anushka.
    """
    sarvam_key = os.environ.get("SARVAM_API_KEY", "").strip()
    gemini_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
        or os.environ.get("GOOGLE_API_KEY", "").strip()
    )
    if not sarvam_key or sarvam_key.startswith("REPLACE"):
        print(
            "No SARVAM_API_KEY — skipping voice override. "
            "Run scripts/fix_antoc_hindi_voice.py after adding the key."
        )
        return {}
    if not gemini_key or gemini_key.startswith("REPLACE"):
        print(
            "No GEMINI_API_KEY/GOOGLE_API_KEY — cannot build BYOK pipeline. "
            "Run scripts/fix_antoc_hindi_voice.py after adding Gemini key."
        )
        return {}

    return {
        "model_configuration_v2_override": {
            "version": 2,
            "mode": "byok",
            "byok": {
                "mode": "pipeline",
                "pipeline": {
                    "llm": {
                        "provider": "google",
                        "model": "gemini-2.5-flash",
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
                        "model": "saarika:v2.5",
                        "language": SARVAM_LANG,
                        "api_key": sarvam_key,
                    },
                },
            },
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--name",
        default="Arushi Antoc Hindi Receptionist",
        help="Workflow / agent display name",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish the draft after create/update",
    )
    parser.add_argument(
        "--workflow-id",
        type=int,
        default=None,
        help="Update an existing workflow instead of creating a new one",
    )
    parser.add_argument(
        "--test-phone",
        default=None,
        help="Optional: test get_lead_context after create",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_base = require_env("ANTOC_BASE_URL")
    antoc_secret = require_env("ANTOC_VOICE_SECRET")

    try:
        with _opener().open(
            dograh_base.rstrip("/") + "/api/v1/health", timeout=15
        ) as resp:
            health = json.loads(resp.read().decode())
            print(f"Dograh health: {health.get('status')} v{health.get('version')}")
    except Exception as e:
        raise SystemExit(f"Cannot reach Dograh at {dograh_base}: {e}") from e

    cred_uuid = ensure_credential(dograh_base, api_key, antoc_secret)
    tool_uuids = ensure_tools(dograh_base, api_key, antoc_base, cred_uuid)
    fix_tool_urls(dograh_base, api_key, antoc_base, tool_uuids)

    definition = build_definition(
        tool_uuids=list(tool_uuids.values()),
        credential_uuid=cred_uuid,
        antoc_base=antoc_base,
    )
    configs = hindi_tts_configurations()

    if args.workflow_id is not None:
        workflow_id = args.workflow_id
        put_body: dict[str, Any] = {
            "name": args.name,
            "workflow_definition": definition,
        }
        if configs:
            put_body["workflow_configurations"] = configs
        api_request(
            "PUT",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}",
            api_key,
            put_body,
        )
        print(f"Updated workflow id={workflow_id}")
    else:
        created = api_request(
            "POST",
            dograh_base,
            "/api/v1/workflow/create/definition",
            api_key,
            {"name": args.name, "workflow_definition": definition},
        )
        workflow_id = created["id"]
        print(f"Created workflow id={workflow_id} name={created.get('name')}")
        if configs:
            api_request(
                "PUT",
                dograh_base,
                f"/api/v1/workflow/{workflow_id}",
                api_key,
                {
                    "name": args.name,
                    "workflow_definition": definition,
                    "workflow_configurations": configs,
                },
            )
            print(
                f"Set Hindi TTS voice={SARVAM_VOICE} (Anushka / Arushi) "
                f"lang={SARVAM_LANG} on workflow {workflow_id}"
            )
        else:
            print("Workflow created without TTS override (no SARVAM_API_KEY).")

    if args.publish:
        api_request(
            "POST",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}/publish",
            api_key,
            {},
        )
        print(f"Published workflow {workflow_id}")

    if args.test_phone:
        result = api_request(
            "POST",
            dograh_base,
            f"/api/v1/tools/{tool_uuids['get_lead_context']}/test",
            api_key,
            {"llm_params": {"phone": args.test_phone}},
        )
        print("Test get_lead_context:")
        print(json.dumps(result, indent=2, default=str)[:2000])

    print(
        f"Open: {dograh_base.rstrip('/')}/workflow/{workflow_id}\n"
        "Use Test Agent / Test Audio. Agent name spoken: Arushi; "
        f"TTS speaker: {SARVAM_VOICE}."
    )


if __name__ == "__main__":
    main()
