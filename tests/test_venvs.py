"""Pruebas de la regla de venvs con un home temporal y un reloj simulado en Linux."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAY = 86400


@unittest.skipUnless(sys.platform.startswith("linux"), "requiere la semántica de Ubuntu y /proc")
class VenvCleanupTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o077))
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.project = self.home / "proyecto"
        self.project.mkdir()
        self.source = self.project / "programa.py"
        self.source.write_text("print(42)\n")
        self.env = self.project / "entorno con espacios"
        self.python = self.env / "bin/python"
        subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", self.env], check=True)
        self.site = next((self.env / "lib").glob("python*/site-packages"))
        self.stamp = self.site / "fclean_venv_usage_v1.pth"
        cleaner = (ROOT / "fclean").read_text(encoding="utf-8")
        policy = cleaner.split("venv_policy=$(cat <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        scanner = cleaner.split("inspect_home_venvs() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        self.inspector = policy + "\n" + scanner

    def inspect(self, mode, days=0, env=None, answers=""):
        # Solo esta prueba adelanta el reloj. fclean no admite alterar el plazo desde el entorno.
        clock = f"import time\ntime.time = lambda: {time.time() + days * DAY!r}\n"
        env = dict(env or os.environ, HOME=str(self.home), XDG_CONFIG_HOME=str(self.home / ".config"))
        return subprocess.run([sys.executable, "-IS", "-c", clock + self.inspector, self.home, mode, ""], input=answers,
                              cwd=self.home, env=env, text=True, capture_output=True, check=True)

    def test_preview_grace_period_and_expiry(self):
        self.assertIn("Se iniciaría", self.inspect("--dry-run", 7).stderr)
        self.assertFalse(self.stamp.exists())
        self.inspect("--clean")
        self.assertTrue(self.stamp.exists())
        self.inspect("--clean", 4)
        self.assertTrue(self.python.exists())
        self.assertIn(str(self.env), self.inspect("--dry-run", 6).stdout)
        self.assertTrue(self.python.exists())
        self.assertIn(str(self.env), self.inspect("--clean", 6).stdout)
        self.assertFalse(self.env.exists())
        self.assertTrue(self.source.is_file())

    def test_active_project_without_python(self):
        self.inspect("--clean")
        future = time.time() + 4 * DAY
        os.utime(self.source, (future, future))
        self.inspect("--clean", 6)
        self.assertTrue(self.python.exists())

    def test_running_interpreter_and_abnormal_exit(self):
        self.inspect("--clean")
        code = "import sys; print('ready', flush=True); sys.stdin.readline()"
        with subprocess.Popen([self.python, "-I", "-c", code], cwd=self.home, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertTrue(list(self.site.glob(".fclean-running-*")))
            self.inspect("--clean", 8)
            self.assertTrue(self.python.exists())
            process.kill()
        self.inspect("--clean", 8)
        self.assertTrue(self.python.exists())
        self.assertFalse(list(self.site.glob(".fclean-running-*")))

    def test_foreign_locked_session_is_kept_then_gets_five_days_after_crash(self):
        self.inspect("--clean")
        code = ("import sys; from pathlib import Path; "
                "next(Path(sys.argv[1]).glob('.fclean-running-*')).read_bytes(); print('listo', flush=True); input()")
        with subprocess.Popen([self.python, "-I", "-c", code, self.site], cwd=self.home, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), "listo")
                token, = self.site.glob(".fclean-running-*")
                # Cambia solo el arranque del nombre. El proceso sigue bloqueando el mismo inodo.
                suffix = token.name.split(".", 2)[2]
                remote = token.rename(token.with_name(".fclean-running-01234567-89ab-4def-8123-0123456789ab." + suffix))
                old = time.time() - 30 * DAY
                os.utime(self.stamp, (old, old))
                self.inspect("--clean")
                self.assertTrue(remote.is_file())
                self.assertEqual(self.stamp.stat().st_mtime_ns // 10**9, int(old))
            finally:
                process.kill()
        self.inspect("--dry-run")
        self.assertTrue(remote.is_file())
        self.inspect("--clean")
        self.assertFalse(remote.exists())
        self.assertGreater(self.stamp.stat().st_mtime, old + 29 * DAY)
        self.inspect("--clean", 4)
        self.assertTrue(self.python.exists())
        self.assertIn(str(self.env), self.inspect("--clean", 6).stdout)
        self.assertTrue(self.source.is_file())

    def test_exit_cleans_up_even_when_the_filesystem_rejects_locks(self):
        self.inspect("--clean")
        code = """
import fcntl, site, sys
def unavailable(*args):
    raise OSError("no locks")
fcntl.flock = unavailable
site.addsitedir(sys.argv[1])
print("listo", flush=True)
input()
"""
        with subprocess.Popen([self.python, "-IS", "-c", code, self.site], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            self.assertEqual(process.stdout.readline().strip(), "listo")
            token, = self.site.glob(".fclean-running-*")
            self.assertEqual(token.read_bytes(), b"")
            old = time.time() - 10 * DAY
            os.utime(self.stamp, (old, old))
            self.inspect("--clean")
            self.assertTrue(token.is_file())
            process.communicate("\n", timeout=5)
            self.assertEqual(process.returncode, 0)
        self.assertFalse(token.exists())
        self.assertGreater(self.stamp.stat().st_mtime, old + 9 * DAY)

    def test_old_local_boot_can_be_confirmed_but_unverifiable_foreign_sessions_are_kept(self):
        self.inspect("--clean")
        previous = "00000000-0000-4000-8000-000000000001"
        foreign = "00000000-0000-4000-8000-000000000002"
        local_token = self.site / f".fclean-running-{previous}.100.abcdefgh"
        other_token = self.site / f".fclean-running-{foreign}.100.abcdefgh"
        local_token.touch()
        other_token.touch()
        # Incluso con el formato actual, este cliente solo ve bloqueos flock locales.
        limited = self.site / f".fclean-running-{foreign}.101.abcdefgh"
        limited.write_bytes(b"fclean-running-v3\n")
        commands = self.home / "comandos"
        commands.mkdir()
        record = {"_BOOT_ID": previous.replace("-", ""), "_MACHINE_ID": Path("/etc/machine-id").read_text().strip(),
                  "_HOSTNAME": os.uname().nodename}
        for name, response in (("journalctl", json.dumps(record)), ("findmnt", "nfs4 rw,local_lock=flock")):
            command = commands / name
            command.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(response) + "\n")
            command.chmod(0o700)
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"])
        self.inspect("--dry-run", 30, env)
        self.assertTrue(local_token.exists())
        preview = self.inspect("--clean", 30, env)
        self.assertFalse(local_token.exists())
        for token in (other_token, limited):
            self.assertTrue(token.exists())
            self.assertIn(str(token), preview.stderr)
        self.assertTrue(self.python.exists())

    def test_forked_child_holds_only_its_own_marker(self):
        self.inspect("--clean")
        code = """
import os
if os.fork():
    os._exit(0)
print("listo", flush=True)
input()
"""
        with subprocess.Popen([self.python, "-I", "-c", code], cwd=self.home, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as parent:
            self.assertEqual(parent.stdout.readline().strip(), "listo")
            self.assertEqual(parent.wait(timeout=5), 0)
            try:
                self.assertEqual(len(list(self.site.glob(".fclean-running-*"))), 2)
                self.inspect("--clean", 10)
                active, = self.site.glob(".fclean-running-*")
                self.assertNotIn(f".{parent.pid}.", active.name)
            finally:
                parent.communicate("\n", timeout=5)
        self.assertEqual(list(self.site.glob(".fclean-running-*")), [])

    def test_upgrades_both_previous_hooks_and_preserves_changes_to_the_module(self):
        self.inspect("--clean")
        module = self.site / "_fclean_venv_usage_v1.py"
        current = module.read_bytes()
        for version in ("v1", "v2"):
            with self.subTest(version=version):
                old = (ROOT / "tests/fixtures" / f"venv_hook_{version}.txt").read_bytes()
                module.write_bytes(old)
                self.inspect("--dry-run", 30)
                self.assertEqual(module.read_bytes(), old)
                self.inspect("--clean", 30)
                self.assertEqual(module.read_bytes(), current)
                self.assertTrue(self.python.exists())
        custom = current + b"# local\n"
        module.write_bytes(custom)
        self.assertIn("ajeno o modificado", self.inspect("--clean", 30).stderr)
        self.assertEqual(module.read_bytes(), custom)

    def test_old_keep_files_do_not_exclude_an_inactive_venv(self):
        for directory in (self.project, self.env, self.site):
            (directory / ".fclean-keep").touch()
        self.inspect("--clean")
        self.assertTrue(self.stamp.exists())
        preview = self.inspect("--dry-run", 12)
        self.assertIn(str(self.env), preview.stdout)
        self.assertNotIn("Venv protegido", preview.stderr)
        self.inspect("--clean", 12)
        self.assertFalse(self.env.exists())
        self.assertTrue((self.project / ".fclean-keep").exists())

    def test_registered_project_protects_multiple_venvs_until_individually_confirmed(self):
        self.inspect("--clean")
        second = self.project / "otro entorno"
        subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", second], check=True)
        registry = self.home / ".config/fclean/protected-projects.json"
        registry.parent.mkdir(parents=True)
        registry.write_text(json.dumps([str(self.project)]))
        self.inspect("--clean", 30)
        self.assertTrue(self.stamp.exists())
        preview = self.inspect("--dry-run", 30)
        for venv in (self.env, second):
            self.assertIn(f"Venv protegido {str(venv)!r}: proyecto registrado", preview.stderr)
        self.assertEqual(preview.stdout, "")
        self.inspect("--fclean", answers="\n")
        self.assertTrue(self.python.exists())
        self.assertTrue((second / "bin/python").exists())
        result = self.inspect("--fclean", answers="n\ny\n")
        self.assertEqual(result.stderr.count("[y/N]"), 2)
        order = sorted((self.env, second), key=lambda v: result.stderr.index(repr(str(v))))
        self.assertTrue(order[0].exists())
        self.assertFalse(order[1].exists())
        self.assertEqual(json.loads(registry.read_text()), [str(self.project)])

    def test_funinstall_removes_tracking_and_live_tokens_without_deleting_the_venv(self):
        self.inspect("--clean")
        (self.env / "entrega.ipynb").write_text("{}")
        with subprocess.Popen([self.python, "-I", "-c", "print('listo', flush=True); input()"],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), "listo")
                self.assertTrue(list(self.site.glob(".fclean-running-*")))
                self.inspect("--funinstall")
                self.assertFalse(self.stamp.exists())
                self.assertFalse((self.site / "_fclean_venv_usage_v1.py").exists())
                self.assertEqual(list(self.site.glob(".fclean-running-*")), [])
                self.assertFalse((self.site / "__pycache__").exists())
                self.assertIsNone(process.poll())
                self.assertTrue(self.python.exists())
                self.inspect("--funinstall")
            finally:
                process.terminate()
        self.assertTrue((self.env / "entrega.ipynb").is_file())

    def test_funinstall_preserves_modified_tracking_and_reports_failure(self):
        self.inspect("--clean")
        original = self.stamp.read_bytes()
        self.stamp.write_bytes(original + b"# cambio personal\n")
        with self.assertRaises(subprocess.CalledProcessError) as error:
            self.inspect("--funinstall")
        self.assertIn("ajeno o modificado", error.exception.stderr)
        self.assertEqual(self.stamp.read_bytes(), original + b"# cambio personal\n")
        self.assertTrue((self.site / "_fclean_venv_usage_v1.py").is_file())
        self.assertTrue(self.python.exists())

    def test_funinstall_preserves_an_untracked_venv_and_its_empty_cache(self):
        cache = self.site / "__pycache__"
        cache.mkdir()
        self.inspect("--funinstall")
        self.assertTrue(cache.is_dir())
        self.assertTrue(self.python.exists())
        self.assertFalse(self.stamp.exists())

    def test_unassigned_or_mixed_directories(self):
        self.source.unlink()
        self.inspect("--clean", 10)
        self.assertFalse(self.stamp.exists())
        self.source.touch()
        (self.env / "entrega.ipynb").touch()
        self.inspect("--clean", 10)
        self.assertFalse(self.stamp.exists())
        self.assertTrue(self.python.exists())

    def test_fclean_removes_recent_and_active_venv(self):
        self.inspect("--clean")
        (self.site / "fuente-externa").symlink_to(self.source)
        code = "import sys; print('ready', flush=True); sys.stdin.readline()"
        with subprocess.Popen([self.python, "-I", "-c", code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertIn(str(self.env), self.inspect("--fclean").stdout)
        self.assertFalse(self.env.exists())
        self.assertEqual(self.source.read_text(), "print(42)\n")

    def test_fclean_ignores_old_keep_files_without_prompting(self):
        for directory in (self.home, self.project, self.env, self.site):
            (directory / ".fclean-keep").touch()
        result = self.inspect("--fclean", answers="n\n")
        self.assertNotIn("[y/N]", result.stderr)
        self.assertFalse(self.env.exists())
        self.assertTrue(self.source.exists())

    def test_fclean_without_project_or_tracking(self):
        self.source.unlink()
        self.assertIn(str(self.env), self.inspect("--fclean").stdout)
        self.assertFalse(self.env.exists())
        self.assertTrue(self.project.is_dir())

    def test_fclean_preserves_runtime_bundled_with_extension(self):
        extension = self.home / ".vscode/extensions/ejemplo.editor-1.0"
        extension.mkdir(parents=True)
        (extension / "package.json").write_text("{}")
        self.env.rename(extension / "runtime")
        self.inspect("--fclean")
        self.assertTrue((extension / "runtime/pyvenv.cfg").is_file())
        self.assertTrue((extension / "runtime/bin/python").exists())

    def test_fclean_preserves_mixed_project(self):
        notebook = self.env / "entrega.ipynb"
        notebook.touch()
        self.inspect("--fclean")
        self.assertTrue(notebook.exists())


if __name__ == "__main__":
    unittest.main()
