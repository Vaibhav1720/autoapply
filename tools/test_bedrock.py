#!/usr/bin/env python3
"""Quick Bedrock health check using AWS_BEARER_TOKEN_BEDROCK from .env."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"
REGION = os.environ.get("AWS_REGION", "us-east-1")
MODELS = [
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-opus-4-5-20251101-v1:0",
]


def load_env() -> None:
    if not ENV_FILE.exists():
        print(f"Missing {ENV_FILE}")
        sys.exit(1)
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def converse(model_id: str, token: str) -> tuple[bool, str]:
    url = f"https://bedrock-runtime.{REGION}.amazonaws.com/model/{model_id}/converse"
    body = json.dumps(
        {"messages": [{"role": "user", "content": [{"text": "Say hi in one word."}]}]}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = json.loads(resp.read())
            text = (
                data.get("output", {})
                .get("message", {})
                .get("content", [{}])[0]
                .get("text", "")
            )
            return True, text.strip() or "(empty)"
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            msg = json.loads(raw).get("message", raw)
        except json.JSONDecodeError:
            msg = raw
        return False, f"HTTP {e.code}: {msg[:200]}"


def main() -> None:
    load_env()
    token = os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "")
    if not token:
        print("AWS_BEARER_TOKEN_BEDROCK is not set in .env")
        sys.exit(1)
    print(f"Region: {REGION}")
    print(f"Token: set ({len(token)} chars)\n")

    ok_any = False
    for mid in MODELS:
        ok, detail = converse(mid, token)
        status = "OK " if ok else "FAIL"
        print(f"{status} {mid}")
        print(f"     {detail}\n")
        ok_any = ok_any or ok

    if not ok_any:
        print("No models succeeded.")
        if "INVALID_PAYMENT_INSTRUMENT" in detail:
            print(
                "\n→ Fix AWS Billing: add a payment method at "
                "https://console.aws.amazon.com/billing/"
            )
        sys.exit(1)
    print("At least one model works — use that ID in Cursor custom model.")


if __name__ == "__main__":
    main()
