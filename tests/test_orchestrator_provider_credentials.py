import json
import os
import pickle
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from orchestrator.provider_credentials import (
    ProviderCredentialError,
    ProviderCredentials,
    build_provider_adapters,
    load_provider_credentials,
    require_minimized_worker_environment,
    trusted_system_temp_root,
    trusted_worker_environment,
    validate_invocation_temp_directory,
    validate_dedicated_key,
)


class _CountingEnvironment(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pop_counts = {}

    def pop(self, name, *args):
        self.pop_counts[name] = self.pop_counts.get(name, 0) + 1
        return super().pop(name, *args)


class ProviderCredentialBootstrapTests(unittest.TestCase):
    CODEX = "synthetic-zentra-codex-key"
    CLAUDE = "synthetic-zentra-claude-key"

    def _environment(self, **extra):
        return _CountingEnvironment(
            {
                "ZENTRA_CODEX_API_KEY": self.CODEX,
                "ZENTRA_CLAUDE_API_KEY": self.CLAUDE,
                "PATH": "/synthetic/bin",
                "LANG": "C.UTF-8",
                "UNRELATED_PARENT_STATE": "must-not-survive",
                **extra,
            }
        )

    def test_consumo_unico_remueve_bootstrap_y_minimiza_worker(self):
        environment = self._environment()
        credentials = load_provider_credentials(environment)
        self.assertEqual(credentials.codex_api_key, self.CODEX)
        self.assertEqual(credentials.claude_api_key, self.CLAUDE)
        self.assertEqual(
            environment.pop_counts,
            {"ZENTRA_CODEX_API_KEY": 1, "ZENTRA_CLAUDE_API_KEY": 1},
        )
        self.assertEqual(environment, trusted_worker_environment())

    def test_valores_invalidos_fallan_sin_exponerlos(self):
        for value in (None, "", " ", True, 1, [], "bad\nkey", " padded"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ProviderCredentialError) as rejected:
                    validate_dedicated_key(value, provider="synthetic")
                self.assertNotIn(repr(value), str(rejected.exception))

    def test_ambiente_credential_like_inesperado_falla_sin_leer_valor(self):
        secret = "synthetic-never-report"
        environment = self._environment(GITHUB_TOKEN=secret)
        with self.assertRaises(ProviderCredentialError) as rejected:
            load_provider_credentials(environment)
        self.assertIn("GITHUB_TOKEN", str(rejected.exception))
        self.assertNotIn(secret, str(rejected.exception))
        self.assertEqual(environment.pop_counts, {})

    def test_credencial_faltante_falla_y_nunca_se_serializa(self):
        environment = self._environment()
        del environment["ZENTRA_CLAUDE_API_KEY"]
        with self.assertRaises(ProviderCredentialError):
            load_provider_credentials(environment)

        credentials = ProviderCredentials(
            codex_api_key=self.CODEX, claude_api_key=self.CLAUDE
        )
        rendered = repr(credentials)
        self.assertEqual(rendered, "ProviderCredentials(<redacted>)")
        self.assertNotIn(self.CODEX, rendered)
        self.assertNotIn(self.CLAUDE, rendered)
        with self.assertRaises((TypeError, pickle.PicklingError)):
            pickle.dumps(credentials)
        with self.assertRaises(TypeError):
            json.dumps(credentials)

    def test_composicion_entrega_claves_explicitas_despues_de_remover_env(self):
        environment = self._environment()
        credentials = load_provider_credentials(environment)
        worker = mock.Mock(name="ProviderWorkerInvoker")
        fake_modules = {
            "orchestrator.provider_worker": types.SimpleNamespace(
                ProviderWorkerInvoker=worker
            ),
        }
        with mock.patch.dict(sys.modules, fake_modules):
            adapters = build_provider_adapters(credentials)
        self.assertEqual(
            worker.call_args_list,
            [
                mock.call(provider="codex", api_key=self.CODEX),
                mock.call(provider="claude", api_key=self.CLAUDE),
            ],
        )
        self.assertEqual(set(adapters), {"codex", "claude"})
        self.assertNotIn("ZENTRA_CODEX_API_KEY", environment)
        self.assertNotIn("ZENTRA_CLAUDE_API_KEY", environment)

    def test_os_environ_parent_solo_cambia_cuando_bootstrap_es_explicito(self):
        snapshot = dict(os.environ)
        environment = self._environment()
        load_provider_credentials(environment)
        self.assertEqual(dict(os.environ), snapshot)

    def test_guard_ineludible_acepta_solo_allowlist_y_rechaza_nombres_sin_leer_valores(self):
        require_minimized_worker_environment(trusted_worker_environment())
        secret = "synthetic-parent-secret-never-provider"
        hostile = (
            ("PATH", "/private/tmp/attacker-bin"),
            ("PATH", ":/usr/bin"),
            ("PATH", "relative:/usr/bin"),
            ("TMPDIR", "/private/tmp/attacker-temp"),
            ("TMP", "/private/tmp/attacker-temp"),
            ("TEMP", "/private/tmp/attacker-temp"),
            ("SSL_CERT_FILE", "/private/tmp/attacker-ca.pem"),
            ("SSL_CERT_DIR", "/private/tmp/attacker-certs"),
            ("REQUESTS_CA_BUNDLE", "/private/tmp/attacker-ca.pem"),
            ("CURL_CA_BUNDLE", "/private/tmp/attacker-ca.pem"),
            ("SENTINEL_PARENT_SECRET", secret),
            ("aws_secret_access_key", secret),
            ("HTTP_PROXY", "http://synthetic.invalid"),
            ("HOME", "/private/tmp/personal-home"),
            ("PYTHONPATH", "/private/tmp/attacker-modules"),
            ("DYLD_INSERT_LIBRARIES", "/private/tmp/attacker.dylib"),
            ("CLAUDECODE", "1"),
        )
        for name, value in hostile:
            environment = trusted_worker_environment()
            environment[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(
                ProviderCredentialError
            ) as rejected:
                require_minimized_worker_environment(environment)
            self.assertNotIn(value, str(rejected.exception))

    def test_runtime_confiable_es_fijo_y_temp_root_no_viene_del_parent(self):
        self.assertEqual(
            trusted_worker_environment(),
            {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
        root = trusted_system_temp_root()
        self.assertIn(root, {"/private/tmp", "/tmp"})
        with mock.patch.dict(os.environ, {"TMPDIR": "/private/tmp/attacker"}):
            self.assertEqual(trusted_system_temp_root(), root)

    def test_temp_invocacion_exige_owner_modo_canonico_y_rechaza_symlink(self):
        root = Path(trusted_system_temp_root())
        owned = Path(tempfile.mkdtemp(prefix="zentra-temp-test-", dir=root))
        link = root / f"{owned.name}-link"
        try:
            owned.chmod(0o700)
            self.assertEqual(validate_invocation_temp_directory(owned), owned)
            link.symlink_to(owned, target_is_directory=True)
            with self.assertRaises(ProviderCredentialError):
                validate_invocation_temp_directory(link)
            owned.chmod(0o755)
            with self.assertRaises(ProviderCredentialError):
                validate_invocation_temp_directory(owned)
        finally:
            link.unlink(missing_ok=True)
            shutil.rmtree(owned, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
