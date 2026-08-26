from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class JarvisContractTests(unittest.TestCase):
    def test_complete_agent_package_exists(self):
        required = {
            "README.md", "CONTRACT.md", "IDENTITY.md", "PLAYBOOK.md",
            "PRINCIPLES.md", "LEARNINGS.md", "PROGRESS.md",
            "knowledge/README.md", "knowledge/SOURCES.md",
        }
        base = ROOT / "agents" / "jarvis"
        self.assertEqual([], sorted(str(path) for path in required if not (base / path).is_file()))

    def test_contract_has_every_agent_standard_section(self):
        contract = (ROOT / "agents/jarvis/CONTRACT.md").read_text(encoding="utf-8")
        standard = (ROOT / "agents/AGENT_STANDARD.md").read_text(encoding="utf-8")
        standard_headings = [
            line for line in standard.splitlines()
            if line.startswith("### ") and line[4:6].rstrip(".").isdigit()
        ]
        for heading in standard_headings:
            self.assertIn("## " + heading[4:], contract)

    def test_contract_states_non_authority_and_preserves_roles(self):
        contract = (ROOT / "agents/jarvis/CONTRACT.md").read_text(encoding="utf-8")
        for phrase in (
            "Chugel remains the sole authoritative",
            "not a Chugel decision",
            "act as Emilio",
            "review in Emma's place",
            "must preserve Emma's fresh, separate context",
            "invoke a provider",
        ):
            self.assertIn(phrase, contract)


if __name__ == "__main__":
    unittest.main()
