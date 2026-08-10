#!/usr/bin/env python3
"""Wire Antoc pre-call + phone templates + prompt on Dograh agent.

- Start Call: Pre-Call Data Fetch → Antoc /api/voice/dograh/pre-call
- Tools: phone value_template {{phone_number}}
- Global / Agenda prompts from Antoc GET /api/voice/prompt
- Publish workflow

Usage:
  python3 scripts/configure_antoc_precall.py
  python3 scripts/configure_antoc_precall.py --workflow-id 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = Path(__file__).resolve().parent / "antoc_voice.env"

# Import tool defs from seed script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    CREDENTIAL_NAME,
    TOOLS,
    ensure_credential,
    load_env_file,
    require_env,
    tool_payload,
)


def api_request(
    method: str,
    base: str,
    path: str,
    api_key: str,
    body: dict | None = None,
    timeout: int = 60,
) -> Any:
    url = base.rstrip("/") + path
    data = None
    headers = {"X-API-Key": api_key, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        err = e.read().decode()
        raise SystemExit(f"{method} {path} → HTTP {e.code}: {err[:800]}") from e


def antoc_prompt(antoc_base: str, secret: str) -> dict:
    url = antoc_base.rstrip("/") + "/api/voice/prompt?channel=voice"
    req = urllib.request.Request(
        url,
        headers={
            "X-Voice-Secret": secret,
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def upsert_tools(
    base: str, api_key: str, antoc_base: str, credential_uuid: str
) -> dict[str, str]:
    existing = api_request("GET", base, "/api/v1/tools/?status=active", api_key) or []
    by_name = {t["name"]: t for t in existing}
    uuids: dict[str, str] = {}

    for tool in TOOLS:
        name = tool["name"]
        payload = tool_payload(tool, antoc_base, credential_uuid)
        if name in by_name:
            tool_uuid = by_name[name]["tool_uuid"]
            api_request(
                "PUT",
                base,
                f"/api/v1/tools/{tool_uuid}",
                api_key,
                {
                    "name": payload["name"],
                    "description": payload["description"],
                    "icon": payload["icon"],
                    "icon_color": payload["icon_color"],
                    "definition": payload["definition"],
                },
            )
            uuids[name] = tool_uuid
            print(f"Updated tool: {name} ({tool_uuid})")
        else:
            created = api_request(
                "POST", base, "/api/v1/tools/", api_key, payload
            )
            uuids[name] = created["tool_uuid"]
            print(f"Created tool: {name} ({uuids[name]})")
    return uuids


def patch_workflow(
    definition: dict,
    *,
    precall_url: str,
    credential_uuid: str,
    system_prompt: str,
    first_message: str,
    tool_uuids: list[str],
    antoc_base: str,
    business_name: str = "hamare office",
) -> tuple[dict, list[str]]:
    changes: list[str] = []
    nodes = definition.get("nodes") or []
    call_end_url = antoc_base.rstrip("/") + "/api/voice/call-end"

    for node in nodes:
        ntype = node.get("type")
        data = node.setdefault("data", {})

        if ntype == "startCall":
            data["pre_call_fetch_enabled"] = True
            data["pre_call_fetch_url"] = precall_url
            data["pre_call_fetch_credential_uuid"] = credential_uuid
            data["greeting_type"] = "text"
            data["greeting"] = (
                "Namaste {{lead_name}} ji, {{business_name}} mein aapka swagat hai. "
                "Main Arushi bol rahi hoon. Main aapki kaise madad kar sakti hoon?"
            )
            # Answer on Start Call (no Agenda hop) — cuts first-reply LLM double cost
            data["prompt"] = (
                "You handle the FULL call after the greeting.\n"
                "OUTPUT: ONLY short Hindi/Hinglish. Never English thinking.\n"
                "lead_name={{lead_name}} lead_found={{lead_found}} "
                "phone={{phone_number}}\n"
                "lead_summary={{lead_summary}}\n"
                "prior_requirements={{prior_requirements}}\n"
                "If lead_name set: NEVER ask naam. Never ask phone when known.\n"
                "Do NOT call move_to_main_agenda — answer here.\n"
                "One question at a time. end_call_summary once before goodbye.\n"
            )
            data["tool_uuids"] = list(
                dict.fromkeys([*(data.get("tool_uuids") or []), *tool_uuids])
            )
            data["allow_interrupt"] = True
            data["add_global_prompt"] = True
            changes.append("startCall:pre_call+single-hop")

        if ntype in ("globalNode", "global"):
            data["prompt"] = system_prompt
            changes.append("globalNode:prompt")

        if ntype == "agentNode":
            data["prompt"] = (
                "Speak ONLY Hindi/Hinglish — zero English planning.\n"
                "lead_name={{lead_name}} lead_found={{lead_found}} "
                "phone={{phone_number}}\n"
                "lead_summary={{lead_summary}}\n"
                "prior_requirements={{prior_requirements}}\n"
                "If lead_name is set: NEVER ask naam.\n"
                "Ask ONE question. Call end_call_summary before goodbye."
            )
            existing = list(data.get("tool_uuids") or [])
            merged = list(dict.fromkeys([*existing, *tool_uuids]))
            data["tool_uuids"] = merged
            changes.append("agentNode:prompt+tools")

        if ntype == "webhook":
            data["enabled"] = True
            data["http_method"] = "POST"
            data["endpoint_url"] = call_end_url
            data["credential_uuid"] = credential_uuid
            data["payload_template"] = {
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
            }
            if not data.get("name"):
                data["name"] = "Antoc call-end"
            changes.append("webhook:recording+transcript")

    # Ensure a webhook node exists
    if not any(n.get("type") == "webhook" for n in nodes):
        nodes.append(
            {
                "id": "webhook_antoc_call_end",
                "type": "webhook",
                "position": {"x": 960, "y": 80},
                "data": {
                    "name": "Antoc call-end",
                    "enabled": True,
                    "http_method": "POST",
                    "endpoint_url": call_end_url,
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
            }
        )
        definition["nodes"] = nodes
        changes.append("webhook:created")

    return definition, changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--workflow-id", type=int, default=0)
    parser.add_argument("--publish", action="store_true", default=True)
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()

    load_env_file(args.env_file)
    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_base = require_env("ANTOC_BASE_URL")
    antoc_secret = require_env("ANTOC_VOICE_SECRET")
    workflow_id = args.workflow_id or int(
        os.environ.get("DOGRAH_WORKFLOW_ID") or "3"
    )
    # Prefer agent 3 for Antoc Hindi
    if not args.workflow_id and os.environ.get("DOGRAH_WORKFLOW_ID") == "1":
        workflow_id = 3

    print(f"Dograh: {dograh_base} workflow={workflow_id}")
    print(f"Antoc: {antoc_base}")

    # Health
    health = api_request("GET", dograh_base, "/api/v1/health", api_key)
    print(f"Health: {health.get('status')} v{health.get('version')}")

    cred_uuid = ensure_credential(dograh_base, api_key, antoc_secret)
    print(f"Credential: {CREDENTIAL_NAME} ({cred_uuid})")

    tool_uuids = upsert_tools(dograh_base, api_key, antoc_base, cred_uuid)

    prompt = antoc_prompt(antoc_base, antoc_secret)
    system_prompt = prompt.get("system_prompt") or ""
    first_message = prompt.get("first_message") or ""
    # Local override: never re-ask name when pre-call already has lead_name.
    # Keeps working even if Antoc /api/voice/prompt is not redeployed yet.
    name_lock = (
        "\n\nHARD RULES — NEVER VIOLATE:\n"
        "- Speak ONLY the final Hindi/Hinglish reply. NEVER output English thinking/"
        'planning ("The user wants…", "I need to ask…").\n'
        "- If {{lead_name}} is non-empty OR lead_found is true: NEVER ask for name "
        '(no "apna naam bataiye"). Address them as {{lead_name}} ji and help.\n'
        "- If {{phone_number}} is set OR phone_known is true: NEVER ask for mobile.\n"
        "- Prefer end_call_summary once at end; avoid mid-call create/update.\n"
    )
    if "NEVER ask for name" not in system_prompt:
        system_prompt = system_prompt + name_lock
    print(f"Prompt loaded ({len(system_prompt)} chars)")

    wf = api_request(
        "GET", dograh_base, f"/api/v1/workflow/fetch/{workflow_id}", api_key
    )
    definition = wf.get("workflow_definition") or wf.get("draft_definition") or {}
    if not definition.get("nodes"):
        raise SystemExit(f"Workflow {workflow_id} has no nodes")

    precall_url = antoc_base.rstrip("/") + "/api/voice/dograh/pre-call"
    definition, changes = patch_workflow(
        definition,
        precall_url=precall_url,
        credential_uuid=cred_uuid,
        system_prompt=system_prompt,
        first_message=first_message,
        tool_uuids=list(tool_uuids.values()),
        antoc_base=antoc_base,
        business_name=prompt.get("business_name") or "hamare office",
    )
    print("Changes:", ", ".join(changes) or "(none)")

    # Save draft — preserve other workflow fields
    api_request(
        "PUT",
        dograh_base,
        f"/api/v1/workflow/{workflow_id}",
        api_key,
        {
            "name": wf.get("name") or "Arushi Antoc Hindi Receptionist",
            "workflow_definition": definition,
            "template_context_variables": wf.get("template_context_variables"),
            "call_disposition_codes": wf.get("call_disposition_codes"),
            "workflow_configurations": wf.get("workflow_configurations"),
        },
    )
    print(f"Saved draft workflow {workflow_id}")

    if args.publish and not args.no_publish:
        api_request(
            "POST",
            dograh_base,
            f"/api/v1/workflow/{workflow_id}/publish",
            api_key,
            {},
        )
        print(f"Published workflow {workflow_id}")

    # Verify start node
    wf2 = api_request(
        "GET", dograh_base, f"/api/v1/workflow/fetch/{workflow_id}", api_key
    )
    def2 = wf2.get("workflow_definition") or {}
    for node in def2.get("nodes") or []:
        if node.get("type") == "startCall":
            d = node.get("data") or {}
            print(
                "startCall pre_call:",
                d.get("pre_call_fetch_enabled"),
                d.get("pre_call_fetch_url"),
                "cred=",
                (d.get("pre_call_fetch_credential_uuid") or "")[:8] + "…",
            )

    print("\nDone. Call +912264232909 — agent should not ask for 10-digit number.")
    print(f"Open: {dograh_base.rstrip('/')}/workflow/{workflow_id}")


if __name__ == "__main__":
    main()
