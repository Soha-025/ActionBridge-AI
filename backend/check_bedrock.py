"""Manual smoke test for the Bedrock provider layer.

Not part of any automated test suite. Requires valid AWS credentials with
Bedrock model access for the model id in BEDROCK_MODEL_ID.

Run from backend/:

    python check_bedrock.py

Proves three things only:
  1. BedrockProvider can be constructed from the environment.
  2. A plain Converse request returns text.
  3. A Converse request with one harmless dummy tool returns a toolUse block.

This script does NOT touch any ActionBridge service, database, agent, or MCP.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Load backend/.env before importing llm_provider so the env vars are set
# at BedrockProvider construction time.
load_dotenv()

from llm_provider import BedrockProvider, LLMError  # noqa: E402


def _extract_text(content_blocks):
    for b in content_blocks or []:
        if isinstance(b, dict) and "text" in b:
            return b["text"] or ""
    return ""


def _print_env_banner():
    region = os.environ.get("AWS_REGION") or "<unset>"
    model = os.environ.get("BEDROCK_MODEL_ID") or "<unset>"
    # Never print credentials. Region + model id are not secrets.
    print(f"AWS_REGION        = {region}")
    print(f"BEDROCK_MODEL_ID  = {model}")
    print()


def test_plain_text(provider: BedrockProvider) -> bool:
    print("Test A: plain converse (expect 'BEDROCK_OK')")
    try:
        resp = provider.converse(
            messages=[
                {"role": "user", "content": [{"text": "Reply with exactly: BEDROCK_OK"}]}
            ]
        )
    except LLMError as e:
        print(f"[A] FAILED: {e}")
        return False

    text = _extract_text(resp["message"].get("content", []))
    print(f"[A] stop_reason = {resp['stop_reason']!r}")
    print(f"[A] text        = {text!r}")
    print(f"[A] usage       = {resp['usage']}")

    if "BEDROCK_OK" in (text or ""):
        print("[A] PASS")
        return True
    print("[A] WARN: model responded but did not include the exact token 'BEDROCK_OK'. "
          "Credentials work; the model just ignored the instruction. "
          "Consider a stronger model for tool use.")
    return True  # transport is fine


def test_tool_use(provider: BedrockProvider) -> bool:
    print("\nTest B: dummy tool 'get_demo_status' (expect a toolUse block)")
    dummy_tool = {
        "toolSpec": {
            "name": "get_demo_status",
            "description": (
                "Return the current demo status. "
                "Always call this tool when the user asks about demo status."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "reason": {
                            "type": "string",
                            "description": "Short reason for the call.",
                        }
                    },
                    "required": ["reason"],
                }
            },
        }
    }
    try:
        resp = provider.converse(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"text": "Call the get_demo_status tool with reason 'smoke-test'."}
                    ],
                }
            ],
            tools=[dummy_tool],
        )
    except LLMError as e:
        print(f"[B] FAILED: {e}")
        return False

    content = resp["message"].get("content", []) or []
    tool_uses = [b for b in content if isinstance(b, dict) and "toolUse" in b]

    print(f"[B] stop_reason        = {resp['stop_reason']!r}")
    print(f"[B] tool_use_blocks    = {len(tool_uses)}")
    if tool_uses:
        tu = tool_uses[0]["toolUse"]
        print(f"[B] tool name          = {tu.get('name')!r}")
        print(f"[B] tool input         = {tu.get('input')!r}")
        print("[B] PASS: model supports tool use through Bedrock Converse.")
        return True

    text = _extract_text(content)
    print(f"[B] text (no toolUse)  = {text!r}")
    print("[B] FAIL: no toolUse block. The selected model may not support tool use, "
          "or model access for a tool-capable Claude model is not enabled in Bedrock.")
    return False


def main():
    _print_env_banner()

    try:
        provider = BedrockProvider()
    except LLMError as e:
        print(f"[config] BedrockProvider init failed: {e}")
        print("Fix: set AWS_REGION and BEDROCK_MODEL_ID in backend/.env, "
              "and make sure boto3 can find credentials "
              "(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or ~/.aws/credentials).")
        sys.exit(1)

    a = test_plain_text(provider)
    b = test_tool_use(provider)

    print("\n--- summary ---")
    print(f"Test A (plain text)   : {'PASS' if a else 'FAIL'}")
    print(f"Test B (tool use)     : {'PASS' if b else 'FAIL'}")
    sys.exit(0 if (a and b) else 2)


if __name__ == "__main__":
    main()
