#!/usr/bin/env python3
"""Create Neha — internal Antoc CRM cold-call sales agent.

Sells Antoc AI CRM to brokers and real-estate developers via Gemini Live
speech-to-speech (Hindi). Does NOT modify inbound workflow 3 or outbound
workflow 4.

Wired to Antoc AI - Keval place_id=antoc_8b8c2f2df75a1142 so call-end
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
# Antoc AI - Keval (8160869109) — do not use VOICE_DEFAULT_PLACE_ID
ANTOC_CRM_PLACE_ID = "antoc_8b8c2f2df75a1142"

# Gemini Live S2S (female voice Kore) + companion LLM for extraction/QA
REALTIME_MODEL = "gemini-3.1-flash-live-preview"
REALTIME_VOICE = "Kore"
REALTIME_LANGUAGE = "hi"
COMPANION_LLM_MODEL = "gemini-3.5-flash-lite"

GREETING = "Namaste! Main Neha bol rahi hoon Antoc CRM se."

GLOBAL_PROMPT = """
You are Neha — a WOMAN from Antoc AI, Ahmedabad. Soft outbound first-contact call to real estate brokers and developers about Antoc AI CRM.

FEMALE GRAMMAR — always feminine Hindi, every turn, no exceptions:
USE: bol rahi hoon, karungi, dungi, sakti hoon, jaati hoon, karti hoon, sun rahi hoon, batati hoon, milungi.
NEVER: bol raha, karunga, dunga, sakta hoon, jaata, karta, sun raha, batata, milunga.

Greeting was ALREADY spoken. NEVER repeat Namaste or the opening line.

SPEED: respond immediately when user speaks — never silence, never wait for them to repeat.
After YOU ask a question, STOP — one sentence only, then listen. NEVER combine two steps in one turn.

SCHEDULING — highest priority:
- User names ANY time (do baje, teen baje, dopahar do, 2 baje, gyarah baje) → CONFIRM that exact time immediately. Never re-offer other slots.
- "aaj nahi" / not today → offer KAL only. Say "kal" — never parso, never person, never day after tomorrow unless user asks.
- Once time is agreed → confirm + whatsapp link + end_call_summary same turn. Stop.

If user says hello/helo after you asked for time: "Ji sun rahi hoon" — if they already picked a time, CONFIRM it; do not re-offer gyarah/do baje again.

One step per turn. One question max per turn. No English openers. Times in Hindi words: kal subah das baje, kal dopahar do baje. "whatsapp" lowercase.

end_call_summary same turn as final goodbye when closing.
""".strip()

START_PROMPT = """
Greeting already played — do NOT repeat it.
lead_name={{lead_name}}. Notes (ignore if empty): {{outbound_script}}

=== HARD RULES (break these = failed call) ===
1. ONE step per turn. Never two questions. Never pitch in same turn as step 1.
2. "bolie" / "haan" / "hello" right after greeting = NOT "yes I do real estate". Step 1 only.
3. User picks a time → CONFIRM that time. Never ignore it. Never re-offer different slots.
4. Demo day = KAL by default. Never parso unless they say parso.

STEP 1 — first user reply after greeting (bolie/haan/ji/hello — anything):
EXACTLY one line, then STOP:
"Kya aap real estate mein kaam karte hain?"
WRONG (never do this): asking RE question AND pitching CRM in same turn.

STEP 2 — separate turn, ONLY after clear answer to step 1:
NO/nahi → thank + end_call_summary not_interested. Stop.
BUSY → "Kab call karun?" → end_call_summary callback_requested. Stop.
YES to real estate → one pitch line + one question, STOP:
"Hum real estate ke liye AI CRM banate hain — calling aur whatsapp automation. Aap abhi leads kaise manage karte hain?"

STEP 3 — separate turn, after they say manual/Excel/CRM:
Manual → turn A only: "Samajh gayi — manual mein leads miss ho jaati hain. Das se pandrah minute ka live demo dikha sakti hoon, theek hai?"
If yes / haan → turn B only: "Kal subah gyarah baje, ya kal dopahar do baje — kaunsa time theek rahega?"
Use "kal" always — never parso, never person.

STEP 4 — scheduling:
"aaj nahi" / not today → "Theek hai, kal chalega." Offer kal times once: "Kal subah gyarah baje, ya kal dopahar do baje?"
User picks do baje / 2 baje / dopahar do / teen baje / gyarah baje → LOCK IT same turn:
"Theek hai, kal dopahar do baje demo fix karte hain. Main whatsapp par link bhej dungi. Milte hain."
→ end_call_summary demo_scheduled with exact locked time. Stop.
Do NOT offer new times after they already picked one.

Time words: subah das/gyarah/barah baje, dopahar do/teen/chaar baje, shaam paanch/chhe/saat baje.

Never skip step 1. Never merge steps. Never masculine verbs. Never BHK/buy/rent. Never get_lead_context.
""".strip()

END_PROMPT = (
    "Only if call is already ending: one soft Hindi line, shukriya ji, achha din rahe. Do not use if user still scheduling."
)


def byok_realtime_hindi(*, gemini_key: str) -> dict[str, Any]:
    """BYOK Gemini Live speech-to-speech — no Sarvam STT/TTS cascade."""
    return {
        "version": 2,
        "mode": "byok",
        "byok": {
            "mode": "realtime",
            "realtime": {
                "realtime": {
                    "provider": "google_realtime",
                    "model": REALTIME_MODEL,
                    "api_key": gemini_key,
                    "voice": REALTIME_VOICE,
                    "language": REALTIME_LANGUAGE,
                },
                "llm": {
                    "provider": "google",
                    "model": COMPANION_LLM_MODEL,
                    "api_key": gemini_key,
                },
            },
        },
    }


# Latest completed Antoc CRM KB doc. Republishing without this wipes
# retrieve_from_knowledge_base from the live agent.
DEFAULT_KB_DOCUMENT_UUID = "1a469012-ef50-4079-9848-f7bafe876ae7"


def build_definition(
    *,
    tool_uuids: list[str],
    credential_uuid: str,
    antoc_base: str,
    trigger_path: str = "",
    document_uuids: list[str] | None = None,
) -> dict[str, Any]:
    start_id = "start"
    end_id = "end"
    global_id = "global"
    webhook_id = "webhook"
    trigger_id = "api_trigger"
    docs = document_uuids or [DEFAULT_KB_DOCUMENT_UUID]

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
                    "delayed_start": False,
                    "pre_call_fetch_enabled": False,
                    "tool_uuids": tool_uuids,
                    "document_uuids": docs,
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


def voice_config(*, gemini_key: str) -> dict[str, Any]:
    return {
        "model_configuration_v2_override": byok_realtime_hindi(gemini_key=gemini_key),
        "max_call_duration": 360,
        # Nudge sooner if telephony VAD misses a reply (8s vs default 10s).
        "max_user_idle_timeout": 8.0,
        # Realtime owns turn-taking; keep ambience off so it does not fight Live audio.
        "ambient_noise_configuration": {"enabled": False},
    }


def existing_trigger_path(base: str, api_key: str, workflow_id: int) -> str:
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)
    for node in (wf.get("workflow_definition") or {}).get("nodes") or []:
        if node.get("type") == "trigger":
            return str((node.get("data") or {}).get("trigger_path") or "")
    return ""


def existing_document_uuids(base: str, api_key: str, workflow_id: int) -> list[str]:
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)
    for node in (wf.get("workflow_definition") or {}).get("nodes") or []:
        if node.get("type") == "startCall":
            docs = (node.get("data") or {}).get("document_uuids") or []
            return [str(u) for u in docs if u]
    return []


def latest_kb_document_uuid(base: str, api_key: str) -> str:
    """Prefer the newest completed antoc_crm_knowledge_base.txt, else default."""
    try:
        docs = api_request("GET", base, "/api/v1/knowledge-base/documents", api_key)
    except Exception:
        return DEFAULT_KB_DOCUMENT_UUID
    candidates = [
        d
        for d in (docs.get("documents") or [])
        if d.get("processing_status") == "completed"
        and str(d.get("filename") or "").startswith("antoc_crm_knowledge_base")
    ]
    if not candidates:
        return DEFAULT_KB_DOCUMENT_UUID
    candidates.sort(key=lambda d: d.get("id") or 0, reverse=True)
    return str(candidates[0].get("document_uuid") or DEFAULT_KB_DOCUMENT_UUID)


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
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip() or require_env(
        "GOOGLE_API_KEY"
    )

    cred_uuid = ensure_credential(dograh_base, api_key, antoc_secret)
    tool_uuids = ensure_tools(dograh_base, api_key, antoc_base, cred_uuid)
    trigger_path = ""
    document_uuids: list[str] = []
    if args.workflow_id:
        trigger_path = existing_trigger_path(dograh_base, api_key, args.workflow_id)
        document_uuids = existing_document_uuids(
            dograh_base, api_key, args.workflow_id
        )
    if not document_uuids:
        document_uuids = [latest_kb_document_uuid(dograh_base, api_key)]

    definition = build_definition(
        tool_uuids=[
            tool_uuids[n]
            for n in ("end_call_summary",)
            if n in tool_uuids
        ],
        credential_uuid=cred_uuid,
        antoc_base=antoc_base,
        trigger_path=trigger_path,
        document_uuids=document_uuids,
    )
    cfg = voice_config(gemini_key=gemini_key)

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

    ov = (wf.get("workflow_configurations") or {}).get(
        "model_configuration_v2_override"
    ) or {}
    byok = ov.get("byok") or {}
    rt = ((byok.get("realtime") or {}).get("realtime")) or {}
    print("\nDid NOT modify inbound 3 or outbound 4.")
    print(f"CRM sales agent: {dograh_base}/workflow/{workflow_id}")
    print(f"Wired call-end placeId={ANTOC_CRM_PLACE_ID} (Antoc AI - Keval).")
    print(f"Knowledge base docs on start node: {document_uuids}")
    print(
        f"Model: mode={ov.get('mode')} byok.mode={byok.get('mode')} "
        f"realtime={rt.get('provider')}/{rt.get('model')} "
        f"voice={rt.get('voice')} lang={rt.get('language')}"
    )
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
