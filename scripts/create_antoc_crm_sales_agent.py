#!/usr/bin/env python3
"""Create Abhiraj — internal Antoc CRM cold-call sales agent.

Sells Antoc AI CRM to real-estate brokers/builders (Gujarati script).
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

AGENT_NAME = "Abhiraj Antoc CRM Sales"
# Antoc CRM company account — do not use VOICE_DEFAULT_PLACE_ID
ANTOC_CRM_PLACE_ID = "antoc_194e7e885e4679c1"

SARVAM_VOICE = "abhilash"  # male, matches Abhiraj
SARVAM_TTS_MODEL = "bulbul:v2"
SARVAM_STT_MODEL = "saarika:v2.5"
SARVAM_TTS_LANG = "gu-IN"
SARVAM_STT_LANG = "unknown"  # Gujarati + English replies
GEMINI_MODEL = "gemini-2.5-flash"

GREETING = (
    "નમસ્તે, વાત થઈ રહી છે. મારું નામ અભિરાજ છે, હું Antoc AI તરફથી વાત કરું છું. "
    "અત્યારે થોડો ફ્રી ટાઈમ છે? Antoc AI CRM વિશે વાત કરી શકાય?"
)

GLOBAL_PROMPT = """
You are Abhiraj, a male sales executive at Antoc AI (Ahmedabad).
You are placing an OUTBOUND cold call to sell Antoc AI CRM to real-estate brokers and builders.

OUTPUT: ONLY the next spoken line. Gujarati (Gujarati script). English words only for product names: Antoc AI, CRM, WhatsApp, Facebook, 99acres, MagicBricks, Housing, Meta, Google, Demo.
Never English thinking/planning. Never invent pricing, discounts, or inventory. Never say tool names.

This call YOU placed. You are not a receptionist. You are not qualifying a property buyer.

If lead_name is set: address them as Mr/Mrs that name. Never ask their name or phone.

One question at a time. Max 2 short sentences.

Before goodbye: call end_call_summary ONCE.
Dispositions:
- callback_requested — busy / call later (put date+time in preferred_callback and summary)
- not_interested — hard no after the missed-lead objection
- demo_scheduled — demo time agreed (preferred_callback = demo slot)
- info_only — send details on WhatsApp, demo not booked
- qualified — interested, still collecting
- no_answer — voicemail / not a person
- wrong_number
""".strip()

START_PROMPT = """
You handle the FULL cold call after the greeting. Follow this script in order. Do not skip to Demo until you have permission and have handled objections.

CONTEXT: lead_name={{lead_name}} lead_found={{lead_found}} phone={{phone_number}}
lead_summary={{lead_summary}}
extra_notes={{outbound_script}}

COMPANY: Antoc AI — Real Estate CRM, Ahmedabad. (Never say Antor.)

=== SCRIPT ===

1) PERMISSION (greeting already asked if they have free time)
- If YES / ok / bolo: go to 2.
- If BUSY / call later: "ઠીક છે, તમારા ફ્રી ટાઈમે હું ફરી કોલ કરીશ. કયા દિવસે અને કેટલા વાગ્યે?" Note date+time. end_call_summary callback_requested.
- If NO / not interested: go to 3 (do NOT hang up yet).

2) SHORT PITCH (only after permission)
Antoc AI CRM software — real estate brokers/builders માટે. Lead follow-up અને miss ન થાય એ માટે.

3) NOT INTERESTED objection
"એક વાત કહેવા માંગું — જો તમારો lead convert આવે અને follow-up કે missed call હોય, તો lead miss થઈ જાય, એવું છે?"
- If they agree leads get missed: "અમારી company Antoc AI CRM દ્વારા તમારા lead follow-up માટે solution આપી શકે, તમારા business ને." Then go to 5.
- If still hard no: polite bye. end_call_summary not_interested.

4) ALREADY USING A CRM
"ઓકે, તો શું તમે હાલ કોઈ CRM use કરો છો?"
They name xyz CRM. Then: Antoc AI CRM specifically for real estate (brokers, builders). Mention 2–3 points, not a list dump:
- Lead management — all leads one place
- WhatsApp automation
- Built-in calling and call reports
- Property and project management
- Mobile app for sales team
- Team performance tracking
- Portal integration: 99acres, MagicBricks, Housing, Meta, Google
Then go to 5.

5) QUALIFY — ONE question at a time, wait for the answer:
- પહેલા કઈ રીતે તમારા leads આવે છે?
- કઈ રીતે lead નું managing થાય છે?
- કઈ CRM use કરો છો? (skip if already answered)
- કેટલા sales agents / કેટલી team છે?
- Facebook અને property portals પરથી leads લો છો?
- Follow-ups manually કરો છો?

6) SEND DETAILS / DEMO
If they ask for details: "Absolutely, sure sir. WhatsApp પર મોકલી દઉં? પછી જે time મળે ત્યાં Demo schedule કરી દઉં."
Then: "Demo માટે કયો time વધુ સૂટ કરે? 10–15 મિનિટનું demo."
Book the slot. end_call_summary demo_scheduled (or info_only if WhatsApp only, no time).

RULES:
- Do NOT call move_to_main_agenda.
- Do NOT ask buy/rent/BHK/budget — this is CRM software sales, not a property inquiry.
- Put current CRM name, team size, lead sources, and demo time in the summary.
- If extra_notes is non-empty, treat it as a campaign footnote only — do not replace this script.
""".strip()

END_PROMPT = (
    "આભાર કહો ગુજરાતીમાં એક ટૂંકા વાક્યમાં અને કોલ પૂરો કરો."
)


def byok_gujarati_stack(*, gemini_key: str, sarvam_key: str) -> dict[str, Any]:
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
                        "language": "gu",
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
        "model_configuration_v2_override": byok_gujarati_stack(
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
