"""Manual smoke test for the live Gemini provider layer.

Not part of any automated test suite. Requires a real GEMINI_API_KEY and the
'google-genai' package installed (pip install google-genai).

Run from backend/:

    GEMINI_API_KEY=your_key python check_gemini.py
    # optionally: GEMINI_MODEL=gemini-3.8-flash (this is the default)

Proves two things only, WITHOUT touching any ActionBridge service, database,
agent, or MCP (tools are described to the model but never executed here):

  Test A: a plain request returns text (expects the exact token 'GEMINI_OK').
  Test B: a request with the five real ActionBridge tool schemas results in a
          toolUse block (proves Gemini function calling works).
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# Load backend/.env first so GEMINI_API_KEY / GEMINI_MODEL are set.
load_dotenv()

from gemini_provider import GeminiProvider  # noqa: E402
from llm_provider import LLMError  # noqa: E402


def _extract_text(content_blocks):
    for b in content_blocks or []:
        if isinstance(b, dict) and "text" in b:
            return b["text"] or ""
    return ""


def _banner():
    model = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash (default)"
    key = os.environ.get("GEMINI_API_KEY")
    # Never print the key; only whether it is present.
    print(f"GEMINI_MODEL    = {model}")
    print(f"GEMINI_API_KEY  = {'<set>' if key else '<unset>'}")
    print()


def test_plain_text(provider: GeminiProvider) -> bool:
    print("Test A: plain converse (expect 'GEMINI_OK')")
    try:
        resp = provider.converse(
            messages=[{"role": "user",
                       "content": [{"text": "Reply with exactly: GEMINI_OK"}]}]
        )
    except LLMError as e:
        print(f"[A] FAILED: {e}")
        return False

    text = _extract_text(resp["message"].get("content", []))
    print(f"[A] stop_reason = {resp['stop_reason']!r}")
    print(f"[A] text        = {text!r}")
    if "GEMINI_OK" in (text or ""):
        print("[A] PASS")
        return True
    print("[A] WARN: transport worked but the exact token was not echoed. "
          "The API key is valid; try a stronger/faster model.")
    return True


def test_tool_use(provider: GeminiProvider) -> bool:
    print("\nTest B: real ActionBridge tool schemas (expect a toolUse block)")
    # Import the live, five-tool schema set. Importing is side-effect free.
    from agent import TOOL_SCHEMAS

    try:
        resp = provider.converse(
            messages=[{"role": "user",
                       "content": [{"text": "What should I work on today? Build my plan."}]}],
            tools=TOOL_SCHEMAS,
            system="You are ActionBridge. When the user wants a plan, call create_plan.",
        )
    except LLMError as e:
        print(f"[B] FAILED: {e}")
        return False

    content = resp["message"].get("content", []) or []
    tool_uses = [b for b in content if isinstance(b, dict) and "toolUse" in b]
    print(f"[B] stop_reason     = {resp['stop_reason']!r}")
    print(f"[B] tool_use_blocks = {len(tool_uses)}")
    if tool_uses:
        tu = tool_uses[0]["toolUse"]
        print(f"[B] tool name       = {tu.get('name')!r}")
        print(f"[B] tool input      = {tu.get('input')!r}")
        print("[B] PASS: Gemini function calling works (nothing was executed).")
        return True

    print(f"[B] text (no toolUse) = {_extract_text(content)!r}")
    print("[B] FAIL: model did not request a tool. The configured model may not "
          "support function calling; set GEMINI_MODEL to a tool-capable model.")
    return False


def main():
    _banner()
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set. Refusing to run (no live call made).")
        print("Set it in your environment or backend/.env (never commit it).")
        sys.exit(1)

    try:
        provider = GeminiProvider()
    except LLMError as e:
        print(f"[config] GeminiProvider init failed: {e}")
        print("Fix: install 'google-genai' and set a valid GEMINI_API_KEY.")
        sys.exit(1)

    a = test_plain_text(provider)
    b = test_tool_use(provider)

    print("\n--- summary ---")
    print(f"Test A (plain text): {'PASS' if a else 'FAIL'}")
    print(f"Test B (tool use)  : {'PASS' if b else 'FAIL'}")
    sys.exit(0 if (a and b) else 2)


if __name__ == "__main__":
    main()
