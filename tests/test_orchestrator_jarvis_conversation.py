"""orchestrator/jarvis_conversation.py -- Jarvis's own subscription-only
conversational dispatch. No test invokes the real `claude` binary: each
writes a genuinely executable fake script standing in for the CLI,
mirroring tests/test_orchestrator_claude_cli_adapter.py's convention."""

from __future__ import annotations

import json
import stat
import tempfile
import textwrap
import unittest
from pathlib import Path

from orchestrator.jarvis_conversation import (
    ConversationTurnResult,
    DraftFieldSuggestion,
    JarvisConversationError,
    SubscriptionAuthRequired,
    converse,
)

_SUBSCRIPTION_AUTH = {"authMethod": "claude.ai", "subscriptionType": "pro"}
_API_KEY_AUTH = {"authMethod": "apiKey"}
_LOGGED_OUT_AUTH = {"authMethod": None}

_FAKE_CLAUDE_TEMPLATE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json
    import sys

    AUTH_STATUS = {auth_status}
    MODE = {mode!r}

    args = sys.argv[1:]
    if args[:2] == ["auth", "status"]:
        print(json.dumps(AUTH_STATUS))
        sys.exit(0)

    stdin_data = sys.stdin.read()  # the prompt, consumed but not required by the fake
    turn = {{"reply": "Got it -- shaping that into a draft.", "suggestion": {{
        "outcome": "Ship the thing", "scope": ["do the thing"], "non_goals": [],
        "acceptance_criteria": ["it works"], "open_questions": [],
    }}}}
    if MODE == "success_bare":
        print(json.dumps(turn))
        sys.exit(0)
    if MODE == "success_result_dict":
        print(json.dumps({{"type": "result", "subtype": "success", "result": turn}}))
        sys.exit(0)
    if MODE == "success_result_string":
        print(json.dumps({{"type": "result", "subtype": "success", "result": json.dumps(turn)}}))
        sys.exit(0)
    if MODE == "success_markdown_fenced":
        fenced = "```json\\n" + json.dumps(turn) + "\\n```"
        print(json.dumps({{"type": "result", "subtype": "success", "result": fenced}}))
        sys.exit(0)
    if MODE == "report_env_marker":
        import os
        seen = os.environ.get("ANTHROPIC_API_KEY", "__ABSENT__")
        report = {{"reply": seen, "suggestion": None}}
        print(json.dumps(report))
        sys.exit(0)
    if MODE == "echo_trusted_citations":
        received = json.loads(stdin_data)
        # Echo exactly what was received back as the reply text, so the
        # test can assert on precisely what reached the subprocess's
        # stdin -- not what the caller merely intended to send.
        report = {{"reply": json.dumps(received.get("trusted_citations")), "suggestion": None}}
        print(json.dumps(report))
        sys.exit(0)
    if MODE == "no_suggestion_yet":
        print(json.dumps({{"reply": "Tell me more about the outcome you want.", "suggestion": None}}))
        sys.exit(0)
    if MODE == "malformed_json":
        print("{{not valid json")
        sys.exit(0)
    if MODE == "missing_reply":
        print(json.dumps({{"suggestion": None}}))
        sys.exit(0)
    if MODE == "suggestion_not_object":
        print(json.dumps({{"reply": "ok", "suggestion": "not-an-object"}}))
        sys.exit(0)
    if MODE == "scope_not_a_string_list":
        print(json.dumps({{"reply": "ok", "suggestion": {{"scope": "not-a-list", "outcome": None,
            "non_goals": None, "acceptance_criteria": None, "open_questions": None}}}}))
        sys.exit(0)
    if MODE == "nonzero_exit":
        print("boom", file=sys.stderr)
        sys.exit(1)
    if MODE == "hang":
        import time
        time.sleep(30)
        sys.exit(0)
    print(json.dumps({{"reply": "unhandled mode", "suggestion": None}}))
''')


def _write_fake_claude(tmp_dir: Path, *, auth_status: dict, mode: str) -> str:
    script_path = tmp_dir / "fake_claude.py"
    script_path.write_text(_FAKE_CLAUDE_TEMPLATE.format(auth_status=json.dumps(auth_status), mode=mode))
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return str(script_path)


class SubscriptionAuthTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def test_refuses_closed_when_authenticated_via_api_key(self):
        cli = _write_fake_claude(self._tmp, auth_status=_API_KEY_AUTH, mode="success_bare")
        with self.assertRaises(SubscriptionAuthRequired):
            converse([{"role": "user", "text": "hi"}], None, cli_executable=cli)

    def test_refuses_closed_when_logged_out(self):
        cli = _write_fake_claude(self._tmp, auth_status=_LOGGED_OUT_AUTH, mode="success_bare")
        with self.assertRaises(SubscriptionAuthRequired):
            converse([{"role": "user", "text": "hi"}], None, cli_executable=cli)

    def test_refuses_closed_when_cli_missing(self):
        with self.assertRaises(SubscriptionAuthRequired):
            converse([{"role": "user", "text": "hi"}], None, cli_executable=str(self._tmp / "does-not-exist"))


class HappyPathTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def test_success_bare_envelope(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_bare")
        result = converse([{"role": "user", "text": "ship the thing"}], None, cli_executable=cli)
        self.assertIsInstance(result, ConversationTurnResult)
        self.assertEqual("Got it -- shaping that into a draft.", result.reply)
        self.assertEqual(
            DraftFieldSuggestion(
                outcome="Ship the thing", scope=("do the thing",), non_goals=(),
                acceptance_criteria=("it works",), open_questions=(),
            ),
            result.suggestion,
        )

    def test_success_result_dict_envelope(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_result_dict")
        result = converse([], None, cli_executable=cli)
        self.assertEqual("Ship the thing", result.suggestion.outcome)

    def test_success_result_string_envelope(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_result_string")
        result = converse([], None, cli_executable=cli)
        self.assertEqual("Ship the thing", result.suggestion.outcome)

    def test_markdown_fenced_result_is_stripped_and_parsed(self):
        """Real, live-observed model behavior even with --system-prompt:
        despite explicit instructions not to, a reply is occasionally
        wrapped in a ```json ... ``` fence -- defense in depth, never the
        primary contract."""
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="success_markdown_fenced")
        result = converse([], None, cli_executable=cli)
        self.assertEqual("Ship the thing", result.suggestion.outcome)

    def test_no_suggestion_yet_is_a_valid_turn(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="no_suggestion_yet")
        result = converse([{"role": "user", "text": "hey"}], None, cli_executable=cli)
        self.assertEqual("Tell me more about the outcome you want.", result.reply)
        self.assertIsNone(result.suggestion)

    def test_trusted_citations_reach_the_subprocess_stdin_exactly(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="echo_trusted_citations")
        citations = ({"knowledgeId": "k1", "claim": "Zentra is a cost platform.", "label": "FACT", "tier": "canonical"},)
        result = converse([], None, trusted_citations=citations, cli_executable=cli)
        import json as _json
        self.assertEqual(list(citations), _json.loads(result.reply))

    def test_default_trusted_citations_is_an_empty_list_on_the_wire(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="echo_trusted_citations")
        result = converse([], None, cli_executable=cli)  # trusted_citations omitted
        import json as _json
        self.assertEqual([], _json.loads(result.reply))


class FailClosedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def test_malformed_json_raises(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="malformed_json")
        with self.assertRaises(JarvisConversationError):
            converse([], None, cli_executable=cli)

    def test_missing_reply_raises(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="missing_reply")
        with self.assertRaises(JarvisConversationError):
            converse([], None, cli_executable=cli)

    def test_suggestion_not_an_object_raises(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="suggestion_not_object")
        with self.assertRaises(JarvisConversationError):
            converse([], None, cli_executable=cli)

    def test_suggestion_field_wrong_type_raises(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="scope_not_a_string_list")
        with self.assertRaises(JarvisConversationError):
            converse([], None, cli_executable=cli)

    def test_nonzero_exit_raises(self):
        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="nonzero_exit")
        with self.assertRaises(JarvisConversationError):
            converse([], None, cli_executable=cli)


class NeverUsesApiKeyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def test_ambient_api_key_env_var_never_reaches_the_subprocess(self):
        """The fake CLI itself never checks its own env for this test --
        instead this proves the *auth status* call is what gates
        everything: even with a real-looking key sitting in the parent
        environment, subscription auth (not the key) determines the
        outcome, and the api-key auth-status response is still refused."""
        import os
        import unittest.mock as mock

        cli = _write_fake_claude(self._tmp, auth_status=_API_KEY_AUTH, mode="success_bare")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-should-never-be-used"}):
            with self.assertRaises(SubscriptionAuthRequired):
                converse([], None, cli_executable=cli)

    def test_ambient_api_key_env_var_is_actually_absent_in_the_dispatch_subprocess(self):
        """Unlike the test above (which only proves the auth-status gate
        refuses api-key auth), this proves the *positive* claim directly:
        on the happy (subscription-authenticated) path, a real-looking
        ANTHROPIC_API_KEY sitting in this process's own environment does
        not reach the dispatch subprocess's environment at all. The fake
        CLI's `report_env_marker` mode echoes back exactly what it saw."""
        import os
        import unittest.mock as mock

        cli = _write_fake_claude(self._tmp, auth_status=_SUBSCRIPTION_AUTH, mode="report_env_marker")
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-should-never-be-used"}):
            result = converse([], None, cli_executable=cli)
        self.assertEqual("__ABSENT__", result.reply)

    def test_other_non_subscription_auth_env_vars_are_also_stripped(self):
        """ANTHROPIC_API_KEY is not the only way to steer the CLI off of
        subscription auth -- ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, and
        the Bedrock/Vertex billing-routing flags must be stripped too."""
        import os
        import unittest.mock as mock
        from orchestrator.jarvis_conversation import _NON_SUBSCRIPTION_AUTH_ENV_VARS, _subscription_environment

        ambient = {var: "should-never-be-used" for var in _NON_SUBSCRIPTION_AUTH_ENV_VARS}
        with mock.patch.dict(os.environ, ambient):
            environment = _subscription_environment()
        for var in _NON_SUBSCRIPTION_AUTH_ENV_VARS:
            self.assertNotIn(var, environment)


if __name__ == "__main__":
    unittest.main()
