import ast
from pathlib import Path
import unittest

from jarvis.authorization import parse_authorization_command, render_authorization_command
from jarvis.drafts import build_draft_envelope
from tests.test_jarvis_drafts import valid_draft


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_IMPORT_PREFIXES = (
    "orchestrator.autonomous_runner",
    "orchestrator.wiring",
    "orchestrator.provider_router",
    "orchestrator.adapters",
)
ALLOWED_CHUGEL_CALLS = {"list_missions", "get_mission"}
KNOWLEDGE_MODULES = {"jarvis.knowledge", "jarvis.knowledge_storage", "jarvis.knowledge_authorization", "jarvis.learning_projection"}


def _chugel_boundary_violations(filename: str, source: str) -> tuple[str, ...]:
    """Statically reject Chugel access outside the single read-only seam."""
    tree = ast.parse(source, filename=filename)
    allowed_module = filename == "mission_query.py"
    module_aliases: set[str] = set()
    direct_aliases: dict[str, str] = {}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "orchestrator":
                    violations.append("bare-orchestrator-import")
                elif alias.name == "orchestrator.chugel":
                    module_aliases.add(alias.asname or "orchestrator.chugel")
                    if not allowed_module:
                        violations.append("chugel-import-outside-boundary")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "orchestrator":
                for alias in node.names:
                    if alias.name == "chugel":
                        module_aliases.add(alias.asname or alias.name)
                        if not allowed_module:
                            violations.append("chugel-import-outside-boundary")
            elif node.module == "orchestrator.chugel":
                for alias in node.names:
                    direct_aliases[alias.asname or alias.name] = alias.name
                    if not allowed_module:
                        violations.append("chugel-import-outside-boundary")
                    if alias.name not in ALLOWED_CHUGEL_CALLS:
                        violations.append("unauthorized-chugel-symbol")

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in module_aliases:
                calls.add(node.func.attr)
                if node.func.attr not in ALLOWED_CHUGEL_CALLS:
                    violations.append("unauthorized-chugel-call")
        elif isinstance(node.func, ast.Name) and node.func.id in direct_aliases:
            symbol = direct_aliases[node.func.id]
            calls.add(symbol)
            if symbol not in ALLOWED_CHUGEL_CALLS:
                violations.append("unauthorized-chugel-call")

    if allowed_module and calls != ALLOWED_CHUGEL_CALLS:
        violations.append("required-read-calls-not-exact")
    return tuple(sorted(set(violations)))


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

    def test_only_mission_query_imports_chugel_and_only_reads(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            self.assertEqual(
                _chugel_boundary_violations(path.name, path.read_text(encoding="utf-8")),
                (),
                path,
            )

    def test_chugel_boundary_rejects_adversarial_import_and_call_forms(self):
        cases = {
            "direct mutation import": ("other.py", "from orchestrator.chugel import transition\ntransition('x')"),
            "direct read outside seam": ("other.py", "from orchestrator.chugel import get_mission\nget_mission('x')"),
            "bare orchestrator": ("other.py", "import orchestrator\norchestrator.chugel.get_mission('x')"),
            "module mutation": ("mission_query.py", "from orchestrator import chugel\nchugel.list_missions()\nchugel.get_mission('x')\nchugel.transition('x')"),
            "aliased mutation": ("mission_query.py", "import orchestrator.chugel as c\nc.list_missions()\nc.get_mission('x')\nc.decide_gate('x')"),
        }
        for label, (filename, source) in cases.items():
            with self.subTest(label=label):
                self.assertTrue(_chugel_boundary_violations(filename, source))

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

    def test_orchestrator_never_imports_jarvis_knowledge(self):
        for path in (ROOT / "orchestrator").glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): names.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom): names.append(node.module or "")
            self.assertTrue(KNOWLEDGE_MODULES.isdisjoint(names), (path, names))

    def test_knowledge_modules_have_no_chugel_reasoning_or_provider_path(self):
        forbidden = ("orchestrator", "subprocess", "socket", "requests", "httpx", "openai", "anthropic")
        for name in ("knowledge.py", "knowledge_storage.py", "knowledge_authorization.py", "learning_projection.py"):
            source = (ROOT / "jarvis" / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name)
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom): imports.append(node.module or "")
            self.assertFalse(any(value.startswith(forbidden) for value in imports), (name, imports))
            self.assertNotRegex(source.lower(), r"\b(prompt|llm|provider|execute_mission|submit_mission)\b")

    def test_no_phase_zero_file_mentions_runtime_mission_directory_as_storage(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            self.assertNotIn("orchestrator/missions", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
