"""All external processes/filesystem mutations are mocked; no real installs.

Run: python3 -B tests/test_environment.py
"""

import ast
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/environment.py"
SPEC = importlib.util.spec_from_file_location("diagram_environment", MODULE_PATH)
environment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(environment)


def completed(data=None, code=0, stderr=""):
    return subprocess.CompletedProcess([], code, json.dumps(data or {}), stderr)


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        # Fail closed: no test may inadvertently spawn a real installer/probe.
        self.run = self.enter_patch(mock.patch.object(environment.subprocess, "run"))
        self.run.side_effect = AssertionError("Unexpected external process")
        self.enter_patch(mock.patch.dict(environment.os.environ, {}, clear=True))
        self.enter_patch(mock.patch.object(environment.platform, "system", return_value="Darwin"))
        self.enter_patch(mock.patch.object(environment.sys, "executable", "/runtime/python"))
        self.enter_patch(mock.patch.object(environment.sys, "version_info", (3, 11, 9)))
        self.paths = {"/runtime/python"}
        self.enter_patch(mock.patch.object(environment, "_executable", side_effect=lambda path: bool(path) and str(path) in self.paths))
        self.which = self.enter_patch(mock.patch.object(environment.shutil, "which", return_value=None))
        self.enter_patch(mock.patch.object(environment.Path, "exists", return_value=False))
        self.enter_patch(mock.patch.object(environment.Path, "is_symlink", return_value=False))
        self.enter_patch(mock.patch.object(environment.Path, "read_bytes", side_effect=FileNotFoundError))
        self.enter_patch(mock.patch.object(environment.Path, "read_text", side_effect=FileNotFoundError))
        self.enter_patch(mock.patch.object(environment.Path, "glob", return_value=[]))
        self.enter_patch(mock.patch.object(environment.Path, "is_file", return_value=False))
        self.enter_patch(mock.patch.object(environment.os, "geteuid", return_value=1000, create=True))
        self.enter_patch(mock.patch.object(environment.os, "mkdir", side_effect=AssertionError("No directories may be created")))
        self.enter_patch(mock.patch.object(environment.Path, "write_text", side_effect=AssertionError("No files may be written")))
        self.python_data = {"version": "3.11.9"}
        self.node_data = {"version": "22.1.0"}
        self.pillow_data = {"version": "10.4.0", "ok": True}
        self.font_data = {"ok": True, "path": "/fonts/cjk.ttc"}

    def enter_patch(self, patch):
        result = patch.start()
        self.addCleanup(patch.stop)
        return result

    def runtime_mocks(self, node=True, fonts=True):
        if node:
            self.paths.add("/runtime/node")
            self.which.side_effect = lambda name: "/runtime/node" if name == "node" else None
        if fonts:
            self.enter_patch(mock.patch.object(environment, "_font_candidates", return_value=["/fonts/cjk.ttc"]))

        def run(command, **kwargs):
            self.assertIs(kwargs["shell"], False)
            self.assertTrue(kwargs["capture_output"])
            self.assertLessEqual(kwargs["timeout"], environment.INSTALL_TIMEOUT)
            if environment._PYTHON_PROBE in command:
                self.assertIn("-B", command)
                return completed(self.python_data)
            if environment._NODE_PROBE in command:
                return completed(self.node_data)
            if str(environment.SKILL_ROOT / 'scripts/topology_snapshot.mjs') in command:
                return completed({'ok': True, 'path': '/runtime/chrome'})
            if environment._PILLOW_PROBE in command:
                self.assertIn("-B", command)
                return completed(self.pillow_data)
            if environment._FONT_PROBE in command:
                self.assertEqual(json.loads(kwargs["input"]), ["/fonts/cjk.ttc"])
                return completed(self.font_data)
            raise AssertionError("Unexpected command: {!r}".format(command))
        self.run.side_effect = run

    @staticmethod
    def checks(report):
        return {check["id"]: check for check in report["checks"]}

    def test_icons_only_checks_python_and_no_browser(self):
        self.runtime_mocks(node=False)
        report = environment.doctor("icons")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(list(self.checks(report)), ["python"])
        self.assertEqual(report["executables"], {"node": None, "python": "/runtime/python", "drawio": None})
        self.assertEqual(report["manual"], [])
        self.which.assert_not_called()
        self.assertEqual(self.run.call_count, 1)

    def test_archify_no_npm_drawio_pillow_or_browser_execution(self):
        self.runtime_mocks()
        report = environment.doctor("archify")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(set(self.checks(report)), {"python", "node"})
        self.assertEqual(report["manual"][0]["status"], "manual")
        self.assertEqual(report["manual"][0]["id"], "browser")
        self.assertEqual(self.which.call_args_list, [mock.call("node")])
        self.assertEqual(self.run.call_count, 2)
        for check in report["checks"]:
            self.assertTrue({"id", "required", "status", "version", "path", "purpose", "install_hint"} <= check.keys())
            self.assertIn(check["status"], ("ready", "blocked"))
        json.dumps(report)

    def test_missing_node_blocks_archify_not_icons(self):
        self.runtime_mocks(node=False)
        report = environment.doctor("archify")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.checks(report)["node"]["status"], "blocked")
        self.assertIsNone(report["executables"]["node"])
        self.assertEqual(environment.doctor("icons")["status"], "ready")

    def test_node_version_boundaries_and_malformed_output(self):
        self.runtime_mocks()
        for version, expected in (("17.9.1", "blocked"), ("18.0.0", "ready"), ("20.1.0", "ready"),
                                  (None, "blocked"), ("not-a-version", "blocked"), ("18.0.0-rc.1", "blocked")):
            with self.subTest(version=version):
                self.node_data = {"version": version}
                self.assertEqual(self.checks(environment.doctor("archify"))["node"]["status"], expected)

    def test_python_version_and_missing_executable(self):
        self.runtime_mocks()
        for version, expected in (("3.8.20", "blocked"), ("3.9.0", "ready"), ("3.12.2", "ready")):
            with self.subTest(version=version):
                self.python_data = {"version": version}
                self.assertEqual(environment.doctor("icons")["status"], expected)
        self.paths.clear()
        self.run.reset_mock()
        self.assertEqual(environment.doctor("icons")["status"], "blocked")
        self.run.assert_not_called()

    def test_old_entry_python_cannot_be_hidden_by_new_venv(self):
        self.runtime_mocks()
        with mock.patch.object(environment.sys, "version_info", (3, 8, 10)):
            self.assertEqual(environment.doctor("gif")["status"], "blocked")
        self.assertEqual(self.run.call_count, 1)

    def test_drawio_missing_is_required_only_for_preview(self):
        self.runtime_mocks()
        no_preview = environment.doctor("drawio", require_preview=False)
        self.assertEqual(no_preview["status"], "ready")
        self.assertFalse(self.checks(no_preview)["drawio"]["required"])
        self.assertEqual(self.checks(no_preview)["drawio"]["status"], "blocked")
        preview = environment.doctor("drawio")
        self.assertEqual(preview["status"], "blocked")
        self.assertTrue(self.checks(preview)["drawio"]["required"])

    def test_drawio_override_with_spaces_static_only(self):
        self.runtime_mocks()
        path = "/local tools/draw.io"
        self.paths.add(path)
        with mock.patch.dict(environment.os.environ, {"DRAWIO_BIN": path}):
            report = environment.doctor("drawio")
        self.assertEqual(report["executables"]["drawio"], path)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(self.run.call_count, 2)  # Python and Node, never Electron.
        self.assertIn("NOT tested", self.checks(report)["drawio"]["detail"])
        self.assertEqual({task["id"] for task in report["manual"]}, {"browser", "drawio_export"})

    def test_invalid_drawio_override_does_not_fall_back(self):
        self.paths.add("/Applications/draw.io.app/Contents/MacOS/draw.io")
        with mock.patch.dict(environment.os.environ, {"DRAWIO_BIN": "/missing/drawio --version"}):
            self.assertIsNone(environment._drawio_path())
        self.run.assert_not_called()

    def test_drawio_standard_app_version_without_execution(self):
        path = "/Applications/draw.io.app/Contents/MacOS/draw.io"
        self.paths.add(path)
        data = environment.plistlib.dumps({"CFBundleShortVersionString": "26.0.1"})
        with mock.patch.object(environment.Path, "read_bytes", return_value=data):
            check = environment._check_drawio()
        self.assertEqual(check["version"], "26.0.1")
        self.assertEqual(check["path"], path)
        self.run.assert_not_called()

    def test_drawio_path_fallback_and_no_fake_version(self):
        self.paths.add("/runtime/drawio")
        self.which.side_effect = lambda name: "/runtime/drawio" if name == "drawio" else None
        check = environment._check_drawio()
        self.assertEqual(check["status"], "ready")
        self.assertIsNone(check["version"])
        self.run.assert_not_called()

    def test_drawio_override_can_be_a_single_path_executable_name(self):
        self.paths.add("/runtime/drawio")
        self.which.side_effect = lambda name: "/runtime/drawio" if name == "drawio" else None
        with mock.patch.dict(environment.os.environ, {"DRAWIO_BIN": "drawio"}):
            self.assertEqual(environment._drawio_path(), "/runtime/drawio")
        self.run.assert_not_called()

    def test_gif_prefers_skill_venv_and_ignores_node_drawio(self):
        self.runtime_mocks(node=False)
        venv_python = str(environment._venv_python())
        self.paths.add(venv_python)
        report = environment.doctor("gif")
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["executables"]["python"], venv_python)
        self.assertEqual(set(self.checks(report)), {"python", "pillow", "font"})
        self.assertTrue(all(call.args[0][0] == venv_python for call in self.run.call_args_list))
        self.which.assert_not_called()

    def test_gif_falls_back_to_current_python(self):
        self.runtime_mocks(node=False)
        self.assertEqual(environment.doctor("gif")["executables"]["python"], "/runtime/python")

    def test_pillow_missing_old_or_broken_blocks_gif_and_font(self):
        self.runtime_mocks(node=False)
        for data in ({"ok": False, "error": "No module named PIL"}, {"ok": True, "version": "9.5.0"},
                     {"ok": False, "version": "10.4.0", "error": "codec failed"}):
            with self.subTest(data=data):
                self.pillow_data = data
                self.run.reset_mock()
                report = environment.doctor("gif")
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(self.checks(report)["font"]["status"], "blocked")
                self.assertEqual(self.run.call_count, 2)  # Do not pretend the font was tested.

    def test_font_missing_or_unusable_is_blocked(self):
        self.runtime_mocks(node=False)
        self.font_data = {"ok": False, "error": "missing glyphs"}
        self.assertEqual(environment.doctor("gif")["status"], "blocked")
        with mock.patch.object(environment, "_font_candidates", return_value=[]):
            self.run.reset_mock()
            report = environment.doctor("gif")
            self.assertEqual(self.checks(report)["font"]["status"], "blocked")
            self.assertEqual(self.run.call_count, 2)

    def test_all_collects_every_dependency_and_respects_preview_flag(self):
        self.runtime_mocks()
        report = environment.doctor("all", require_preview=False)
        self.assertEqual(set(self.checks(report)), {"python", "node", "drawio", "pillow", "font", "browser"})
        self.assertEqual(report["status"], "ready")
        self.assertEqual(environment.doctor("all")["status"], "blocked")

    def test_probe_errors_and_non_json_are_blocked(self):
        for failure in (FileNotFoundError("missing"), PermissionError("denied"), subprocess.TimeoutExpired("python", 15)):
            with self.subTest(failure=failure):
                self.run.side_effect = failure
                self.assertEqual(environment.doctor("icons")["status"], "blocked")
        self.run.side_effect = None
        for stdout in ("garbage", "[]", "null"):
            self.run.return_value = subprocess.CompletedProcess([], 0, stdout, "")
            self.assertEqual(environment.doctor("icons")["status"], "blocked")
        self.run.return_value = completed({"version": "3.11.9"}, code=1)
        self.assertEqual(environment.doctor("icons")["status"], "blocked")

    def test_invalid_api_values_rejected_before_any_command(self):
        for value in ("shell", "node; touch /tmp/evil", "", None, []):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    environment.install_plan(value)
                with self.assertRaises(ValueError):
                    environment.install(value, confirmed=True)
                with self.assertRaises(ValueError):
                    environment.doctor(value)
        with self.assertRaises(ValueError):
            environment.doctor("drawio", require_preview="false")
        self.run.assert_not_called()

    def test_plan_is_inert_and_pillow_targets_only_skill_venv(self):
        plan = environment.install_plan("pillow")
        self.assertTrue(plan["supported"])
        self.assertEqual(len(plan["commands"]), 2)
        self.assertEqual(plan["commands"][0][-1], str(environment.SKILL_ROOT / ".venv"))
        self.assertEqual(plan["commands"][1][0], str(environment._venv_python()))
        self.assertIn("--only-binary=:all:", plan["commands"][1])
        self.assertIn("https://pypi.org/simple", plan["commands"][1])
        for dependency in environment.DEPENDENCIES:
            plan = environment.install_plan(dependency)
            self.assertTrue({"commands", "source", "impact_paths", "manual_instructions"} <= plan.keys())
            json.dumps(plan)
        self.run.assert_not_called()

    def test_install_refuses_without_literal_true(self):
        for confirmation in (False, None, 1, "yes", "true"):
            with self.subTest(confirmation=confirmation):
                report = environment.install("pillow", confirmed=confirmation)
                self.assertEqual(report["status"], "refused")
                self.assertEqual(report["executed"], [])
                self.assertEqual(report["checks"], [])
        self.run.assert_not_called()

    def test_no_package_manager_is_never_bootstrapped(self):
        for dependency in ("node", "python", "drawio", "font", "browser"):
            report = environment.install(dependency, confirmed=True)
            self.assertEqual(report["status"], "unsupported")
            self.assertFalse(report["plan"]["supported"])
            self.assertEqual(report["plan"]["commands"], [])
            self.assertIn("never installs a package manager", report["reason"])
        self.run.assert_not_called()

    def use_brew(self):
        self.paths.add("/opt/homebrew/bin/brew")
        self.which.side_effect = lambda name: "/opt/homebrew/bin/brew" if name == "brew" else None

    def test_brew_plans_are_whitelisted_argv_not_shell(self):
        self.use_brew()
        for dependency in ("node", "python", "drawio", "font", "browser"):
            plan = environment.install_plan(dependency)
            self.assertTrue(plan["supported"])
            command = plan["commands"][0]
            self.assertEqual(command[:2], ["/opt/homebrew/bin/brew", "install"])
            self.assertFalse({"sudo", "curl", "sh", "bash", "-c"}.intersection(command))
            self.assertTrue(plan["source"])
            self.assertTrue(plan["impact_paths"])
        self.run.assert_not_called()

    def test_linux_requires_existing_apt_and_no_elevation(self):
        self.paths.add("/usr/bin/apt-get")
        self.which.return_value = "/usr/bin/apt-get"
        with mock.patch.object(environment.platform, "system", return_value="Linux"):
            self.assertFalse(environment.install_plan("node")["supported"])
            with mock.patch.object(environment.os, "geteuid", return_value=0):
                for dependency in ("node", "python", "font"):
                    self.assertTrue(environment.install_plan(dependency)["supported"])
                self.assertFalse(environment.install_plan("drawio")["supported"])
        self.run.assert_not_called()

    def test_windows_without_supported_manager_is_manual(self):
        with mock.patch.object(environment.platform, "system", return_value="Windows"):
            self.assertFalse(environment.install_plan("node")["supported"])
        self.run.assert_not_called()

    def test_existing_unsafe_venv_is_not_overwritten(self):
        with mock.patch.object(environment.Path, "is_symlink", return_value=True):
            self.assertFalse(environment.install_plan("pillow")["supported"])
        with mock.patch.object(environment.Path, "exists", return_value=True):
            self.assertFalse(environment.install_plan("pillow")["supported"])
            with mock.patch.object(environment.Path, "read_text", return_value="include-system-site-packages = true\n"):
                self.assertFalse(environment.install_plan("pillow")["supported"])
        self.run.assert_not_called()

    def test_existing_isolated_venv_plan_skips_creation(self):
        self.paths.add(str(environment._venv_python()))
        with mock.patch.object(environment.Path, "exists", return_value=True), \
                mock.patch.object(environment.Path, "read_text", return_value="include-system-site-packages = false\n"):
            plan = environment.install_plan("pillow")
        self.assertTrue(plan["supported"])
        self.assertEqual(len(plan["commands"]), 1)
        self.run.assert_not_called()

    def test_duplicate_venv_isolation_settings_are_rejected(self):
        self.paths.add(str(environment._venv_python()))
        cfg = "include-system-site-packages = false\ninclude-system-site-packages = true\n"
        with mock.patch.object(environment.Path, "exists", return_value=True), \
                mock.patch.object(environment.Path, "read_text", return_value=cfg):
            self.assertFalse(environment.install_plan("pillow")["supported"])
        self.run.assert_not_called()

    def test_cjk_font_override_is_passed_as_data_not_a_command(self):
        self.runtime_mocks(node=False, fonts=False)
        font = "/fonts/my font; not-a-command.ttf"
        self.font_data["path"] = font
        self.run.side_effect = [completed(self.python_data), completed(self.pillow_data), completed(self.font_data)]
        with mock.patch.dict(environment.os.environ, {"CJK_FONT": font}):
            report = environment.doctor("gif")
        self.assertEqual(self.checks(report)["font"]["path"], font)
        self.assertEqual(json.loads(self.run.call_args.kwargs["input"]), [font])
        self.assertNotIn(font, self.run.call_args.args[0])

    def test_successful_command_with_blocked_postcheck_is_not_ready(self):
        self.use_brew()
        self.run.side_effect = None
        self.run.return_value = completed()
        report = environment.install("node", confirmed=True)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["executed"][0]["returncode"], 0)
        self.assertEqual(report["checks"][0]["status"], "blocked")
        self.assertIs(self.run.call_args.kwargs["shell"], False)

    def test_install_failure_still_rechecks_and_never_claims_success(self):
        self.use_brew()
        self.run.side_effect = None
        self.run.return_value = completed(code=1, stderr="package manager failed")
        report = environment.install("node", confirmed=True)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "package manager failed")
        self.assertEqual(report["checks"][0]["id"], "node")

    def test_install_timeout_reports_failure(self):
        self.use_brew()
        self.run.side_effect = subprocess.TimeoutExpired("brew", 600)
        report = environment.install("node", confirmed=True)
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["executed"][0]["returncode"])

    def test_success_requires_an_actual_postcheck(self):
        self.use_brew()
        self.paths.add("/runtime/node")
        self.which.side_effect = lambda name: {"brew": "/opt/homebrew/bin/brew", "node": "/runtime/node"}.get(name)
        self.run.side_effect = [completed(), completed({"version": "22.1.0"})]
        report = environment.install("node", confirmed=True)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["checks"][0]["version"], "22.1.0")
        self.assertEqual(self.run.call_count, 2)

    def test_browser_install_is_still_manual_acceptance(self):
        self.use_brew()
        self.paths.add("/Applications/Firefox.app/Contents/MacOS/firefox")
        self.run.side_effect = None
        self.run.return_value = completed()
        report = environment.install("browser", confirmed=True)
        self.assertEqual(report["status"], "manual")
        self.assertEqual(report["checks"][0]["status"], "ready")
        self.assertEqual(report["manual"][0]["status"], "manual")
        self.assertEqual(self.run.call_count, 1)

    def test_browser_missing_after_installer_success_is_blocked(self):
        self.use_brew()
        self.run.side_effect = None
        self.run.return_value = completed()
        report = environment.install("browser", confirmed=True)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["checks"][0]["status"], "blocked")
        self.assertIsNone(report["checks"][0]["path"])
        self.assertEqual(self.run.call_count, 1)

    def test_pillow_never_runs_pip_with_unverified_isolation(self):
        self.runtime_mocks(node=False)
        with mock.patch.object(environment, "_isolation_error", return_value="wrong prefix"), \
                mock.patch.object(environment, "_run", return_value={"command": [], "returncode": 0, "stdout": "", "stderr": ""}) as run:
            report = environment.install("pillow", confirmed=True)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["reason"], "wrong prefix")
        self.assertTrue(all("pip" not in call.args[0] for call in run.call_args_list))

    def test_isolation_prefix_is_checked_not_just_pyvenv_cfg(self):
        self.paths.add(str(environment._venv_python()))
        self.run.side_effect = None
        with mock.patch.object(environment, "_venv_problem", return_value=None):
            self.run.return_value = completed({"prefix": "/system", "base_prefix": "/system", "user_site": False})
            self.assertIsNotNone(environment._isolation_error())
            venv = str(environment.SKILL_ROOT / ".venv")
            self.run.return_value = completed({"prefix": venv, "base_prefix": "/system", "user_site": False,
                                               "install_paths": {key: venv for key in ("purelib", "platlib", "scripts", "data")}})
            self.assertIsNone(environment._isolation_error())
            self.run.return_value = completed({"prefix": venv, "base_prefix": "/system", "user_site": False,
                                               "install_paths": {key: "/outside" for key in ("purelib", "platlib", "scripts", "data")}})
            self.assertIsNotNone(environment._isolation_error())

    def test_confirmed_pillow_plan_executes_in_order_with_mocked_postcheck(self):
        self.run.side_effect = [completed(), completed()]
        check = environment._check("pillow", True, str(environment._venv_python()), "10.4.0")
        with mock.patch.object(environment, "_isolation_error", return_value=None), \
                mock.patch.object(environment, "_postcheck", return_value=check) as postcheck:
            report = environment.install("pillow", confirmed=True)
        self.assertEqual(report["status"], "ready")
        self.assertEqual(len(report["executed"]), 2)
        self.assertIn("venv", self.run.call_args_list[0].args[0])
        self.assertIn("pip", self.run.call_args_list[1].args[0])
        postcheck.assert_called_once_with("pillow")
        for call in self.run.call_args_list:
            self.assertIs(call.kwargs["shell"], False)
            self.assertEqual(call.kwargs["env"]["PIP_CONFIG_FILE"], environment.os.devnull)

    def test_failed_venv_creation_stops_before_pip(self):
        self.run.side_effect = [completed(code=1, stderr="ensurepip unavailable")]
        with mock.patch.object(environment, "_postcheck", return_value=environment._check("pillow", False)):
            report = environment.install("pillow", confirmed=True)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.run.call_count, 1)

    def test_preload_and_pip_environment_is_not_inherited(self):
        with mock.patch.dict(environment.os.environ, {"NODE_OPTIONS": "--require evil", "NODE_PATH": "/evil",
                                                     "PYTHONPATH": "/evil", "PIP_TARGET": "/system", "PIP_EXTRA_INDEX_URL": "https://invalid"}):
            child = environment._child_env(installing=True)
        for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PIP_TARGET", "PIP_EXTRA_INDEX_URL"):
            self.assertNotIn(key, child)
        self.assertEqual(child["HOMEBREW_NO_AUTO_UPDATE"], "1")

    def test_probe_environment_clears_all_python_and_icon_overrides(self):
        self.runtime_mocks()
        unsafe = {"PYTHONHOME": "/evil", "PYTHONPATH": "/evil", "PYTHONSTARTUP": "/evil",
                  "PYTHONINSPECT": "1", "PYTHONUSERBASE": "/evil", "PYTHONDONTWRITEBYTECODE": "0",
                  "NODE_OPTIONS": "--require /evil", "NODE_PATH": "/evil", "ICON_PERSONAL_ROOT": "/evil",
                  "ARCH_ICONS_ROOT": "/evil", "ARCHITECTURE_DIAGRAM_ICONS": "/evil",
                  "ARCHITECTURE_DIAGRAM_THEME": "dark"}
        kept = {"PATH": "/runtime", "DRAWIO_BIN": "/tools/draw io", "CJK_FONT": "/fonts/cjk.ttc",
                "ARCHITECTURE_DIAGRAM_FONT": "/fonts/selected.ttc", "LANG": "zh_CN.UTF-8"}
        with mock.patch.dict(environment.os.environ, {**unsafe, **kept}, clear=True):
            before = dict(environment.os.environ)
            self.assertEqual(environment.doctor("archify")["status"], "ready")
            for call in self.run.call_args_list:
                self.assertEqual(call.kwargs["env"], {**kept, "PYTHONDONTWRITEBYTECODE": "1"})
            self.assertEqual(dict(environment.os.environ), before)

    def test_install_sanitization_retains_only_explicit_install_controls(self):
        with mock.patch.dict(environment.os.environ, {"PIP_CONFIG_FILE": "/evil/pip.conf", "PIP_PREFIX": "/outside",
                                                     "PYTHONHOME": "/outside", "ARCH_ICONS_ROOT": "/outside",
                                                     "DRAWIO_BIN": "/tools/drawio", "CJK_FONT": "/fonts/cjk.ttc"}):
            child = environment._child_env(installing=True)
        self.assertEqual(child["PIP_CONFIG_FILE"], environment.os.devnull)
        self.assertEqual(child["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(child["DRAWIO_BIN"], "/tools/drawio")
        self.assertEqual(child["CJK_FONT"], "/fonts/cjk.ttc")
        for key in ("PIP_PREFIX", "PYTHONHOME", "ARCH_ICONS_ROOT"):
            self.assertNotIn(key, child)
        self.run.assert_not_called()


class ProbeContractTests(unittest.TestCase):
    def test_embedded_probes_compile_on_python39(self):
        for code in (environment._PYTHON_PROBE, environment._PILLOW_PROBE,
                     environment._FONT_PROBE, environment._ISOLATION_PROBE):
            ast.parse(code, feature_version=(3, 9))
        ast.parse(MODULE_PATH.read_text(encoding="utf-8"), feature_version=(3, 9))

    def test_font_probe_rejects_tofu_and_accepts_distinct_chinese_glyphs(self):
        class Mask:
            size = (1, 1)

            def __init__(self, char, tofu):
                self.value = 1 if tofu else ord(char) % 254 + 1

            def __bytes__(self):
                return bytes([self.value])

        pil = types.ModuleType("PIL")
        pil.Image = mock.Mock()
        pil.ImageDraw = mock.Mock()
        pil.ImageFont = mock.Mock()
        for tofu, expected in ((True, False), (False, True)):
            font = mock.Mock()
            font.getmask.side_effect = lambda char: Mask(char, tofu)
            pil.ImageFont.truetype.return_value = font
            stream = io.StringIO()
            with mock.patch.dict(sys.modules, {"PIL": pil}), \
                    mock.patch.object(sys, "stdin", io.StringIO('["/fonts/test.ttc"]')), \
                    mock.patch.object(sys, "stdout", stream):
                exec(environment._FONT_PROBE, {})
            self.assertIs(json.loads(stream.getvalue())["ok"], expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
