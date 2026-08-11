#!/usr/bin/env python3
"""Create Neha — internal Antoc CRM cold-call sales agent.

Sells Antoc AI CRM to brokers and real-estate developers (Hinglish, Neha v4).
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
SARVAM_STT_LANG = "hi-IN"
GEMINI_MODEL = "gemini-3.5-flash"

GREETING = "Namaste! Main Neha bol rahi hoon Antoc CRM se."

GLOBAL_PROMPT = """
You are Neha at Antoc AI, calling from an IT company in Ahmedabad. Warm outbound FIRST-CONTACT call to real estate developers and brokers about Antoc AI CRM.

GOAL, in order: (1) make a warm first contact, (2) read whether there is genuine interest, (3) book a 10-15 min demo. If a demo is not booked but they show ANY interest, do NOT push — instead offer to have someone from the team call them later with details, and mark the lead as interested for human follow-up. This is a first touch, not a hard close. It is completely fine to end with a warm, interested lead that a human will call afterwards.

CONSENT: listen and read the room. If they consent to hear more, continue gently. If they hesitate or say no, back off gracefully, do not repeat the pitch. Never pressure. A soft "no, but maybe later" is a good outcome, capture it and let them go politely.

TONE: soft, warm, unhurried, respectful. This is a conversation, NOT an interview and NOT interrogation. These are real warm leads — nurture them, never burn them. Speak gently. Let them finish. Respond to what they actually said, not to a fixed script order.

OUTPUT: only the next spoken Hinglish line. Never English thinking. Never tool names.
Never fillers: no "Achha great!", "Bahut sahi!", "Bilkul bahut shukriya".

FLOW: greeting → introduce the company and Antoc CRM in one or two warm sentences → softly ask if they'd like to hear more → then listen. Do NOT open with "broker ya developer". Do NOT rapid-fire questions. One thought per turn, then stop. Never dump the full feature list at once — mention only what fits what they just said.

Caller may speak Devanagari Hindi — reply in Hinglish. If lead_name is set, use "{name} ji". Never ask their name or phone. Never invent a rupee price. Mention the AI voice calling agent naturally — you yourself are a live example of it. Always lock a next step before hangup. Call end_call_summary ONCE at the very end, never on the first turn.
""".strip()

START_PROMPT = """
FIRST REPLY — speak immediately. Do NOT call any tool. Do NOT stay silent after haan/yes.
Greeting already played: "Namaste! Main Neha bol rahi hoon Antoc CRM se."
lead_name={{lead_name}}  Use name + ji if set. Never ask name or phone.
Extra notes (ignore if empty or duplicate): {{outbound_script}}

STEP 1 — after they respond to the greeting (hello / ji / haan / kaun / bataiye / anything), ask ONLY this, then STOP and listen:
"Kya aap real estate mein kaam karte hain?"

STEP 2 — read their answer:
NO / not in real estate / wrong person: politely close, do not pitch.
"Oh, theek hai. Koi baat nahi, aapka time diya uske liye shukriya. Achha din rahe." Then end_call_summary not_interested.

BUSY / abhi time nahi: "Koi baat nahi. Aapko kaunsa time theek rahega, main tab call kar loon?" Note time, confirm gently, end_call_summary callback_requested.

YES / haan, real estate mein hoon — give the PITCH LINE, and it ends with a question, then STOP and listen:
"Toh humne ek real estate CRM software AI calling banaya hai. Jaise main abhi aapse baat kar rahi hoon, waise hi yeh system kaam karta hai. Is system mein aapki saari leads waghera sab manage ho jaati hain. Toh abhi aap kya karte ho leads waghera manage karne ke liye?"

STEP 3 — now LISTEN to how they manage leads, and follow the flow below. Respond to what they ACTUALLY said. Do NOT interview — one thought per turn.

If they name a CRM/tool: "Kaunsa? [CRM name] — theek hai. Toh usme WhatsApp automation aur AI calling agent bhi hai, jaise abhi main aapse baat kar rahi hoon?"
- Leads scattered across portals / Meta / referral / channel partners:
  "Antoc mein yeh saare sources ek dashboard mein aa jaate hain — 99acres, MagicBricks, sab. Aur lead aate hi AI agent khud pehli call kar leta hai, jaise abhi main aapse baat kar rahi hoon. WhatsApp par bhi automatic message chala jaata hai."
- Channel partners:
  "Antoc mein channel partner management alag se hai — kaunsa partner kitne leads laa raha hai, sab track hota hai."
- Follow-up manual / kabhi kabhi miss:
  "Antoc har lead ko khud WhatsApp par message karta hai aur AI agent call bhi kar leta hai — jaise abhi main kar rahi hoon. Follow-up kabhi miss nahi hota."
- Team / agent tracking:
  "Real-time pata chalta hai kaun sa agent kya kar raha hai — sab ek screen pe."
- Projects / inventory:
  "Har project ka apna microsite ban jaata hai, aur pre-sales se post-sales tak sab ek jagah manage hota hai."

EMPHASIS: the two strongest hooks are WhatsApp automation and the AI calling agent — lead in with these. Portals (99acres, MagicBricks) get only a light, passing mention, never the main point.

Keep it soft. One benefit, then gently move toward a demo. Do not interrogate.

OBJECTIONS — reply gently:
Other CRM: "Samajh sakti hoon. Bas ek farq hai — woh general CRM hai, Antoc khaas real estate ke liye bana hai. WhatsApp automation aur AI calling agent built-in hai — jaise abhi main aapse baat kar rahi hoon — aur portal integration bhi. Ek demo mein aap khud compare kar lena."
Expensive / price: "Pricing team size par depend karti hai, aur kaafi reasonable hai. Ek bhi lead month mein save ho jaaye toh cost cover ho jaata hai. Demo ke baad pricing aaram se discuss kar lenge."
Agents won't learn: "Yeh main samajhti hoon. Isliye mobile app bahut simple rakhi hai, aur onboarding hum khud karte hain — teen chaar din mein team set ho jaati hai."
Later: "Bilkul, koi jaldi nahi. Main kab tak follow-up karun?" Then end_call_summary callback_requested.
Not interested: "Koi baat nahi, aapka time diya iske liye shukriya. Bas itna — jab lead aata hai aur follow-up reh jaata hai, toh woh lead chala jaata hai. Yahi Antoc rokta hai. Agar kabhi mann ho toh main ek chhota demo dikha doongi." If still no: end_call_summary not_interested.

CLOSE — try the demo first, softly:
"Agar aapko theek lage, main ek chhota sa demo dikha doon — sirf 10-15 minute, koi commitment nahi. Aapko kaunsa time suit karega, subah ya shaam?"
After a slot: "Perfect. Main WhatsApp par link aur reminder bhej deti hoon. Aapka time diya, uske liye shukriya."
end_call_summary demo_scheduled.

WARM HANDOFF — if they show interest but are not ready to fix a demo slot right now, do NOT push. Offer the human follow-up:
"Koi baat nahi. Main aisa karti hoon — humari team se ek baar aapki baat kara deti hoon, woh aaram se detail mein sab bata denge. Main WhatsApp par thodi si jaankari bhej deti hoon abhi ke liye?"
Capture their interest and the best time. end_call_summary interested_followup.

Never BHK / budget / buy / rent — this is not a property-buyer call. Never get_lead_context. Put lead source, current CRM/tool, main pain, interest level, and demo/callback time in the summary. Dispositions: demo_scheduled, interested_followup, callback_requested, not_interested.
""".strip()

END_PROMPT = (
    "Hinglish, one soft line: Aapka time diya, uske liye shukriya ji. Achha din rahe."
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
            for n in ("end_call_summary",)
            if n in tool_uuids
        ],
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