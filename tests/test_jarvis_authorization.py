import dataclasses
import unittest

from jarvis.authorization import (
    AuthorizationSyntaxError,
    parse_authorization_command,
    render_authorization_command,
    validate_authorization_intent,
)
from jarvis.drafts import build_draft_envelope
from jarvis.models import AuthorizationIntent
from tests.test_jarvis_drafts import valid_draft


class JarvisAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.envelope = build_draft_envelope(valid_draft())
        self.command = render_authorization_command(self.envelope)

    def codes(self, check):
        return {reason.code for reason in check.reasons}

    def test_exact_command_parses_and_validates(self):
        intent = parse_authorization_command(self.command)
        self.assertTrue(validate_authorization_intent(intent, current=self.envelope).allowed)
        self.assertFalse(hasattr(intent, "decided_by"))

    def test_one_terminal_line_ending_is_accepted(self):
        self.assertEqual(
            parse_authorization_command(self.command),
            parse_authorization_command(self.command + "\n"),
        )

    def test_loose_or_ambiguous_phrases_are_rejected(self):
        invalid = [
            "yes", "approve", "go ahead", self.command.lower(),
            " " + self.command, self.command + " ", self.command.replace(" ", "  ", 1),
            self.command + "\nextra", self.command.replace("REVISION 1", "REVISION 01"),
            self.command.replace("b2e7", "B2e7"),
        ]
        for value in invalid:
            with self.assertRaises(AuthorizationSyntaxError, msg=repr(value)):
                parse_authorization_command(value)

    def test_stale_revision_is_rejected(self):
        intent = parse_authorization_command(self.command)
        newer_draft = dataclasses.replace(
            self.envelope.draft, revision=2, updated_at="2026-08-25T20:00:01Z"
        )
        current = build_draft_envelope(newer_draft)
        check = validate_authorization_intent(intent, current=current)
        self.assertIn("REVISION_NOT_CURRENT", self.codes(check))

    def test_wrong_digest_is_rejected(self):
        intent = dataclasses.replace(parse_authorization_command(self.command), digest="b" * 64)
        self.assertIn("DIGEST_MISMATCH", self.codes(validate_authorization_intent(intent, current=self.envelope)))

    def test_corrupt_envelope_is_rejected(self):
        corrupt = dataclasses.replace(self.envelope, digest="b" * 64)
        intent = parse_authorization_command(self.command)
        self.assertIn("DRAFT_CORRUPT", self.codes(validate_authorization_intent(intent, current=corrupt)))

    def test_open_questions_prevent_authorization_readiness(self):
        draft = dataclasses.replace(valid_draft(), open_questions=("Which failure dominates?",))
        envelope = build_draft_envelope(draft)
        intent = parse_authorization_command(render_authorization_command(envelope))
        self.assertIn(
            "DRAFT_NOT_AUTHORIZATION_READY",
            self.codes(validate_authorization_intent(intent, current=envelope)),
        )


if __name__ == "__main__":
    unittest.main()
