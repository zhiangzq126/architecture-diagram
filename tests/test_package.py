"""Offline temporary fixtures; never execute fixture package code."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "package.py"
SPEC = importlib.util.spec_from_file_location("architecture_package_under_test", SCRIPT)
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="architecture-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.sandbox = Path(self.temporary.name).resolve()
        self.root = self.sandbox / "source-with-another-name"
        self.root.mkdir()
        self.write("SKILL.md", b"---\nname: architecture-diagram\n---\nFixture only.\n")
        self.write("assets/example.txt", b"example asset\n")
        self.write("scripts/untrusted.py", b"raise RuntimeError('must never execute')\n")
        self.manifest = {
            "skill": "architecture-diagram",
            "version": "0.1.0",
            "distribution": " local-private; licenses require review; 不得自动发布 \n",
            "inventory": {
                "algorithm": "SHA-256 of each installed file; excludes manifest.json itself",
                "files": {},
            },
        }
        for relative in ("SKILL.md", "assets/example.txt", "scripts/untrusted.py"):
            self.register(relative)
        self.save_manifest()

    def write(self, relative, content=b"test"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def register(self, relative):
        content = (self.root / relative).read_bytes()
        self.manifest["inventory"]["files"][relative] = {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}

    def save_manifest(self):
        (self.root / "manifest.json").write_text(
            json.dumps(self.manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def cli(self, *args, success=True, root=None, script=SCRIPT, add_root=True):
        command = [sys.executable, "-B", str(script), *map(str, args)]
        if add_root:
            command += ["--root", str(root or self.root)]
        process = subprocess.run(command, cwd=self.sandbox, capture_output=True,
                                 text=True, timeout=20)
        self.assertEqual(process.stderr, "", process.stderr)
        self.assertEqual(len(process.stdout.splitlines()), 1, process.stdout)
        result = json.loads(process.stdout)
        self.assert_result(result, success)
        if success:
            self.assertEqual(process.returncode, 0, result)
        else:
            self.assertNotEqual(process.returncode, 0, result)
        return result

    def assert_result(self, result, success):
        for key in ("ok", "version", "distribution", "sha256", "bytes", "files", "diagnostic"):
            self.assertIn(key, result)
        self.assertEqual(result["ok"], success, result)
        self.assertIsInstance(result["diagnostic"], str)
        self.assertLessEqual(len(result["diagnostic"]), PACKAGE.DIAGNOSTIC_LIMIT)
        if not success:
            self.assertIsNone(result["sha256"])
            self.assertIsNone(result["bytes"])
            self.assertIsNone(result["files"])

    def failed_verify(self, phrase):
        result = self.cli("verify", success=False)
        self.assertIn(phrase, result["diagnostic"])
        return result

    def in_process_build(self, output):
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            status = PACKAGE.main(["build", "--root", str(self.root), "--output", str(output)])
        result = json.loads(capture.getvalue())
        self.assertNotEqual(status, 0, result)
        self.assert_result(result, False)
        return result

    def snapshot(self):
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mode,
                                                  path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file()}

    def test_verify_passes_and_is_read_only(self):
        before = self.snapshot()
        result = self.cli("verify")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(result["version"], "0.1.0")
        self.assertEqual(result["distribution"], self.manifest["distribution"])
        self.assertEqual(result["files"], 4)
        self.assertEqual(result["bytes"], sum(len(record[0]) for record in before.values()))
        self.assertEqual(result["hash_scope"], "manifest.json")
        self.assertEqual(result["sha256"], hashlib.sha256(before["manifest.json"][0]).hexdigest())
        self.assertIn("not a signature", result["diagnostic"])
        self.assertFalse(list(self.root.rglob("__pycache__")))

    def test_same_length_byte_change_fails_hash(self):
        self.write("assets/example.txt", b"Example asset\n")
        self.failed_verify("sha256 mismatch")

    def test_length_change_fails_bytes(self):
        self.write("assets/example.txt", b"longer changed asset\n")
        self.failed_verify("bytes mismatch")

    def test_missing_file(self):
        (self.root / "assets/example.txt").unlink()
        self.failed_verify("missing files")

    def test_missing_manifest(self):
        (self.root / "manifest.json").unlink()
        self.failed_verify("FileNotFoundError")

    def test_extra_file(self):
        self.write("extra.txt")
        self.failed_verify("unregistered files")

    def test_hidden_unregistered_file_is_not_ignored(self):
        self.write(".git/config")
        self.failed_verify("unregistered files")

    def test_bad_hash_schema(self):
        for value in (None, 12, "0" * 63, "g" * 64, "0" * 64 + "\n"):
            with self.subTest(value=value):
                self.manifest["inventory"]["files"]["SKILL.md"]["sha256"] = value
                self.save_manifest()
                self.failed_verify("invalid sha256")

    def test_wrong_valid_hash(self):
        self.manifest["inventory"]["files"]["SKILL.md"]["sha256"] = "0" * 64
        self.save_manifest()
        self.failed_verify("sha256 mismatch")

    def test_bad_bytes_schema(self):
        for value in (None, True, -1, "12", 12.0):
            with self.subTest(value=value):
                self.manifest["inventory"]["files"]["SKILL.md"]["bytes"] = value
                self.save_manifest()
                self.failed_verify("invalid bytes")

    def test_wrong_declared_bytes(self):
        self.manifest["inventory"]["files"]["SKILL.md"]["bytes"] += 1
        self.save_manifest()
        self.failed_verify("bytes mismatch")

    def test_malicious_inventory_paths(self):
        paths = ("../outside", "/tmp/outside", "a/../../outside", "a/../b", "./SKILL.md",
                 "a//b", "a/", "", "C:/outside", "C:\\outside", "a\\..\\b",
                 "a\x00b", "a\nb", "//host/share", "a./b", "a /b", "a:b")
        for path in paths:
            with self.subTest(path=path):
                self.manifest["inventory"]["files"][path] = {"sha256": "0" * 64, "bytes": 0}
                self.save_manifest()
                self.cli("verify", success=False)
                del self.manifest["inventory"]["files"][path]

    def test_excluded_paths_cannot_be_registered(self):
        for path in (".venv/config", "__pycache__/cached.pyc", "assets/.DS_Store",
                     "assets/.venv/config"):
            with self.subTest(path=path):
                self.manifest["inventory"]["files"][path] = {"sha256": "0" * 64, "bytes": 0}
                self.save_manifest()
                self.failed_verify("excluded runtime/cache path")
                del self.manifest["inventory"]["files"][path]

    def test_manifest_cannot_register_itself(self):
        self.register("manifest.json")
        self.save_manifest()
        self.failed_verify("exclude manifest.json itself")

    def test_missing_root_entry(self):
        del self.manifest["inventory"]["files"]["SKILL.md"]
        self.save_manifest()
        self.failed_verify("unique root SKILL.md")

    def test_missing_root_skill_file(self):
        (self.root / "SKILL.md").unlink()
        self.failed_verify("missing files")

    def test_nested_registered_skill_entry(self):
        self.write("nested/SKILL.md")
        self.register("nested/SKILL.md")
        self.save_manifest()
        self.failed_verify("duplicate/non-root SKILL.md")

    def test_nested_unregistered_skill_entry(self):
        self.write("nested/SKILL.md")
        self.failed_verify("duplicate/non-root SKILL.md")

    def test_case_variant_skill_entry(self):
        self.manifest["inventory"]["files"]["skill.md"] = {"sha256": "0" * 64, "bytes": 0}
        self.save_manifest()
        self.failed_verify("duplicate/non-root SKILL.md")

    def test_duplicate_json_keys(self):
        for raw in ('{"version":"0.1.0","version":"9.9.9"}',
                    '{"inventory":{"files":{"SKILL.md":{},"SKILL.md":{}}}}'):
            with self.subTest(raw=raw):
                self.write("manifest.json", raw.encode())
                self.failed_verify("duplicate JSON key")

    def test_case_colliding_inventory_names(self):
        self.manifest["inventory"]["files"]["ASSETS/EXAMPLE.TXT"] = {
            "sha256": "0" * 64, "bytes": 0}
        self.save_manifest()
        self.failed_verify("case-colliding")

    def test_manifest_case_variant_is_rejected_before_file_scan(self):
        self.manifest["inventory"]["files"]["Manifest.json"] = {
            "sha256": "0" * 64, "bytes": 0}
        self.save_manifest()
        self.failed_verify("case-colliding")

    def test_registered_file_symlink(self):
        external = self.sandbox / "external.txt"
        external.write_bytes((self.root / "assets/example.txt").read_bytes())
        (self.root / "assets/example.txt").unlink()
        (self.root / "assets/example.txt").symlink_to(external)
        self.failed_verify("symlink")

    def test_directory_symlink(self):
        external = self.sandbox / "external-assets"
        (self.root / "assets").rename(external)
        (self.root / "assets").symlink_to(external, target_is_directory=True)
        self.failed_verify("symlink")

    def test_dangling_unregistered_symlink(self):
        (self.root / "dangling").symlink_to(self.sandbox / "missing")
        self.failed_verify("symlink")

    def test_manifest_symlink(self):
        external = self.sandbox / "external-manifest.json"
        (self.root / "manifest.json").rename(external)
        (self.root / "manifest.json").symlink_to(external)
        self.cli("verify", success=False)

    def test_root_symlink(self):
        alias = self.sandbox / "source-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        result = self.cli("verify", root=alias, success=False)
        self.assertIn("root must not be a symlink", result["diagnostic"])

    def test_real_venv_with_runtime_symlinks_is_excluded(self):
        self.write(".venv/pyvenv.cfg")
        self.write(".venv/SKILL.md", b"not a package entry")
        (self.root / ".venv/python").symlink_to(sys.executable)
        (self.root / ".venv/dangling").symlink_to(self.sandbox / "missing")
        result = self.cli("verify")
        self.assertEqual(result["files"], 4)
        output = self.sandbox / "runtime-excluded.zip"
        self.cli("build", "--output", output)
        with zipfile.ZipFile(output) as archive:
            self.assertFalse(any(".venv" in member for member in archive.namelist()))

    def test_venv_itself_must_not_be_a_symlink(self):
        external = self.sandbox / "external-venv"
        external.mkdir()
        for target in (external, self.sandbox / "missing"):
            with self.subTest(target=target):
                (self.root / ".venv").symlink_to(target, target_is_directory=True)
                self.failed_verify("symlink")
                (self.root / ".venv").unlink()

    def test_venv_regular_file_is_rejected(self):
        self.write(".venv")
        self.failed_verify("real root .venv directory")

    def test_nested_venv_is_not_silently_ignored(self):
        self.write("assets/.venv/config")
        self.failed_verify("real root .venv directory")

    def test_cache_and_ds_store_are_ignored(self):
        self.write("__pycache__/module.pyc")
        self.write("assets/__pycache__/nested/module.pyc")
        self.write(".DS_Store")
        self.write("assets/.DS_Store")
        self.cli("verify")
        output = self.sandbox / "without-cache.zip"
        self.cli("build", "--output", output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(len(archive.namelist()), 4)

    def test_symlinks_in_ignored_cache_are_rejected(self):
        self.write("__pycache__/module.pyc")
        (self.root / "__pycache__/escape").symlink_to(self.sandbox / "missing")
        self.failed_verify("symlink")

    def test_ignored_entry_symlinks_are_rejected(self):
        for relative in (".DS_Store", "__pycache__"):
            with self.subTest(relative=relative):
                (self.root / relative).symlink_to(self.sandbox / "missing")
                self.failed_verify("symlink")
                (self.root / relative).unlink()

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires POSIX FIFO")
    def test_special_files_are_rejected_without_blocking(self):
        os.mkfifo(self.root / "pipe")
        self.failed_verify("not a regular file/directory")

    def test_package_code_is_never_executed(self):
        marker = self.sandbox / "must-not-exist"
        self.write("setup.py", ("from pathlib import Path\nPath(%r).touch()\n" % str(marker)).encode())
        self.register("setup.py")
        self.save_manifest()
        self.cli("verify")
        self.cli("build", "--output", self.sandbox / "passive.zip")
        self.assertFalse(marker.exists())

    def test_build_is_byte_reproducible_and_read_only(self):
        before = self.snapshot()
        first, second = self.sandbox / "first.zip", self.sandbox / "second.zip"
        result1 = self.cli("build", "--output", first)
        result2 = self.cli("build", "--output", second)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(result1["sha256"], result2["sha256"])
        self.assertEqual(result1["sha256"], hashlib.sha256(first.read_bytes()).hexdigest())
        self.assertEqual(result1["bytes"], first.stat().st_size)
        self.assertEqual(result1["files"], 4)
        self.assertEqual(result1["hash_scope"], "archive")
        self.assertEqual(result1["distribution"], self.manifest["distribution"])
        self.assertEqual(before, self.snapshot())

    def test_build_ignores_source_timestamps_and_modes(self):
        first, second = self.sandbox / "before.zip", self.sandbox / "after.zip"
        self.cli("build", "--output", first)
        for path in self.root.rglob("*"):
            if path.is_file():
                os.chmod(path, 0o700)
                os.utime(path, (1700000000, 1700000000))
        self.cli("build", "--output", second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_zip_members_and_metadata(self):
        self.write("assets/空白 file.txt", b"")
        self.register("assets/空白 file.txt")
        self.save_manifest()
        output = self.sandbox / "content.zip"
        self.cli("build", "--output", output)
        records = self.manifest["inventory"]["files"]
        expected = ["architecture-diagram/" + path for path in sorted([*records, "manifest.json"])]
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), expected)
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.comment, b"")
            for member in archive.infolist():
                relative = member.filename.removeprefix("architecture-diagram/")
                self.assertEqual(archive.read(member), (self.root / relative).read_bytes())
                self.assertEqual(member.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(member.create_system, 3)
                self.assertEqual(member.external_attr >> 16, stat.S_IFREG | 0o644)
                self.assertEqual(member.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(member.extra, b"")
                self.assertEqual(member.comment, b"")

    def test_archive_can_be_verified_after_restore_without_switching(self):
        output = self.sandbox / "rollback-base.zip"
        self.cli("build", "--output", output)
        destination = self.sandbox / "restore-fixture"
        destination.mkdir()
        with zipfile.ZipFile(output) as archive:
            archive.extractall(destination)
        restored = self.cli("verify", root=destination / "architecture-diagram")
        original = self.cli("verify")
        self.assertEqual(restored["sha256"], original["sha256"])
        self.assertEqual(restored["version"], original["version"])

    def test_existing_output_file_is_preserved(self):
        output = self.sandbox / "existing.zip"
        output.write_bytes(b"existing release")
        result = self.cli("build", "--output", output, success=False)
        self.assertIn("refusing overwrite", result["diagnostic"])
        self.assertEqual(output.read_bytes(), b"existing release")

    def test_existing_output_directory_is_preserved(self):
        output = self.sandbox / "existing.zip"
        output.mkdir()
        self.cli("build", "--output", output, success=False)
        self.assertTrue(output.is_dir())

    def test_existing_output_symlinks_are_preserved(self):
        target = self.sandbox / "target.zip"
        target.write_bytes(b"old archive")
        for destination in (target, self.sandbox / "missing.zip"):
            with self.subTest(destination=destination):
                output = self.sandbox / "link.zip"
                output.symlink_to(destination)
                self.cli("build", "--output", output, success=False)
                self.assertTrue(output.is_symlink())
                self.assertEqual(output.readlink(), destination)
                self.assertEqual(target.read_bytes(), b"old archive")
                output.unlink()

    def test_output_inside_source_is_rejected(self):
        output = self.root / "assets" / "release.zip"
        result = self.cli("build", "--output", output, success=False)
        self.assertIn("outside the source", result["diagnostic"])
        self.assertFalse(output.exists())

    def test_output_parent_alias_to_source_is_rejected(self):
        alias = self.sandbox / "alias"
        alias.symlink_to(self.root / "assets", target_is_directory=True)
        result = self.cli("build", "--output", alias / "release.zip", success=False)
        self.assertIn("outside the source", result["diagnostic"])
        self.assertFalse((self.root / "assets/release.zip").exists())

    def test_output_parent_must_already_exist(self):
        output = self.sandbox / "missing-parent" / "release.zip"
        self.cli("build", "--output", output, success=False)
        self.assertFalse(output.parent.exists())

    def test_relative_output_is_rejected(self):
        result = self.cli("build", "--output", "relative.zip", success=False)
        self.assertIn("absolute path", result["diagnostic"])
        self.assertFalse((self.sandbox / "relative.zip").exists())

    def test_build_verifies_before_creating_output(self):
        self.write("unexpected.txt")
        output = self.sandbox / "must-not-exist.zip"
        self.cli("build", "--output", output, success=False)
        self.assertFalse(output.exists())

    def test_changed_file_during_build_cleans_partial_output(self):
        original = PACKAGE._consume
        output = self.sandbox / "changed.zip"

        def changing(source, relative, expected=None, destination=None):
            if destination is not None and relative == "assets/example.txt":
                self.write(relative, b"Example asset\n")
            return original(source, relative, expected, destination)

        with mock.patch.object(PACKAGE, "_consume", side_effect=changing):
            result = self.in_process_build(output)
        self.assertIn("sha256 mismatch", result["diagnostic"])
        self.assertFalse(output.exists())

    def test_manifest_change_during_build_cleans_partial_output(self):
        original = PACKAGE._check_set
        output = self.sandbox / "changed-manifest.zip"
        calls = 0

        def changing(source, records):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.manifest["version"] = "0.2.0"
                self.save_manifest()
            return original(source, records)

        with mock.patch.object(PACKAGE, "_check_set", side_effect=changing):
            result = self.in_process_build(output)
        self.assertIn("mismatch", result["diagnostic"])
        self.assertFalse(output.exists())

    def test_extra_file_during_build_cleans_partial_output(self):
        original = PACKAGE._check_set
        output = self.sandbox / "extra-during-build.zip"
        calls = 0

        def changing(source, records):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.write("extra-after-verify.txt")
            return original(source, records)

        with mock.patch.object(PACKAGE, "_check_set", side_effect=changing):
            result = self.in_process_build(output)
        self.assertIn("unregistered files", result["diagnostic"])
        self.assertFalse(output.exists())

    def test_output_created_by_competitor_is_not_overwritten(self):
        original = PACKAGE._output_path
        output = self.sandbox / "competing.zip"

        def competing(source, requested):
            resolved = original(source, requested)
            resolved.write_bytes(b"competitor output")
            return resolved

        with mock.patch.object(PACKAGE, "_output_path", side_effect=competing):
            self.in_process_build(output)
        self.assertEqual(output.read_bytes(), b"competitor output")

    def test_write_error_cleans_partial_output(self):
        output = self.sandbox / "write-error.zip"
        with mock.patch.object(PACKAGE.zipfile.ZipFile, "open", side_effect=OSError("write failed")):
            result = self.in_process_build(output)
        self.assertIn("write failed", result["diagnostic"])
        self.assertFalse(output.exists())

    def test_invalid_json_and_nonfinite_values(self):
        for content in (b"{", b"[]", b"null", b"\xff", b'{"version": NaN}',
                        b'{"version": Infinity}'):
            with self.subTest(content=content):
                self.write("manifest.json", content)
                self.cli("verify", success=False)

    def test_invalid_manifest_schema(self):
        original = json.dumps(self.manifest)
        mutations = (("skill", "another-skill"), ("version", ""), ("version", 1),
                     ("distribution", None), ("inventory", []), ("inventory", {"files": []}),
                     ("inventory", {"files": {"SKILL.md": []}}))
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                self.manifest = json.loads(original)
                self.manifest[key] = value
                self.save_manifest()
                self.cli("verify", success=False)

    def test_cli_usage_errors_are_json_and_nonzero(self):
        for arguments in ((), ("unknown",), ("refresh",), ("build",), ("verify", "--unknown")):
            with self.subTest(arguments=arguments):
                self.cli(*arguments, success=False, add_root=False)

    def test_help_is_json(self):
        result = self.cli("--help", add_root=False)
        self.assertIn("verify", result["diagnostic"])
        self.assertIn("build", result["diagnostic"])

    def test_missing_root_is_json_error(self):
        self.cli("verify", root=self.sandbox / "missing-root", success=False)

    def test_diagnostics_truncate_file_lists_clearly(self):
        for index in range(30):
            self.write("extra-%03d.txt" % index)
        result = self.failed_verify("truncated; 20 more paths omitted")
        self.assertNotIn("extra-029", result["diagnostic"])

    def test_long_diagnostic_is_bounded(self):
        result = self.cli("verify", "--" + "x" * 5000, success=False)
        self.assertIn("truncated; original characters:", result["diagnostic"])
        self.assertEqual(len(result["diagnostic"]), PACKAGE.DIAGNOSTIC_LIMIT)

    def test_default_root_is_relative_to_script_not_cwd(self):
        local_script = self.root / "scripts/package.py"
        shutil.copyfile(SCRIPT, local_script)
        self.register("scripts/package.py")
        self.save_manifest()
        self.cli("verify", script=local_script, add_root=False)


if __name__ == "__main__":
    unittest.main()
