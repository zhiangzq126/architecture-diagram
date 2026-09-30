"""Offline subprocess CLI regressions; never install dependencies or launch Electron."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts/diagram.py'
NODE = shutil.which('node')
if not NODE and Path('/opt/homebrew/bin/node').is_file():
    NODE = '/opt/homebrew/bin/node'


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.task_path = self.work / 'task.json'
        self.output = self.work / 'out'
        self.empty_bin = self.work / 'empty-bin'
        self.empty_bin.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('PYTHON', 'PIP_')) and key not in
                    {'NODE_OPTIONS', 'NODE_PATH', 'ICON_PERSONAL_ROOT', 'ARCH_ICONS_ROOT',
                     'ARCHITECTURE_DIAGRAM_ICONS', 'ARCHITECTURE_DIAGRAM_THEME', 'DRAWIO_BIN'}}
        self.env['PATH'] = str(Path(NODE).parent) if NODE else str(self.empty_bin)
        self.env['DRAWIO_BIN'] = str(self.work / 'no-electron')
        self.request = {'contract_version': 1, 'name': 'service', 'type': 'architecture',
                        'output_dir': str(self.output),
                        'targets': [{'format': 'html', 'source': str(ROOT / 'examples/service.architecture.json')},
                                    {'format': 'drawio', 'source': str(ROOT / 'examples/service.drawio'), 'preview': False}]}

    def cli(self, *args, env=None, guard_install=False):
        # Protect the entry interpreter while preserving poisoned variables for child-env tests.
        command = [sys.executable, '-E', '-s', '-B']
        if guard_install:
            # Fail closed if the real unconfirmed CLI tries any installer or probe process.
            bootstrap = (
                'import runpy,sys; from unittest.mock import patch; '
                'entry=sys.argv[1]; sys.path.insert(0,str(__import__("pathlib").Path(entry).parent)); '
                'sys.argv=sys.argv[1:]; '
                'guard=patch("subprocess.run",side_effect=AssertionError("Unconfirmed install executed a process")); '
                'guard.start(); runpy.run_path(entry,run_name="__main__")'
            )
            command.extend(['-c', bootstrap])
        command.extend([str(CLI), *map(str, args)])
        result = subprocess.run(command, env=env if env is not None else self.env, cwd=self.work,
                                text=True, capture_output=True, timeout=90, check=False)
        self.assertNotIn('Traceback', result.stderr, result.stderr)
        try:
            payload = json.loads(result.stdout)
        except ValueError:
            self.fail(f'CLI did not return JSON: {result.stdout}\n{result.stderr}')
        return result, payload

    def run_task(self, env=None):
        self.task_path.write_text(json.dumps(self.request), encoding='utf-8')
        return self.cli('run', self.task_path, env=env)

    def assert_receipt(self, target):
        path = Path(target['receipt'])
        receipt = json.loads(path.read_text(encoding='utf-8'))
        manifest = (ROOT / 'manifest.json').read_bytes()
        self.assertEqual(receipt['skill_version'], json.loads(manifest)['version'])
        self.assertEqual(receipt['module_manifest']['sha256'], hashlib.sha256(manifest).hexdigest())
        self.assertEqual(receipt['visual_review'], 'pending')
        for artifact in receipt['artifacts']:
            data = (path.parent / artifact['path']).read_bytes()
            self.assertEqual(artifact['bytes'], len(data))
            self.assertEqual(artifact['sha256'], hashlib.sha256(data).hexdigest())
        self.assertTrue(all(Path(p).is_file() for p in target['artifacts']))
        self.assertFalse(list(self.output.glob('.architecture-diagram-*')))

    def test_doctor_without_node_path_is_blocked(self):
        result, payload = self.cli('doctor', 'archify', env={**self.env, 'PATH': str(self.empty_bin)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload['status'], 'blocked')
        checks = {check['id']: check for check in payload['checks']}
        self.assertEqual(checks['python']['status'], 'ready')
        self.assertEqual(checks['node']['status'], 'blocked')
        self.assertIsNone(payload['executables']['node'])
        self.assertFalse(self.output.exists())

    def test_run_without_node_blocks_both_targets_without_artifacts(self):
        result, payload = self.run_task(env={**self.env, 'PATH': str(self.empty_bin)})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload['status'], 'failed')
        self.assertEqual([t['status'] for t in payload['targets']], ['blocked', 'blocked'])
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertIn('preflight', result.stderr)

    def test_install_pillow_without_confirm_never_executes(self):
        before = (ROOT / '.venv').exists()
        result, payload = self.cli('install', 'pillow', guard_install=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload['status'], 'refused')
        self.assertEqual(payload['executed'], [])
        self.assertEqual(payload['checks'], [])
        self.assertTrue(payload['plan']['requires_confirmation'])
        self.assertEqual((ROOT / '.venv').exists(), before)

    def test_route_rejects_invalid_contract_types_and_nul(self):
        original = copy.deepcopy(self.request)
        cases = [('contract_version', True), ('contract_version', 1.0), ('name', []), ('type', {}),
                 ('theme', []), ('quality', {}), ('output_dir', 'bad\x00path'),
                 ('icons', {'root': None}), ('icons', {'root': False}), ('icons', {'root': {}}),
                 ('icons', {'root': 'bad\x00root'}), ('targets', [{'format': [], 'source': 'x'}])]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.request = {**original, field: value}
                self.task_path.write_text(json.dumps(self.request), encoding='utf-8')
                result, payload = self.cli('route', self.task_path)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload['status'], 'failed')
                self.assertTrue(payload['diagnostic'])
                self.assertFalse(self.output.exists())

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_native_html_bad_shapes_do_not_abort_valid_drawio(self):
        original = json.loads((ROOT / 'examples/service.architecture.json').read_text(encoding='utf-8'))
        source = self.work / 'bad.json'
        self.request['targets'][0]['source'] = str(source)
        bad_models = [None, [], {**original, 'components': None}, {**original, 'components': {}},
                      {**original, 'components': [None]}, {**original, 'components': ['node']},
                      {**original, 'components': [{'id': []}]}, {**original, 'components': [{'id': None}]},
                      {**original, 'meta': None}, {**original, 'meta': []},
                      {**original, 'schema_version': True}, {**original, 'diagram_type': 'workflow'},
                      {**original, 'components': original['components'] + [original['components'][0]]},
                      {**original, 'connections': [None]}]
        for index, model in enumerate(bad_models):
            with self.subTest(index=index, model=model):
                self.request['name'] = f'bad{index}'
                source.write_text(json.dumps(model), encoding='utf-8')
                result, payload = self.run_task()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload['status'], 'partial')
                self.assertEqual([t['status'] for t in payload['targets']], ['failed', 'generated'])
                self.assertTrue(payload['targets'][0]['diagnostic'])
                self.assertFalse((self.output / (self.request['name'] + '-html')).exists())
                self.assert_receipt(payload['targets'][1])

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_native_invalid_json_and_encoding_are_target_local(self):
        source = self.work / 'bad.json'
        self.request['targets'][0]['source'] = str(source)
        for index, raw in enumerate((b'{', b'\xff', b'NaN', b'{"meta":{},"meta":{}}', b'[' * 2000 + b']' * 2000)):
            with self.subTest(raw=raw[:40]):
                self.request['name'] = f'raw{index}'
                source.write_bytes(raw)
                result, payload = self.run_task()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload['status'], 'partial')
                self.assertEqual([t['status'] for t in payload['targets']], ['failed', 'generated'])
                self.assert_receipt(payload['targets'][1])

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_bad_native_xml_keeps_valid_html(self):
        source = self.work / 'bad.drawio'
        raw = (ROOT / 'examples/service.drawio').read_bytes()
        self.request['targets'].reverse()
        self.request['targets'][0]['source'] = str(source)
        cases = [b'<mxfile>', b'\xff', raw.replace(b'id="orders"', b'id="gateway"'),
                 b'<mxfile><diagram>compressed-data</diagram></mxfile>']
        for index, data in enumerate(cases):
            with self.subTest(index=index):
                self.request['name'] = f'xml{index}'
                source.write_bytes(data)
                result, payload = self.run_task()
                self.assertEqual(result.returncode, 1)
                self.assertEqual(payload['status'], 'partial')
                self.assertEqual([t['status'] for t in payload['targets']], ['failed', 'generated'])
                self.assert_receipt(payload['targets'][1])

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_poisoned_environment_does_not_break_preflight_or_delivery(self):
        env = {**self.env, 'PYTHONPATH': str(self.work / 'evil'), 'PYTHONHOME': str(self.work / 'evil'),
               'PYTHONINSPECT': '1', 'PYTHONSTARTUP': str(self.work / 'evil.py'),
               'NODE_OPTIONS': '--require=' + str(self.work / 'missing-preload.cjs'),
               'NODE_PATH': str(self.work / 'missing-modules'), 'ICON_PERSONAL_ROOT': str(self.work / 'evil'),
               'ARCH_ICONS_ROOT': str(self.work / 'evil'), 'ARCHITECTURE_DIAGRAM_ICONS': str(self.work / 'evil.json'),
               'DRAWIO_BIN': str(self.work / 'no-electron')}
        result, payload = self.run_task(env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(payload['status'], 'generated')
        for target in payload['targets']:
            self.assertEqual(target['preflight']['status'], 'ready')
            self.assert_receipt(target)
        self.assertFalse(list(self.work.rglob('__pycache__')))

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_existing_delivery_is_unchanged_and_cli_exits_nonzero(self):
        first, payload = self.run_task()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        second, repeated = self.run_task()
        self.assertEqual(second.returncode, 1)
        self.assertEqual([t['status'] for t in repeated['targets']], ['failed', 'failed'])
        after = {str(p.relative_to(self.output)): p.read_bytes() for p in self.output.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(list(self.output.glob('.architecture-diagram-*')))

    @unittest.skipUnless(NODE, 'Node >=18 is needed for offline native render regression')
    def test_dangling_output_symlink_is_preserved_and_sibling_generated(self):
        self.output.mkdir()
        dest = self.output / 'service-html'
        missing = self.work / 'absent'
        dest.symlink_to(missing, target_is_directory=True)
        result, payload = self.run_task()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload['status'], 'partial')
        self.assertEqual([t['status'] for t in payload['targets']], ['failed', 'generated'])
        self.assertEqual(dest.readlink(), missing)
        self.assertFalse(missing.exists())
        self.assert_receipt(payload['targets'][1])


if __name__ == '__main__':
    unittest.main(verbosity=2)
