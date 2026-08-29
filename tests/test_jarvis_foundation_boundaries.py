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
# Mission 004: two more narrow, disclosed Chugel seams, distinct from
# mission_query.py's read-only one. Each is checked as a SUBSET (a module
# need not use every call it's allowed), never as the exact-equality rule
# mission_query.py alone still gets (that module's whole contract is "read
# exactly these two calls, nothing else").
WRITE_CHUGEL_MODULE = "mission_write.py"
ALLOWED_WRITE_CHUGEL_CALLS = {"get_mission", "create_mission", "decide_gate", "transition"}
COORDINATOR_CHUGEL_MODULE = "mission_coordinator.py"
ALLOWED_COORDINATOR_CHUGEL_CALLS = {"get_mission"}
KNOWLEDGE_MODULES = {
    "jarvis.knowledge", "jarvis.knowledge_storage", "jarvis.knowledge_authorization",
    "jarvis.learning_projection", "jarvis.knowledge_retrieval", "jarvis.repository_freshness",
    "jarvis.zentra_evidence",
}
SOLE_SUBPROCESS_MODULE = "repository_freshness.py"
SOLE_KNOWLEDGE_SEARCH_MODULES = {"mission_context.py", "knowledge_retrieval.py", "cli.py"}
# Mission 005: the module that reads jarvis/zentra_sources_policy.json
# (the SHA/ref/repo/allow-list/tier manifest) and calls
# RepositoryFreshnessResolver.read_blob(). Never jarvis.control_plane_server,
# never orchestrator.jarvis_conversation -- there is structurally no path
# from a live conversation turn to this policy or to read_blob(). cli.py
# is included because it is the only human-operator entry point that
# invokes zentra_evidence's ingestion path (see the "knowledge propose-source"
# subcommand).
SOLE_ZENTRA_POLICY_READERS = {"zentra_evidence.py", "cli.py"}
SOLE_COORDINATOR_IMPORTER = "mission_coordinator.py"
COORDINATOR_ONLY_IMPORTS = {"orchestrator.autonomous_runner", "orchestrator.publish_executor",
                            "orchestrator.merge_executor", "orchestrator.publish_identity_repair"}


def _chugel_boundary_violations(filename: str, source: str) -> tuple[str, ...]:
    """Statically reject Chugel access outside the three disclosed seams:
    mission_query.py (read-only, exact two calls), mission_write.py
    (create_mission/decide_gate/transition/get_mission), and
    mission_coordinator.py (get_mission only)."""
    tree = ast.parse(source, filename=filename)
    if filename == "mission_query.py":
        allowed_module, allowed_calls, exact = True, ALLOWED_CHUGEL_CALLS, True
    elif filename == WRITE_CHUGEL_MODULE:
        allowed_module, allowed_calls, exact = True, ALLOWED_WRITE_CHUGEL_CALLS, False
    elif filename == COORDINATOR_CHUGEL_MODULE:
        allowed_module, allowed_calls, exact = True, ALLOWED_COORDINATOR_CHUGEL_CALLS, False
    else:
        allowed_module, allowed_calls, exact = False, ALLOWED_CHUGEL_CALLS, False

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
                    if alias.name not in allowed_calls:
                        violations.append("unauthorized-chugel-symbol")

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.value.id in module_aliases:
                calls.add(node.func.attr)
                if node.func.attr not in allowed_calls:
                    violations.append("unauthorized-chugel-call")
        elif isinstance(node.func, ast.Name) and node.func.id in direct_aliases:
            symbol = direct_aliases[node.func.id]
            calls.add(symbol)
            if symbol not in allowed_calls:
                violations.append("unauthorized-chugel-call")

    if allowed_module and exact and calls != allowed_calls:
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
            if path.name == SOLE_SUBPROCESS_MODULE:
                continue  # the one deliberate, narrowly scoped exception -- see the dedicated tests below
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
            self.assertTrue(forbidden_imports.isdisjoint(imports), (path, imports))

    def _subprocess_ast_signals(self, source: str, filename: str) -> tuple[str, ...]:
        """Every reasonably-detectable way a module could gain subprocess
        capability: direct import, aliased import, from-import, dynamic
        importlib.import_module, and literal __import__."""
        tree = ast.parse(source, filename=filename)
        signals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                        signals.append("import")
            elif isinstance(node, ast.ImportFrom):
                if node.module == "subprocess":
                    signals.append("from-import")
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "__import__":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "subprocess":
                        signals.append("dunder-import")
                elif isinstance(func, ast.Attribute) and func.attr == "import_module":
                    if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "subprocess":
                        signals.append("importlib")
        return tuple(signals)

    def test_subprocess_capability_exists_in_exactly_one_jarvis_module(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            signals = self._subprocess_ast_signals(path.read_text(encoding="utf-8"), path.name)
            if path.name == SOLE_SUBPROCESS_MODULE:
                self.assertTrue(signals, "the sole authorized module must actually use subprocess")
            else:
                self.assertEqual(signals, (), (path, signals))

    def test_subprocess_boundary_rejects_adversarial_forms_outside_the_sole_module(self):
        cases = {
            "aliased import": "import subprocess as sp\nsp.run(['x'])",
            "from-import": "from subprocess import run\nrun(['x'])",
            "dynamic importlib": "import importlib\nimportlib.import_module('subprocess').run(['x'])",
            "dunder import": "m = __import__('subprocess')\nm.run(['x'])",
        }
        for label, source in cases.items():
            with self.subTest(label=label):
                self.assertTrue(self._subprocess_ast_signals(source, "knowledge_retrieval.py"))

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
        for name in (
            "knowledge.py", "knowledge_storage.py", "knowledge_authorization.py",
            "learning_projection.py", "knowledge_retrieval.py", "repository_freshness.py",
            "zentra_evidence.py",
        ):
            source = (ROOT / "jarvis" / name).read_text(encoding="utf-8")
            tree = ast.parse(source, filename=name)
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import): imports.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom): imports.append(node.module or "")
            module_forbidden = tuple(value for value in forbidden if not (name == SOLE_SUBPROCESS_MODULE and value == "subprocess"))
            self.assertFalse(any(value.startswith(module_forbidden) for value in imports), (name, imports))
            self.assertNotRegex(source.lower(), r"\b(prompt|llm|provider|execute_mission|submit_mission)\b")

    def test_no_phase_zero_file_mentions_runtime_mission_directory_as_storage(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            self.assertNotIn("orchestrator/missions", path.read_text(encoding="utf-8"))

    # --- Mission 004 additions ---------------------------------------

    def _imports(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
        return names

    def test_only_mission_coordinator_imports_the_autonomous_pipeline(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            names = self._imports(path)
            hits = COORDINATOR_ONLY_IMPORTS.intersection(names)
            if path.name == SOLE_COORDINATOR_IMPORTER:
                continue
            self.assertFalse(hits, (path, hits))

    def test_only_narrow_seam_modules_search_trusted_knowledge(self):
        for path in (ROOT / "jarvis").glob("*.py"):
            if path.name in SOLE_KNOWLEDGE_SEARCH_MODULES:
                continue
            names = self._imports(path)
            self.assertNotIn("jarvis.knowledge_retrieval", names, path)
            self.assertFalse(
                any(n == "jarvis" for n in names) and "knowledge_retrieval" in path.read_text(encoding="utf-8"),
                path,
            )

    def test_mission_proposal_cannot_import_any_knowledge_module(self):
        path = ROOT / "jarvis" / "mission_proposal.py"
        names = set(self._imports(path))
        forbidden = {"jarvis.knowledge_retrieval", "jarvis.knowledge_storage", "jarvis.repository_freshness"}
        self.assertTrue(forbidden.isdisjoint(names), names)

    # --- Mission 005 additions ---------------------------------------

    def test_only_narrow_seam_modules_read_the_zentra_sources_policy(self):
        """Structural enforcement of 'impossible to expand from
        conversation': jarvis.zentra_evidence (the only module that loads
        zentra_sources_policy.json and calls read_blob()) may only be
        imported by itself and by the human-operator CLI -- never by
        jarvis.control_plane_server or anything reachable from a live
        conversation turn."""
        for path in (ROOT / "jarvis").glob("*.py"):
            if path.name in SOLE_ZENTRA_POLICY_READERS:
                continue
            names = self._imports(path)
            self.assertNotIn("jarvis.zentra_evidence", names, path)
            self.assertFalse(
                any(n == "jarvis" for n in names) and "zentra_evidence" in path.read_text(encoding="utf-8"),
                path,
            )

    def test_orchestrator_module_never_imports_zentra_evidence_either(self):
        """Round-2 independent review, P2: the narrow-seam check above
        only scanned jarvis/*.py. This closes the same gap explicitly for
        orchestrator/*.py -- in particular orchestrator/jarvis_conversation.py,
        the actual live conversational dispatch path -- rather than
        relying only on jarvis.zentra_evidence's inclusion in
        KNOWLEDGE_MODULES (checked generically by
        test_orchestrator_never_imports_jarvis_knowledge above) to make
        this explicit and independently re-derivable."""
        for path in (ROOT / "orchestrator").glob("*.py"):
            names = self._imports(path)
            self.assertNotIn("jarvis.zentra_evidence", names, path)

    def test_read_blob_is_called_only_from_the_sole_zentra_policy_reader(self):
        """Round-2 independent review, P2: jarvis.control_plane_server
        legitimately holds a live RepositoryFreshnessResolver instance
        (server.zentra_resolver, for jarvis.mission_context.draft_briefing()'s
        freshness re-checks) -- nothing about that import alone prevents
        conversation-reachable code from also calling its read_blob()
        method directly. This asserts the literal call-site text appears
        nowhere except read_blob()'s own definition (repository_freshness.py)
        and its sole caller (zentra_evidence.py)."""
        allowed = {"repository_freshness.py", "zentra_evidence.py"}
        for path in list((ROOT / "jarvis").glob("*.py")) + list((ROOT / "orchestrator").glob("*.py")):
            if path.name in allowed:
                continue
            self.assertNotIn(".read_blob(", path.read_text(encoding="utf-8"), path)

    def test_control_plane_server_never_imports_zentra_evidence(self):
        # The single most important assertion for "conversación no puede
        # ampliar la allow-list": the live HTTP/conversational surface
        # has zero import path to the policy loader or read_blob(), not
        # merely "doesn't currently call it".
        path = ROOT / "jarvis" / "control_plane_server.py"
        names = set(self._imports(path))
        self.assertNotIn("jarvis.zentra_evidence", names)

    def test_build_mission_definition_signature_has_no_free_text_slot(self):
        import inspect

        from jarvis.mission_proposal import JoseDecisions, build_mission_definition

        sig = inspect.signature(build_mission_definition)
        params = list(sig.parameters.values())
        self.assertEqual([p.name for p in params], ["objective_text", "decisions"])
        self.assertEqual(params[1].annotation, "JoseDecisions")

        field_types = {f: t for f, t in JoseDecisions.__annotations__.items()}
        self.assertEqual(field_types, {
            "outcome": "str",
            "scope": "tuple[str, ...]",
            "non_goals": "tuple[str, ...]",
            "acceptance_criteria": "tuple[str, ...]",
        })


if __name__ == "__main__":
    unittest.main()
