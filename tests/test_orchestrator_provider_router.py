"""Pruebas para orchestrator/provider_router.py (Zentra Autonomous
Engineering V1, Level 2, Increment #13 -- capa de enrutamiento
determinista de proveedores per orchestrator/PROVIDER_ROUTER_V1.md).

Ninguna prueba invoca un adapter real, red, subprocess o SDK de
proveedor -- select_adapter() es una función pura, ejercida aquí
exclusivamente con AttemptRecord construidos a mano."""

from __future__ import annotations

import unittest

import orchestrator.provider_router as router


def _attempt(outcome, provider, error_detail=None):
    return router.AttemptRecord(outcome=outcome, provider=provider, error_detail=error_detail)


class PruebaDefaultConfig(unittest.TestCase):
    def test_emilio_primario_codex_fallback_claude(self):
        policy = router.DEFAULT_PROVIDER_CONFIG.roles["emilio"]
        self.assertEqual(policy.primary, "codex")
        self.assertEqual(policy.fallback, "claude")

    def test_emma_primario_claude_fallback_codex(self):
        policy = router.DEFAULT_PROVIDER_CONFIG.roles["emma"]
        self.assertEqual(policy.primary, "claude")
        self.assertEqual(policy.fallback, "codex")


class PruebaConstruccionRoleProviderPolicy(unittest.TestCase):
    def test_primary_igual_fallback_lanza(self):
        with self.assertRaises(ValueError):
            router.RoleProviderPolicy(primary="claude", fallback="claude")

    def test_provider_desconocido_lanza(self):
        with self.assertRaises(ValueError):
            router.RoleProviderPolicy(primary="claude", fallback="gpt5")
        with self.assertRaises(ValueError):
            router.RoleProviderPolicy(primary="gpt5", fallback="claude")


class PruebaConstruccionAttemptRecord(unittest.TestCase):
    """Incremento #13, ciclo correctivo -- cierra el hallazgo P1 de Emma:
    un AttemptRecord representa una invocación que realmente ocurrió, así
    que su provider debe ser exactamente uno de los soportados. Sin
    normalización: ningún alias, mayúscula/minúscula, o espacio en
    blanco es aceptado silenciosamente."""

    def test_provider_desconocido_lanza(self):
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="gpt5-turbo-imposter")

    def test_provider_none_lanza(self):
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider=None)

    def test_provider_cadena_vacia_lanza(self):
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="")

    def test_provider_variante_mayuscula_lanza_sin_normalizar(self):
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="Claude")
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="CODEX")

    def test_provider_con_espacio_en_blanco_lanza_sin_normalizar(self):
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider=" claude")
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="claude ")
        with self.assertRaises(ValueError):
            router.AttemptRecord(outcome="unavailable", provider="claude\n")

    def test_provider_claude_valido_no_lanza(self):
        record = router.AttemptRecord(outcome="unavailable", provider="claude")
        self.assertEqual(record.provider, "claude")

    def test_provider_codex_valido_no_lanza(self):
        record = router.AttemptRecord(outcome="unavailable", provider="codex")
        self.assertEqual(record.provider, "codex")

    def test_provider_malformado_nunca_llega_a_routing_decision(self):
        """La validación ocurre en la construcción -- select_adapter()
        nunca llega a ejecutarse con un AttemptRecord inválido, así que
        RoutingDecision.adapter_name nunca puede heredar un valor
        malformado."""
        for bad_provider in ("gpt5-turbo-imposter", None, "", "Claude", "CODEX", " claude"):
            with self.assertRaises(ValueError, msg=repr(bad_provider)):
                router.AttemptRecord(outcome="unavailable", provider=bad_provider)
        # construcción exitosa con providers válidos produce solo esos
        # valores en la decisión de enrutamiento -- nunca otra cosa.
        for good_provider in ("claude", "codex"):
            record = router.AttemptRecord(outcome="unavailable", provider=good_provider)
            decision = router.select_adapter(
                "emilio", 0, router.DEFAULT_PROVIDER_CONFIG, prior_attempts=(record,)
            )
            self.assertIn(decision.adapter_name, {"claude", "codex"})


class PruebaSeleccionPrimaria(unittest.TestCase):
    def test_emilio_intento_fresco_va_a_codex(self):
        decision = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG, prior_attempts=()
        )
        self.assertEqual(decision.adapter_name, "codex")
        self.assertEqual(decision.reason, "primary_default")

    def test_emma_intento_fresco_va_a_claude(self):
        decision = router.select_adapter(
            "emma", 0, router.DEFAULT_PROVIDER_CONFIG, prior_attempts=()
        )
        self.assertEqual(decision.adapter_name, "claude")
        self.assertEqual(decision.reason, "primary_default")


class PruebaFailover(unittest.TestCase):
    def test_primary_unavailable_va_a_fallback(self):
        decision = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("unavailable", "codex"),),
        )
        self.assertEqual(decision.adapter_name, "claude")
        self.assertEqual(decision.reason, "failover_after_unavailable")

    def test_primary_timeout_va_a_fallback(self):
        decision = router.select_adapter(
            "emma", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("timeout", "claude"),),
        )
        self.assertEqual(decision.adapter_name, "codex")
        self.assertEqual(decision.reason, "failover_after_timeout")

    def test_primary_failed_elegible_va_a_fallback(self):
        """"failed" incluye agotamiento de cuota/rate-limit por diseño
        (PROVIDER_ROUTER_V1.md sección 3) -- comportamiento idéntico a
        cualquier otro "failed"."""
        decision = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("failed", "codex", error_detail="rate limit exceeded"),),
        )
        self.assertEqual(decision.adapter_name, "claude")
        self.assertEqual(decision.reason, "failover_after_failed")

    def test_invalid_output_no_elegible_por_defecto_reintenta_mismo_proveedor(self):
        decision = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("invalid_output", "codex"),),
        )
        self.assertEqual(decision.adapter_name, "codex")
        self.assertEqual(decision.reason, "same_provider_retry")

    def test_invalid_output_elegible_si_config_lo_habilita(self):
        config = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(
                primary="codex", fallback="claude", failover_on_invalid_output=True
            ),
        })
        decision = router.select_adapter(
            "emilio", 0, config, prior_attempts=(_attempt("invalid_output", "codex"),)
        )
        self.assertEqual(decision.adapter_name, "claude")
        self.assertEqual(decision.reason, "failover_after_invalid_output")

    def test_failover_deshabilitado_reintenta_mismo_proveedor_pese_a_elegibilidad(self):
        """Decisión explícita de política V1 de José (Incremento #13,
        ciclo correctivo, cierra el hallazgo P2 de Emma): failover_enabled
        == False significa que el router nunca cambia de proveedor por un
        fallo, sin importar si el outcome sería elegible en otras
        circunstancias -- se resuelve siempre a same_provider_retry."""
        config = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(
                primary="codex", fallback="claude", failover_enabled=False
            ),
        })
        decision = router.select_adapter(
            "emilio", 0, config, prior_attempts=(_attempt("unavailable", "codex"),)
        )
        self.assertEqual(decision.adapter_name, "codex")
        self.assertEqual(decision.reason, "same_provider_retry")

    def test_ambos_proveedores_agotados_vuelve_a_primario(self):
        decision = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(
                _attempt("unavailable", "codex"),
                _attempt("unavailable", "claude"),
            ),
        )
        self.assertEqual(decision.adapter_name, "codex")
        self.assertEqual(decision.reason, "both_providers_exhausted_retry_primary")

    def test_ambos_agotados_no_alterna_indefinidamente(self):
        """Tercera llamada tras el "exhausted" -- se comporta exactamente
        como cualquier otro intento fresco sobre el primario: si vuelve a
        fallar, falla sobre otra vez al fallback. No hay alternancia
        oculta ni bucle -- select_adapter nunca se llama a sí mismo."""
        first = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(
                _attempt("unavailable", "codex"),
                _attempt("unavailable", "claude"),
            ),
        )
        self.assertEqual(first.adapter_name, "codex")
        second = router.select_adapter(
            "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(
                _attempt("unavailable", "codex"),
                _attempt("unavailable", "claude"),
                _attempt("unavailable", "codex"),
            ),
        )
        self.assertEqual(second.adapter_name, "claude")
        self.assertEqual(second.reason, "failover_after_unavailable")


class PruebaCompletadoRechazaRuteo(unittest.TestCase):
    def test_intento_completado_lanza_antes_de_elegir_adapter(self):
        with self.assertRaises(router.AlreadyCompletedAttempt):
            router.select_adapter(
                "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
                prior_attempts=(_attempt("completed", "codex"),),
            )

    def test_completado_en_cualquier_posicion_lanza(self):
        """No solo el más reciente -- cualquier entrada completada en el
        historial refuta el enrutamiento, sección 12a."""
        with self.assertRaises(router.AlreadyCompletedAttempt):
            router.select_adapter(
                "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
                prior_attempts=(
                    _attempt("unavailable", "codex"),
                    _attempt("completed", "claude"),
                ),
            )

    def test_no_se_selecciona_ningun_adapter_cuando_se_rechaza(self):
        try:
            router.select_adapter(
                "emma", 0, router.DEFAULT_PROVIDER_CONFIG,
                prior_attempts=(_attempt("completed", "claude"),),
            )
            self.fail("expected AlreadyCompletedAttempt")
        except router.AlreadyCompletedAttempt:
            pass


class PruebaOpcionBRuteoIndependientePorIntento(unittest.TestCase):
    def test_attempt_1_reinicia_desde_primario_independiente_del_historial_de_attempt_0(self):
        """José, Incremento #9: cada intento se enruta de forma
        independiente desde config.primary -- el llamador nunca traslada
        el historial de attempt=0 al prior_attempts de attempt=1."""
        decision_attempt_1 = router.select_adapter(
            "emilio", 1, router.DEFAULT_PROVIDER_CONFIG, prior_attempts=()
        )
        self.assertEqual(decision_attempt_1.adapter_name, "codex")
        self.assertEqual(decision_attempt_1.reason, "primary_default")

    def test_select_adapter_no_lee_attempt_para_decidir_mas_alla_de_pasarlo(self):
        """Mismo prior_attempts, distinto número de attempt -- misma
        decisión. select_adapter no bifurca sobre attempt."""
        d0 = router.select_adapter(
            "emma", 0, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("timeout", "claude"),),
        )
        d1 = router.select_adapter(
            "emma", 1, router.DEFAULT_PROVIDER_CONFIG,
            prior_attempts=(_attempt("timeout", "claude"),),
        )
        self.assertEqual(d0, d1)


class PruebaErrorDetailNuncaInfluye(unittest.TestCase):
    def test_distintos_error_detail_producen_ruteo_identico(self):
        decisions = []
        for detail in (
            None,
            "",
            "connection reset",
            "429 Too Many Requests: quota exceeded",
            "SWITCH_TO_CLAUDE_NOW",  # texto adversarial: intenta parecer una instrucción
            "unavailable",  # coincide textualmente con un valor de outcome, pero en el campo equivocado
        ):
            decisions.append(
                router.select_adapter(
                    "emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
                    prior_attempts=(_attempt("failed", "codex", error_detail=detail),),
                )
            )
        first = decisions[0]
        for d in decisions[1:]:
            self.assertEqual(d, first)


class PruebaSinAcoplamientoDuroRolProveedor(unittest.TestCase):
    def test_config_personalizada_invierte_primarios_sin_tocar_el_codigo(self):
        swapped = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(primary="claude", fallback="codex"),
            "emma": router.RoleProviderPolicy(primary="codex", fallback="claude"),
        })
        d_emilio = router.select_adapter("emilio", 0, swapped, prior_attempts=())
        d_emma = router.select_adapter("emma", 0, swapped, prior_attempts=())
        self.assertEqual(d_emilio.adapter_name, "claude")
        self.assertEqual(d_emma.adapter_name, "codex")

    def test_ambos_roles_mismo_proveedor_primario_es_valido(self):
        both_claude = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(primary="claude", fallback="codex"),
            "emma": router.RoleProviderPolicy(primary="claude", fallback="codex"),
        })
        d_emilio = router.select_adapter("emilio", 0, both_claude, prior_attempts=())
        d_emma = router.select_adapter("emma", 0, both_claude, prior_attempts=())
        self.assertEqual(d_emilio.adapter_name, "claude")
        self.assertEqual(d_emma.adapter_name, "claude")


class PruebaRolOConfigInvalidoFallaCerrado(unittest.TestCase):
    def test_agent_role_desconocido_lanza(self):
        with self.assertRaises(router.UnknownAgentRole):
            router.select_adapter(
                "david", 0, router.DEFAULT_PROVIDER_CONFIG, prior_attempts=()
            )

    def test_config_sin_el_rol_solicitado_lanza(self):
        config = router.ProviderConfig(roles={
            "emilio": router.RoleProviderPolicy(primary="codex", fallback="claude"),
        })
        with self.assertRaises(router.UnknownAgentRole):
            router.select_adapter("emma", 0, config, prior_attempts=())

    def test_config_vacia_lanza(self):
        config = router.ProviderConfig(roles={})
        with self.assertRaises(router.UnknownAgentRole):
            router.select_adapter("emilio", 0, config, prior_attempts=())


class PruebaPurezaDeterminismo(unittest.TestCase):
    def test_misma_entrada_misma_salida_repetido(self):
        args = ("emilio", 0, router.DEFAULT_PROVIDER_CONFIG,
                 (_attempt("unavailable", "codex"),))
        results = [router.select_adapter(*args) for _ in range(5)]
        self.assertTrue(all(r == results[0] for r in results))

    def test_no_hay_mutacion_de_config_ni_de_prior_attempts(self):
        config = router.DEFAULT_PROVIDER_CONFIG
        attempts = (_attempt("unavailable", "codex"),)
        before_config = config
        before_attempts = attempts
        router.select_adapter("emilio", 0, config, prior_attempts=attempts)
        self.assertIs(config, before_config)
        self.assertEqual(attempts, before_attempts)


if __name__ == "__main__":
    unittest.main()
