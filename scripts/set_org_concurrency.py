#!/usr/bin/env python3
"""Set this Dograh org's concurrent PSTN call cap (default 25).

Antoc can queue 25 dials; Dograh was defaulting to 10 org slots, so only
10 rings happened. This writes CONCURRENT_CALL_LIMIT on the org.

Usage:
  python3 scripts/set_org_concurrency.py
  python3 scripts/set_org_concurrency.py --limit 25
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seed_antoc_voice_tools import (  # noqa: E402
    DEFAULT_ENV_FILE,
    api_request,
    load_env_file,
    require_env,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 100:
        raise SystemExit("--limit must be 1..100")

    load_env_file(args.env_file)
    base = require_env("DOGRAH_BASE_URL")
    api_key = require_env("DOGRAH_API_KEY")

    before = api_request(
        "GET", base, "/api/v1/organizations/campaign-defaults", api_key
    )
    print(
        f"Before: concurrent_call_limit={before.get('concurrent_call_limit')} "
        f"from_numbers={before.get('from_numbers_count')}"
    )

    updated = api_request(
        "PUT",
        base,
        "/api/v1/organizations/concurrent-call-limit",
        api_key,
        {"value": args.limit},
    )
    print(f"Set: {json.dumps(updated)}")

    after = api_request(
        "GET", base, "/api/v1/organizations/campaign-defaults", api_key
    )
    print(
        f"After: concurrent_call_limit={after.get('concurrent_call_limit')} "
        f"from_numbers={after.get('from_numbers_count')}"
    )


if __name__ == "__main__":
    main()
