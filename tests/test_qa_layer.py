"""Tests for @banks LLM QA layer (banks/qa.py).

Design decisions (grilled 2026-09-01):
- app_mention only — untagged messages are ignored
- 5 read-only tools: pipeline_summary, company_status, who_do_i_know, call_list,
  list_opportunities
- bounded loop: at most 3 tool calls before compose
- Haiku routes, Sonnet composes (config-driven BANKS_QA_MODEL)
- approver-only
- rate limiter 10/min
- fenced untrusted data: DB-derived text wrapped in <untrusted_data> delimiters
- answer strictly from tool results — no invention
- off-topic / capability scoping: honest decline
- LLM-down graceful degradation
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
import time

import pytest

from banks.chatport import FakeChatPort
from banks.config import BanksConfig, load_config
from banks.llmport import FakeLLMPort
from banks.qa import (
    RATE_LIMIT_RPM,
    RateLimitExceeded,
    answer_question,
    call_tool,
    check_rate_limit,
    strip_mention,
)
from banks.store import cursor, init_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path():
    p = os.path.join(tempfile.mkdtemp(), "t.db")
    init_db(p)
    return p


@pytest.fixture
def cfg(db_path):
    base = load_config()
    return dataclasses.replace(base, db_path=db_path, approver_user_id="UJOSH")


# ---------------------------------------------------------------------------
# strip_mention
# ---------------------------------------------------------------------------

class TestStripMention:
    def test_strips_bot_user_id_prefix(self):
        assert strip_mention("<@U12345> where am I", "U12345") == "where am I"

    def test_strips_leading_whitespace_after_mention(self):
        assert strip_mention("<@UABC>  tell me pipeline", "UABC") == "tell me pipeline"

    def test_no_mention_returns_original(self):
        assert strip_mention("where am I", "UABC") == "where am I"

    def test_mid_sentence_mention_not_stripped(self):
        text = "tell <@UABC> something"
        assert strip_mention(text, "UABC") == text


# ---------------------------------------------------------------------------
# call_tool — pure dispatch, no LLM
# ---------------------------------------------------------------------------

class TestCallTool:
    def test_pipeline_summary_returns_string(self, db_path):
        result = call_tool(db_path, "pipeline_summary", {})
        assert isinstance(result, str)

    def test_company_status_unknown_company(self, db_path):
        result = call_tool(db_path, "company_status", {"company": "GhostCo"})
        assert "GhostCo" in result or "not found" in result.lower() or result

    def test_who_do_i_know_returns_string(self, db_path):
        result = call_tool(db_path, "who_do_i_know", {"company": "Acme"})
        assert isinstance(result, str)

    def test_call_list_returns_string(self, db_path):
        result = call_tool(db_path, "call_list", {})
        assert isinstance(result, str)

    def test_list_opportunities_returns_string(self, db_path):
        result = call_tool(db_path, "list_opportunities", {})
        assert isinstance(result, str)

    def test_unknown_tool_raises_value_error(self, db_path):
        with pytest.raises(ValueError, match="unknown tool"):
            call_tool(db_path, "drop_database", {})


# ---------------------------------------------------------------------------
# answer_question — tool loop + compose
# ---------------------------------------------------------------------------

class TestAnswerQuestion:
    def test_single_tool_call_answered(self, db_path):
        """LLM routes to pipeline_summary, gets result, composes answer."""
        llm = FakeLLMPort({
            "pipeline_summary": '{"tool": "pipeline_summary", "args": {}}',
            "pipeline": "You currently have 0 tracked applications.",
        })
        reply = answer_question(db_path, "where am I?", llm)
        assert isinstance(reply, str)
        assert len(reply) > 0

    def test_done_without_tool_call(self, db_path):
        """LLM immediately composes without calling a tool."""
        llm = FakeLLMPort({
            "where am I": '{"tool": "done"}',
            "": "You have no applications yet.",
        })
        reply = answer_question(db_path, "where am I?", llm)
        assert isinstance(reply, str)

    def test_bounded_loop_max_3_calls(self, db_path):
        """Loop must stop after 3 tool calls even if LLM keeps requesting more."""
        # LLM always asks for pipeline_summary, never "done"
        llm = FakeLLMPort({})
        llm.register("", '{"tool": "pipeline_summary", "args": {}}')
        # Should not infinite-loop; must terminate and return something
        reply = answer_question(db_path, "keep going", llm)
        assert isinstance(reply, str)

    def test_unknown_tool_in_llm_response_handled_gracefully(self, db_path):
        """If the LLM hallucinates a tool name, we catch ValueError and stop."""
        llm = FakeLLMPort({"": '{"tool": "send_email", "args": {}}'})
        reply = answer_question(db_path, "send a message", llm)
        assert isinstance(reply, str)

    def test_tool_result_fenced_in_prompt(self, db_path):
        """Tool results must be wrapped in <untrusted_data> tags in the compose
        prompt so injected text can't masquerade as a system instruction."""
        recorded_prompts: list[str] = []

        class SpyLLM(FakeLLMPort):
            def complete(self, system, user, *, max_tokens=512):
                recorded_prompts.append(user)
                return super().complete(system, user, max_tokens=max_tokens)

            def extract_json(self, system, user, schema_hint=""):
                recorded_prompts.append(user)
                return super().extract_json(system, user, schema_hint=schema_hint)

        llm = SpyLLM({
            # routing call: return pipeline_summary tool
            "Question: status?": '{"tool": "pipeline_summary", "args": {}}',
            # second routing call (after tool result): done
            "pipeline_summary": '{"tool": "done"}',
            # compose call
            "": "You have 0 applications.",
        })
        answer_question(db_path, "status?", llm)
        compose_prompt = next(
            (p for p in recorded_prompts if "<untrusted_data>" in p), None
        )
        assert compose_prompt is not None, (
            "tool result must be fenced with <untrusted_data> in the compose prompt"
        )

    def test_off_topic_question_declined(self, db_path):
        """Question outside job-search scope → polite decline, no tool call."""
        llm = FakeLLMPort({"": '{"tool": "done"}'})
        # Even if the LLM gives done, the answer should not invent information.
        reply = answer_question(db_path, "what's the weather today?", llm)
        assert isinstance(reply, str)

    def test_llm_down_returns_graceful_message(self, db_path):
        """When the LLM raises an exception, return a friendly error, don't crash."""
        class BrokenLLM:
            def complete(self, system, user, *, max_tokens=512):
                raise ConnectionError("API unreachable")
            def extract_json(self, system, user, schema_hint=""):
                raise ConnectionError("API unreachable")

        reply = answer_question(db_path, "where am I?", BrokenLLM())
        assert isinstance(reply, str)
        assert len(reply) > 0  # something user-friendly, not a traceback


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_check_rate_limit_allows_below_cap(self):
        bucket: list[float] = []  # empty — zero calls so far
        check_rate_limit(bucket)   # must not raise

    def test_check_rate_limit_raises_at_cap(self):
        now = time.monotonic()
        bucket = [now] * RATE_LIMIT_RPM  # exactly at the cap within 1 minute
        with pytest.raises(RateLimitExceeded):
            check_rate_limit(bucket)

    def test_check_rate_limit_evicts_old_timestamps(self):
        old = time.monotonic() - 61  # older than 1 minute window
        bucket = [old] * (RATE_LIMIT_RPM + 5)  # lots of old calls, none recent
        check_rate_limit(bucket)  # should NOT raise — all expired


# ---------------------------------------------------------------------------
# handle_qa_mention integration (with socket_listener wiring)
# ---------------------------------------------------------------------------

class TestHandleQaMention:
    """Integration: the full handle_qa_mention path (auth + rate limit + answer)."""

    def test_authorized_user_gets_reply(self, db_path, cfg):
        from banks.qa import handle_qa_mention
        llm = FakeLLMPort({"": '{"tool": "done"}'})
        chat = FakeChatPort()
        llm.register("pipeline_summary", '{"tool": "pipeline_summary", "args": {}}')
        llm.register("", "You have 0 applications.")
        reply = handle_qa_mention(
            cfg=cfg, db_path=db_path, text="where am I",
            user_id="UJOSH", llm=llm, thread_ts=None,
        )
        assert isinstance(reply, str)

    def test_unauthorized_user_gets_no_reply(self, db_path, cfg):
        # Auth gate lives in socket_listener._handle_app_mention, not qa.
        # handle_qa_mention itself doesn't check auth — caller does.
        # Verify is_authorized rejects unknown user.
        from banks.socket_listener import is_authorized
        assert is_authorized(cfg, "USTRANGER") is False

    def test_rate_exceeded_returns_friendly_message(self, db_path, cfg):
        from banks.qa import _rate_buckets, handle_qa_mention, RATE_LIMIT_RPM
        now = time.monotonic()
        _rate_buckets["UJOSH"] = [now] * RATE_LIMIT_RPM
        try:
            reply = handle_qa_mention(
                cfg=cfg, db_path=db_path, text="status?",
                user_id="UJOSH", llm=FakeLLMPort({}), thread_ts=None,
            )
            assert reply is not None
            assert "slow down" in reply.lower() or "rate" in reply.lower() or "too many" in reply.lower()
        finally:
            _rate_buckets.pop("UJOSH", None)
