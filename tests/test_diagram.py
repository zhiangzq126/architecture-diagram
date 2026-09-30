import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import diagram
from drawio_adapter import attach_icons, parse_diagram


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'task.json'
        self.request = {'contract_version': 1, 'name': 'example', 'type': 'architecture',
                        'targets': [{'format': 'html', 'source': str(ROOT / 'examples/service.architecture.json')}],
                        'output_dir': 'out'}

    def load(self):
        self.path.write_text(json.dumps(self.request))
        return diagram.load_request(self.path)

    def test_route_and_relative_output(self):
        value = self.load()
        self.assertEqual(value['output_dir'], str((self.path.parent / 'out').resolve()))
        self.assertEqual(diagram.route(value)[0]['backend'], 'archify')

    def test_unknown_and_duplicate_targets(self):
        for fmt in ('svg', 'html'):
            self.request['targets'].append({'format': fmt, 'source': str(ROOT / 'examples/service.architecture.json')})
            with self.assertRaises(ValueError):
                self.load()
            self.request['targets'].pop()

    def test_no_silent_uml_conversion(self):
        self.request['type'] = 'uml'
        with self.assertRaises(ValueError):
            self.load()

    def test_topology_gif_requires_explicit_matching_derive(self):
        self.request['targets'] = [{'format': 'gif', 'source': str(ROOT / 'examples/service.architecture.json')}]
        with self.assertRaisesRegex(ValueError, 'explicit derive'):
            self.load()
        for derive in ('workflow', 'sequence', {}, [], True):
            self.request['targets'][0]['derive'] = derive
            with self.assertRaises(ValueError):
                self.load()
        self.request['targets'][0]['derive'] = 'architecture'
        self.request['icons'] = {'bindings': [{'node_id': 'db', 'query': 'MySQL'}]}
        loaded = self.load()
        self.assertEqual(diagram.route(loaded)[0]['mode'], 'topology')
        for field, value in [('geometry', 'shared'), ('preview', False)]:
            self.request['targets'][0][field] = value
            with self.assertRaises(ValueError):
                self.load()
            del self.request['targets'][0][field]
        self.request['targets'][0]['source'] = str(ROOT / 'examples/service.drawio')
        with self.assertRaisesRegex(ValueError, 'typed JSON'):
            self.load()

    def test_topology_all_formats_can_share_source_and_icons(self):
        source = str(ROOT / 'examples/service.architecture.json')
        self.request['targets'] = [{'format': 'html', 'source': source},
                                   {'format': 'drawio', 'source': source, 'geometry': 'shared'},
                                   {'format': 'gif', 'source': source, 'derive': 'architecture'}]
        self.request['icons'] = {'bindings': [{'node_id': 'db', 'query': 'MySQL'}]}
        self.assertEqual(len(diagram.route(self.load())), 3)

    def test_no_skill_output(self):
        self.request['output_dir'] = str(ROOT / 'assets')
        with self.assertRaises(ValueError):
            self.load()

    def test_no_shell_artifact_name(self):
        self.request['name'] = '../overwrite'
        with self.assertRaises(ValueError):
            self.load()

    def test_wrong_json_field_types_raise_value_error(self):
        original = copy.deepcopy(self.request)
        for field in ('contract_version', 'name', 'type', 'theme', 'quality', 'output_dir', 'targets', 'icons'):
            for invalid in (None, True, False, 1, 1.0, [], {}):
                if (field == 'contract_version' and type(invalid) is int) or (field == 'icons' and invalid == {}):
                    continue
                with self.subTest(field=field, invalid=invalid):
                    self.request = copy.deepcopy(original)
                    self.request[field] = invalid
                    with self.assertRaises(ValueError):
                        self.load()

    def test_wrong_target_field_types_raise_value_error(self):
        original = copy.deepcopy(self.request)
        for field in ('format', 'source', 'preview'):
            for invalid in (None, 1, 1.0, [], {}, ''):
                with self.subTest(field=field, invalid=invalid):
                    self.request = copy.deepcopy(original)
                    self.request['targets'] = [{'format': 'drawio', 'source': str(ROOT / 'examples/service.drawio')}]
                    self.request['targets'][0][field] = invalid
                    with self.assertRaises(ValueError):
                        self.load()
        for invalid in (None, True, 'html', []):
            self.request = copy.deepcopy(original)
            self.request['targets'] = [invalid]
            with self.assertRaises(ValueError):
                self.load()

    def test_icon_root_rejects_wrong_types_empty_and_nul(self):
        for root in (None, False, True, 0, 1.0, [], {}, '', '  ', 'icons\x00pack'):
            with self.subTest(root=root):
                self.request['icons'] = {'root': root}
                with self.assertRaisesRegex(ValueError, 'icons.root'):
                    self.load()
        self.request['icons'] = {'root': 'missing-local-pack'}
        self.assertEqual(self.load()['icons']['root'], str((self.path.parent / 'missing-local-pack').resolve()))

    def test_paths_reject_nul_and_symlink_loops(self):
        for field in ('source', 'output_dir'):
            with self.subTest(field=field):
                original = copy.deepcopy(self.request)
                owner = self.request['targets'][0] if field == 'source' else self.request
                owner[field] = 'bad\x00path'
                with self.assertRaisesRegex(ValueError, 'without NUL'):
                    self.load()
                self.request = original
        loop = self.path.parent / 'loop'
        loop.symlink_to(loop.name)
        self.request['icons'] = {'root': str(loop)}
        with self.assertRaisesRegex(ValueError, 'Invalid icons.root'):
            self.load()

    def test_invalid_bindings_rejected_even_when_routing(self):
        for bindings in (None, {}, [None], [{'node_id': [], 'query': 'x'}],
                         [{'node_id': 'x', 'query': False}], [{'node_id': 'x', 'query': 'x\x00'}],
                         [{'node_id': 'x', 'query': 'x', 'unknown': 'x'}],
                         [{'node_id': 'x', 'query': 'x'}] * 2):
            with self.subTest(bindings=bindings):
                self.request['icons'] = {'bindings': bindings}
                with self.assertRaises(ValueError):
                    self.load()

    def test_invalid_json_and_duplicate_fields(self):
        for raw in ('null', '[]', 'true', '1', '"task"', '{', '{"contract_version":1,"contract_version":1}',
                    '{"contract_version":NaN}', '{"contract_version":Infinity}', '[' * 2000 + ']' * 2000):
            with self.subTest(raw=raw[:80]):
                self.path.write_text(raw)
                with self.assertRaises(ValueError):
                    diagram.load_request(self.path)

    def test_binding_fallback_preserves_labels(self):
        value = self.load()
        value['icons']['bindings'] = [{'node_id': 'orders', 'query': 'unknown'}]
        model, registry = diagram.prepare_archify(value['targets'][0]['source'], value, {'prepared': {}})
        self.assertEqual(model['components'][1]['label'], '订单服务')
        self.assertEqual(len(model['connections']), 2)
        self.assertNotIn('brand', model['components'][1])
        self.assertEqual(registry, {})

    def test_missing_binding_node(self):
        value = self.load()
        value['icons']['bindings'] = [{'node_id': 'no-node', 'query': 'unknown'}]
        with self.assertRaises(ValueError):
            diagram.prepare_archify(value['targets'][0]['source'], value, {'prepared': {}})

    def test_same_svg_different_identities(self):
        value = self.load()
        value['icons']['bindings'] = [{'node_id': node, 'query': node} for node in ('gateway', 'store')]
        icon = {'svg_sha256': 'a' * 64, 'source_sha256': 'b' * 64, 'product_id': '产品A',
                'variant_id': 'vector', 'theme': {'dark_backdrop': False}}
        model, registry = diagram.prepare_archify(value['targets'][0]['source'], value,
            {'prepared': {'gateway': icon, 'store': {**icon, 'product_id': '产品B'}}})
        self.assertEqual(len(registry), 2)
        self.assertNotEqual(model['components'][0]['brand'], model['components'][2]['brand'])

    def test_partial_blocked_and_existing_delivery(self):
        value = self.load()
        value['targets'].append({'format': 'drawio', 'source': str(ROOT / 'examples/service.drawio')})
        ready = {'status': 'ready', 'executables': {}}
        blocked = {'status': 'blocked', 'executables': {}}
        def render(target, request, resolved, stage, tools):
            artifact = stage / 'example.html'
            artifact.write_text('<html>verified mock</html>')
            return {'validation': 'passed'}, [artifact]
        with patch('environment.doctor', side_effect=[ready, blocked]), patch('diagram.render_archify', side_effect=render):
            result = diagram.execute(value)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([t['status'] for t in result['targets']], ['generated', 'blocked'])
        artifact = Path(value['output_dir']) / 'example-html/example.html'
        before = artifact.read_bytes()
        with patch('environment.doctor', side_effect=[ready, blocked]), patch('diagram.render_archify') as render:
            result = diagram.execute(value)
        render.assert_not_called()
        self.assertEqual(result['targets'][0]['status'], 'failed')
        self.assertEqual(artifact.read_bytes(), before)
        self.assertFalse(list(Path(value['output_dir']).glob('.architecture-diagram-*')))

    def test_failed_target_keeps_other_output(self):
        value = self.load()
        value['targets'].append({'format': 'drawio', 'source': str(ROOT / 'examples/service.drawio')})
        def render(target, request, resolved, stage, tools):
            artifact = stage / 'example.drawio'
            artifact.write_text('verified mock')
            return {}, [artifact]
        with patch('environment.doctor', return_value={'status': 'ready', 'executables': {}}), \
             patch('diagram.render_archify', side_effect=RuntimeError('render failed')), \
             patch('diagram.render_drawio', side_effect=render):
            result = diagram.execute(value)
        self.assertEqual([t['status'] for t in result['targets']], ['failed', 'generated'])
        self.assertFalse(list(Path(value['output_dir']).glob('.architecture-diagram-*')))


class NativeModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / 'model.json'
        self.spec = diagram.read_json(ROOT / 'examples/service.architecture.json')
        self.request = {'type': 'architecture', 'icons': {'bindings': []}}

    def prepare(self, spec):
        diagram.dump(self.source, spec)
        return diagram.prepare_archify(self.source, self.request, {'prepared': {}})

    def test_non_object_and_invalid_header(self):
        for invalid in (None, True, [], 'model', 1):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.prepare(invalid)
        for key, value in (('diagram_type', []), ('diagram_type', 'workflow'),
                           ('schema_version', True), ('schema_version', 1.0), ('schema_version', None)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.prepare({**self.spec, key: value})

    def test_meta_shape(self):
        for invalid in (None, True, [], '', 1):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'meta'):
                self.prepare({**self.spec, 'meta': invalid})
        self.spec.pop('meta')
        with self.assertRaisesRegex(ValueError, 'meta'):
            self.prepare(self.spec)

    def test_all_five_node_collection_shapes(self):
        for kind, collection in diagram.COLLECTIONS.items():
            self.request['type'] = kind
            spec = {**self.spec, 'diagram_type': kind}
            for invalid in (None, True, {}, 'nodes', [None], [1], [[]], ['node']):
                with self.subTest(kind=kind, invalid=invalid), self.assertRaises(ValueError):
                    self.prepare({**spec, collection: invalid})
            spec.pop(collection, None)
            with self.assertRaisesRegex(ValueError, collection):
                self.prepare(spec)

    def test_invalid_ids_and_duplicate_ids_before_binding(self):
        for invalid in (None, True, 1, 1.0, [], {}, '', '  ', 'x\x00y'):
            spec = copy.deepcopy(self.spec)
            spec['components'][0]['id'] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'id'):
                self.prepare(spec)
        self.spec['components'].append(copy.deepcopy(self.spec['components'][0]))
        self.request['icons']['bindings'] = [{'node_id': 'gateway', 'query': 'fallback'}]
        with self.assertRaisesRegex(ValueError, 'Duplicate Archify node ID'):
            self.prepare(self.spec)

    def test_invalid_and_external_brand_rejected(self):
        for brand in ([], {}, True, 'https://invalid.example/icon.svg', 'ad-icon:untrusted'):
            spec = copy.deepcopy(self.spec)
            spec['components'][0]['brand'] = brand
            with self.subTest(brand=brand), self.assertRaisesRegex(ValueError, 'Source brands'):
                self.prepare(spec)


class FailureBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.outdir = Path(self.tmp.name) / 'out'
        self.outdir.mkdir()
        self.request = {'contract_version': 1, 'name': 'example', 'type': 'architecture', 'icons': {'bindings': []},
                        'output_dir': str(self.outdir),
                        'targets': [{'format': 'html', 'source': str(ROOT / 'examples/service.architecture.json')},
                                    {'format': 'drawio', 'source': str(ROOT / 'examples/service.drawio'), 'preview': False}]}
        doctor = patch('environment.doctor', return_value={'status': 'ready', 'executables':
                       {'node': '/runtime/node', 'drawio': '/runtime/drawio', 'python': sys.executable, 'font': '/fonts/cjk.ttc'}})
        doctor.start()
        self.addCleanup(doctor.stop)
        # Fail closed: renderer/process mocks must cover every execution in this class.
        process = patch('diagram.run_process', side_effect=AssertionError('Unexpected external process'))
        self.process = process.start()
        self.addCleanup(process.stop)

    @staticmethod
    def render(target, request, resolved, stage, tools):
        artifact = stage / (request['name'] + '.' + target['format'])
        artifact.write_text('mock artifact')
        return {}, [artifact]

    def assert_partial(self, result, statuses):
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([t['status'] for t in result['targets']], statuses)
        self.assertFalse(list(self.outdir.glob('.architecture-diagram-*')))
        for item in result['targets']:
            dest = self.outdir / (self.request['name'] + '-' + item['format'])
            self.assertEqual(dest.exists(), item['status'] == 'generated')
            if item['status'] == 'generated':
                self.assertTrue(Path(item['receipt']).is_file())

    def test_backend_exceptions_do_not_abort_sibling(self):
        for index, error in enumerate((ValueError('invalid model'), TypeError('bad shape'), AttributeError('bad meta'),
                                       KeyError('node'), RuntimeError('render failed'), OSError('cannot read'),
                                       subprocess.TimeoutExpired('render', 180))):
            self.request['name'] = f'error{index}'
            with self.subTest(error=type(error).__name__), \
                    patch('diagram.render_archify', side_effect=error), \
                    patch('diagram.render_drawio', side_effect=self.render):
                result = diagram.execute(self.request)
            self.assert_partial(result, ['failed', 'generated'])
            self.assertIn(type(error).__name__, result['targets'][0]['diagnostic'])

    def test_validation_and_delivery_timeouts_cleanup_stage(self):
        for phase in ('validate', 'deliver'):
            self.request['name'] = phase
            def run(args, env, timeout=180):
                if phase == 'deliver' and str(args[2]) == 'deliver':
                    Path(args[5]).write_text('incomplete html')
                if str(args[2]) == phase:
                    raise subprocess.TimeoutExpired(phase, timeout)
                return {'status': 'passed'}
            self.process.side_effect = run
            with self.subTest(phase=phase), patch('diagram.render_drawio', side_effect=self.render):
                result = diagram.execute(self.request)
            self.assert_partial(result, ['failed', 'generated'])
            self.assertIn('TimeoutExpired', result['targets'][0]['diagnostic'])

    def test_drawio_validation_and_png_failures_only_fail_drawio(self):
        self.request['targets'].reverse()
        self.request['targets'][0]['preview'] = True
        for phase in ('validate_timeout', 'validate_failure', 'validate_warning', 'export_timeout',
                      'export_failure', 'png_missing', 'png_invalid'):
            self.request['name'] = phase
            def run(args, env, timeout=180):
                exporting = '--export' in args
                if not exporting:
                    if phase == 'validate_timeout':
                        raise subprocess.TimeoutExpired('validate', timeout)
                    if phase == 'validate_failure':
                        raise RuntimeError('validation failed')
                    return {'stdout': '\u26a0 warning' if phase == 'validate_warning' else 'valid'}
                if phase == 'export_timeout':
                    raise subprocess.TimeoutExpired('drawio', timeout)
                if phase == 'export_failure':
                    raise RuntimeError('PNG export failed')
                if phase == 'png_invalid':
                    Path(args[args.index('--output') + 1]).write_bytes(b'not a png')
                return {}
            self.process.side_effect = run
            with self.subTest(phase=phase), patch('diagram.render_archify', side_effect=self.render):
                result = diagram.execute(self.request)
            self.assert_partial(result, ['failed', 'generated'])

    def test_multipage_png_failure_removes_earlier_png(self):
        tree = ET.parse(ROOT / 'examples/service.drawio').getroot()
        tree.append(copy.deepcopy(tree.find('diagram')))
        source = Path(self.tmp.name) / 'multipage.drawio'
        source.write_bytes(ET.tostring(tree))
        self.request['targets'].reverse()
        self.request['targets'][0].update(source=str(source), preview=True)
        def run(args, env, timeout=180):
            if '--export' in args:
                if args[args.index('--page-index') + 1] == '1':
                    raise RuntimeError('second page export failed')
                Path(args[args.index('--output') + 1]).write_bytes(b'\x89PNG\r\n\x1a\n')
            return {'stdout': ''}
        self.process.side_effect = run
        with patch('diagram.render_archify', side_effect=self.render):
            result = diagram.execute(self.request)
        self.assert_partial(result, ['failed', 'generated'])
        self.assertEqual(self.process.call_count, 3)
        self.assertFalse(list(self.outdir.rglob('*.png')))

    def test_existing_file_directory_and_symlinks_are_not_overwritten(self):
        for kind in ('file', 'directory', 'symlink', 'dangling'):
            self.request['name'] = kind
            dest = self.outdir / (kind + '-html')
            external = Path(self.tmp.name) / (kind + '-external')
            if kind == 'file':
                dest.write_text('keep')
            elif kind == 'directory':
                dest.mkdir()
            else:
                if kind == 'symlink':
                    external.mkdir()
                dest.symlink_to(external, target_is_directory=True)
            with self.subTest(kind=kind), patch('diagram.render_archify') as render, \
                    patch('diagram.render_drawio', side_effect=self.render):
                result = diagram.execute(self.request)
            render.assert_not_called()
            self.assertEqual([t['status'] for t in result['targets']], ['failed', 'generated'])
            self.assertTrue(os.path.lexists(dest))
            if kind == 'file':
                self.assertEqual(dest.read_text(), 'keep')
            elif kind == 'directory':
                self.assertEqual(list(dest.iterdir()), [])
            else:
                self.assertEqual(dest.readlink(), external)
                self.assertEqual(external.exists(), kind == 'symlink')
            self.assertFalse(list(self.outdir.glob('.architecture-diagram-*')))

    def test_late_destination_is_not_replaced_and_stale_stage_untouched(self):
        stale = self.outdir / '.architecture-diagram-stale'
        stale.mkdir()
        (stale / 'keep').write_text('previous interrupted run')
        def render(target, request, resolved, stage, tools):
            (self.outdir / 'example-html').mkdir()
            return self.render(target, request, resolved, stage, tools)
        with patch('diagram.render_archify', side_effect=render), patch('diagram.render_drawio', side_effect=self.render):
            result = diagram.execute(self.request)
        self.assertEqual([t['status'] for t in result['targets']], ['failed', 'generated'])
        self.assertEqual(list((self.outdir / 'example-html').iterdir()), [])
        self.assertEqual((stale / 'keep').read_text(), 'previous interrupted run')
        self.assertEqual(list(self.outdir.glob('.architecture-diagram-*')), [stale])

    def test_publication_failure_rolls_back_and_receipt_is_last(self):
        original = Path.rename
        moved = []
        def rename(path, dest):
            moved.append(Path(dest))
            if Path(dest).parent.name == 'example-html' and len(moved) == 2:
                raise OSError('publication failed')
            return original(path, dest)
        with patch('diagram.render_archify', side_effect=self.render), \
                patch('diagram.render_drawio', side_effect=self.render), patch.object(Path, 'rename', rename):
            result = diagram.execute(self.request)
        self.assert_partial(result, ['failed', 'generated'])
        self.assertEqual(moved[-1].name, 'receipt.json')

    def test_manifest_version_not_hardcoded(self):
        read_json = diagram.read_json
        def read(path):
            result = read_json(path)
            return {**result, 'version': '9.8.7-test'} if Path(path) == ROOT / 'manifest.json' else result
        with patch('diagram.read_json', side_effect=read), patch('diagram.render_archify', side_effect=self.render), \
                patch('diagram.render_drawio', side_effect=self.render):
            result = diagram.execute(self.request)
        self.assertEqual(result['status'], 'generated')
        for target in result['targets']:
            receipt = read_json(target['receipt'])
            self.assertEqual(receipt['skill_version'], '9.8.7-test')
            self.assertEqual(receipt['module_manifest'], diagram.digest(ROOT / 'manifest.json'))

    def test_invalid_manifest_cannot_emit_wrong_version(self):
        read_json = diagram.read_json
        with patch('diagram.read_json', side_effect=lambda p: {} if Path(p) == ROOT / 'manifest.json' else read_json(p)), \
                patch('diagram.render_archify', side_effect=self.render), patch('diagram.render_drawio', side_effect=self.render):
            result = diagram.execute(self.request)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(list(self.outdir.iterdir()), [])

    def test_gif_uses_isolated_python_and_probed_font(self):
        stage = Path(self.tmp.name)
        target = {'source': 'template.json'}
        def run(args, env, timeout):
            self.assertEqual(args[:3], [sys.executable, '-I', '-B'])
            self.assertEqual(env['ARCHITECTURE_DIAGRAM_FONT'], '/probed/cjk.ttc')
            self.assertEqual(timeout, 300)
            (stage / 'example.gif').write_bytes(b'GIF89a')
            (stage / 'example.png').write_bytes(b'png mock')
            return {}
        self.process.side_effect = run
        diagram.render_gif(target, self.request, {}, stage, {'python': sys.executable, 'font': '/probed/cjk.ttc'})
        self.process.assert_called_once()


class DrawioRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='drawio-render-')
        self.addCleanup(self.tmp.cleanup)
        self.stage = Path(self.tmp.name)
        self.target = {'source': str(ROOT / 'examples/p0-order-platform.drawio')}
        self.request = {'name': 'multipage', 'icons': {'bindings': []}}
        self.tools = {'node': '/runtime/node', 'drawio': '/runtime/draw io'}
        self.output = self.stage / 'multipage.drawio'
        self.validation_argv = [self.tools['node'], ROOT / 'modules/drawio/scripts/validate.mjs', self.output]
        process = patch('diagram.run_process', side_effect=AssertionError('Unexpected external process'))
        self.process = process.start()
        self.addCleanup(process.stop)

    def test_multipage_png_export_argv_and_artifacts(self):
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jF1sAAAAASUVORK5CYII=')
        images = [self.stage / 'multipage-01.png', self.stage / 'multipage-02.png']
        expected = [self.validation_argv] + [
            [self.tools['drawio'], '--export', '--format', 'png', '--scale', '2', '--border', '24',
             '--page-index', str(page), '--output', image, self.output]
            for page, image in enumerate(images)]

        def run(args, env):
            if args != self.validation_argv:
                self.assertIn(args, expected[1:])
                Path(args[args.index('--output') + 1]).write_bytes(png)
            return {'stdout': 'valid'}

        self.process.side_effect = run
        checks, files = diagram.render_drawio(self.target, self.request, {'prepared': {}}, self.stage, self.tools)
        self.assertEqual([call.args[0] for call in self.process.call_args_list], expected)
        self.assertEqual(files, [self.output, *images])
        self.assertEqual(checks['pages'], 2)
        self.assertEqual(checks['render'], 'passed')
        self.assertEqual(len(ET.parse(self.output).getroot().findall('diagram')), 2)
        for image in images:
            self.assertEqual(image.read_bytes(), png)

    def test_preview_false_validates_xml_without_export(self):
        self.process.side_effect = None
        self.process.return_value = {'stdout': 'valid'}
        checks, files = diagram.render_drawio({**self.target, 'preview': False}, self.request,
                                             {'prepared': {}}, self.stage, {'node': self.tools['node']})
        self.process.assert_called_once_with(self.validation_argv, diagram.environment_for())
        self.assertEqual(checks['pages'], 2)
        self.assertEqual(checks['render'], 'not_requested')
        self.assertEqual(files, [self.output])
        self.assertTrue(self.output.is_file())
        self.assertFalse(list(self.stage.glob('*.png')))


class ChildEnvironmentTests(unittest.TestCase):
    def test_render_and_preflight_share_sanitized_environment(self):
        from environment import _child_env
        unsafe = {'PYTHONPATH': '/evil', 'PYTHONHOME': '/evil', 'PYTHONSTARTUP': '/evil',
                  'PYTHONUSERBASE': '/evil', 'PYTHONINSPECT': '1', 'PYTHONDONTWRITEBYTECODE': '0',
                  'NODE_OPTIONS': '--require /evil', 'NODE_PATH': '/evil', 'PIP_TARGET': '/evil',
                  'ICON_PERSONAL_ROOT': '/evil', 'ARCH_ICONS_ROOT': '/evil',
                  'ARCHITECTURE_DIAGRAM_ICONS': '/evil', 'ARCHITECTURE_DIAGRAM_THEME': 'dark'}
        kept = {'PATH': '/runtime', 'DRAWIO_BIN': '/apps/draw io', 'CJK_FONT': '/fonts/cjk.ttc',
                'ARCHITECTURE_DIAGRAM_FONT': '/fonts/selected.ttc', 'HOME': '/home/user', 'LANG': 'zh_CN.UTF-8'}
        with patch.dict(os.environ, {**unsafe, **kept}, clear=True):
            before = dict(os.environ)
            env = diagram.environment_for()
            self.assertEqual(env, _child_env())
            self.assertEqual(env, {**kept, 'PYTHONDONTWRITEBYTECODE': '1'})
            registry = Path('/trusted/registry.json')
            self.assertEqual(diagram.environment_for(registry), {**env, 'ARCHITECTURE_DIAGRAM_ICONS': str(registry)})
            self.assertEqual(dict(os.environ), before)


class DrawioTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / 'examples/service.drawio').read_bytes()
        svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#f00" d="M0 0h24v24H0z"/></svg>'
        self.prepared = {'gateway': {'svg_data_uri': 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode(), 'theme': {'dark_backdrop': True}}}

    def test_child_icon_preserves_business_node_and_edges(self):
        output, count = attach_icons(self.raw, self.prepared, [{'node_id': 'gateway', 'query': 'Nginx'}, {'node_id': 'orders', 'query': 'unknown'}])
        tree = ET.fromstring(output)
        cells = {cell.get('id'): cell for cell in tree.findall('.//mxCell')}
        self.assertEqual(count, 1)
        self.assertEqual(cells['gateway'].get('value'), '接入网关<br>Nginx')
        self.assertEqual(cells['gateway-orders'].get('source'), 'gateway')
        self.assertEqual(cells['gateway__ad_icon'].get('parent'), 'gateway')
        self.assertEqual(cells['gateway__ad_icon'].get('connectable'), '0')
        self.assertIn('订单服务', cells['orders'].get('value'))
        self.assertNotIn('orders__ad_icon', cells)
        self.assertNotIn(b' />', output)

    def test_bad_edge(self):
        with self.assertRaises(ValueError):
            parse_diagram(self.raw.replace(b'target="store"', b'target="missing"'))

    def test_dtd_and_scripts(self):
        for raw in (b'<!DOCTYPE mxfile>' + self.raw, self.raw.replace('Nginx'.encode(), b'&lt;script&gt;alert(1)&lt;/script&gt;')):
            with self.assertRaises(ValueError):
                parse_diagram(raw)

    def test_unknown_node(self):
        with self.assertRaises(ValueError):
            attach_icons(self.raw, {}, [{'node_id': 'absent', 'query': 'x'}])

    def test_small_node_is_not_enlarged(self):
        with self.assertRaises(ValueError):
            attach_icons(self.raw.replace(b'width="200"', b'width="90"'), self.prepared, [{'node_id': 'gateway', 'query': 'Nginx'}])

    def test_ambiguous_page_binding(self):
        tree = ET.fromstring(self.raw)
        tree.append(copy.deepcopy(tree.find('diagram')))
        with self.assertRaises(ValueError):
            attach_icons(ET.tostring(tree), self.prepared, [{'node_id': 'gateway', 'query': 'Nginx'}])

    def check_acceptance(self, case, icon_nodes):
        from acceptance import check_drawio
        source = ROOT / 'examples/p0-order-platform.drawio'
        prepared = {node: self.prepared['gateway'] for node in icon_nodes}
        output, _ = attach_icons(source.read_bytes(), prepared, [{'node_id': node} for node in icon_nodes])
        tree = ET.fromstring(output)
        # Acceptance must identify injected children by structure, not an ID suffix.
        for index, cell in enumerate(tree.findall('.//mxCell[@connectable="0"]')):
            cell.set('id', f'injected-child-{index}')
        check_drawio(source, io.BytesIO(ET.tostring(tree)), case)

    def test_acceptance_binding_checks_allow_renamed_icon_children(self):
        for case, nodes in (('order-platform', ['primary', 'archive']),
                            ('multipage', ['primary', 'archive', 'commit'])):
            with self.subTest(case=case):
                self.check_acceptance(case, nodes)

    def test_acceptance_rejects_missing_resolved_icons(self):
        for case, nodes in (('order-platform', ['primary', 'archive']),
                            ('multipage', ['primary', 'archive', 'commit'])):
            for missing in nodes:
                with self.subTest(case=case, missing=missing), self.assertRaisesRegex(ValueError, missing):
                    self.check_acceptance(case, [node for node in nodes if node != missing])

    def test_acceptance_rejects_fallback_and_unbound_icons(self):
        for case, nodes, forbidden in (('order-platform', ['primary', 'archive'], ['orders', 'commit']),
                                       ('multipage', ['primary', 'archive', 'commit'], ['orders', 'manual'])):
            for extra in forbidden:
                with self.subTest(case=case, extra=extra), self.assertRaisesRegex(ValueError, extra):
                    self.check_acceptance(case, [*nodes, extra])


if __name__ == '__main__':
    unittest.main()
