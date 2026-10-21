"""Provider-neutral LLM abstraction for ActionBridge.

Only BedrockProvider is implemented here. Any future provider (e.g. Nvidia)
should subclass LLMProvider and return the same dictionary shape from .converse().

Nothing in this file knows about ActionBridge's domain (commitments, plans,
replanning, MCP, or dates). It is deliberately boring transport code.
"""

from __future__ import annotations

import os
import re

import boto3
from botocore.exceptions import BotoCoreError, ClientError


class LLMError(Exception):
    """Raised for any provider-side failure.

    The message is safe to log or show to operators: it never contains raw
    AWS access keys, session tokens, or signed URLs.
    """


class LLMProvider:
    """Abstract provider interface.

    Subclasses must implement .converse(messages, tools, system) and return:

        {
            "message":     {"role": "assistant", "content": [ ...raw blocks... ]},
            "stop_reason": "end_turn" | "tool_use" | ...,
            "usage":       {"inputTokens": ..., "outputTokens": ..., "totalTokens": ...},
        }

    `content` must contain the raw provider blocks (text, toolUse, ...) so the
    caller (agent loop) can inspect both text and tool use without losing data.
    """

    def converse(self, messages, tools=None, system=None):  # pragma: no cover
        raise NotImplementedError


# Matches AWS access key ids and long base64-ish blobs (session tokens, signed URLs).
_AK_RE = re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")
_BLOB_RE = re.compile(r"[A-Za-z0-9/+=]{40,}")


def _redact(text: str) -> str:
    if not text:
        return ""
    text = _AK_RE.sub("<AK_REDACTED>", text)
    text = _BLOB_RE.sub("<BLOB_REDACTED>", text)
    return text[:240]


class BedrockProvider(LLMProvider):
    """Amazon Bedrock Converse API implementation.

    Configuration comes exclusively from the environment:

        AWS_REGION        e.g. "us-east-1"                        (required)
        BEDROCK_MODEL_ID  a Claude/other tool-capable model id    (required)

    AWS credentials are resolved by boto3's standard chain
    (env vars -> ~/.aws/credentials -> IAM role -> etc.). This class never
    reads a hard-coded key and never logs one.
    """

    def __init__(self):
        region = os.environ.get("AWS_REGION")
        model_id = os.environ.get("BEDROCK_MODEL_ID")
        if not region:
            raise LLMError("AWS_REGION environment variable is not set.")
        if not model_id:
            raise LLMError("BEDROCK_MODEL_ID environment variable is not set.")

        self.region = region
        self.model_id = model_id

        try:
            # No explicit credentials here: boto3's default chain is the source of truth.
            self.client = boto3.client("bedrock-runtime", region_name=region)
        except (BotoCoreError, ClientError) as err:
            raise LLMError(self._safe_msg(err)) from None

    # ------------------------------------------------------------------ #
    def converse(self, messages, tools=None, system=None):
        kwargs = {"modelId": self.model_id, "messages": list(messages)}

        if system:
            # Bedrock expects a list of {text: ...} content blocks.
            kwargs["system"] = [{"text": system}] if isinstance(system, str) else list(system)

        # Only include toolConfig when there is at least one tool.
        if tools:
            kwargs["toolConfig"] = {"tools": list(tools)}

        try:
            resp = self.client.converse(**kwargs)
        except (BotoCoreError, ClientError) as err:
            raise LLMError(self._safe_msg(err)) from None
        except Exception as err:  # defensive: SDK may raise unexpected types
            raise LLMError(
                f"Bedrock call failed: {type(err).__name__}: {_redact(str(err))}"
            ) from None

        if not isinstance(resp, dict):
            raise LLMError("Bedrock returned a non-dict response.")

        output = resp.get("output") or {}
        message = output.get("message") or {"role": "assistant", "content": []}

        return {
            "message": message,
            "stop_reason": resp.get("stopReason"),
            "usage": resp.get("usage") or {},
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _safe_msg(err) -> str:
        """Build a debuggable, secret-free LLMError message from a boto3 error."""
        name = type(err).__name__
        code = ""
        text = ""

        if isinstance(err, ClientError):
            e = (err.response or {}).get("Error", {}) or {}
            code = e.get("Code") or ""
            text = e.get("Message") or ""
        else:
            text = str(err) or ""

        parts = [f"Bedrock call failed: {name}"]
        if code:
            parts.append(code)
        redacted = _redact(text)
        if redacted:
            parts.append(redacted)
        return " | ".join(parts)
