"""Offline standard-library tests for the public read-only icon boundary.

Run: python3 -B -m unittest discover -s <skill>/tests -p test_icons.py -v
"""
from pathlib import Path
import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

SKILL = Path(__file__).resolve().parents[1]
ENTRY = SKILL / 'scripts' / 'icons.py'
icons = types.ModuleType('architecture_icons_under_test')
icons.__file__ = str(ENTRY)
exec(compile(ENTRY.read_bytes(), str(ENTRY), 'exec'), icons.__dict__)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill="#12AB34" d="M0 0h24v24H0z"/></svg>'
OTHER_SVG = SVG.replace(b'#12AB34', b'#E55A22')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def svg(body, attributes=''):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" ' + attributes + '>' + body + '</svg>').encode()


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='architecture-icons-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'pack'
        (self.root / 'catalog').mkdir(parents=True)
        (self.root / 'assets').mkdir()
        self.catalog = {'schema_version': 1, 'products': [], 'variants': []}
        self.add_product('generic/alpha', 'Alpha', ['alpha', 'shared'])
        self.add_product('generic/beta', 'Beta', ['beta', 'shared'])
        self.add_product('aws/alpha', 'Cloud Alpha', ['alpha'])
        self.add_product('generic/pending', 'Pending', ['pending'], status='pending')
        self.add_variant('generic/alpha', 'alternate', OTHER_SVG)
        self.add_variant('generic/alpha', 'pending', OTHER_SVG, status='pending')
        self.flush()

    def asset(self, raw):
        sha = digest(raw)
        rel = f'assets/{sha[:2]}/{sha}.svg'
        path = self.root / rel
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(raw)
        return {'path': rel, 'sha256': sha, 'format': 'svg', 'representation': 'vector', 'bytes': len(raw)}

    def add_product(self, pid, name, aliases, status='ready'):
        product = {'id': pid, 'provider': pid.split('/')[0], 'name': name, 'aliases': aliases,
                   'status': status, 'kind': 'component', 'default_variant': pid + '@default' if status == 'ready' else None}
        self.catalog['products'].append(product)
        self.add_variant(pid, 'default', SVG, status=status)
        return product

    def add_variant(self, pid, suffix, raw, status='ready'):
        variant = {'id': pid + '@' + suffix, 'product_id': pid, 'style': suffix, 'status': status,
                   'asset': self.asset(raw), 'theme': {'preserve_colors': True, 'dark_backdrop': False}}
        self.catalog['variants'].append(variant)
        return variant

    def flush(self):
        (self.root / 'catalog/index.json').write_text(json.dumps(self.catalog), encoding='utf-8')

    def bind(self, query='generic/alpha', **kwargs):
        return icons.resolve_bindings([{'node_id': 'node', 'query': query, **kwargs}], self.root)

    def assert_error(self, result, code=None):
        self.assertEqual(result['icons'][0]['status'], 'error', result)
        self.assertEqual(result['prepared'], {})
        self.assertEqual(result['icons'][0]['fallback'], 'card')
        if code:
            self.assertEqual(result['icons'][0]['diagnostic']['code'], code, result)

    def engine_result(self):
        return icons._query(self.root, 'resolve', 'generic/alpha')

    def fake_process(self, result, code=0):
        return subprocess.CompletedProcess([], code, json.dumps(result).encode(), b'')

    def test_resolved_contract_and_separate_hashes(self):
        result = self.bind()
        self.assertEqual(set(result), {'contract_version', 'icons', 'prepared'})
        self.assertEqual(result['contract_version'], 2)
        card = result['icons'][0]
        self.assertEqual((card['node_id'], card['query'], card['status']), ('node', 'generic/alpha', 'resolved'))
        prepared = result['prepared']['node']
        self.assertEqual(set(prepared), {'svg_data_uri', 'svg_sha256', 'source_sha256', 'product_id', 'variant_id', 'theme'})
        self.assertTrue(prepared['svg_data_uri'].startswith('data:image/svg+xml;base64,'))
        safe = base64.b64decode(prepared['svg_data_uri'].split(',', 1)[1], validate=True)
        self.assertEqual(prepared['source_sha256'], digest(SVG))
        self.assertEqual(prepared['svg_sha256'], digest(safe))
        self.assertNotEqual(prepared['source_sha256'], prepared['svg_sha256'])
        self.assertEqual(card['source_sha256'], prepared['source_sha256'])
        self.assertEqual(card['svg_sha256'], prepared['svg_sha256'])
        self.assertEqual(prepared['theme'], {'preserve_colors': True, 'dark_backdrop': False})
        self.assertIn(b'#12AB34', safe)
        ET.fromstring(safe)

    def test_empty_bindings(self):
        self.assertEqual(icons.resolve_bindings([]), {'contract_version': 2, 'icons': [], 'prepared': {}})

    def test_invalid_inputs_and_duplicates_raise_before_engine(self):
        invalid = [None, {}, (), '[]', [None], [{}], [{'node_id': 'n'}],
                   [{'node_id': '', 'query': 'x'}], [{'node_id': 1, 'query': 'x'}],
                   [{'node_id': 'n', 'query': []}], [{'node_id': 'n', 'query': ' '}],
                   [{'node_id': 'n', 'query': 'x', 'provider': None}],
                   [{'node_id': 'n', 'query': 'x', 'variant': 1}],
                   [{'node_id': 'n', 'query': 'x', 'extra': True}],
                   [{'node_id': 'n\x00', 'query': 'x'}],
                   [{'node_id': 'n', 'query': 'x'}, {'node_id': 'n', 'query': 'y'}]]
        with mock.patch.object(icons, '_run_engine') as runner:
            for value in invalid:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    icons.resolve_bindings(value, self.root)
            runner.assert_not_called()

    def test_missing_ambiguous_pending_remain_ordered_cards(self):
        result = icons.resolve_bindings([
            {'node_id': 'missing', 'query': 'never-existed'},
            {'node_id': 'ambiguous', 'query': 'shared'},
            {'node_id': 'pending', 'query': 'generic/pending'},
            {'node_id': 'ready', 'query': 'generic/alpha'},
        ], self.root)
        self.assertEqual([row['status'] for row in result['icons']], ['not_found', 'ambiguous', 'pending_review', 'resolved'])
        self.assertEqual([row['node_id'] for row in result['icons']], ['missing', 'ambiguous', 'pending', 'ready'])
        self.assertEqual(set(result['prepared']), {'ready'})
        for row in result['icons'][:3]:
            self.assertEqual(row['fallback'], 'card')
            self.assertTrue(row['diagnostic']['message'])
            self.assertIsNone(row['source_sha256'])

    def test_fuzzy_match_is_not_selected(self):
        self.assertEqual(self.bind('Alp')['icons'][0]['status'], 'no_exact_match')

    def test_provider_constraint_and_independent_preference(self):
        self.assertEqual(self.bind('alpha')['icons'][0]['product_id'], 'generic/alpha')
        aws = self.bind('alpha', provider='aws')
        self.assertEqual(aws['icons'][0]['product_id'], 'aws/alpha')
        self.assertEqual(aws['icons'][0]['provider'], 'aws')
        absent = self.bind('alpha', provider='tencent')
        self.assertEqual(absent['icons'][0]['status'], 'not_found')
        self.assertFalse(absent['prepared'])
        wrong_id = self.bind('generic/alpha', provider='aws')
        self.assertNotEqual(wrong_id['icons'][0]['status'], 'resolved')

    def test_component_vendor_reuse_keeps_identity_diagnostic(self):
        self.add_product('aliyun/redis', 'Cloud Redis', ['Cloud Redis'])
        self.add_product('aws/redis', 'AWS Redis', ['AWS Redis'])
        mapping = {'schema_version': 1, 'components': [{'id': 'component/redis', 'name': 'Redis', 'aliases': [],
                   'vendor_product_ids': ['aliyun/redis', 'aws/redis'], 'preferred_svg_product_id': 'aliyun/redis'}]}
        (self.root / 'catalog/component-mappings.json').write_text(json.dumps(mapping))
        self.flush()
        row = self.bind('Redis')['icons'][0]
        self.assertEqual(row['match_type'], 'vendor_svg_reuse')
        self.assertEqual(row['display_name'], 'Redis')
        self.assertEqual(row['provider'], 'aliyun')
        self.assertIn('不表示部署在阿里云', row['diagnostic']['message'])
        self.assertEqual(self.bind('Redis', provider='aws')['icons'][0]['product_id'], 'aws/redis')
        self.assertEqual(self.bind('Redis', provider='huawei')['icons'][0]['status'], 'not_found')

    def test_variant_override_pending_wrong_product_and_no_writes(self):
        before = self.snapshot(self.root)
        default = self.bind()['icons'][0]
        alternate = self.bind(variant='generic/alpha@alternate')['icons'][0]
        self.assertEqual(alternate['variant_id'], 'generic/alpha@alternate')
        self.assertEqual(alternate['selection'], 'explicit_variant')
        self.assertNotEqual(default['source_sha256'], alternate['source_sha256'])
        for value in ('missing', 'generic/beta@default'):
            result = self.bind(variant=value)
            self.assertEqual(result['icons'][0]['status'], 'invalid_variant')
            self.assertFalse(result['prepared'])
        self.assertEqual(self.bind(variant='generic/alpha@pending')['icons'][0]['status'], 'pending_review')
        self.assertEqual(before, self.snapshot(self.root))

    def test_preferences_are_read_only(self):
        path = self.root / 'catalog/preferences.json'
        path.write_text(json.dumps({'schema_version': 1, 'defaults': {'generic/alpha': 'generic/alpha@alternate'}}))
        before = path.read_bytes()
        self.assertEqual(self.bind()['icons'][0]['selection'], 'user_preference')
        self.assertEqual(self.bind(variant='generic/alpha@default')['icons'][0]['selection'], 'explicit_variant')
        self.assertEqual(path.read_bytes(), before)

    @staticmethod
    def snapshot(root):
        return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in root.rglob('*') if p.is_file()}

    def test_custom_pack_cannot_supply_python_and_no_pycache_writes(self):
        malicious = 'raise RuntimeError("RESOURCE PYTHON MUST NEVER EXECUTE")\n'
        for rel in ('library.py', 'iconlib.py', 'sitecustomize.py', 'usercustomize.py', 'scripts/iconlib.py',
                    'modules/icons/engine/iconlib.py', 'modules/icons/engine/library.py'):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(malicious)
        before = self.snapshot(self.root)
        engine_before = self.snapshot(icons.ENGINE_DIR)
        with mock.patch.dict(os.environ, {'ICON_PERSONAL_ROOT': str(self.root / 'wrong'), 'PYTHONPATH': str(self.root)}):
            result = self.bind()
        self.assertEqual(result['icons'][0]['status'], 'resolved')
        self.assertEqual(before, self.snapshot(self.root))
        self.assertEqual(engine_before, self.snapshot(icons.ENGINE_DIR))
        self.assertFalse(list(self.root.rglob('__pycache__')))

    def test_isolated_subprocess_options_and_old_environment_removed(self):
        real_run = subprocess.run
        with mock.patch.object(icons.subprocess, 'run', wraps=real_run) as runner:
            with mock.patch.dict(os.environ, {'ICON_PERSONAL_ROOT': '/bogus', 'PYTHONPATH': '/bogus'}):
                self.assertEqual(self.bind()['icons'][0]['status'], 'resolved')
        args, kwargs = runner.call_args
        self.assertEqual(args[0][1:4], ['-I', '-S', '-B'])
        self.assertNotIn('ICON_PERSONAL_ROOT', kwargs['env'])
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        self.assertEqual(kwargs['env']['PYTHONDONTWRITEBYTECODE'], '1')
        self.assertEqual(kwargs['cwd'], str(icons.ENGINE_DIR))
        self.assertEqual(kwargs['timeout'], icons.PROCESS_TIMEOUT)

    def test_default_root_ignores_legacy_environment(self):
        with mock.patch.dict(os.environ, {'ICON_PERSONAL_ROOT': str(self.root), 'PYTHONPATH': str(self.root)}):
            result = icons.resolve_bindings([{'node_id': 'real', 'query': 'aliyun/acm'}])
        self.assertEqual(result['icons'][0]['status'], 'resolved', result)
        self.assertTrue(result['icons'][0]['asset_path'].startswith(str(icons.DEFAULT_ROOT)))

    def test_bad_root_yields_per_node_errors(self):
        result = icons.resolve_bindings([{'node_id': 'a', 'query': 'x'}, {'node_id': 'b', 'query': 'x'}], self.root / 'missing')
        self.assertEqual([row['diagnostic']['code'] for row in result['icons']], ['root_error', 'root_error'])
        self.assertFalse(result['prepared'])

    def test_bad_catalog_returns_error(self):
        for raw in ('{}', '{"schema_version":2}', '{"schema_version":1,"schema_version":1}', 'not json'):
            with self.subTest(raw=raw):
                (self.root / 'catalog/index.json').write_text(raw)
                self.assert_error(self.bind())

    def test_hash_mismatch_and_missing_asset(self):
        path = self.root / self.catalog['variants'][0]['asset']['path']
        path.write_bytes(OTHER_SVG)
        self.assert_error(self.bind(), 'hash_mismatch')
        path.unlink()
        self.assert_error(self.bind(), 'asset_missing')

    def test_catalog_path_escape_and_absolute_paths_rejected(self):
        asset = self.catalog['variants'][0]['asset']
        for relative in ('../outside.svg', '/tmp/outside.svg', 'catalog/index.json',
                         'assets/../catalog/index.json', 'assets//x.svg', 'assets\\xx\\x.svg'):
            with self.subTest(relative=relative):
                asset['path'] = relative
                self.flush()
                self.assert_error(self.bind(), 'path_error')

    def test_hash_path_must_be_content_addressed(self):
        self.catalog['variants'][0]['asset']['sha256'] = '0' * 64
        self.flush()
        self.assert_error(self.bind(), 'hash_mismatch')

    def test_symlink_file_rejected_even_if_digest_matches(self):
        path = self.root / self.catalog['variants'][0]['asset']['path']
        external = self.root.parent / 'external.svg'
        external.write_bytes(SVG)
        path.unlink()
        path.symlink_to(external)
        self.assert_error(self.bind(), 'path_error')

    def test_symlink_directory_and_assets_root_rejected(self):
        path = self.root / self.catalog['variants'][0]['asset']['path']
        original = path.parent
        external = self.root.parent / 'external-assets'
        original.rename(external)
        original.symlink_to(external, target_is_directory=True)
        self.assert_error(self.bind(), 'path_error')
        original.unlink()
        external.rename(original)
        assets = self.root / 'assets'
        assets.rename(external)
        assets.symlink_to(external, target_is_directory=True)
        self.assert_error(self.bind(), 'path_error')

    def test_in_root_symlinks_are_also_rejected(self):
        path = self.root / self.catalog['variants'][0]['asset']['path']
        copy_path = self.root / 'assets/copy.svg'
        copy_path.write_bytes(SVG)
        path.unlink()
        path.symlink_to(copy_path)
        self.assert_error(self.bind(), 'path_error')

    def test_nonregular_asset_is_rejected_without_blocking(self):
        path = self.root / self.catalog['variants'][0]['asset']['path']
        path.unlink()
        os.mkfifo(path)
        self.assert_error(self.bind(), 'path_error')

    def test_response_absolute_relative_consistency(self):
        original = self.engine_result()
        for absolute in ('assets/x.svg', str(self.root.parent / 'outside.svg'),
                         str(self.root) + '/assets/../' + original['asset_relative_path']):
            with self.subTest(path=absolute):
                payload = {**original, 'asset_path': absolute}
                with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
                    self.assert_error(self.bind(), 'path_error')

    def test_response_schema_and_contract_errors(self):
        original = self.engine_result()
        cases = []
        missing = copy.deepcopy(original)
        del missing['theme']
        cases.append((missing, 'schema_error'))
        bad_theme = copy.deepcopy(original)
        bad_theme['theme']['preserve_colors'] = 1
        cases.append((bad_theme, 'schema_error'))
        cases.extend([({**original, 'contract_version': value}, 'contract_error') for value in (1, '2', True, None)])
        cases.extend([({**original, 'sha256': 'bad'}, 'schema_error'),
                      ({'contract_version': 2, 'status': 'invented'}, 'schema_error'),
                      ({'contract_version': 2, 'query': 'x', 'matches': []}, 'schema_error')])
        for payload, code in cases:
            with self.subTest(payload=payload), mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
                self.assert_error(self.bind(), code)

    def test_huge_integer_schema_errors_do_not_fail_other_bindings(self):
        fields = [(self.catalog['variants'][0]['theme'], 'preserve_colors'),
                  (self.catalog['products'][0], 'kind')]
        for record, key in fields:
            with self.subTest(field=key), mock.patch.dict(record, {key: 10 ** 400}):
                self.flush()
                result = icons.resolve_bindings([
                    {'node_id': 'invalid', 'query': 'generic/alpha'},
                    {'node_id': 'valid', 'query': 'generic/beta'},
                ], self.root)
                self.assertEqual([row['status'] for row in result['icons']], ['error', 'resolved'])
                self.assertEqual(result['icons'][0]['diagnostic']['code'], 'schema_error')
                self.assertEqual(result['icons'][0]['fallback'], 'card')
                self.assertEqual(set(result['prepared']), {'valid'})

    def test_schema_numeric_checks_handle_huge_integers_and_nonfinite_floats(self):
        check = icons._library.check_schema
        huge = 10 ** 400
        for rule in ({}, {'type': 'integer'}, {'type': 'number', 'minimum': 0}):
            with self.subTest(rule=rule):
                self.assertEqual(check(huge, rule), [])
        self.assertTrue(check(-huge, {'minimum': 0}))
        self.assertTrue(check(huge, {'exclusiveMinimum': huge}))
        self.assertTrue(check(True, {'type': 'number'}))
        for value in (float('inf'), float('-inf'), float('nan')):
            for rule in ({}, {'type': 'number'}):
                with self.subTest(value=value, rule=rule):
                    self.assertTrue(check(value, rule))

    def test_invalid_json_and_duplicate_keys_rejected(self):
        for raw in (b'not json', b'[]', b'{"contract_version":2,"contract_version":2}', b'NaN', b'\xff'):
            with self.subTest(raw=raw), mock.patch.object(icons, '_run_engine', return_value=subprocess.CompletedProcess([], 0, raw, b'')):
                self.assert_error(self.bind())

    def test_broken_or_unsupported_bundled_schema_returns_error(self):
        schema_path = self.root.parent / 'test-schema.json'
        for raw in ('not json', '{}', '{"$ref":[]}', '{"unsupportedKeyword":true}'):
            schema_path.write_text(raw)
            with self.subTest(raw=raw), mock.patch.object(icons, 'SCHEMA_PATH', schema_path):
                self.assert_error(self.bind(), 'schema_error')
        schema_path.unlink()
        with mock.patch.object(icons, 'SCHEMA_PATH', schema_path):
            self.assert_error(self.bind(), 'schema_error')

    def test_response_extensions_cannot_crash_or_influence_bindings(self):
        payload = {**self.engine_result(), 'candidates': [None], 'node_id': 'wrong', 'query': 'wrong'}
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
            result = self.bind(provider='generic')
        self.assertEqual(result['icons'][0]['status'], 'resolved')
        self.assertEqual(result['icons'][0]['node_id'], 'node')
        self.assertEqual(result['icons'][0]['query'], 'generic/alpha')
        for value in ([], {}, 1):
            payload = {'contract_version': 2, 'error': 'broken engine', 'error_code': value}
            with self.subTest(value=value), mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload, 1)):
                self.assert_error(self.bind(), 'engine_error')

    def test_oversized_asset_rejected_before_parsing(self):
        raw = b' ' * (icons._library.MAX_SVG_BYTES + 1)
        self.catalog['variants'][0]['asset'] = self.asset(raw)
        self.flush()
        self.assert_error(self.bind(), 'size_limit')

    def test_serialized_image_byte_limit_before_prepared(self):
        limit = 1_048_576
        overhead = len(icons._library.safe_svg(svg('<desc>x</desc>'))) - 1
        bindings = []
        for node_id, size in (('near', limit - 1), ('exact', limit), ('over', limit + 1)):
            padding = size - overhead
            text = 'é' * (padding // 2) + 'x' * (padding % 2)
            raw = svg('<desc>' + text + '</desc>')
            self.assertLess(len(raw), limit)
            variant = self.add_variant('generic/alpha', node_id, raw)
            bindings.append({'node_id': node_id, 'query': 'generic/alpha', 'variant': variant['id']})
        bindings.append({'node_id': 'valid', 'query': 'generic/beta'})
        self.flush()
        result = icons.resolve_bindings(bindings, self.root)
        self.assertEqual([row['status'] for row in result['icons']], ['resolved', 'resolved', 'error', 'resolved'])
        self.assertEqual(set(result['prepared']), {'near', 'exact', 'valid'})
        for node_id, size in (('near', limit - 1), ('exact', limit)):
            prepared = result['prepared'][node_id]
            safe = base64.b64decode(prepared['svg_data_uri'].split(',', 1)[1], validate=True)
            self.assertEqual(len(safe), size)
            self.assertEqual(prepared['svg_sha256'], digest(safe))
        rejected = result['icons'][2]
        self.assertEqual(rejected['diagnostic']['code'], 'size_limit')
        self.assertEqual(rejected['fallback'], 'card')
        self.assertIsNotNone(rejected['source_sha256'])
        self.assertIsNone(rejected['svg_sha256'])

    def test_process_exit_and_response_disagreement(self):
        payload = self.engine_result()
        for code in (-9, 3, 1, 2):
            with self.subTest(code=code), mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload, code)):
                self.assert_error(self.bind(), 'process_error')
        fallback = icons._query(self.root, 'resolve', 'missing')
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(fallback, 0)):
            self.assert_error(self.bind(), 'process_error')
        error = {'contract_version': 2, 'error': 'broken engine'}
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(error, 1)):
            self.assert_error(self.bind(), 'engine_error')

    def test_timeout_and_spawn_failure_return_error(self):
        for exception, code in ((subprocess.TimeoutExpired('engine', 30), 'process_timeout'), (OSError('denied'), 'process_error')):
            with self.subTest(code=code), mock.patch.object(icons.subprocess, 'run', side_effect=exception):
                self.assert_error(self.bind(), code)

    def test_untrusted_response_provider_and_variant_rechecked(self):
        payload = self.engine_result()
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
            self.assert_error(self.bind(provider='aws'), 'provider_mismatch')
            self.assert_error(self.bind(variant='generic/alpha@alternate'), 'variant_mismatch')

    def test_untrusted_prepared_payload_is_never_forwarded(self):
        payload = {**self.engine_result(), 'svg_data_uri': 'data:text/html,<script/>', 'prepared': {'evil': True}}
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
            result = self.bind()
        self.assertEqual(result['icons'][0]['status'], 'resolved')
        self.assertNotIn('svg_data_uri', result['icons'][0])
        self.assertNotIn('prepared', result['icons'][0])
        self.assertTrue(result['prepared']['node']['svg_data_uri'].startswith('data:image/svg+xml;base64,'))

    def test_parent_rechecks_hash_after_child_resolution(self):
        payload = self.engine_result()
        Path(payload['asset_path']).write_bytes(OTHER_SVG)
        with mock.patch.object(icons, '_run_engine', return_value=self.fake_process(payload)):
            self.assert_error(self.bind(), 'hash_mismatch')

    def test_unsafe_svg_does_not_fail_other_bindings(self):
        self.catalog['variants'][0]['asset'] = self.asset(svg('<script>alert(1)</script>'))
        self.flush()
        result = icons.resolve_bindings([{'node_id': 'unsafe', 'query': 'generic/alpha'},
                                         {'node_id': 'safe', 'query': 'generic/beta'}], self.root)
        self.assertEqual([row['status'] for row in result['icons']], ['error', 'resolved'])
        self.assertEqual(result['icons'][0]['diagnostic']['code'], 'unsafe_svg')
        self.assertEqual(set(result['prepared']), {'safe'})
        self.assertIsNotNone(result['icons'][0]['source_sha256'])
        self.assertIsNone(result['icons'][0]['svg_sha256'])

    def test_security_failures_surface_as_api_cards(self):
        payloads = [svg('<script/>'), svg('<g onclick="alert(1)"/>'), svg('<image href="https://evil/x"/>'),
                    svg('<use href="//evil/x"/>'), svg('<foreignObject/>'),
                    b'<!DOCTYPE svg SYSTEM "file:///etc/passwd">' + SVG,
                    svg('<style>path{fill:red}</style>'), svg('<path fill="url(#missing)"/>')]
        for raw in payloads:
            with self.subTest(raw=raw):
                self.catalog['variants'][0]['asset'] = self.asset(raw)
                self.flush()
                self.assert_error(self.bind(), 'unsafe_svg')

    def test_two_instances_are_separate_image_payloads(self):
        raw = svg('<defs><linearGradient id="shared"><stop stop-color="#aabbcc"/></linearGradient></defs><path fill="url(#shared)" d="M0 0h5"/>')
        self.catalog['variants'][0]['asset'] = self.asset(raw)
        self.flush()
        result = icons.resolve_bindings([{'node_id': 'first', 'query': 'generic/alpha'}, {'node_id': 'second', 'query': 'generic/alpha'}], self.root)
        self.assertEqual(set(result['prepared']), {'first', 'second'})
        self.assertEqual(result['prepared']['first']['svg_data_uri'], result['prepared']['second']['svg_data_uri'])
        self.assertNotIn('svg', result['prepared']['first'])

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ENTRY), '--root', str(self.root), *args],
                              capture_output=True, timeout=20, check=False)

    def test_cli_search_show_resolve_bind(self):
        search = self.cli('search', 'alpha', '--provider', 'aws', '--limit', '1')
        self.assertEqual(search.returncode, 0, search.stderr)
        matches = json.loads(search.stdout)['matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['provider'], 'aws')
        show = self.cli('show', 'generic/alpha')
        self.assertEqual(show.returncode, 0, show.stderr)
        self.assertEqual(json.loads(show.stdout)['product']['id'], 'generic/alpha')
        resolve = self.cli('resolve', 'alpha', '--provider', 'aws', '--node-id', 'aws-node')
        self.assertEqual(resolve.returncode, 0, resolve.stderr)
        self.assertIn('aws-node', json.loads(resolve.stdout)['prepared'])
        variant = self.cli('resolve', 'generic/alpha', '--variant', 'generic/alpha@alternate', '--node-id', 'alt')
        self.assertEqual(json.loads(variant.stdout)['prepared']['alt']['variant_id'], 'generic/alpha@alternate')
        bindings = self.root.parent / 'bindings.json'
        bindings.write_text(json.dumps([{'node_id': 'missing', 'query': 'nothing'}, {'node_id': 'ready', 'query': 'generic/alpha'}]))
        bind = self.cli('bind', str(bindings))
        self.assertEqual(bind.returncode, 2)
        self.assertEqual(set(json.loads(bind.stdout)['prepared']), {'ready'})
        self.assertEqual(self.cli('resolve', 'absent').returncode, 2)
        self.assertEqual(self.cli('search', 'alpha', '--limit', '0').returncode, 1)

    def test_cli_bind_bad_inputs_and_maintenance_commands_denied(self):
        bindings = self.root.parent / 'bindings.json'
        for content in ('{}', '[{"node_id":"n","query":"x"},{"node_id":"n","query":"y"}]', 'invalid'):
            bindings.write_text(content)
            result = self.cli('bind', str(bindings))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)['status'], 'error')
        before = self.snapshot(self.root)
        for command in ('prefer', 'import', 'review', 'serve', 'validate'):
            with self.subTest(command=command):
                self.assertEqual(self.cli(command).returncode, 2)
        self.assertEqual(before, self.snapshot(self.root))

    def test_arguments_beginning_with_dash_are_data(self):
        self.assertEqual(self.bind('--root')['icons'][0]['status'], 'not_found')


class SvgSafetyTests(unittest.TestCase):
    def assert_unsafe(self, raw, code='unsafe_svg'):
        with self.assertRaises(icons.IconError) as caught:
            icons._library.safe_svg(raw)
        self.assertEqual(caught.exception.code, code)

    def test_common_shapes_defs_gradients_clips_masks_and_local_href(self):
        raw = svg('''<defs>
            <linearGradient id="linear"><stop offset="0" stop-color="#FF6600"/><stop offset="1" style="stop-color:rgb(10,20,30);stop-opacity:0.5"/></linearGradient>
            <radialGradient id="radial" href="#linear"/>
            <clipPath id="clip"><rect width="24" height="24"/></clipPath>
            <mask id="mask"><circle cx="12" cy="12" r="10" fill="white"/></mask>
            <symbol id="symbol"><path d="M0 0h4v4z" fill="#12AB34"/></symbol>
            </defs><title id="label">Icon</title><desc id="description">Safe</desc>
            <g aria-labelledby="label" aria-describedby="description" transform="translate(1 2)">
            <rect width="4" height="4" fill="url(#linear)" clip-path="url('#clip')" mask="url(#mask)"/>
            <ellipse cx="4" cy="4" rx="2" ry="1" fill="url(#radial)"/>
            <line x1="0" x2="24" y1="0" y2="24" stroke="#ff0000"/>
            <polyline points="0,0 1,1 2,2"/><polygon points="0,0 5,0 5,5"/>
            <use href="#symbol"/><text x="1" y="2"><tspan>Label</tspan></text></g>''')
        safe = icons._library.safe_svg(raw)
        self.assertIn(b'#FF6600', safe)
        self.assertIn(b'rgb(10,20,30)', safe)
        self.assertIn(b'#12AB34', safe)
        ET.fromstring(safe)

    def test_safe_inline_styles_and_xlink(self):
        raw = svg('<defs><linearGradient id="paint"><stop stop-color="#abc"/></linearGradient><path id="shape" d="M0 0h3"/></defs>'
                  '<use xlink:href="#shape" style="fill:URL(\'\x23paint\');stroke:#123456;stroke-width:2;opacity:0.8"/>',
                  'xmlns:xlink="http://www.w3.org/1999/xlink"')
        self.assertIn(b'#123456', icons._library.safe_svg(raw))

    def test_long_font_family_identifiers(self):
        family = 'Ab_0-' * 20_000
        for attribute in ('font-family="' + family + '"', 'style="font-family:' + family + '"'):
            with self.subTest(attribute=attribute[:30]):
                safe = icons._library.safe_svg(svg('<text ' + attribute + '>Label</text>'))
                self.assertIn(family.encode(), safe)
        self.assert_unsafe(svg('<text font-family="' + family + ' (1)">Label</text>'))
        self.assert_unsafe(svg('<g transform="' + family + 'translate(1)"/>'))

    def test_long_normal_path_data_is_preserved(self):
        path = 'M0 0' + 'l1-1' * 20_000 + 'z'
        safe = icons._library.safe_svg(svg('<path d="' + path + '" transform="translate(1 2) scale(2)"/>'))
        element = ET.fromstring(safe).find('{' + icons._library.SVG_NS + '}path')
        self.assertEqual(element.get('d'), path)
        self.assertEqual(element.get('transform'), 'translate(1 2) scale(2)')

    def test_script_events_foreignobject_external_links_and_animation(self):
        bodies = ['<script/>', '<SCRIPT/>', '<g onload="alert(1)"/>', '<path oNcLiCk="alert(1)"/>',
                  '<foreignObject/>', '<image href="data:image/png;base64,eA=="/>', '<a href="https://evil/"/>',
                  '<use href="https://evil/icon.svg#x"/>', '<use href="//evil/x"/>', '<use href="file:///etc/passwd"/>',
                  '<use href="data:image/svg+xml,x"/>', '<animate attributeName="href"/>', '<set attributeName="fill"/>',
                  '<filter/>', '<iframe/>', '<svg xml:base="https://evil/"/>']
        for body in bodies:
            with self.subTest(body=body):
                self.assert_unsafe(svg(body))

    def test_dtd_entities_processing_instructions_and_encodings(self):
        raws = [b'<!DOCTYPE svg SYSTEM "https://evil/test.dtd">' + SVG,
                b'<!DOCTYPE svg [<!ENTITY x "expanded">]>' + svg('<text>&x;</text>'),
                b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>' + SVG,
                b'<?xml-stylesheet href="https://evil/style.css"?>' + SVG,
                svg('<text>&unknown;</text>'), SVG.decode().encode('utf-16'),
                b'<?xml version="1.0" encoding="iso-8859-1"?>' + SVG, b'<svg>']
        for raw in raws:
            with self.subTest(raw=raw[:100]):
                self.assert_unsafe(raw)
        self.assertIn(b'&amp;', icons._library.safe_svg(svg('<title>A &amp; B</title>')))

    def test_css_blocks_escape_tricks_and_nonallowlisted_properties(self):
        bodies = ['<style>path{fill:red}</style>', '<style/>', '<path style="background:url(https://evil/x)"/>',
                  '<path fill="URL(https://evil/x)"/>', '<path fill="url(//evil/x)"/>',
                  '<path fill="url(data:image/svg+xml,x)"/>', '<path fill="u\\72l(https://evil/x)"/>',
                  '<path fill="url/**/(#x)"/>', '<path fill="var(--color)"/>',
                  '<path style="fill:expression(alert(1))"/>', '<path style="fill:red;@import:evil"/>',
                  '<path style="fill:red;behavior:url(#x)"/>', '<path style="fill:red;fill:blue"/>',
                  '<path style="fill"/>', '<path style="-moz-binding:url(#x)"/>',
                  '<path style="fill:attr(x)"/>', '<path fill="url (#missing)"/>',
                  '<path style="filter:blur(2px)"/>', '<path unknown="url(#x)"/>']
        for body in bodies:
            with self.subTest(body=body):
                self.assert_unsafe(svg(body))

    def test_ids_references_duplicates_cycles_and_reference_type(self):
        bodies = ['<use href="#missing"/>', '<path fill="url(#missing)"/>',
                  '<path style="mask:url(#missing)"/>', '<g aria-labelledby="missing"/>',
                  '<path id="duplicate"/><rect id="duplicate"/>', '<g id="bad id"/>',
                  '<g id="loop"><use href="#loop"/></g>',
                  '<defs><linearGradient id="a" href="#b"/><linearGradient id="b" href="#a"/></defs>',
                  '<path id="shape"/><rect fill="url(#shape)"/>',
                  '<path id="shape"/><rect clip-path="url(#shape)"/>',
                  '<path id="shape"/><rect mask="url(#shape)"/>',
                  '<path id="shape"/><linearGradient href="#shape"/>',
                  '<g id="a"><g id="b"/></g><use href="#a" style="fill:url(#b)"/>']
        for body in bodies:
            with self.subTest(body=body):
                self.assert_unsafe(svg(body))

    def test_namespace_spoofing_rejected(self):
        raws = [b'<svg xmlns="https://evil" viewBox="0 0 1 1"/>',
                svg('<x:script xmlns:x="https://evil"/>'),
                svg('<g xmlns:x="https://evil" x:onload="alert(1)"/>'),
                svg('<use xmlns:x="https://evil" x:href="#shape"/>')]
        for raw in raws:
            with self.subTest(raw=raw):
                self.assert_unsafe(raw)

    def test_size_depth_and_element_limits(self):
        lib = icons._library
        self.assert_unsafe(b' ' * (lib.MAX_SVG_BYTES + 1), 'size_limit')
        self.assert_unsafe(svg('<g>' * lib.MAX_SVG_DEPTH + '</g>' * lib.MAX_SVG_DEPTH), 'svg_complexity_limit')
        self.assert_unsafe(svg('<path/>' * lib.MAX_SVG_ELEMENTS), 'svg_complexity_limit')
        self.assertTrue(lib.safe_svg(svg('<g>' * (lib.MAX_SVG_DEPTH - 1) + '</g>' * (lib.MAX_SVG_DEPTH - 1))))

    def test_exponential_use_expansion_bounded(self):
        body = '<defs><path id="n0" d="M0 0h1"/>'
        for index in range(1, 17):
            body += f'<g id="n{index}"><use href="#n{index - 1}"/><use href="#n{index - 1}"/></g>'
        body += '</defs><use href="#n16"/>'
        self.assert_unsafe(svg(body), 'svg_complexity_limit')

    def test_reference_chain_depth_bounded_even_for_cached_targets(self):
        body = '<defs><path id="n0" d="M0 0h1"/>'
        for index in range(1, 66):
            body += f'<use id="n{index}" href="#n{index - 1}"/>'
        self.assert_unsafe(svg(body + '</defs><use href="#n65"/>'), 'svg_complexity_limit')

    def test_viewbox_and_dimension_validation(self):
        self.assertIn(b'viewBox="0 0 12 24"', icons._library.safe_svg(b'<svg width="12px" height="24"/>'))
        for raw in (b'<svg/>', b'<svg viewBox="0 0 0 5"/>', b'<svg viewBox="0 0 NaN 5"/>',
                    b'<svg viewBox="0 0 1e999 5"/>', b'<svg width="0" height="5"/>', b'<path/>'):
            with self.subTest(raw=raw):
                self.assert_unsafe(raw)


if __name__ == '__main__':
    unittest.main()
