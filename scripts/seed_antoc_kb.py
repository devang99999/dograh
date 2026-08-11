#!/usr/bin/env python3
"""Upload Antoc CRM knowledge base and attach it to Neha's workflow (wf5).

Steps:
  1. Upload antoc_crm_knowledge_base.txt via presigned URL
  2. Trigger chunked processing (vector embeddings)
  3. Poll until processing_status == "completed"
  4. Patch wf5 start node with document_uuid
  5. Publish wf5

Usage:
  python3 scripts/seed_antoc_kb.py
  python3 scripts/seed_antoc_kb.py --workflow-id 5
  python3 scripts/seed_antoc_kb.py --workflow-id 5 --no-publish
  python3 scripts/seed_antoc_kb.py --doc-uuid <existing-uuid>  # skip re-upload
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    DEFAULT_ENV_FILE,
    api_request,
    load_env_file,
    require_env,
)

KB_FILE = Path(__file__).resolve().parent / "antoc_crm_knowledge_base.txt"
KB_FILENAME = "antoc_crm_knowledge_base.txt"
KB_MIME = "text/plain"
RETRIEVAL_MODE = "chunked"  # vector search — the "deep" mode
POLL_INTERVAL = 4  # seconds between status checks
POLL_TIMEOUT = 300  # 5 minutes max wait


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def get_upload_url(base: str, api_key: str, filename: str, mime: str) -> dict:
    return api_request(
        "POST",
        base,
        "/api/v1/knowledge-base/upload-url",
        api_key,
        {"filename": filename, "mime_type": mime},
    )


def put_file_to_presigned_url(upload_url: str, file_bytes: bytes, mime: str) -> None:
    req = urllib.request.Request(upload_url, data=file_bytes, method="PUT")
    req.add_header("Content-Type", mime)
    req.add_header("Content-Length", str(len(file_bytes)))
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read().decode(errors="replace")
        raise RuntimeError(
            f"Presigned PUT failed HTTP {status}: {body}"
        ) from exc
    if status not in (200, 204):
        raise RuntimeError(f"Presigned PUT returned unexpected status {status}")


def process_document(
    base: str, api_key: str, document_uuid: str, s3_key: str, retrieval_mode: str
) -> dict:
    return api_request(
        "POST",
        base,
        "/api/v1/knowledge-base/process-document",
        api_key,
        {
            "document_uuid": document_uuid,
            "s3_key": s3_key,
            "retrieval_mode": retrieval_mode,
        },
    )


def poll_document_status(base: str, api_key: str, document_uuid: str) -> str:
    """Block until status is 'completed' or 'failed'. Returns final status."""
    deadline = time.time() + POLL_TIMEOUT
    dots = 0
    while time.time() < deadline:
        doc = api_request(
            "GET",
            base,
            f"/api/v1/knowledge-base/documents/{document_uuid}",
            api_key,
        )
        status = doc.get("processing_status", "unknown")
        if status == "completed":
            print(
                f"\r  ✓ Document processed: {doc.get('total_chunks', 0)} chunks  "
            )
            return "completed"
        if status == "failed":
            err = doc.get("processing_error") or "unknown error"
            print(f"\r  ✗ Processing failed: {err}  ")
            return "failed"
        dots = (dots + 1) % 4
        print(
            f"\r  Processing {'.' * (dots + 1):<4} (status={status})  ",
            end="",
            flush=True,
        )
        time.sleep(POLL_INTERVAL)
    print("\r  ✗ Timed out waiting for document processing.  ")
    return "timeout"


# ---------------------------------------------------------------------------
# Workflow patching
# ---------------------------------------------------------------------------

def fetch_workflow(base: str, api_key: str, workflow_id: int) -> dict:
    return api_request("GET", base, f"/api/v1/workflow/fetch/{workflow_id}", api_key)


def patch_start_node_documents(
    definition: dict, document_uuid: str
) -> tuple[dict, bool]:
    """Add document_uuid to the start node's document_uuids. Returns (definition, changed)."""
    changed = False
    for node in definition.get("nodes", []):
        if node.get("type") == "startCall":
            data = node.setdefault("data", {})
            existing: list = data.get("document_uuids") or []
            if document_uuid not in existing:
                data["document_uuids"] = existing + [document_uuid]
                changed = True
            else:
                print(f"  Document already attached to startCall node.")
            break
    return definition, changed


def update_and_publish_workflow(
    base: str,
    api_key: str,
    workflow_id: int,
    name: str,
    definition: dict,
    configurations: dict,
    publish: bool,
) -> None:
    api_request(
        "PUT",
        base,
        f"/api/v1/workflow/{workflow_id}",
        api_key,
        {
            "name": name,
            "workflow_definition": definition,
            "workflow_configurations": configurations,
        },
    )
    print(f"  Updated workflow {workflow_id}")
    if publish:
        api_request(
            "POST",
            base,
            f"/api/v1/workflow/{workflow_id}/publish",
            api_key,
            {},
        )
        print(f"  Published workflow {workflow_id}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument(
        "--workflow-id",
        type=int,
        default=5,
        help="Dograh workflow ID for Neha (default: 5)",
    )
    parser.add_argument(
        "--doc-uuid",
        default="",
        help="Skip upload and use an already-uploaded document UUID.",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Patch the workflow but do not republish.",
    )
    parser.add_argument(
        "--mode",
        choices=["chunked", "full_document"],
        default=RETRIEVAL_MODE,
        help=(
            "Retrieval mode: 'chunked' (vector search, deep KB — needs embedding key) "
            "or 'full_document' (inject entire text, no embedding key required)."
        ),
    )
    args = parser.parse_args()

    load_env_file(args.env_file)
    dograh_base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")

    document_uuid = args.doc_uuid.strip()

    # ------------------------------------------------------------------
    # Step 1–3: upload (skip if --doc-uuid supplied)
    # ------------------------------------------------------------------
    if document_uuid:
        print(f"Skipping upload, using existing document UUID: {document_uuid}")
    else:
        if not KB_FILE.exists():
            print(f"ERROR: knowledge base file not found: {KB_FILE}", file=sys.stderr)
            sys.exit(1)

        file_bytes = KB_FILE.read_bytes()
        print(f"Uploading {KB_FILE.name} ({len(file_bytes):,} bytes, mode={args.mode}) …")

        upload_info = get_upload_url(dograh_base, api_key, KB_FILENAME, KB_MIME)
        document_uuid = upload_info["document_uuid"]
        s3_key = upload_info["s3_key"]
        upload_url = upload_info["upload_url"]

        print(f"  Got presigned URL. document_uuid={document_uuid}")
        put_file_to_presigned_url(upload_url, file_bytes, KB_MIME)
        print("  File uploaded to storage.")

        print("  Triggering document processing …")
        process_document(dograh_base, api_key, document_uuid, s3_key, args.mode)

        print("  Waiting for processing to complete …")
        final_status = poll_document_status(dograh_base, api_key, document_uuid)
        if final_status != "completed":
            print(
                f"\nERROR: Document did not complete processing (status={final_status}).",
                file=sys.stderr,
            )
            if args.mode == "chunked":
                print(
                    "  If you see an embedding key error, either:\n"
                    "    (a) Configure an embedding API key under AI Models → Embedding in the UI, or\n"
                    "    (b) Re-run with --mode full_document (no embedding key required).",
                    file=sys.stderr,
                )
            sys.exit(1)

    # ------------------------------------------------------------------
    # Step 4–5: attach document to start node + publish
    # ------------------------------------------------------------------
    print(f"\nAttaching document {document_uuid} to workflow {args.workflow_id} …")
    wf = fetch_workflow(dograh_base, api_key, args.workflow_id)
    definition = wf.get("workflow_definition") or {}
    configurations = wf.get("workflow_configurations") or {}
    wf_name = wf.get("name") or f"Workflow {args.workflow_id}"

    definition, changed = patch_start_node_documents(definition, document_uuid)
    if changed:
        update_and_publish_workflow(
            dograh_base,
            api_key,
            args.workflow_id,
            wf_name,
            definition,
            configurations,
            publish=not args.no_publish,
        )
    else:
        print("  No changes needed. Workflow already has this document.")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n✓ Done.")
    print(f"  Document UUID : {document_uuid}")
    print(f"  Workflow      : {dograh_base}/workflow/{args.workflow_id}")
    print(f"  Retrieval mode: {args.mode}")
    if args.mode == "chunked":
        print(
            "\n  Deep KB is active. Neha will call retrieve_from_knowledge_base during calls."
        )
        print(
            "  Requires: Embedding API key configured under AI Models → Embedding in the UI."
        )
    else:
        print(
            "\n  Full Document mode: entire KB text is injected into context on every query."
        )
    print(
        "\n  To update the KB later: edit scripts/antoc_crm_knowledge_base.txt and re-run"
        " this script (a new document will be uploaded and attached)."
    )


if __name__ == "__main__":
    main()
