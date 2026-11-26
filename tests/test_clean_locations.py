"""Pruebas de la CLI con un home desechable en Ubuntu."""

import configparser
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "fclean"


@unittest.skipUnless(sys.platform.startswith("linux"), "requiere Ubuntu y /proc")
class CleanLocationsTests(unittest.TestCase):
    def setUp(self):
        import pwd

        if os.getuid() == 0:
            self.skipTest("fclean se ejecuta como usuario del campus")
        self.addCleanup(os.umask, os.umask(0o077))
        self.home = self.new_directory()
        self.runtime = self.new_directory()
        self.commands = self.new_directory()
        try:
            self.user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            # Algunos contenedores asignan un UID sin entrada en la base de usuarios.
            self.user = "fclean-test"
            self.stub("id", 'if [ "$#" -eq 1 ] && [ "$1" = -un ]; then\n'
                      f"    printf '%s\\n' {self.user}\nelse\n    exec /usr/bin/id \"$@\"\nfi")
        self.env = dict(os.environ, HOME=str(self.home), USER=self.user,
                        XDG_RUNTIME_DIR=str(self.runtime), XDG_CONFIG_HOME=str(self.home / ".config"),
                        PATH=str(self.commands) + os.pathsep + os.environ["PATH"])
        for name in ("BASH_ENV", "ENV", "VIRTUAL_ENV", "VSCODE_EXTENSIONS", "ZDOTDIR", "SGO", "XDG_CACHE_HOME",
                     "XDG_DATA_HOME", "UV_CACHE_DIR", "HF_HOME"):
            self.env.pop(name, None)
        self.stub("pgrep", "exit 1")
        self.stub("flatpak", "exit 0")
        self.stub("curl", "exit 1")

    def stub(self, name, body):
        command = self.commands / name
        command.write_text(f"#!/bin/sh\n{body}\n")
        command.chmod(0o700)

    def new_directory(self, parent=None):
        temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temp.cleanup)
        return Path(temp.name).resolve()

    def mount_sgoinfre(self, *, create_personal=True):
        shm = Path("/dev/shm")
        if not shm.is_dir() or shm.stat().st_dev == self.home.stat().st_dev:
            self.skipTest("requiere /dev/shm en otro sistema de archivos")
        shared = self.new_directory(shm)
        (self.home / "sgoinfre").symlink_to(shared, target_is_directory=True)
        personal = shared / self.user
        if create_personal:
            personal.mkdir()
        return personal

    def run_command(self, *command, cwd=None, check=True):
        result = subprocess.run(command, env=self.env, cwd=cwd or self.home,
                                capture_output=True, text=True, timeout=30)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def run_cli(self, *options, check=True):
        return self.run_command("bash", SCRIPT, *options, check=check)

    def put(self, relative, content="datos", base=None):
        path = (base or self.home) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def allocated_kib(self, *paths):
        entries = {(info.st_dev, info.st_ino): info.st_blocks
                   for path in paths for info in (path.lstat(),)}
        return (sum(entries.values()) + 1) // 2

    def assert_preview_summary(self, output, *, cleanup, migration):
        summary = output.split("Resumen estimado (--dry-run):\n", 1)[1]
        # Estos casos usan archivos pequeños para comparar las cantidades sin redondeo.
        self.assertLess(max(cleanup, migration), 1024)
        cleanup_text = f"{cleanup} KiB" if cleanup else "0 B"
        migration_text = f"{migration} KiB" if migration else "0 B"
        self.assertIn(f"Total por limpieza (home y sgoinfre): {cleanup_text}\n", summary)
        self.assertIn(f"Total por migración opcional (espacio liberado en home): {migration_text}\n", summary)
        self.assertTrue(summary.endswith(
            "  Estimaciones independientes: no se suman, pueden incluir el mismo contenido.\n"))

    def test_preview_summary_with_nothing_to_clean_or_migrate(self):
        self.assert_preview_summary(self.run_cli("--dry-run").stdout, cleanup=0, migration=0)
        self.assertEqual(list(self.home.iterdir()), [])
        self.mount_sgoinfre()
        self.assert_preview_summary(self.run_cli("--dry-run").stdout, cleanup=0, migration=0)
        self.assertFalse((self.home / ".cache").exists())
        self.assertFalse((self.home / ".vscode").exists())

    def test_preview_summary_without_sgoinfre_still_totals_cleanup(self):
        cached = self.put(".cache/archivo")
        npm = self.put(".npm/_cacache/paquete")
        self.assert_preview_summary(self.run_cli("--dry-run").stdout,
                                    cleanup=self.allocated_kib(cached, npm), migration=0)
        self.assertTrue(cached.is_file())
        self.assertTrue(npm.is_file())
        self.assertFalse((self.runtime / "42-fclean.lock").exists())

    def test_preview_summary_totals_both_optional_migrations_independently(self):
        personal = self.mount_sgoinfre()
        cached = self.put(".cache/archivo con espacio\ny salto")
        linked = cached.with_name("enlace duro")
        os.link(cached, linked)
        package = self.put(".vscode/extensions/ejemplo.editor/package.json", "{}")
        migrated = self.allocated_kib(cached.parent, cached, linked,
                                     package.parent.parent, package.parent, package)
        self.assert_preview_summary(self.run_cli("--dry-run").stdout,
                                    cleanup=self.allocated_kib(cached), migration=migrated)
        self.assertTrue(cached.samefile(linked))
        self.assertTrue(package.is_file())
        self.assertEqual(list(personal.iterdir()), [])

    def test_preview_summary_does_not_count_already_migrated_links(self):
        personal = self.mount_sgoinfre()
        cached = self.put(".cache/archivo", base=personal)
        self.put(".vscode/extensions/ejemplo.editor/package.json", "{}", base=personal)
        for relative in (".cache", ".vscode/extensions"):
            source = self.home / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(personal / relative, target_is_directory=True)
        self.assert_preview_summary(self.run_cli("--dry-run").stdout,
                                    cleanup=self.allocated_kib(cached), migration=0)
        self.assertTrue(cached.is_file())
        self.assertTrue((self.home / ".cache").is_symlink())

    @unittest.skipUnless(shutil.which("rsync"), "requiere rsync para detectar conflictos de caché")
    def test_preview_summary_excludes_only_the_conflicting_migration(self):
        personal = self.mount_sgoinfre()
        cached = self.put(".cache/archivo", "igual")
        package = self.put(".vscode/extensions/ejemplo.editor/package.json", "igual")
        remote_cache = self.put(".cache/archivo", "igual", base=personal)
        remote_package = self.put(".vscode/extensions/ejemplo.editor/package.json", "igual", base=personal)
        cleanup = self.allocated_kib(cached, remote_cache)
        for conflicting, migratable in (
                (remote_cache, (package.parent.parent, package.parent, package)),
                (remote_package, (cached.parent, cached))):
            with self.subTest(conflict=conflicting):
                conflicting.write_text("distinto")
                preview = self.run_cli("--dry-run")
                self.assertIn("Se omite la migración opcional", preview.stderr)
                self.assert_preview_summary(preview.stdout, cleanup=cleanup,
                                            migration=self.allocated_kib(*migratable))
                conflicting.write_text("igual")
        self.assertEqual(cached.read_text(), "igual")
        self.assertEqual(package.read_text(), "igual")

    def stub_update_server(self):
        self.stub("curl", r'''
while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) output=$2; shift ;;
        --etag-save) etag=$2; shift ;;
        --etag-compare) sent=$(cat "$2"); shift ;;
        https://*) url=$1 ;;
    esac
    shift
done
printf '%s %s\n' "$url" "${sent:-sin-etag}" >> "$HOME/update-requests"
status=$(cat "$HOME/update-status")
case "$status" in
    200) cp "$HOME/upstream-script" "$output"; printf '"revision-2"\n' > "$etag" ;;
    304) ;;
    *) exit 1 ;;
esac
printf '%s' "$status"
''')

    def test_installed_clean_updates_once_and_falls_back_to_the_last_copy(self):
        self.stub_update_server()
        self.run_cli("--install")
        installed = self.home / ".config/fclean/fclean"
        upstream = installed.read_text().replace("Contenido eliminado:", "Limpieza desde la actualización:")
        self.put("upstream-script", upstream)
        self.put("update-status", "200")
        cached = self.put(".cache/temporal")
        result = self.run_command("bash", installed, "--clean")
        self.assertIn("fclean actualizado", result.stderr)
        self.assertIn("Limpieza desde la actualización:", result.stdout)
        self.assertFalse(cached.exists())
        self.assertEqual(installed.read_text(), upstream)
        requests = self.home / "update-requests"
        url = "https://raw.githubusercontent.com/Aimarr201/fclean/main/fclean"
        self.assertEqual(requests.read_text().splitlines(), [f"{url} sin-etag"])
        self.put("update-status", "304")
        self.assertIn("Limpieza desde la actualización:", self.run_command("bash", installed, "--clean").stdout)
        self.assertEqual(requests.read_text().splitlines()[-1], f'{url} "revision-2"')
        self.run_command("bash", installed, "--dry-run")
        self.run_command("bash", installed, "--fclean")
        self.assertEqual(len(requests.read_text().splitlines()), 2)
        self.put("upstream-script", upstream + "\nif\n")
        for status in ("200", "503"):
            with self.subTest(status=status):
                self.put("update-status", status)
                cached = self.put(".cache/temporal")
                result = self.run_command("bash", installed, "--clean")
                self.assertIn("No se pudo actualizar", result.stderr)
                self.assertIn("Limpieza desde la actualización:", result.stdout)
                self.assertEqual(installed.read_text(), upstream)
                self.assertFalse(cached.exists())

    def test_manual_clean_uses_an_updated_runtime_copy_without_replacing_source(self):
        self.stub_update_server()
        original = SCRIPT.read_text()
        source = self.put("descarga/fclean", original)
        self.put("upstream-script", original.replace("Contenido eliminado:", "Limpieza actualizada:"))
        status = self.put("update-status", "200")
        for response in ("200", "304", "503"):
            with self.subTest(response=response):
                status.write_text(response)
                result = self.run_command("bash", source, "--clean")
                self.assertIn("Limpieza actualizada:", result.stdout)
                self.assertEqual(source.read_text(), original)
        self.assertEqual(len((self.home / "update-requests").read_text().splitlines()), 3)

    @unittest.skipUnless(shutil.which("git"), "requiere Git para probar cambios del repositorio")
    def test_clean_preserves_worktree_edits(self):
        self.stub_update_server()
        original = SCRIPT.read_text()
        source = self.put("repositorio/fclean", original)
        repo = source.parent
        self.run_command("git", "init", "-q", cwd=repo)
        self.run_command("git", "add", "fclean", cwd=repo)
        self.run_command("git", "-c", "user.name=Pruebas", "-c", "user.email=pruebas@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "-qm", "Versión original", cwd=repo)
        self.put("update-status", "200")
        self.put("upstream-script", original.replace("Contenido eliminado:", "Versión remota:"))
        edited = original.replace("Contenido eliminado:", "Versión local:")
        source.write_text(edited)
        self.assertIn("Versión local:", self.run_command("bash", source, "--clean").stdout)
        self.assertEqual(source.read_text(), edited)
        self.assertFalse((self.home / "update-requests").exists())
        source.write_text(original)
        self.assertIn("Versión remota:", self.run_command("bash", source, "--clean").stdout)
        self.assertEqual(self.run_command("git", "status", "--porcelain", cwd=repo).stdout, "")

    def test_unmigrated_cache_without_sgoinfre(self):
        cache = self.home / ".cache"
        cache.mkdir()
        (cache / "local").touch()
        self.run_cli("--clean")
        self.assertEqual(list(cache.iterdir()), [])

    def test_both_copies_without_shell_configuration(self):
        personal = self.mount_sgoinfre()
        sentinel = personal / "datos"
        sentinel.touch()
        for base in (self.home, personal):
            cache = base / ".cache"
            extensions = base / ".vscode/extensions"
            cache.mkdir()
            extensions.mkdir(parents=True)
            for root in (cache, extensions):
                (root / "visible").touch()
                (root / ".oculto").touch()
                (root / "enlace").symlink_to(sentinel)
            extension = extensions / "ejemplo.editor-1.0"
            discarded = extension / "Cache/old_Cache_Data_001"
            discarded.mkdir(parents=True)
            (extension / "package.json").write_text("{}")
            (discarded / "0123456789abcdef_0").touch()
            (discarded / "indice-incluido").touch()
        self.run_cli("--clean")
        for base in (self.home, personal):
            self.assertEqual(list((base / ".cache").iterdir()), [])
            extensions = base / ".vscode/extensions"
            self.assertTrue((extensions / "visible").is_file())
            self.assertTrue((extensions / ".oculto").is_file())
            self.assertTrue((extensions / "enlace").is_symlink())
            extension = extensions / "ejemplo.editor-1.0"
            self.assertEqual((extension / "package.json").read_text(), "{}")
            self.assertEqual([p.name for p in (extension / "Cache/old_Cache_Data_001").iterdir()],
                             ["indice-incluido"])
        self.assertTrue(sentinel.exists())

    def test_migrated_links_survive_and_cache_is_reported_once(self):
        personal = self.mount_sgoinfre()
        for relative in (".cache", ".vscode/extensions"):
            target = personal / relative
            target.mkdir(parents=True)
            (target / "visible").touch()
            link = self.home / relative
            link.parent.mkdir(exist_ok=True)
            link.symlink_to(target, target_is_directory=True)
        preview = self.run_cli("--dry-run").stdout
        self.assertEqual(preview.count(f"\t{str(personal / '.cache')!r} ("), 1)
        self.assertTrue((personal / ".cache/visible").exists())
        self.run_cli("--clean")
        self.assertEqual(list((personal / ".cache").iterdir()), [])
        self.assertTrue((personal / ".vscode/extensions/visible").exists())
        self.run_cli("--fclean")
        for relative in (".cache", ".vscode/extensions"):
            self.assertTrue((self.home / relative).is_symlink())
        self.assertEqual(list((personal / ".cache").iterdir()), [])
        self.assertTrue((personal / ".vscode/extensions/visible").is_file())

    def test_clean_removes_marked_extensions_and_keeps_registered_versions(self):
        extensions = self.home / ".vscode/extensions"
        for version in ("1.0.0", "2.0.0", "3.0.0"):
            key = f"ejemplo.editor-{version}"
            self.put(f".vscode/extensions/{key}/package.json",
                     json.dumps({"publisher": "ejemplo", "name": "editor", "version": version}))
            self.put(f".vscode/extensions/{key}/.oculto")
        obsolete = self.put(".vscode/extensions/.obsolete", json.dumps({
            "ejemplo.editor-1.0.0": True, "ejemplo.editor-2.0.0": True,
            "ejemplo.editor-3.0.0": False, "../../unrelated": True,
        }))
        self.put(".vscode/extensions/extensions.json", json.dumps([{"relativeLocation": "ejemplo.editor-2.0.0"}]))
        marked = obsolete.read_text()
        candidate = extensions / "ejemplo.editor-1.0.0"
        preview = self.run_cli("--dry-run").stdout
        self.assertIn("ejemplo.editor-1.0.0", preview)
        self.assertNotIn("ejemplo.editor-2.0.0", preview)
        self.assertTrue((candidate / ".oculto").is_file())
        manifest = (candidate / "package.json").read_text()
        self.assertIn("extensión marcada en .obsolete", self.run_cli("--clean").stdout)
        self.assertFalse(candidate.exists())
        self.put(".vscode/extensions/ejemplo.editor-1.0.0/package.json", manifest)
        self.assertIn("extensión marcada en .obsolete", self.run_cli("--fclean").stdout)
        self.assertFalse(candidate.exists())
        self.assertTrue((extensions / "ejemplo.editor-2.0.0/.oculto").is_file())
        self.assertTrue((extensions / "ejemplo.editor-3.0.0/.oculto").is_file())
        self.assertEqual(obsolete.read_text(), marked)
        self.assertNotIn("extensión marcada en .obsolete", self.run_cli("--clean").stdout)

    def test_obsolete_extensions_clean_with_editor_open_and_preserve_registered_versions(self):
        personal = self.mount_sgoinfre()
        (self.home / ".vscode").symlink_to(personal / ".vscode", target_is_directory=True)
        keys = [f"ejemplo.editor-{version}.0.0-linux-x64" for version in (1, 2, 3)]
        for version, key in enumerate(keys, 1):
            self.put(f".vscode/extensions/{key}/package.json", json.dumps({
                "publisher": "ejemplo", "name": "editor", "version": f"{version}.0.0",
                "__metadata": {"targetPlatform": "linux-x64"},
            }), base=personal)
        self.put(".vscode/extensions/.obsolete", json.dumps(dict.fromkeys(keys, True)), base=personal)
        self.put(".config/Code/User/profiles/perfil/extensions.json",
                 json.dumps([{"location": {"path": str(self.home / ".vscode/extensions" / keys[1])}}]))
        nfs = self.put(f".vscode/extensions/{keys[2]}/.nfs123", base=personal)
        outside = self.put("otro/archivo")
        (personal / ".vscode/extensions" / keys[0] / "enlace").symlink_to(outside)
        candidate = personal / ".vscode/extensions" / keys[0]
        self.stub("pgrep", "exit 0")
        lock = self.home / ".config/Code/SingletonLock"
        lock.symlink_to("otro-equipo-1234")
        self.assertIn(keys[0], self.run_cli("--dry-run").stdout)
        self.run_cli("--clean")
        self.assertFalse(candidate.exists())
        self.assertTrue(lock.is_symlink())
        self.assertTrue(outside.is_file())
        self.assertTrue((personal / ".vscode/extensions" / keys[1]).is_dir())
        self.assertTrue(nfs.is_file())
        self.assertTrue(nfs.with_name("package.json").is_file())

    def test_invalid_obsolete_metadata_skips_extension_removal(self):
        candidate = self.put(".vscode/extensions/ejemplo.editor-1.0.0/package.json",
                             '{"publisher":"ejemplo","name":"editor","version":"1.0.0"}')
        obsolete = self.put(".vscode/extensions/.obsolete", "[")
        cached = self.put(".cache/temporal")
        result = self.run_cli("--clean")
        self.assertIn("Se omite", result.stderr)
        self.assertFalse(cached.exists())
        self.assertTrue(candidate.is_file())
        obsolete.write_text('{"ejemplo.editor-1.0.0":true}')
        self.put(".vscode/extensions/extensions.json", "{")
        result = self.run_cli("--clean")
        self.assertIn("Se omite", result.stderr)
        self.assertTrue(candidate.is_file())

    def test_both_cleanup_modes_clean_busy_applications_and_preserve_profiles(self):
        self.stub("pgrep", "exit 0")
        self.stub("flatpak", "echo com.slack.Slack")
        targets = (
            ".config/Slack/Service Worker/CacheStorage",
            ".config/google-chrome/Default/Cache",
            ".config/Code/CachedExtensionVSIXs",
            ".var/app/com.slack.Slack/config/Slack/Code Cache",
            "snap/firefox/common/.cache",
            "snap/chromium/common/.cache",
        )
        (self.home / ".config/google-chrome/Default").mkdir(parents=True)
        lock = self.home / ".config/google-chrome/SingletonLock"
        lock.symlink_to("puesto-1234")
        prefs = self.home / ".config/google-chrome/Default/Preferences"
        prefs.write_text("{}")
        for mode in ("--clean", "--fclean"):
            with self.subTest(mode=mode):
                files = [self.put(f"{relative}/cached") for relative in targets]
                if mode == "--clean":
                    preview = self.run_cli("--dry-run").stdout
                    self.assertTrue(all(str(path.parent) in preview for path in files))
                    self.assertTrue(all(path.is_file() for path in files))
                with files[0].open("rb") as handle:
                    self.run_cli(mode)
                    self.assertTrue(all(not path.exists() for path in files))
                    self.assertEqual(handle.read(), b"datos")
        self.assertTrue(lock.is_symlink())
        self.assertEqual(prefs.read_text(), "{}")

    def test_clean_does_not_require_a_flatpak_process_listing(self):
        flatpak = self.put(".var/app/com.slack.Slack/config/Slack/GPUCache/datos")
        native = self.put(".config/Slack/GPUCache/datos")
        idle = self.put(".config/discord/GPUCache/datos")
        self.stub("flatpak", 'touch "$HOME/flatpak-consultado"\nexit 1')
        self.run_cli("--clean")
        self.assertFalse(native.exists())
        self.assertFalse(flatpak.exists())
        self.assertFalse(idle.exists())
        self.assertFalse((self.home / "flatpak-consultado").exists())

    def test_default_help_preview_and_invalid_options(self):
        cached = self.put(".cache/visible")
        help_text = self.run_cli("--help").stdout
        self.assertIn("Uso:", help_text)
        self.assertEqual(self.run_cli().stdout, help_text)
        self.assertEqual(self.run_cli("-h").stdout, help_text)
        self.assertIn("Contenido que se eliminaría", self.run_cli("--dry-run").stdout)
        self.assertTrue(cached.is_file())
        self.assertFalse((self.runtime / "42-fclean.lock").exists())
        for options in (("--desconocida",), ("--clean", "--fclean")):
            with self.subTest(options=options):
                result = self.run_cli(*options, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Uso:", result.stderr)
        self.assertTrue(cached.is_file())

    def test_clean_selected_files_and_preserves_unrelated_link_targets(self):
        targets = [self.put(relative) for relative in (
            ".cache/.oculto", ".cache/visible/.oculto", ".local/share/Trash/files/descartado", ".npm/_cacache/paquete",
            ".bun/install/cache/paquete", ".var/app/ejemplo/cache/visible",
            ".var/app/com.google.Chrome/config/google-chrome/Default/File System/datos",
            ".config/BraveSoftware/Brave-Browser/Profile 12/GPUCache/datos",
            ".config/VSCodium/Code Cache/datos",
        )]
        preserved = [self.put(relative) for relative in (
            ".cache/visible/.nfs123", ".config/VSCodium/User/settings.json", "mis-datos",
        )]
        inner_link = self.home / ".cache/enlace"
        inner_link.symlink_to(preserved[-1])
        outside = self.new_directory()
        sentinel = self.put("archivo", base=outside)
        linked_cache = self.home / ".config/VSCodium/GPUCache"
        linked_cache.symlink_to(outside, target_is_directory=True)
        self.run_cli("--clean")
        self.assertTrue(all(not path.exists() for path in targets))
        self.assertTrue(all(path.is_file() for path in preserved))
        self.assertFalse(inner_link.is_symlink())
        self.assertTrue(linked_cache.is_symlink())
        self.assertTrue(sentinel.is_file())

    def test_clean_preserves_venvs_in_the_general_cache(self):
        personal = self.mount_sgoinfre()
        local = self.home / ".cache/.entorno"
        moved = personal / ".cache/.entorno"
        for venv in (local, moved):
            venv.parent.mkdir()
            self.run_command(sys.executable, "-I", "-m", "venv", "--without-pip", venv)
            (venv.parent / ".temporal").touch()
        self.run_cli("--clean")
        for venv in (local, moved):
            self.assertTrue((venv / "pyvenv.cfg").is_file())
            self.assertFalse((venv.parent / ".temporal").exists())
        self.run_cli("--fclean")
        self.assertFalse(local.exists())
        self.assertFalse(moved.exists())

    def test_cache_migration_copies_links_and_can_be_repeated(self):
        personal = self.mount_sgoinfre()
        src = self.put(".cache/archivo")
        self.put(".cache/.oculto")
        os.link(src, src.with_name("duro"))
        src.with_name("simbolico").symlink_to("archivo")
        self.assertIn("Migración opcional", self.run_cli("--dry-run").stdout)
        dst = personal / ".cache"
        self.assertFalse(dst.exists())
        self.run_cli("--migrate-cache")
        self.assertEqual(Path(os.readlink(self.home / ".cache")), self.home / "sgoinfre" / self.user / ".cache")
        self.assertEqual((dst / "archivo").read_text(), "datos")
        self.assertTrue((dst / "archivo").samefile(dst / "duro"))
        self.assertEqual(os.readlink(dst / "simbolico"), "archivo")
        self.assertTrue((dst / ".oculto").exists())
        self.assertIn("Se conserva el enlace existente", self.run_cli("--migrate-cache").stdout)

    def test_first_migration_creates_private_storage_with_a_linked_home(self):
        personal = self.mount_sgoinfre(create_personal=False)
        logical_home = self.new_directory() / "home-enlazado"
        logical_home.symlink_to(self.home, target_is_directory=True)
        self.env["HOME"] = str(logical_home)
        self.put(".cache/local")
        self.assertIn(f"Migración opcional: {self.home / '.cache'}", self.run_cli("--dry-run").stdout)
        self.assertFalse(personal.exists())
        self.run_cli("--migrate-cache")
        self.assertEqual(personal.stat().st_mode & 0o777, 0o700)
        self.assertEqual(os.readlink(self.home / ".cache"), str(logical_home / "sgoinfre" / self.user / ".cache"))
        self.assertEqual((personal / ".cache/local").read_text(), "datos")

    def test_cache_migration_preserves_special_files_and_reports_the_blocker(self):
        import stat

        personal = self.mount_sgoinfre()
        source = self.put(".cache/archivo")
        fifo = source.with_name("tuberia")
        os.mkfifo(fifo)
        preview = self.run_cli("--dry-run")
        self.assertIn("archivo especial", preview.stderr)
        self.assertIn(f"Migración opcional: {self.home / '.vscode/extensions'}", preview.stdout)
        result = self.run_cli("--migrate-cache", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("archivo especial", result.stderr)
        self.assertTrue(stat.S_ISFIFO(fifo.stat().st_mode))
        self.assertTrue(source.is_file())
        self.assertFalse((personal / ".cache").exists())

    @unittest.skipUnless(shutil.which("rsync"), "requiere rsync para combinar cachés")
    def test_cache_migration_merges_compatible_directories(self):
        personal = self.mount_sgoinfre()
        self.put(".cache/comun", "igual")
        self.put(".cache/comun", "igual", base=personal)
        self.put(".cache/local", "local")
        self.put(".cache/remoto", "remoto", base=personal)
        self.run_cli("--migrate-cache")
        self.assertTrue((self.home / ".cache").is_symlink())
        self.assertEqual({path.name: path.read_text() for path in (personal / ".cache").iterdir()},
                         {"comun": "igual", "local": "local", "remoto": "remoto"})

    @unittest.skipUnless(shutil.which("rsync"), "requiere rsync para detectar conflictos de caché")
    def test_cache_migration_refuses_conflicting_files(self):
        personal = self.mount_sgoinfre()
        src = self.put(".cache/comun", "local")
        dst = self.put(".cache/comun", "remoto", base=personal)
        self.put(".cache/solo-local")
        preview = self.run_cli("--dry-run")
        self.assertIn("Hay diferencias", preview.stderr)
        self.assertNotIn(f"Migración opcional: {src.parent}", preview.stdout)
        self.assertIn(f"Migración opcional: {self.home / '.vscode/extensions'}", preview.stdout)
        result = self.run_cli("--migrate-cache", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Hay diferencias", result.stderr)
        self.assertFalse(src.parent.is_symlink())
        self.assertFalse((dst.parent / "solo-local").exists())
        self.assertEqual((src.read_text(), dst.read_text()), ("local", "remoto"))

    def test_extension_migration_copies_extensions_and_can_be_repeated(self):
        personal = self.mount_sgoinfre()
        relative = ".vscode/extensions/ejemplo.editor/package.json"
        self.put(relative, "{}")
        settings = self.put(".vscode/settings.json", '{"tema": "ejemplo"}')
        self.run_cli("--migrate")
        self.assertTrue((self.home / ".vscode/extensions").is_symlink())
        self.assertEqual((personal / relative).read_text(), "{}")
        self.assertEqual(settings.read_text(), '{"tema": "ejemplo"}')
        self.assertIn("Se conserva el enlace existente", self.run_cli("--migrate").stdout)

    def test_migrations_attach_existing_destinations_when_local_directories_are_absent(self):
        personal = self.mount_sgoinfre()
        for option, relative in (("--migrate-cache", ".cache"), ("--migrate", ".vscode/extensions")):
            with self.subTest(option=option):
                sentinel = self.put(f"{relative}/remoto", base=personal)
                self.run_cli(option)
                source = self.home / relative
                self.assertTrue(source.is_symlink())
                self.assertTrue((source / sentinel.name).samefile(sentinel))

    def test_extension_conflict_preserves_both_copies_and_preview_continues(self):
        personal = self.mount_sgoinfre()
        relative = ".vscode/extensions/ejemplo.editor/package.json"
        src = self.put(relative, "local")
        dst = self.put(relative, "remoto", base=personal)
        self.put(".cache/local")
        migration = self.run_cli("--migrate", check=False)
        self.assertEqual(migration.returncode, 1, migration.stdout + migration.stderr)
        self.assertIn("Se conservan ambas carpetas", migration.stderr)
        preview = self.run_cli("--dry-run")
        self.assertIn("Se conservan ambas carpetas", preview.stderr)
        self.assertIn(f"Migración opcional: {self.home / '.cache'}", preview.stdout)
        self.assertEqual((src.read_text(), dst.read_text()), ("local", "remoto"))

    def test_migrations_without_separate_storage_keep_local_data(self):
        (self.home / "sgoinfre" / self.user).mkdir(parents=True)
        cached = self.put(".cache/local")
        extension = self.put(".vscode/extensions/ejemplo.editor/package.json")
        for option in ("--migrate", "--migrate-cache", "--prepare-env"):
            with self.subTest(option=option):
                self.assertEqual(self.run_cli(option, check=False).returncode, 1)
        self.assertTrue(cached.is_file())
        self.assertTrue(extension.is_file())
        self.assertFalse((self.home / "sgoinfre" / self.user / ".cache").exists())

    def test_migrations_respect_running_applications(self):
        personal = self.mount_sgoinfre()
        cached = self.put(".cache/local")
        extension = self.put(".vscode/extensions/ejemplo.editor/package.json")
        self.stub("pgrep", "exit 0")
        self.assert_preview_summary(self.run_cli("--dry-run").stdout,
                                    cleanup=self.allocated_kib(cached), migration=0)
        for option in ("--migrate", "--migrate-cache"):
            with self.subTest(option=option):
                result = self.run_cli(option, check=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("está en uso", result.stderr)
        self.assertTrue(cached.is_file())
        self.assertTrue(extension.is_file())
        self.assertEqual(list(personal.iterdir()), [])

    @unittest.skipUnless(shutil.which("pgrep"), "requiere pgrep para consultar procesos reales")
    def test_detects_a_real_running_editor_process(self):
        self.mount_sgoinfre()
        (self.commands / "pgrep").unlink()
        cached = self.put(".config/Code/Cache/fichero")
        self.put(".cache/archivo")
        self.put(".vscode/extensions/ejemplo/editor")
        editor = self.commands / "code"
        editor.symlink_to(Path(sys.executable).resolve())
        with subprocess.Popen([editor, "-I", "-c", "print('listo', flush=True); input()"],
                              env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
            try:
                self.assertEqual(process.stdout.readline().strip(), "listo")
                for option in ("--migrate", "--migrate-cache"):
                    with self.subTest(option=option):
                        result = self.run_cli(option, check=False)
                        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                        self.assertIn("está en uso", result.stderr)
                self.run_cli("--clean")
                self.assertFalse(cached.exists())
            finally:
                process.terminate()

    def test_migrations_refuse_foreign_and_outward_relative_links(self):
        personal = self.mount_sgoinfre()
        outside = self.new_directory()
        cache = self.home / ".cache"
        cache.symlink_to(outside, target_is_directory=True)
        result = self.run_cli("--migrate-cache", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ya apunta a otro destino", result.stderr)
        self.assertEqual(Path(os.readlink(cache)), outside)
        settings = self.put(".vscode/settings.json")
        extensions = self.home / ".vscode/extensions"
        extensions.mkdir()
        inner = extensions / "proyecto"
        inner.symlink_to("../settings.json")
        result = self.run_cli("--migrate", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("apunta fuera", result.stderr)
        self.assertTrue(settings.is_file())
        self.assertEqual(os.readlink(inner), "../settings.json")
        self.assertEqual(list(personal.iterdir()), [])
        self.assert_preview_summary(self.run_cli("--dry-run").stdout, cleanup=0, migration=0)

    def test_failed_migration_restores_the_original_name_and_allows_retry(self):
        personal = self.mount_sgoinfre()
        relative = ".vscode/extensions/ejemplo.editor/package.json"
        original = self.put(relative, "original")
        self.stub("ln", "exit 1")
        result = self.run_cli("--migrate", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse((self.home / ".vscode/extensions").is_symlink())
        self.assertEqual(original.read_text(), "original")
        self.assertEqual((personal / relative).read_text(), "original")
        self.assertEqual(list((self.home / ".vscode").glob(".fclean-original.*")), [])
        (self.commands / "ln").unlink()
        self.run_cli("--migrate")
        self.assertTrue((self.home / ".vscode/extensions").is_symlink())

    def test_broken_managed_links_and_persistent_data(self):
        personal = self.mount_sgoinfre()
        self.stub("pgrep", "exit 0")
        disposable = (".cache", ".vscode/extensions", "vscode_config/Cache")
        sources = (".cache", ".vscode/extensions", ".config/Code/Cache", ".local/share")
        logical_sgo = self.home / "sgoinfre" / self.user
        for src, dst in zip(sources, (*disposable, ".local/share")):
            link = self.home / src
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(logical_sgo / dst, target_is_directory=True)
        preview = self.run_cli("--dry-run")
        self.assertIn("Se recrearía el destino vacío", preview.stdout)
        self.assertIn("datos persistentes sin destino", preview.stderr)
        self.assertEqual(list(personal.iterdir()), [])
        self.run_cli("--clean")
        for relative in disposable:
            self.assertEqual(list((personal / relative).iterdir()), [])
        self.assertTrue(all((self.home / source).is_symlink() for source in sources))
        self.assertFalse((personal / ".local/share").exists())

    def test_broken_relative_links_to_managed_destinations(self):
        personal = self.mount_sgoinfre()
        logical = self.home / "sgoinfre" / self.user
        for relative in (".cache", ".vscode/extensions"):
            source = self.home / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.symlink_to(os.path.relpath(logical / relative, source.parent))
        preview = self.run_cli("--dry-run").stdout
        for relative in (".cache", ".vscode/extensions"):
            self.assertIn(f"Se recrearía el destino vacío de {self.home / relative}", preview)
            self.assertFalse((personal / relative).exists())
        self.run_cli("--migrate-cache")
        self.run_cli("--migrate")
        for relative in (".cache", ".vscode/extensions"):
            source = self.home / relative
            self.assertEqual(os.readlink(source), os.path.relpath(logical / relative, source.parent))
            self.assertTrue(source.samefile(personal / relative))

    def test_untrusted_link_target_does_not_interrupt_other_repairs(self):
        personal = self.mount_sgoinfre()
        outside = self.new_directory() / "no-crear"
        (personal / ".cache").symlink_to(outside)
        logical = self.home / "sgoinfre" / self.user
        cache = self.home / ".cache"
        cache.symlink_to(logical / ".cache")
        code_cache = self.home / ".config/Code/Cache"
        code_cache.parent.mkdir(parents=True)
        code_cache.symlink_to(logical / "vscode_config/Cache")
        preview = self.run_cli("--dry-run")
        self.assertIn("Se omite la reparación", preview.stderr)
        self.assertIn(f"Se recrearía el destino vacío de {code_cache}", preview.stdout)
        self.assertFalse((personal / "vscode_config").exists())
        cleaned = self.run_cli("--clean")
        self.assertIn("Resumen de limpieza", cleaned.stdout)
        self.assertTrue(code_cache.is_dir())
        self.assertEqual(self.run_cli("--migrate-cache", check=False).returncode, 1)
        self.assertEqual(os.readlink(cache), str(logical / ".cache"))
        self.assertFalse(outside.exists())

    @unittest.skipUnless(shutil.which("zsh"), "requiere Zsh para probar el alias")
    def test_install_alias_autostart_reinstall_and_uninstall(self):
        config = self.home / "config con espacios, dólar $, comilla ' y barra \\"
        self.env["XDG_CONFIG_HOME"] = str(config)
        zshrc = self.home / ".zshrc"
        custom = self.put("zshrc-real", "export MI_VARIABLE=ejemplo\n")
        zshrc.symlink_to(custom.name)
        self.run_cli("--install")
        original_block = custom.read_text()
        self.assertTrue(zshrc.is_symlink())
        self.assertEqual(original_block.count("alias fclean="), 1)
        elsewhere = self.new_directory()
        help_text = self.run_command("zsh", "-ic", "fclean --help", cwd=elsewhere).stdout
        self.assertEqual(help_text, self.run_cli("--help").stdout)
        self.assertEqual(self.run_command("zsh", "-ic", "fclean", cwd=elsewhere).stdout, help_text)

        desktop = config / "autostart/42-fclean.desktop"
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(desktop.read_text())
        entry = parser["Desktop Entry"]
        workdir = Path(entry["Path"].replace("\\\\", "\\"))
        self.assertEqual(workdir, config / "fclean")
        self.assertEqual(entry["Terminal"], "false")
        cached = self.put(".cache/visible")
        self.run_command(*shlex.split(entry["Exec"]), cwd=workdir)
        self.assertFalse(cached.exists())

        backups = list(self.home.glob("zshrc-real.antes-de-fclean.*"))
        self.assertEqual(len(backups), 1)
        self.run_cli("--install")
        self.assertEqual(custom.read_text(), original_block)
        self.assertEqual(list(self.home.glob("zshrc-real.antes-de-fclean.*")), backups)
        cached = self.put(".cache/conservar")
        self.run_command("zsh", "-ic", "fclean --uninstall", cwd=elsewhere)
        manual_block = custom.read_text()
        backups = list(self.home.glob("zshrc-real.antes-de-fclean.*"))
        self.run_cli("--uninstall")
        self.assertFalse(desktop.exists())
        self.assertTrue(zshrc.is_symlink())
        self.assertTrue(manual_block.startswith("export MI_VARIABLE=ejemplo\n"))
        alias_line = next(line for line in original_block.splitlines() if line.startswith("alias fclean="))
        self.assertEqual(manual_block.count(alias_line), 1)
        self.assertNotIn("--prepare-env", manual_block)
        self.assertNotIn("XDG_CACHE_HOME", manual_block)
        self.assertEqual(custom.read_text(), manual_block)
        self.assertEqual(list(self.home.glob("zshrc-real.antes-de-fclean.*")), backups)
        self.assertEqual(self.run_command("zsh", "-ic", "fclean --help", cwd=elsewhere).stdout, help_text)
        self.assertTrue(cached.is_file())
        self.run_command("zsh", "-ic", "fclean --install", cwd=elsewhere)
        self.assertEqual(custom.read_text(), original_block)
        self.assertTrue(desktop.is_file())
        self.run_command("zsh", "-ic", "fclean --uninstall", cwd=elsewhere)
        self.assertEqual(custom.read_text(), manual_block)

    @unittest.skipUnless(shutil.which("zsh"), "requiere Zsh para comprobar el entorno")
    def test_uninstall_stops_sgoinfre_setup_but_keeps_the_alias(self):
        personal = self.mount_sgoinfre()
        self.run_cli("--install")
        names = ("SGO", "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR", "HF_HOME")
        probe = "printf '%s\\n' " + " ".join(f'"${{{name}:-sin-definir}}"' for name in names)
        self.assertNotIn("sin-definir", self.run_command("zsh", "-ic", probe).stdout)
        self.run_cli("--uninstall")
        shutil.rmtree(personal)
        self.assertEqual(self.run_command("zsh", "-ic", probe).stdout.splitlines(), ["sin-definir"] * len(names))
        self.assertIn("Uso:", self.run_command("zsh", "-ic", "fclean --help").stdout)
        self.assertFalse(personal.exists())

    def test_uninstall_without_installation_leaves_user_configuration_alone(self):
        self.run_cli("--uninstall")
        self.assertEqual(list(self.home.iterdir()), [])
        original = "export MI_VARIABLE=ejemplo\nalias fclean='echo personal'\n"
        zshrc = self.put(".zshrc", original)
        self.run_cli("--uninstall")
        self.assertEqual(zshrc.read_text(), original)
        self.assertEqual(list(self.home.glob(".zshrc.antes-de-fclean.*")), [])

    @unittest.skipUnless(shutil.which("zsh"), "requiere Zsh para comprobar el entorno de arranque")
    def test_zsh_uses_verified_mount_and_discards_stale_exports(self):
        personal = self.mount_sgoinfre()
        self.run_cli("--prepare-env")
        for path in (".cache/uv", ".cache/huggingface", ".local/share"):
            self.assertTrue((personal / path).is_dir())
        self.run_cli("--install")
        names = ("SGO", "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_CACHE_DIR", "HF_HOME")
        probe = "printf '%s\\n' " + " ".join(f'"${{{name}:-sin-definir}}"' for name in names)
        base = str(self.home / "sgoinfre" / self.user)
        exported = [base + suffix for suffix in ("", "/.cache", "/.local/share", "/.cache/uv", "/.cache/huggingface")]
        self.assertEqual(self.run_command("zsh", "-ic", probe).stdout.splitlines(), exported)
        self.env.update(zip(names, exported))
        (self.home / "sgoinfre").unlink()
        self.assertEqual(self.run_command("zsh", "-ic", probe).stdout.splitlines(), ["sin-definir"] * len(names))
        self.assertIn("Uso:", self.run_command("zsh", "-ic", "fclean --help").stdout)

    @unittest.skipUnless(shutil.which("zsh"), "requiere Zsh para probar ZDOTDIR")
    def test_install_and_uninstall_in_a_custom_zsh_configuration_directory(self):
        normal = self.put(".zshrc", "# configuración predeterminada\n")
        dotdir = self.home / "configuración de terminal"
        custom = dotdir / ".zshrc"
        self.env["ZDOTDIR"] = str(dotdir)
        self.run_cli("--install")
        self.assertIn("alias fclean=", custom.read_text())
        self.assertEqual(normal.read_text(), "# configuración predeterminada\n")
        self.assertIn("Uso:", self.run_command("zsh", "-ic", "fclean --help").stdout)
        self.run_command("zsh", "-ic", "fclean --install")
        self.assertEqual(custom.read_text().count("alias fclean="), 1)
        self.run_command("zsh", "-ic", "fclean --uninstall")
        self.assertEqual(custom.read_text().count("alias fclean="), 1)
        self.assertNotIn("--prepare-env", custom.read_text())
        self.assertIn("Uso:", self.run_command("zsh", "-ic", "fclean --help").stdout)
        self.assertEqual(normal.read_text(), "# configuración predeterminada\n")

    def test_uninstall_from_custom_zsh_directory_removes_the_old_default_block(self):
        normal = self.put(".zshrc", "# configuración predeterminada\n")
        self.run_cli("--install")
        self.assertIn("alias fclean=", normal.read_text())
        dotdir = self.home / ".config/zsh"
        self.env["ZDOTDIR"] = str(dotdir)
        self.run_cli("--install")
        self.run_cli("--uninstall")
        self.assertTrue(normal.read_text().startswith("# configuración predeterminada\n"))
        for file in (normal, dotdir / ".zshrc"):
            self.assertEqual(file.read_text().count("alias fclean="), 1)
            self.assertNotIn("--prepare-env", file.read_text())

    def test_autostart_escapes_tab_and_backslash_in_the_working_directory(self):
        config = self.home / "config\tcon tabulador y barra \\"
        self.env["XDG_CONFIG_HOME"] = str(config)
        self.run_cli("--install")
        desktop = config / "autostart/42-fclean.desktop"
        self.assertIn(f"Path={self.home}/config\\tcon tabulador y barra \\\\/fclean", desktop.read_text().splitlines())
        if shutil.which("desktop-file-validate"):
            self.run_command("desktop-file-validate", desktop)

    def test_install_upgrades_legacy_block_and_leaves_user_configuration(self):
        old = "# >>> 42-SGOINFRE-SHIELD >>>\nexport SGO=antiguo\n# <<< 42-SGOINFRE-SHIELD <<<\n"
        zshrc = self.put(".zshrc", "# antes\n" + old + "# después\n")
        self.run_cli("--install")
        updated = zshrc.read_text()
        self.assertTrue(updated.startswith("# antes\n"))
        self.assertTrue(updated.endswith("# después\n"))
        self.assertIn("alias fclean=", updated)
        self.assertNotIn("antiguo", updated)
        self.run_cli("--uninstall")
        manual = zshrc.read_text()
        self.assertTrue(manual.startswith("# antes\n"))
        self.assertTrue(manual.endswith("# después\n"))
        self.assertEqual(manual.count("alias fclean="), 1)
        self.assertNotIn("--prepare-env", manual)
        self.assertNotIn("antiguo", manual)

    def test_uninstall_disables_autostart_when_zshrc_target_is_missing(self):
        zshrc = self.home / ".zshrc"
        target = self.put("dotfiles/zshrc", "# configuración personal\n")
        zshrc.symlink_to(target)
        self.run_cli("--install")
        target.unlink()
        self.run_cli("--uninstall")
        self.assertTrue(zshrc.is_symlink())
        self.assertFalse((self.home / ".config/autostart/42-fclean.desktop").exists())

    def test_install_rejects_foreign_files_and_invalid_zsh_configuration(self):
        desktop = self.put(".config/autostart/42-fclean.desktop", "entrada ajena")
        for option in ("--install", "--uninstall", "--funinstall"):
            result = self.run_cli(option, check=False)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertEqual(desktop.read_text(), "entrada ajena")
        desktop.unlink()
        installed = self.put(".config/fclean/fclean", "archivo ajeno")
        self.assertEqual(self.run_cli("--install", check=False).returncode, 1)
        self.assertEqual(installed.read_text(), "archivo ajeno")
        installed.unlink()
        broken = "# >>> fclean: entorno sgoinfre >>>\n"
        zshrc = self.put(".zshrc", broken)
        result = self.run_cli("--install", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(zshrc.read_text(), broken)
        self.assertFalse(desktop.exists())
        self.assertFalse(installed.exists())

    def test_uninstall_preserves_venv_tracking_script_and_migrated_data(self):
        project = self.put("proyecto/requirements.txt", "").parent
        venv = project / ".venv"
        self.run_command(sys.executable, "-I", "-m", "venv", "--without-pip", venv)
        site = next(venv.glob("lib/python*/site-packages"))
        stamp = site / "fclean_venv_usage_v1.pth"
        self.assertIn("Se iniciaría el seguimiento", self.run_cli("--dry-run").stderr)
        self.assertFalse(stamp.exists())
        self.assertIn("Seguimiento iniciado", self.run_cli("--clean").stderr)
        self.assertTrue(stamp.is_file())
        personal = self.mount_sgoinfre()
        cached = self.put(".cache/archivo", base=personal)
        (self.home / ".cache").symlink_to(personal / ".cache", target_is_directory=True)
        self.run_cli("--install")
        installed = self.home / ".config/fclean/fclean"
        tracked = (stamp, site / "_fclean_venv_usage_v1.py", installed, cached)
        original = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino) for path in tracked}
        # Desinstalar ya no necesita Python ni recorre los venvs o los montajes.
        self.stub("python3", "exit 1")
        self.stub("findmnt", "exit 1")
        self.run_cli("--uninstall")
        self.assertEqual({path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
                          for path in tracked}, original)
        self.assertTrue((self.home / ".cache").is_symlink())
        self.assertTrue((venv / "bin/python").exists())
        self.assertFalse((self.home / ".config/autostart/42-fclean.desktop").exists())

    @unittest.skipUnless(shutil.which("zsh"), "requiere Zsh para desinstalar desde el alias")
    def test_funinstall_from_alias_removes_itself_tracking_backups_and_update_copies(self):
        config = self.home / "config con espacios, dólar $, comilla ' y barra \\"
        self.env["XDG_CONFIG_HOME"] = str(config)
        original_zshrc = "export MI_VARIABLE=ejemplo\n"
        actual_zshrc = self.put("dotfiles/zshrc", original_zshrc)
        (self.home / ".zshrc").symlink_to(actual_zshrc)
        self.run_cli("--install")
        installed = config / "fclean/fclean"
        project = self.put("proyecto/requirements.txt", "").parent
        venv = project / ".venv"
        self.run_command(sys.executable, "-I", "-m", "venv", "--without-pip", venv)
        site = next(venv.glob("lib/python*/site-packages"))
        self.run_command("bash", installed, "--clean")
        self.run_command(venv / "bin/python", "-I", "-c", "pass")
        self.assertTrue(list((site / "__pycache__").glob("_fclean_venv_usage_v1.*.pyc")))
        unrelated_pyc = self.put("__pycache__/mi_modulo.pyc", base=site)
        self.put("fclean.etag", 'hash\n"etag"\n', base=installed.parent)
        source = self.put("descarga/fclean", SCRIPT.read_text())
        self.run_command("bash", source, "--clean")
        self.assertTrue(list(self.runtime.glob("fclean-*")))
        self.assertTrue(list(actual_zshrc.parent.glob("zshrc.antes-de-fclean.*")))
        cached = self.put(".cache/conservar")
        personal = self.mount_sgoinfre()
        remote = self.put(".cache/datos", base=personal)
        elsewhere = self.new_directory()
        result = self.run_command("zsh", "-ic", "fclean --funinstall", cwd=elsewhere)
        self.assertIn("Instalación de fclean eliminada", result.stdout)
        self.assertFalse(installed.parent.exists())
        self.assertFalse((config / "autostart/42-fclean.desktop").exists())
        self.assertEqual(actual_zshrc.read_text(), original_zshrc)
        self.assertTrue((self.home / ".zshrc").is_symlink())
        self.assertEqual(list(actual_zshrc.parent.glob("zshrc.antes-de-fclean.*")), [])
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertFalse((site / "fclean_venv_usage_v1.pth").exists())
        self.assertFalse((site / "_fclean_venv_usage_v1.py").exists())
        self.assertEqual(list((site / "__pycache__").iterdir()), [unrelated_pyc])
        self.assertTrue((venv / "bin/python").exists())
        self.assertTrue(cached.is_file())
        self.assertTrue(remote.is_file())
        self.assertEqual(source.read_text(), SCRIPT.read_text())
        self.assertNotEqual(self.run_command("zsh", "-ic", "alias fclean", check=False).returncode, 0)
        self.run_cli("--funinstall")
        self.assertFalse(installed.parent.exists())
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_funinstall_after_uninstall_removes_aliases_in_both_zsh_locations(self):
        normal = self.put(".zshrc", "# predeterminada\n")
        self.run_cli("--install")
        dotdir = self.home / ".config/zsh"
        custom = self.put(".config/zsh/.zshrc", "# personalizada\n")
        self.env["ZDOTDIR"] = str(dotdir)
        self.run_cli("--install")
        self.run_cli("--uninstall")
        self.assertIn("alias fclean=", custom.read_text())
        installed = self.home / ".config/fclean/fclean"
        self.run_command("bash", installed, "--funinstall")
        self.assertEqual(normal.read_text(), "# predeterminada\n")
        self.assertEqual(custom.read_text(), "# personalizada\n")
        self.assertEqual(list(self.home.rglob("*.antes-de-fclean.*")), [])
        self.assertFalse(installed.parent.exists())

    def test_funinstall_removes_legacy_configuration_without_an_installation(self):
        original = "# antes\n# después\n"
        block = "# >>> 42-SGOINFRE-SHIELD >>>\nexport SGO=antiguo\n# <<< 42-SGOINFRE-SHIELD <<<\n"
        zshrc = self.put(".zshrc", "# antes\n" + block + "# después\n")
        self.run_cli("--funinstall")
        self.assertEqual(zshrc.read_text(), original)
        self.assertEqual(list(self.home.iterdir()), [zshrc])
        self.assertEqual(list(self.runtime.iterdir()), [])

    def test_funinstall_removes_tracking_from_a_venv_moved_to_sgoinfre(self):
        personal = self.mount_sgoinfre()
        project = self.put("proyecto/requirements.txt", "").parent
        venv = project / ".venv"
        self.run_command(sys.executable, "-I", "-m", "venv", "--without-pip", venv)
        self.run_cli("--clean")
        moved = personal / "entorno"
        shutil.move(venv, moved)
        venv.symlink_to(moved, target_is_directory=True)
        stamp = next(moved.glob("lib/python*/site-packages/fclean_venv_usage_v1.pth"))
        self.run_cli("--funinstall")
        self.assertFalse(stamp.exists())
        self.assertFalse((stamp.parent / "_fclean_venv_usage_v1.py").exists())
        self.assertTrue(venv.is_symlink())
        self.assertTrue((venv / "bin/python").exists())

    def test_funinstall_refuses_foreign_files_in_the_installation_before_changing_config(self):
        self.run_cli("--install")
        zshrc = self.home / ".zshrc"
        original = zshrc.read_text()
        foreign = self.put(".config/fclean/mis-datos", "conservar")
        result = self.run_cli("--funinstall", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("contenido inesperado", result.stderr)
        self.assertEqual(foreign.read_text(), "conservar")
        self.assertEqual(zshrc.read_text(), original)
        self.assertTrue((foreign.parent / "fclean").is_file())
        self.assertTrue((self.home / ".config/autostart/42-fclean.desktop").is_file())

    def test_funinstall_refuses_a_symlinked_installation(self):
        outside = self.new_directory()
        source = self.put("fclean", SCRIPT.read_text(), base=outside)
        config = self.home / ".config"
        config.mkdir()
        (config / "fclean").symlink_to(outside, target_is_directory=True)
        result = self.run_cli("--funinstall", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(source.read_text(), SCRIPT.read_text())
        self.assertTrue((config / "fclean").is_symlink())

    def test_funinstall_refuses_broken_configuration_and_keeps_the_installed_script(self):
        self.run_cli("--install")
        zshrc = self.put(".zshrc", "# >>> fclean: entorno sgoinfre >>>\n")
        original = zshrc.read_text()
        result = self.run_cli("--funinstall", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(zshrc.read_text(), original)
        self.assertTrue((self.home / ".config/fclean/fclean").is_file())
        self.assertTrue((self.home / ".config/autostart/42-fclean.desktop").is_file())

    def test_funinstall_cleans_interrupted_installation_files_without_following_links(self):
        self.run_cli("--install")
        temporary = (
            ".config/fclean/.instalacion.ABC123ab",
            ".config/fclean/.fclean-update.ABC123ab/script",
            ".config/autostart/.42-fclean.ABC123ab",
            ".zshrc.fclean.ABC123ab",
        )
        for relative in temporary:
            self.put(relative, "copia interrumpida")
        self.put(".fclean-initial.ABC123ab", "copia interrumpida", base=self.runtime)
        self.put(".fclean-update.ABC123ab/state", "estado", base=self.runtime)
        outside = self.put("conservar")
        stage_script = self.home / temporary[1]
        stage_script.unlink()
        stage_script.symlink_to(outside)
        result = self.run_cli("--funinstall", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue((self.home / ".config/fclean/fclean").is_file())
        self.assertEqual(outside.read_text(), "datos")
        stage_script.unlink()
        self.put(temporary[1], "copia interrumpida")
        self.run_cli("--funinstall")
        self.assertTrue(all(not (self.home / relative).exists() for relative in temporary))
        self.assertEqual(list(self.runtime.iterdir()), [])
        self.assertEqual(outside.read_text(), "datos")

    def test_funinstall_keeps_the_script_when_tracking_cannot_be_removed(self):
        project = self.put("proyecto/requirements.txt", "").parent
        venv = project / ".venv"
        self.run_command(sys.executable, "-I", "-m", "venv", "--without-pip", venv)
        self.run_cli("--clean")
        self.run_cli("--install")
        stamp = next(venv.glob("lib/python*/site-packages/fclean_venv_usage_v1.pth"))
        original = stamp.read_text()
        stamp.write_text(original + "# cambio personal\n")
        result = self.run_cli("--funinstall", check=False)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Se conserva la instalación", result.stderr)
        installed = self.home / ".config/fclean/fclean"
        self.assertTrue(installed.is_file())
        self.assertEqual(stamp.read_text(), original + "# cambio personal\n")
        stamp.write_text(original)
        self.run_command("bash", installed, "--funinstall")
        self.assertFalse(installed.parent.exists())
        self.assertFalse(stamp.exists())
        self.assertTrue((venv / "bin/python").exists())

    def test_mutating_actions_respect_the_session_lock(self):
        import fcntl

        cached = self.put(".cache/visible")
        with (self.runtime / "42-fclean.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for option in ("--clean", "--fclean", "--migrate", "--migrate-cache", "--install", "--uninstall", "--funinstall"):
                with self.subTest(option=option):
                    result = self.run_cli(option, check=False)
                    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                    self.assertIn("bloqueo", result.stderr)
            self.assertIn("Contenido que se eliminaría", self.run_cli("--dry-run").stdout)
        self.assertTrue(cached.is_file())


if __name__ == "__main__":
    unittest.main()
