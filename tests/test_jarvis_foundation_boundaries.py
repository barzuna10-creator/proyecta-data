import ast
from pathlib import Path
import unittest

from jarvis.authorization import parse_authorization_command, render_authorization_command
from jarvis.drafts import build_draft_envelope
from tests.test_jarvis_drafts import valid_draft


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_PREFIXES = (
    "orchestrator.chugel",
    "orchestrator.autonomous_runner",
    "orchestrator.wiring",
    "orchestrator.provider_router",
    "orchestrator.adapters",
)


class JarvisFoundationBoundaryTests(unittest.TestCase):
    def test_production_modules_have_no_execution_infrastructure_imports(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
            for name in imported:
                self.assertFalse(name.startswith(FORBIDDEN_IMPORT_PREFIXES), (path, name))

    def test_no_subprocess_network_or_git_automation_symbols(self):
        forbidden_imports = {"subprocess", "socket", "urllib", "requests", "httpx"}
        for path in (ROOT / "jarvis").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
            self.assertTrue(forbidden_imports.isdisjoint(imports), (path, imports))

    def test_authorization_intent_has_no_human_attribution_or_execution(self):
        envelope = build_draft_envelope(valid_draft())
        intent = parse_authorization_command(render_authorization_command(envelope))
        fields = set(intent.__dataclass_fields__)
        self.assertTrue(fields.isdisjoint({
            "decided_by", "decided_at", "mission_id", "gate_name", "execute", "start_runner"
        }))

    def test_no_phase_zero_file_mentions_runtime_mission_directory_as_storage(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            self.assertNotIn("orchestrator/missions", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
