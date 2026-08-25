"""LLM port — Fake + live Anthropic Claude adapter.

All LLM calls in Banks go through this port. The Fake returns scripted responses
so the full pipeline (bill extract, classify, opportunity match) is testable
without an API key. The live adapter requires BANKS_ANTHROPIC_API_KEY.

Tiers (T3-15):
  cheap   — haiku (claude-haiku-4-5-20251001): triage / extraction / classification
  premium — sonnet (claude-sonnet-5): anything Josh or a guest reads directly

Design rule: callers never depend on a specific model; the port owns that choice.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol


class LLMPort(Protocol):
    def complete(self, system: str, user: str, *, max_tokens: int = 512) -> str: ...
    def extract_json(self, system: str, user: str, schema_hint: str = "") -> dict: ...


class FakeLLMPort:
    """Scripted responses for tests. Register patterns before calling."""

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses: dict[str, str] = responses or {}
        self._calls: list[tuple[str, str]] = []

    def register(self, user_fragment: str, response: str) -> None:
        self._responses[user_fragment] = response

    def complete(self, system: str, user: str, *, max_tokens: int = 512) -> str:
        self._calls.append((system, user))
        for fragment, resp in self._responses.items():
            if fragment.lower() in user.lower():
                return resp
        return '{"result": "fake"}'

    def extract_json(self, system: str, user: str, schema_hint: str = "") -> dict:
        raw = self.complete(system, user)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}

    @property
    def calls(self) -> list[tuple[str, str]]:
        return list(self._calls)


class ClaudeLLMPort:
    """Live Anthropic Claude adapter. Requires BANKS_ANTHROPIC_API_KEY in env."""

    CHEAP_MODEL = "claude-haiku-4-5-20251001"   # triage / extraction
    PREMIUM_MODEL = "claude-sonnet-5"            # Josh-facing output
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"

    # Back-compat alias — callers that set ClaudeLLMPort.MODEL still work.
    MODEL = CHEAP_MODEL

    def __init__(self, api_key: str | None = None) -> None:
        self._key = (api_key
                     or os.environ.get("BANKS_ANTHROPIC_API_KEY")
                     or os.environ.get("ANTHROPIC_API_KEY"))
        if not self._key:
            raise ValueError("BANKS_ANTHROPIC_API_KEY (or ANTHROPIC_API_KEY) not set")

    def _model_for_tier(self, tier: str) -> str:
        return self.PREMIUM_MODEL if tier == "premium" else self.CHEAP_MODEL

    def _call(self, system: str, user: str, max_tokens: int,
              tier: str = "cheap") -> str:
        payload = json.dumps({
            "model": self._model_for_tier(tier),
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            self.API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self._key,
                "anthropic-version": self.API_VERSION,
                "User-Agent": "Banks/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        return data["content"][0]["text"].strip()

    def complete(self, system: str, user: str, *, max_tokens: int = 512,
                tier: str = "cheap") -> str:
        return self._call(system, user, max_tokens, tier=tier)

    def extract_json(self, system: str, user: str, schema_hint: str = "",
                     tier: str = "cheap") -> dict:
        sys_prompt = system
        if schema_hint:
            sys_prompt += f"\n\nRespond ONLY with valid JSON matching: {schema_hint}"
        else:
            sys_prompt += "\n\nRespond ONLY with valid JSON, no markdown fences."
        raw = self.complete(sys_prompt, user, max_tokens=1024, tier=tier)
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)


def load_llm_port() -> LLMPort:
    """Return live Claude port if a key is present, Fake otherwise."""
    if os.environ.get("BANKS_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeLLMPort()
    return FakeLLMPort()
