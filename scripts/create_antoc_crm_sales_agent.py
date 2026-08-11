#!/usr/bin/env python3
"""Create Neha — internal Antoc CRM cold-call sales agent.

Sells Antoc AI CRM to real-estate brokers/builders (Hindi).
Does NOT modify inbound workflow 3 or outbound workflow 4.

Wired to Antoc CRM business place_id=antoc_194e7e885e4679c1 so call-end
remarks land on THAT tenant, not VOICE_DEFAULT_PLACE_ID.

Usage:
  python3 scripts/create_antoc_crm_sales_agent.py --publish
  python3 scripts/create_antoc_crm_sales_agent.py --workflow-id N --publish
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    DEFAULT_ENV_FILE,
    api_request,
    ensure_credential,
    ensure_tools,
    load_env_file,
    require_env,
)

AGENT_NAME = "Neha Antoc CRM Sales"
# Antoc CRM company account — do not use VOICE_DEFAULT_PLACE_ID
ANTOC_CRM_PLACE_ID = "antoc_194e7e885e4679c1"

SARVAM_VOICE = "anushka"  # female, matches Neha
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_STT_MODEL = "saarika:v2.5"
SARVAM_TTS_LANG = "hi-IN"
SARVAM_STT_LANG = "unknown"  # Hindi + English replies
GEMINI_MODEL = "gemini-2.5-flash"

GREETING = (
    "नमस्ते, मैं नेहा बोल रही हूँ, Antoc AI की तरफ़ से. "
    "हम Ahmedabad की real estate CRM company हैं. अभी थोड़ा समय है?"
)

GLOBAL_PROMPT = """
You are Neha at Antoc AI, Ahmedabad. Outbound cold call: sell Antoc AI CRM to brokers and builders.

Say ONLY the next line, in Hindi (Devanagari). English only for: Antoc AI, CRM, WhatsApp, Facebook, 99acres, MagicBricks, Housing, Meta, Google, Demo.
No English thinking. No prices. No tool names. Not a receptionist. Not a property-buyer call.

If lead_name is set, use it. Never ask name or phone. One question. Max 2 short sentences.

Before goodbye call end_call_summary once: callback_requested (busy, with time), not_interested, demo_scheduled, info_only (WhatsApp only), qualified, no_answer, wrong_number.
""".strip()

START_PROMPT = """
Greeting already played. Continue as Neha. Speak ONLY Hindi (Devanagari). English words only for: Antoc AI, CRM, WhatsApp, Facebook, 99acres, MagicBricks, Housing, Meta, Google, Demo.
Never say Antor. Never say tool names. Never invent price. One question. Max 2 short sentences.

lead_name={{lead_name}}  If set, use Mr/Mrs that name. Never ask name or phone.
Campaign notes (footnote only, do not replace this flow): {{outbound_script}}

If they have time:
"Antoc AI CRM real estate brokers और builders के लिए है. ताके leads miss न हों और follow-up सही रहे."

If busy:
"कोई बात नहीं. आपके फ्री टाइम पर मैं फिर से कॉल करूँगी. कौन सा दिन और कितने बजे ठीक रहेगा?"
Save day+time, then end_call_summary.

If not interested — do not hang up yet:
"एक बात कहना चाहूँगी. जब आपका lead आता है और follow-up या missed call रह जाती है, तो lead miss हो जाता है — ऐसा होता है ना?"
If they agree: "इसी के लिए हम Antoc AI CRM देते हैं, आपके business के lead follow-up के लिए." Then qualify.
If still no: "धन्यवाद, फिर कभी बात करेंगे." end_call_summary.

If they already use a CRM:
"अच्छा. अभी आप कौन सा CRM use करते हैं?"
After they name it, say 2–3 points only: Antoc AI CRM brokers/builders के लिए है — सारे leads एक जगह, WhatsApp automation, calling और reports, property-project, mobile app, team tracking, 99acres MagicBricks Housing Meta Google.

Then ask ONE at a time:
पहले आपके leads कैसे आते हैं?
उन्हें manage कैसे करते हैं?
कौन सा CRM है? (skip if already answered)
आपके under कितने sales agents हैं?
Facebook और property portals से leads लेते हैं?
Follow-up manually चलता है?

If they want details:
"बिल्कुल. WhatsApp पर भेज दूँ? फिर 10-15 मिनट का demo. कौन सा समय सूट करेगा?"
end_call_summary when demo time is set, or if WhatsApp-only with no time.

Do not ask buy/rent/BHK/budget. Put CRM name, team size, lead source, demo time in summary.
""".strip()

END_PROMPT = (
    "हिंदी में एक छोटे वाक्य में धन्यवाद कहो और कॉल खत्म करो."
)


def byok_hindi_stack(*, gemini_key: str, sarvam_key: str) -> dict[str, Any]:
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
                    "language": SARVAM_TTS_LANG,
                    "speed": 1.0,
                    "api_key": sarvam_key,
                },
                "stt": {
                    "provider": "sarvam",
                    "model": SARVAM_STT_MODEL,
                    "language": SARVAM_STT_LANG,
                    "api_key": sarvam_key,
                },
            },
        },
    }


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
                    "name": "Antoc CRM sales outbound",
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
                    "pre_call_fetch_enabled": False,
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
                    "name": "Antoc CRM call-end",
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
                        "placeId": ANTOC_CRM_PLACE_ID,
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
                        "Caller wants to hang up, is busy after a callback was noted, "
                        "wrong person, or the CRM sales inquiry finished after end_call_summary."
                    ),
                },
            }
        ],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


def voice_config(*, gemini_key: str, sarvam_key: str) -> dict[str, Any]:
    return {
        "model_configuration_v2_override": byok_hindi_stack(
            gemini_key=gemini_key, sarvam_key=sarvam_key
        ),
        "smart_turn_stop_secs": 0.9,
        "provisional_vad_pause_secs": 0.6,
        "turn_start_strategy": "provisional_vad",
        "max_call_duration": 360,
    }


def existing_trigger_path(base: str, api_key: str, workflow_id: int) -> str:
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)
    for node in (wf.get("workflow_definition") or {}).get("nodes") or []:
        if node.get("type") == "trigger":
            return str((node.get("data") or {}).get("trigger_path") or "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--name", default=AGENT_NAME)
    parser.add_argument("--publish", action="store_true", default=True)
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--workflow-id", type=int, default=0)
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
    trigger_path = ""
    if args.workflow_id:
        trigger_path = existing_trigger_path(dograh_base, api_key, args.workflow_id)

    definition = build_definition(
        tool_uuids=[
            tool_uuids[n]
            for n in ("get_lead_context", "end_call_summary")
            if n in tool_uuids
        ]
        or list(tool_uuids.values()),
        credential_uuid=cred_uuid,
        antoc_base=antoc_base,
        trigger_path=trigger_path,
    )
    cfg = voice_config(gemini_key=gemini_key, sarvam_key=sarvam_key)

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
                "workflow_configurations": cfg,
            },
        )
        print(f"Updated CRM sales workflow id={workflow_id}")
    else:
        created = api_request(
            "POST",
            dograh_base,
            "/api/v1/workflow/create/definition",
            api_key,
            {"name": args.name, "workflow_definition": definition},
        )
        workflow_id = created["id"]
        print(f"Created CRM sales workflow id={workflow_id}")
        api_request(
            "PUT",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}",
            api_key,
            {
                "name": args.name,
                "workflow_definition": definition,
                "workflow_configurations": cfg,
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
        print(f"Published CRM sales workflow {workflow_id}")

    wf = api_request(
        "GET", dograh_base, f"/api/v1/workflow/fetch/{workflow_id}", api_key
    )
    trigger_uuid = ""
    for node in (wf.get("workflow_definition") or {}).get("nodes") or []:
        if node.get("type") == "trigger":
            trigger_uuid = (node.get("data") or {}).get("trigger_path") or ""
    workflow_uuid = wf.get("workflow_uuid") or wf.get("uuid") or ""

    print("\nDid NOT modify inbound 3 or outbound 4.")
    print(f"CRM sales agent: {dograh_base}/workflow/{workflow_id}")
    print(f"Wired call-end placeId={ANTOC_CRM_PLACE_ID} (Antoc CRM business).")
    print("Set on Antoc API (.env) — this tenant only, keep global OUTBOUND_* on wf4:")
    print(f"  DOGRAH_CRM_SALES_WORKFLOW_ID={workflow_id}")
    if workflow_uuid:
        print(f"  DOGRAH_CRM_SALES_WORKFLOW_UUID={workflow_uuid}")
    if trigger_uuid:
        print(f"  DOGRAH_CRM_SALES_TRIGGER_UUID={trigger_uuid}")
        print(
            f"  DOGRAH_OUTBOUND_BY_PLACE={ANTOC_CRM_PLACE_ID}:{trigger_uuid}"
        )


if __name__ == "__main__":
    main()
