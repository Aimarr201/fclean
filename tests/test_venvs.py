"""Pruebas de la regla de venvs con un home temporal y un reloj simulado en Linux."""

import os
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
        self.inspector = cleaner.split("inspect_home_venvs() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

    def inspect(self, mode, days=0):
        # Solo esta prueba adelanta el reloj. fclean no admite alterar el plazo desde el entorno.
        clock = f"import time\ntime.time = lambda: {time.time() + days * DAY!r}\n"
        return subprocess.run([sys.executable, "-IS", "-", self.home, mode], input=clock + self.inspector,
                              cwd=self.home, text=True, capture_output=True, check=True)

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

    def test_keep_and_uninstall(self):
        self.inspect("--clean")
        (self.project / ".fclean-keep").touch()
        self.inspect("--clean", 12)
        self.assertTrue(self.python.exists())
        self.inspect("--uninstall")
        self.assertFalse(self.stamp.exists())
        self.assertTrue(self.python.exists())

    def test_unassigned_or_mixed_directories(self):
        self.source.unlink()
        self.inspect("--clean", 10)
        self.assertFalse(self.stamp.exists())
        self.source.touch()
        (self.env / "entrega.ipynb").touch()
        self.inspect("--clean", 10)
        self.assertFalse(self.stamp.exists())
        self.assertTrue(self.python.exists())

    def test_fclean_removes_recent_active_and_protected_venv(self):
        self.inspect("--clean")
        (self.project / ".fclean-keep").touch()
        (self.env / ".fclean-keep").touch()
        (self.site / "fuente-externa").symlink_to(self.source)
        code = "import sys; print('ready', flush=True); sys.stdin.readline()"
        with subprocess.Popen([self.python, "-I", "-c", code], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True) as process:
            self.assertEqual(process.stdout.readline().strip(), "ready")
            self.assertIn(str(self.env), self.inspect("--fclean").stdout)
        self.assertFalse(self.env.exists())
        self.assertEqual(self.source.read_text(), "print(42)\n")
        self.assertTrue((self.project / ".fclean-keep").exists())

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
