"""Pruebas para orchestrator/cli_provider_adapters.py -- el punto de
construcción de los adaptadores CLI zero-cost, y prueba de que Emilio
enruta al CLI de Codex y Emma al CLI de Claude a través del
provider_router existente, sin ningún cambio a chugel/wiring/
agent_invocation/autonomous_runner/provider_router."""

from __future__ import annotations

import unittest

import orchestrator.provider_router as router
from orchestrator.adapters.claude_cli_adapter import ClaudeCliAdapter
from orchestrator.adapters.codex_cli_adapter import CodexCliAdapter
from orchestrator.cli_provider_adapters import build_cli_subscription_adapters


class PruebaConstruccion(unittest.TestCase):
    def test_construye_exactamente_codex_y_claude(self):
        adapters = build_cli_subscription_adapters(
            codex_cli_path=__file__, claude_cli_path=__file__,
        )
        self.assertEqual(set(adapters.keys()), {"codex", "claude"})
        self.assertIsInstance(adapters["codex"], CodexCliAdapter)
        self.assertIsInstance(adapters["claude"], ClaudeCliAdapter)

    def test_ningun_adaptador_api_key_se_usa(self):
        """Los dos adaptadores construidos no son -- ni heredan de, ni
        envuelven -- los adaptadores basados en API key. No hay
        credencial API en absoluto en ninguno de los dos objetos."""
        adapters = build_cli_subscription_adapters(
            codex_cli_path=__file__, claude_cli_path=__file__,
        )
        for name, adapter in adapters.items():
            self.assertFalse(hasattr(adapter, "_api_key"), name)
            self.assertFalse(hasattr(adapter, "api_key"), name)


class PruebaEnrutamientoSinCambios(unittest.TestCase):
    """El router (orchestrator/provider_router.py) ya enruta emilio->codex
    y emma->claude por defecto -- sin ningún cambio para este ciclo. Esta
    prueba confirma que sigue siendo así, y que la construcción de los
    adaptadores CLI usa exactamente esos mismos dos nombres de provider,
    de modo que el adaptador CLI correcto queda detrás de cada rol sin
    tocar wiring.py/agent_invocation.py/autonomous_runner.py."""

    def test_router_sigue_enrutando_emilio_a_codex_y_emma_a_claude(self):
        policy_emilio = router.DEFAULT_PROVIDER_CONFIG.roles["emilio"]
        policy_emma = router.DEFAULT_PROVIDER_CONFIG.roles["emma"]
        self.assertEqual(policy_emilio.primary, "codex")
        self.assertEqual(policy_emma.primary, "claude")

    def test_claves_del_adapters_dict_coinciden_con_los_nombres_del_router(self):
        adapters = build_cli_subscription_adapters(
            codex_cli_path=__file__, claude_cli_path=__file__,
        )
        policy_emilio = router.DEFAULT_PROVIDER_CONFIG.roles["emilio"]
        policy_emma = router.DEFAULT_PROVIDER_CONFIG.roles["emma"]
        self.assertIn(policy_emilio.primary, adapters)
        self.assertIn(policy_emma.primary, adapters)


if __name__ == "__main__":
    unittest.main()
