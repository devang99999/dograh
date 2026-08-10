#!/usr/bin/env python3
"""Seed Antoc CRM voice tools on a self-hosted Dograh instance.

Creates:
  - Credential: Antoc Voice Secret (X-Voice-Secret)
  - HTTP tools: get_lead_context, create_lead, update_lead, end_call_summary
  - Optionally attaches those tools to all agentNode nodes on a workflow

Usage:
  1. Copy scripts/antoc_voice.env.example → scripts/antoc_voice.env
  2. Fill DOGRAH_API_KEY (Dograh UI → Developers → API keys)
     and ANTOC_VOICE_SECRET (Antoc VOICE_AGENT_SECRET)
  3. Run:
       python3 scripts/seed_antoc_voice_tools.py
       python3 scripts/seed_antoc_voice_tools.py --attach-workflow 1
       python3 scripts/seed_antoc_voice_tools.py --test-phone 7016896136

This talks only to YOUR Dograh (DOGRAH_BASE_URL) and Antoc (ANTOC_BASE_URL).
It does not touch dograh cloud or require git push.
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

CREDENTIAL_NAME = "Antoc Voice Secret"

PHONE_PRESET = {
    "name": "phone",
    "type": "string",
    "required": True,
    "value_template": "{{phone_number}}",
}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_lead_context",
        "description": (
            "Look up the caller in Antoc CRM using the known caller ID. "
            "Do not ask the user for their number when phone is already known."
        ),
        "path": "/api/voice/context",
        "parameters": [],
        "preset_parameters": [PHONE_PRESET],
    },
    {
        "name": "create_lead",
        "description": (
            "Create a NEW lead in Antoc CRM only when get_lead_context found "
            "nothing AND end_call_summary cannot run. Prefer end_call_summary. "
            "Include buy/rent, city, area, BHK, budget, purpose when known."
        ),
        "path": "/api/voice/create",
        "parameters": [
            {
                "name": "name",
                "type": "string",
                "required": True,
                "description": "Caller's name.",
            },
            {
                "name": "intent",
                "type": "string",
                "required": False,
                "description": "One of: buy, rent, inquire, sell.",
            },
            {
                "name": "city",
                "type": "string",
                "required": False,
                "description": "City of interest.",
            },
            {
                "name": "area",
                "type": "string",
                "required": False,
                "description": "Locality/area.",
            },
            {
                "name": "bhk",
                "type": "string",
                "required": False,
                "description": "BHK preference, e.g. 2, 3, 10.",
            },
            {
                "name": "property_type",
                "type": "string",
                "required": False,
                "description": "villa, apartment, plot, etc.",
            },
            {
                "name": "budget_min",
                "type": "number",
                "required": False,
                "description": "Minimum budget as a number (INR).",
            },
            {
                "name": "budget_max",
                "type": "number",
                "required": False,
                "description": "Maximum budget as a number (INR).",
            },
            {
                "name": "purpose",
                "type": "string",
                "required": False,
                "description": "self-use or investment.",
            },
            {
                "name": "timeline",
                "type": "string",
                "required": False,
                "description": "When they want to buy/rent.",
            },
            {
                "name": "furnished_status",
                "type": "string",
                "required": False,
                "description": "furnished / semi / unfurnished.",
            },
            {
                "name": "construction_status",
                "type": "string",
                "required": False,
                "description": "ready / under-construction.",
            },
            {
                "name": "project_name",
                "type": "string",
                "required": False,
                "description": "Project or society name if mentioned.",
            },
            {
                "name": "notes",
                "type": "string",
                "required": False,
                "description": "Short free-text notes from the call.",
            },
        ],
        "preset_parameters": [PHONE_PRESET],
    },
    {
        "name": "update_lead",
        "description": (
            "Prefer end_call_summary instead (mid-call updates add latency). "
            "Only use if you must patch an EXISTING lead mid-call."
        ),
        "path": "/api/voice/update",
        "parameters": [
            {
                "name": "lead_id",
                "type": "string",
                "required": False,
                "description": "Antoc lead id if known.",
            },
            {
                "name": "remark",
                "type": "string",
                "required": True,
                "description": "Short note of what the caller said.",
            },
            {
                "name": "intent",
                "type": "string",
                "required": False,
                "description": "buy | rent | inquire | sell if newly stated.",
            },
            {
                "name": "city",
                "type": "string",
                "required": False,
                "description": "City if newly stated.",
            },
            {
                "name": "area",
                "type": "string",
                "required": False,
                "description": "Area/locality if newly stated.",
            },
            {
                "name": "bhk",
                "type": "string",
                "required": False,
                "description": "BHK if newly stated.",
            },
            {
                "name": "property_type",
                "type": "string",
                "required": False,
                "description": "villa, apartment, plot, etc.",
            },
            {
                "name": "budget_min",
                "type": "number",
                "required": False,
                "description": "Min budget if newly stated.",
            },
            {
                "name": "budget_max",
                "type": "number",
                "required": False,
                "description": "Max budget if newly stated.",
            },
            {
                "name": "purpose",
                "type": "string",
                "required": False,
                "description": "self-use or investment.",
            },
            {
                "name": "timeline",
                "type": "string",
                "required": False,
                "description": "Purchase/rent timeline.",
            },
            {
                "name": "furnished_status",
                "type": "string",
                "required": False,
                "description": "furnished / semi / unfurnished.",
            },
            {
                "name": "construction_status",
                "type": "string",
                "required": False,
                "description": "ready / under-construction.",
            },
            {
                "name": "project_name",
                "type": "string",
                "required": False,
                "description": "Project name if mentioned.",
            },
        ],
        "preset_parameters": [
            {
                "name": "phone",
                "type": "string",
                "required": False,
                "value_template": "{{phone_number}}",
            }
        ],
    },
    {
        "name": "end_call_summary",
        "description": (
            "Call ONCE at the end of the conversation before goodbye. "
            "This is the ONLY CRM write during the call — pass disposition, "
            "a Hindi (or Roman Hindi) summary matching the call language, and "
            "all structured fields collected (intent, city, area, bhk, "
            "property_type, budget, purpose, timeline, furnished, construction, "
            "project). Recording + full transcript are attached after hangup "
            "automatically — do not wait for them."
        ),
        "path": "/api/voice/end-summary",
        "parameters": [
            {
                "name": "disposition",
                "type": "string",
                "required": True,
                "description": (
                    "Outcome: qualified, callback_requested, site_visit_interest, "
                    "info_only, not_interested, wrong_number, existing_followup."
                ),
            },
            {
                "name": "summary",
                "type": "string",
                "required": True,
                "description": (
                    "1-4 sentences in the SAME language as the call (Hindi/"
                    "Hinglish preferred for Hindi calls): intent, city/area, "
                    "property type, budget, purpose, callback preference."
                ),
            },
            {
                "name": "language",
                "type": "string",
                "required": False,
                "description": "hi for Hindi calls, en for English. Default hi.",
            },
            {
                "name": "name",
                "type": "string",
                "required": False,
                "description": "Caller name if known/collected.",
            },
            {
                "name": "intent",
                "type": "string",
                "required": False,
                "description": "buy | rent | inquire | sell",
            },
            {
                "name": "city",
                "type": "string",
                "required": False,
                "description": "City of interest.",
            },
            {
                "name": "area",
                "type": "string",
                "required": False,
                "description": "Locality/area, e.g. Sola.",
            },
            {
                "name": "bhk",
                "type": "string",
                "required": False,
                "description": "BHK if stated.",
            },
            {
                "name": "property_type",
                "type": "string",
                "required": False,
                "description": "villa, apartment, plot, etc.",
            },
            {
                "name": "budget_min",
                "type": "number",
                "required": False,
                "description": "Min budget INR number.",
            },
            {
                "name": "budget_max",
                "type": "number",
                "required": False,
                "description": "Max budget INR number (10 crore = 100000000).",
            },
            {
                "name": "purpose",
                "type": "string",
                "required": False,
                "description": "self-use or investment.",
            },
            {
                "name": "timeline",
                "type": "string",
                "required": False,
                "description": "When they want to buy/rent.",
            },
            {
                "name": "furnished_status",
                "type": "string",
                "required": False,
                "description": "furnished / semi / unfurnished.",
            },
            {
                "name": "construction_status",
                "type": "string",
                "required": False,
                "description": "ready / under-construction.",
            },
            {
                "name": "project_name",
                "type": "string",
                "required": False,
                "description": "Project or society name.",
            },
            {
                "name": "square_feet_min",
                "type": "number",
                "required": False,
                "description": "Min carpet/built-up sq.ft if stated.",
            },
            {
                "name": "square_feet_max",
                "type": "number",
                "required": False,
                "description": "Max carpet/built-up sq.ft if stated.",
            },
            {
                "name": "preferred_callback",
                "type": "string",
                "required": False,
                "description": "immediate / evening / etc.",
            },
            {
                "name": "notes",
                "type": "string",
                "required": False,
                "description": "Any extra notes.",
            },
        ],
        "preset_parameters": [PHONE_PRESET],
    },
]


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(
            f"Missing {name}. Set it in the environment or in {DEFAULT_ENV_FILE}"
        )
    return value


def _opener() -> urllib.request.OpenerDirector:
    """Direct HTTPS — ignore broken local HTTP(S)_PROXY tunnels."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api_request(
    method: str,
    base: str,
    path: str,
    api_key: str,
    body: dict[str, Any] | None = None,
) -> Any:
    url = base.rstrip("/") + path
    data = None
    headers = {
        "X-API-Key": api_key,
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _opener().open(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} → HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SystemExit(
            f"{method} {url} failed: {e.reason}\n"
            "If you are on a machine that cannot reach the VM, run this script "
            "from the Dograh VM itself (ssh → python3 scripts/seed_antoc_voice_tools.py)."
        ) from e


def ensure_credential(base: str, api_key: str, secret: str) -> str:
    existing = api_request("GET", base, "/api/v1/credentials/", api_key) or []
    for cred in existing:
        if cred.get("name") == CREDENTIAL_NAME:
            print(f"Credential exists: {CREDENTIAL_NAME} ({cred['uuid']})")
            return cred["uuid"]

    created = api_request(
        "POST",
        base,
        "/api/v1/credentials/",
        api_key,
        {
            "name": CREDENTIAL_NAME,
            "description": "Antoc CRM voice-agent secret (X-Voice-Secret)",
            "credential_type": "api_key",
            "credential_data": {
                "header_name": "X-Voice-Secret",
                "api_key": secret,
            },
        },
    )
    print(f"Created credential: {CREDENTIAL_NAME} ({created['uuid']})")
    return created["uuid"]


def tool_payload(
    tool: dict[str, Any], antoc_base: str, credential_uuid: str
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "method": "POST",
        "url": antoc_base.rstrip("/") + tool["path"],
        "credential_uuid": credential_uuid,
        "timeout_ms": 10000,
        "parameters": tool.get("parameters") or [],
    }
    if tool.get("preset_parameters"):
        config["preset_parameters"] = tool["preset_parameters"]
    return {
        "name": tool["name"],
        "description": tool["description"],
        "category": "http_api",
        "icon": "globe",
        "icon_color": "#3B82F6",
        "definition": {
            "schema_version": 1,
            "type": "http_api",
            "config": config,
        },
    }


def ensure_tools(
    base: str, api_key: str, antoc_base: str, credential_uuid: str
) -> dict[str, str]:
    existing = api_request("GET", base, "/api/v1/tools/?status=active", api_key) or []
    by_name = {t["name"]: t for t in existing}
    uuids: dict[str, str] = {}

    for tool in TOOLS:
        name = tool["name"]
        if name in by_name:
            uuids[name] = by_name[name]["tool_uuid"]
            print(f"Tool exists: {name} ({uuids[name]})")
            continue
        created = api_request(
            "POST",
            base,
            "/api/v1/tools/",
            api_key,
            tool_payload(tool, antoc_base, credential_uuid),
        )
        uuids[name] = created["tool_uuid"]
        print(f"Created tool: {name} ({uuids[name]})")

    return uuids


def attach_tools_to_workflow(
    base: str, api_key: str, workflow_id: int, tool_uuids: list[str]
) -> None:
    wf = api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)
    definition = wf.get("workflow_definition") or {}
    nodes = definition.get("nodes") or []
    if not nodes:
        raise SystemExit(f"Workflow {workflow_id} has no nodes")

    changed = 0
    for node in nodes:
        if node.get("type") != "agentNode":
            continue
        data = node.setdefault("data", {})
        existing = list(data.get("tool_uuids") or [])
        merged = list(dict.fromkeys([*existing, *tool_uuids]))
        if merged != existing:
            data["tool_uuids"] = merged
            changed += 1

    if changed == 0:
        print(
            f"Workflow {workflow_id}: agent nodes already have these tools "
            "(or no agentNode found)."
        )
        return

    api_request(
        "PUT",
        base,
        f"/api/v1/workflow/{workflow_id}",
        api_key,
        {
            "name": wf.get("name"),
            "workflow_definition": definition,
            "template_context_variables": wf.get("template_context_variables"),
            "call_disposition_codes": wf.get("call_disposition_codes"),
            "workflow_configurations": wf.get("workflow_configurations"),
        },
    )
    print(
        f"Attached {len(tool_uuids)} tools to {changed} agentNode(s) on "
        f"workflow {workflow_id} (saved as draft — Publish in UI if needed)."
    )


def test_lookup(base: str, api_key: str, tool_uuid: str, phone: str) -> None:
    result = api_request(
        "POST",
        base,
        f"/api/v1/tools/{tool_uuid}/test",
        api_key,
        {"llm_params": {"phone": phone}},
    )
    print("Test get_lead_context:")
    print(json.dumps(result, indent=2, default=str)[:2000])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--attach-workflow",
        type=int,
        default=None,
        help="Workflow ID to attach tools onto (agent nodes). Example: 1",
    )
    parser.add_argument(
        "--test-phone",
        type=str,
        default=None,
        help="If set, POST /tools/{get_lead_context}/test with this phone",
    )
    args = parser.parse_args()

    load_env_file(args.env_file)

    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")
    antoc_base = require_env("ANTOC_BASE_URL")
    antoc_secret = require_env("ANTOC_VOICE_SECRET")

    # Health (no auth on most Dograh health endpoints)
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

    workflow_id = args.attach_workflow
    if workflow_id is None and os.environ.get("DOGRAH_WORKFLOW_ID", "").strip():
        workflow_id = int(os.environ["DOGRAH_WORKFLOW_ID"].strip())

    if workflow_id is not None:
        attach_tools_to_workflow(
            dograh_base, api_key, workflow_id, list(tool_uuids.values())
        )
    else:
        print(
            "Tools ready. Attach in UI (Main Agenda → Tools) or re-run with "
            "--attach-workflow 1"
        )

    if args.test_phone:
        test_lookup(
            dograh_base, api_key, tool_uuids["get_lead_context"], args.test_phone
        )

    print("Done.")


if __name__ == "__main__":
    main()
