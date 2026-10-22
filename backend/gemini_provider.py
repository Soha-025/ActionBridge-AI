"""Live Gemini LLMProvider for ActionBridge (M3-owned).

Amazon Bedrock is currently unusable in our AWS account because even a direct
``bedrock-runtime converse`` call returns a *daily-token* ``ThrottlingException``
(an AWS-side quota problem, not an app bug). This module adds a real,
production-quality **Google Gemini** provider so the hackathon demo can run a
live LLM, while ``BedrockProvider`` stays the default/primary integration and
``DemoProvider`` remains the deterministic offline emergency backup.

It plugs into the SAME ``ActionBridgeAgent`` tool-use loop, the SAME MCP
boundary, the SAME services and the SAME SQLite database. It is a pure
**translation layer**: it converts ActionBridge's provider contract

    converse(messages, tools, system) -> {"message","stop_reason","usage"}

into Google's current *Interactions* API and back again. It contains NO
scheduling, conflict-detection, replanning or apply logic -- those live only
in ``services.py`` and are reached by the agent through MCP.

Gemini specifics (verified against the Oct-2026 GA "Interactions" API):

    from google import genai                      # package: google-genai
    client = genai.Client(api_key=GEMINI_API_KEY)
    interaction = client.interactions.create(
        model=GEMINI_MODEL, system_instruction=..., input=<steps>,
        tools=<declarations>, store=False)
    interaction.steps          -> function_call steps (.name/.arguments/.id)
    interaction.output_text    -> final text

``store=False`` + a full client-managed history is the documented *stateless*
mode, which matches this agent's stateless ``converse`` contract: every call
rebuilds the whole conversation from ActionBridge message blocks.

Tool safety: only the five model-facing tools are ever sent to Gemini.
``apply_changes`` / ``cancel_change`` / ``get_change`` are structurally absent
from ``agent.TOOL_SCHEMAS`` and additionally filtered out here -- and the
agent's allowlist remains an independent second gate -- so Gemini can never
apply a change; a human must click "Apply Changes" in the UI.
"""

from __future__ import annotations

import json
import os

from llm_provider import LLMError, LLMProvider

# Current tool-capable default used across the Oct-2026 Gemini docs.
DEFAULT_MODEL = "gemini-3.8-flash"

# The ONLY tools a model may be given. Mirrors mcp_server.MODEL_TOOLS and is a
# defensive second filter on top of agent.TOOL_SCHEMAS already omitting the
# forbidden change-lifecycle functions.
_ALLOWED_TOOLS = frozenset({
    "create_commitment",
    "get_commitments",
    "create_plan",
    "detect_conflicts",
    "propose_replan",
})


class GeminiProvider(LLMProvider):
    """Real Gemini provider implementing the ActionBridge LLMProvider contract.

    ``client`` may be injected (used by tests to avoid any network call). When
    omitted, the SDK is imported lazily and a client is built from
    ``GEMINI_API_KEY``. The API key is never logged and is scrubbed from any
    provider error message.
    """

    def __init__(self, client=None, model: str | None = None, api_key: str | None = None):
        self.model = (
            model
            or os.environ.get("GEMINI_MODEL")
            or DEFAULT_MODEL
        )
        self._api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY")

        # Verbatim model steps from each live turn, keyed by the toolUseId we
        # handed back to the agent. The stateless Interactions API requires the
        # model's own steps (thought steps and their encrypted `signature`,
        # model_output, function_call) to be echoed back on the follow-up turn;
        # reconstructing them by hand drops the signature and the API rejects
        # the second request with HTTP 400. The provider instance lives for the
        # whole agent tool loop, so this cache survives across ``converse`` calls.
        self._turn_cache: dict[str, tuple] = {}

        if client is not None:
            self._client = client
            return

        if not self._api_key:
            raise LLMError("GEMINI_API_KEY environment variable is not set.")
        try:
            from google import genai  # provided by the `google-genai` package
        except ImportError:
            raise LLMError(
                "The 'google-genai' package is required for GeminiProvider. "
                "Install it with: pip install google-genai"
            ) from None
        try:
            self._client = genai.Client(api_key=self._api_key)
        except Exception as err:  # never let the key escape in a message
            raise LLMError(self._safe_msg(err)) from None

    # ------------------------------------------------------------------ #
    def converse(self, messages, tools=None, system=None):
        """Translate to Gemini, call it, and translate the reply back."""
        kwargs = {
            "model": self.model,
            "input": self._build_history(messages or [], self._turn_cache),
            "tools": self._declarations(tools or []),
            "store": False,
        }
        if system:
            kwargs["system_instruction"] = system

        try:
            interaction = self._client.interactions.create(**kwargs)
        except LLMError:
            raise
        except Exception as err:  # network/API/quota errors -> secret-safe
            raise LLMError(self._safe_msg(err)) from None

        return self._to_actionbridge(interaction)

    # ---------------------- ActionBridge -> Gemini --------------------- #
    @staticmethod
    def _build_history(messages, turn_cache=None) -> list[dict]:
        """Rebuild the full stateless conversation as Gemini input steps.

        Maps ActionBridge content blocks to Gemini steps:
          * user {"text"}      -> {"type":"user_input", ...}
          * assistant toolUse  -> the model's OWN function_call step, echoed
            verbatim from ``turn_cache`` when available (this preserves the
            accompanying ``thought``/signature steps the API needs), otherwise a
            reconstructed {"type":"function_call", name/arguments/id} step.
          * user toolResult    -> {"type":"function_result", name/call_id/result}
        """
        turn_cache = turn_cache or {}
        # toolUseId -> tool name, so function_results can reference their call.
        id_to_name: dict[str, str] = {}
        for msg in messages:
            for block in msg.get("content") or []:
                if isinstance(block, dict) and isinstance(block.get("toolUse"), dict):
                    use = block["toolUse"]
                    id_to_name[use.get("toolUseId", "")] = use.get("name", "")

        steps: list[dict] = []
        emitted_turns: set = set()
        for msg in messages:
            role = msg.get("role")
            for block in msg.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if role == "user" and "text" in block:
                    steps.append({
                        "type": "user_input",
                        "content": [{"type": "text", "text": block["text"] or ""}],
                    })
                elif isinstance(block.get("toolUse"), dict):
                    use = block["toolUse"]
                    call_id = use.get("toolUseId", "")
                    cached = turn_cache.get(call_id)
                    if cached:
                        # Echo the model's verbatim step dump for this turn once
                        # (thought + model_output + all its function_call steps);
                        # the function_call itself is inside that dump.
                        turn_key, raw_turn = cached
                        if turn_key not in emitted_turns:
                            steps.extend(raw_turn)
                            emitted_turns.add(turn_key)
                        continue
                    steps.append({
                        "type": "function_call",
                        "name": use.get("name", ""),
                        "arguments": use.get("input") or {},
                        "id": call_id,
                    })
                elif isinstance(block.get("toolResult"), dict):
                    res = block["toolResult"]
                    call_id = res.get("toolUseId", "")
                    text = "".join(
                        (b.get("text") or "")
                        for b in (res.get("content") or [])
                        if isinstance(b, dict)
                    )
                    step = {
                        "type": "function_result",
                        "name": id_to_name.get(call_id, ""),
                        "call_id": call_id,
                        "result": [{"type": "text", "text": text}],
                    }
                    if res.get("status") == "error":
                        step["is_error"] = True
                    steps.append(step)
        return steps

    @staticmethod
    def _declarations(tools) -> list[dict]:
        """Convert Bedrock-format toolSchemas to Gemini function declarations.

        Bedrock: {"toolSpec": {"name","description","inputSchema":{"json":...}}}
        Gemini : {"type":"function","name","description","parameters":...}

        Only the five allowed tools survive this filter (defense in depth).
        """
        decls = []
        for tool in tools or []:
            spec = tool.get("toolSpec") if isinstance(tool, dict) else None
            if not isinstance(spec, dict):
                continue
            name = spec.get("name")
            if name not in _ALLOWED_TOOLS:
                continue
            parameters = (spec.get("inputSchema") or {}).get("json") or {
                "type": "object", "properties": {},
            }
            decls.append({
                "type": "function",
                "name": name,
                "description": spec.get("description", ""),
                "parameters": parameters,
            })
        return decls

    # ---------------------- Gemini -> ActionBridge --------------------- #
    def _to_actionbridge(self, interaction) -> dict:
        """Turn a Gemini interaction into the ActionBridge response contract."""
        steps = getattr(interaction, "steps", None) or []
        # Capture the model's raw steps verbatim (when the SDK exposes
        # model_dump) so a follow-up turn can echo them back with signatures.
        raw_turn = self._collect_raw_turn(steps)
        turn_call_ids: list[str] = []

        tool_uses: list[dict] = []
        for i, step in enumerate(steps):
            if self._is_function_call(step):
                name = getattr(step, "name", "") or ""
                args = getattr(step, "arguments", None)
                call_id = getattr(step, "id", None) or f"gemini_call_{i}"
                tool_uses.append({
                    "toolUse": {
                        "name": name,
                        "input": dict(args) if isinstance(args, dict) else {},
                        "toolUseId": str(call_id),
                    }
                })
                turn_call_ids.append(str(call_id))

        if raw_turn and turn_call_ids:
            turn_key = turn_call_ids[0]
            for cid in turn_call_ids:
                self._turn_cache[cid] = (turn_key, raw_turn)

        if tool_uses:
            message = {"role": "assistant", "content": tool_uses}
            stop_reason = "tool_use"
        else:
            text = getattr(interaction, "output_text", None)
            if text is None:
                text = self._text_from_steps(steps)
            message = {"role": "assistant", "content": [{"text": text or ""}]}
            stop_reason = "end_turn"

        return {
            "message": message,
            "stop_reason": stop_reason,
            "usage": self._usage(interaction),
        }

    # ------------------------------ helpers ---------------------------- #
    @staticmethod
    def _collect_raw_turn(steps) -> list[dict] | None:
        """Verbatim JSON dumps of the model's own steps, or None.

        Returns a list only when *every* step exposes ``model_dump`` (i.e. real
        SDK step objects), so the stateless follow-up can replay thought steps
        and their encrypted ``signature``. Legacy/test fakes without
        ``model_dump`` yield None, and the provider falls back to reconstructing
        the function_call (previous behaviour).
        """
        if not steps:
            return None
        raw: list[dict] = []
        for step in steps:
            dump = getattr(step, "model_dump", None)
            if not callable(dump):
                return None
            try:
                raw.append(dump(mode="json", exclude_none=True))
            except Exception:
                return None
        return raw or None

    @staticmethod
    def _is_function_call(step) -> bool:
        t = getattr(step, "type", None)
        if t == "function_call":
            return True
        # tolerate enum-like values (e.g. Type.FUNCTION_CALL)
        return bool(t) and str(t).lower().split(".")[-1] == "function_call"

    @staticmethod
    def _text_from_steps(steps) -> str:
        parts = []
        for step in steps:
            content = getattr(step, "content", None) or []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(block["text"])
                elif getattr(block, "text", None):
                    parts.append(block.text)
        return "".join(parts)

    @staticmethod
    def _usage(interaction) -> dict:
        for attr in ("usage", "usage_metadata", "metadata"):
            value = getattr(interaction, attr, None)
            if isinstance(value, dict):
                return value
        return {}

    def _safe_msg(self, err) -> str:
        """A debuggable, API-key-free message from any Gemini/SDK error.

        ``google-genai`` raises an ``APIError`` carrying an HTTP ``code`` and a
        structured ``details`` payload (this is where the real *400 Bad Request*
        validation text lives). We surface those explicitly instead of flattening
        the error, then scrub the API key and clamp length.
        """
        parts = [f"{type(err).__name__}: {err}" if err else type(err).__name__]
        code = getattr(err, "code", None)
        if code is not None and str(code) not in parts[0]:
            parts.append(f"[code={code}]")
        status = getattr(err, "status_code", None) or getattr(err, "status", None)
        if status is not None:
            parts.append(f"[http_status={status}]")
        details = getattr(err, "details", None)
        if details:
            try:
                parts.append(f"[details={json.dumps(details, default=str)[:600]}]")
            except Exception:
                parts.append(f"[details={details}]")
        text = " ".join(parts)
        if self._api_key:
            text = text.replace(str(self._api_key), "<GEMINI_KEY_REDACTED>")
        return text[:1000]


def _json_default(obj):  # pragma: no cover - tiny helper for debugging/logging
    return getattr(obj, "__dict__", str(obj))
