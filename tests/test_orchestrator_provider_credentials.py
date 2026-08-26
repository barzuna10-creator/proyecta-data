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
    _validate_trusted_runtime_paths,
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


class TrustedRuntimePathTests(unittest.TestCase):
    def _validate(self, path_components, required_binaries):
        approved = set()
        for path in path_components:
            if path.is_symlink():
                target = Path(os.readlink(path))
                if not target.is_absolute():
                    target = path.parent / target
                approved.add(Path(os.path.abspath(target)))
            else:
                approved.add(path)
        _validate_trusted_runtime_paths(
            path_components=tuple(str(path) for path in path_components),
            required_binaries=tuple(str(path) for path in required_binaries),
            approved_directories=frozenset(approved),
            trusted_uid=os.geteuid(),
        )

    @staticmethod
    def _executable(path: Path) -> None:
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)

    def test_acepta_layout_canonico_tipo_macos(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usr_bin = root / "usr-bin"
            bin_directory = root / "bin"
            usr_bin.mkdir()
            bin_directory.mkdir()
            shell = bin_directory / "sh"
            git = usr_bin / "git"
            self._executable(shell)
            self._executable(git)

            self._validate((usr_bin, bin_directory), (shell, git))

    def test_acepta_merged_usr_y_shell_symlink_a_dash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usr_bin = root / "usr-bin"
            usr_bin.mkdir()
            merged_bin = root / "bin"
            merged_bin.symlink_to(usr_bin, target_is_directory=True)
            dash = usr_bin / "dash"
            git = usr_bin / "git"
            self._executable(dash)
            self._executable(git)
            (usr_bin / "sh").symlink_to(dash)

            self._validate((usr_bin, merged_bin), (merged_bin / "sh", git))

    def test_acepta_shell_symlink_directo_a_dash_en_directorio_canonico(self):
        with tempfile.TemporaryDirectory() as temporary:
            trusted_bin = Path(temporary) / "trusted-bin"
            trusted_bin.mkdir()
            dash = trusted_bin / "dash"
            self._executable(dash)
            shell = trusted_bin / "sh"
            shell.symlink_to("dash")

            self._validate((trusted_bin,), (shell,))

    def test_rechaza_symlink_a_destino_no_aprobado(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted_bin = root / "trusted-bin"
            outside = root / "outside"
            trusted_bin.mkdir()
            outside.mkdir()
            malicious = outside / "malicious-shell"
            self._executable(malicious)
            shell = trusted_bin / "sh"
            shell.symlink_to(malicious)

            with self.assertRaises(ProviderCredentialError):
                self._validate((trusted_bin,), (shell,))

    def test_rechaza_target_group_o_world_writable(self):
        for mode in (0o775, 0o757):
            with (
                self.subTest(mode=oct(mode)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                trusted_bin = root / "trusted-bin"
                trusted_bin.mkdir()
                shell = trusted_bin / "sh"
                self._executable(shell)
                shell.chmod(mode)

                with self.assertRaises(ProviderCredentialError):
                    self._validate((trusted_bin,), (shell,))

    def test_rechaza_directorio_confiable_group_o_world_writable(self):
        for mode in (0o775, 0o757):
            with (
                self.subTest(mode=oct(mode)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                trusted_bin = Path(temporary) / "trusted-bin"
                trusted_bin.mkdir()
                shell = trusted_bin / "sh"
                self._executable(shell)
                trusted_bin.chmod(mode)

                with self.assertRaises(ProviderCredentialError):
                    self._validate((trusted_bin,), (shell,))

    def test_reproduce_bypass_rechaza_directorio_intermedio_writable(self):
        for mode in (0o775, 0o757):
            with (
                self.subTest(mode=oct(mode)),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                trusted_bin = root / "trusted-bin"
                transit = root / "writable-transit"
                trusted_bin.mkdir()
                transit.mkdir()
                transit.chmod(mode)
                dash = trusted_bin / "dash"
                self._executable(dash)
                (transit / "hop").symlink_to(dash)
                shell = trusted_bin / "sh"
                shell.symlink_to(transit / "hop")

                with self.assertRaises(ProviderCredentialError):
                    self._validate((trusted_bin,), (shell,))

    def test_rechaza_componente_intermedio_no_confiable_por_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical_bin = root / "canonical-bin"
            canonical_bin.mkdir()
            shell = canonical_bin / "sh"
            self._executable(shell)
            alias_bin = root / "bin"
            alias_bin.symlink_to(canonical_bin, target_is_directory=True)
            original_lstat = Path.lstat

            def lstat_with_untrusted_alias(path):
                metadata = original_lstat(path)
                if path == alias_bin:
                    return types.SimpleNamespace(
                        st_mode=metadata.st_mode,
                        st_uid=os.geteuid() + 1,
                    )
                return metadata

            with mock.patch.object(Path, "lstat", lstat_with_untrusted_alias):
                with self.assertRaises(ProviderCredentialError):
                    self._validate((canonical_bin, alias_bin), (alias_bin / "sh",))

    def test_rechaza_target_final_no_confiable_por_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            trusted_bin = Path(temporary) / "trusted-bin"
            trusted_bin.mkdir()
            dash = trusted_bin / "dash"
            self._executable(dash)
            shell = trusted_bin / "sh"
            shell.symlink_to(dash)
            original_lstat = Path.lstat

            def lstat_with_untrusted_target(path):
                metadata = original_lstat(path)
                if path == dash:
                    return types.SimpleNamespace(
                        st_mode=metadata.st_mode,
                        st_uid=os.geteuid() + 1,
                    )
                return metadata

            with mock.patch.object(Path, "lstat", lstat_with_untrusted_target):
                with self.assertRaises(ProviderCredentialError):
                    self._validate((trusted_bin,), (shell,))

    def test_rechaza_tipo_incorrecto_y_binario_no_ejecutable(self):
        for kind in ("directory", "not-executable"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                trusted_bin = Path(temporary) / "trusted-bin"
                trusted_bin.mkdir()
                shell = trusted_bin / "sh"
                if kind == "directory":
                    shell.mkdir()
                else:
                    shell.write_text("not executable", encoding="utf-8")
                    shell.chmod(0o644)

                with self.assertRaises(ProviderCredentialError):
                    self._validate((trusted_bin,), (shell,))

    def test_rechaza_multihop_que_sale_y_regresa_al_espacio_confiable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted_bin = root / "trusted-bin"
            outside = root / "outside"
            trusted_bin.mkdir()
            outside.mkdir()
            dash = trusted_bin / "dash"
            self._executable(dash)
            (outside / "return-to-trusted").symlink_to(dash)
            shell = trusted_bin / "sh"
            shell.symlink_to(outside / "return-to-trusted")

            with self.assertRaises(ProviderCredentialError):
                self._validate((trusted_bin,), (shell,))

    def test_reproduce_escape_alias_dotdot_rechazado_antes_de_normalizar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted_bin = root / "trusted"
            nested = root / "outside" / "nested"
            trusted_bin.mkdir()
            nested.mkdir(parents=True)
            dash = trusted_bin / "dash"
            self._executable(dash)
            outside_dash = nested.parent / "dash"
            self._executable(outside_dash)
            (trusted_bin / "alias").symlink_to(nested, target_is_directory=True)
            shell = trusted_bin / "sh"
            os.symlink("alias/../dash", shell)
            self.assertEqual(shell.resolve(strict=True), outside_dash.resolve(strict=True))

            with self.assertRaises(ProviderCredentialError):
                self._validate((trusted_bin,), (shell,))

    def test_rechaza_componentes_dot_dotdot_antes_de_normalizar(self):
        with tempfile.TemporaryDirectory() as temporary:
            trusted_bin = Path(temporary) / "trusted-bin"
            trusted_bin.mkdir()
            dash = trusted_bin / "dash"
            self._executable(dash)
            hostile_targets = (
                "../dash",
                "./dash",
                "foo/../dash",
                f"{trusted_bin}/foo/../dash",
            )
            for index, raw_target in enumerate(hostile_targets):
                with self.subTest(raw_target=raw_target):
                    shell = trusted_bin / f"sh-{index}"
                    os.symlink(raw_target, shell)
                    with self.assertRaises(ProviderCredentialError):
                        self._validate((trusted_bin,), (shell,))

    def test_rechaza_separadores_repetidos_o_finales(self):
        with tempfile.TemporaryDirectory() as temporary:
            trusted_bin = Path(temporary) / "trusted-bin"
            trusted_bin.mkdir()
            dash = trusted_bin / "dash"
            self._executable(dash)
            for index, raw_target in enumerate(("dash/", "foo//dash", f"{trusted_bin}//dash")):
                with self.subTest(raw_target=raw_target):
                    shell = trusted_bin / f"sh-{index}"
                    os.symlink(raw_target, shell)
                    with self.assertRaises(ProviderCredentialError):
                        self._validate((trusted_bin,), (shell,))

    def test_rechaza_symlink_desde_directorio_no_confiable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted_bin = root / "trusted-bin"
            untrusted_bin = root / "untrusted-bin"
            trusted_bin.mkdir()
            untrusted_bin.mkdir()
            target = trusted_bin / "shell"
            self._executable(target)
            link = untrusted_bin / "sh"
            link.symlink_to(target)

            with self.assertRaises(ProviderCredentialError):
                self._validate((trusted_bin,), (link,))


if __name__ == "__main__":
    unittest.main()
