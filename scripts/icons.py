#!/usr/bin/env python3
"""Read-only icon API and CLI (Python standard library only).

resolve_bindings([{node_id, query, provider?, variant?}], root=None) returns
contract_version=2, ordered icons status cards, and prepared image payloads.
Only resolved icons enter prepared. Render svg_data_uri with an image/dataURI
backend, NEVER inline the decoded SVG: each image then has its own ID namespace.
Source SHA-256 covers original bytes; svg_sha256 covers the safe serialization.
An explicit root selects resource data only, never executable engine/schema code.
"""
from pathlib import Path
import argparse
import base64
import json
import os
import subprocess
import sys
import types

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PACKAGE_ROOT / 'assets' / 'icon-pack'
ENGINE_DIR = PACKAGE_ROOT / 'modules' / 'icons' / 'engine'
SCHEMA_PATH = PACKAGE_ROOT / 'modules' / 'icons' / 'schemas' / 'agent-response.schema.json'
CONTRACT_VERSION = 2
PROCESS_TIMEOUT = 30
MAX_RESPONSE_BYTES = 16_000_000
MAX_PREPARED_SVG_BYTES = 1_048_576  # Match the Archify image consumer's decoded-byte limit.

# Load only this trusted private source, without touching sys.path/sys.modules or
# creating __pycache__ in the skill. Never import a library from the resource root.
_library_path = ENGINE_DIR / 'library.py'
_library = types.ModuleType('_architecture_diagram_icon_library')
_library.__file__ = str(_library_path)
exec(compile(_library_path.read_bytes(), str(_library_path), 'exec'), _library.__dict__)
IconError = _library.IconError


def _validate_bindings(bindings):
    if not isinstance(bindings, list):
        raise ValueError('bindings must be a list')
    seen = set()
    for index, binding in enumerate(bindings):
        if not isinstance(binding, dict) or not {'node_id', 'query'} <= binding.keys() or binding.keys() - {'node_id', 'query', 'provider', 'variant'}:
            raise ValueError(f'bindings[{index}] must contain node_id, query and optional provider/variant only')
        for key, value in binding.items():
            if not isinstance(value, str) or not value.strip() or '\x00' in value or len(value) > 4096:
                raise ValueError(f'bindings[{index}].{key} must be a nonempty string of at most 4096 characters')
        if binding['node_id'] in seen:
            raise ValueError('Duplicate node_id: ' + binding['node_id'])
        seen.add(binding['node_id'])


def _resource_root(root):
    try:
        path = Path(root if root is not None else DEFAULT_ROOT).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise OSError('Not a resource directory')
        return path
    except (OSError, ValueError, TypeError, RuntimeError) as exc:
        raise IconError('root_error', 'Resource root is not an accessible directory') from exc


def _run_engine(root, command, argument, provider=None, variant=None, limit=None):
    # -I ignores PYTHONPATH/user site/current directory; -S disables site hooks;
    # -B prevents bytecode writes. The only added import path is our private engine.
    bootstrap = (
        'import runpy,sys; '
        'engine=sys.argv.pop(1); sys.path.insert(0,engine); '
        'runpy.run_path(engine+"/iconlib.py",run_name="__main__")'
    )
    args = [sys.executable, '-I', '-S', '-B', '-c', bootstrap, str(ENGINE_DIR), '--root', str(root), command]
    if provider is not None:
        args.append('--provider=' + provider)
    if variant is not None:
        args.append('--variant=' + variant)
    if limit is not None:
        args.append('--limit=' + str(limit))
    args.extend(['--', argument])
    environment = {key: value for key, value in os.environ.items()
                   if key != 'ICON_PERSONAL_ROOT' and not key.startswith('PYTHON')}
    environment['PYTHONDONTWRITEBYTECODE'] = '1'
    try:
        return subprocess.run(args, cwd=str(ENGINE_DIR), env=environment, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=PROCESS_TIMEOUT, check=False)
    except subprocess.TimeoutExpired as exc:
        raise IconError('process_timeout', 'Icon query engine timed out') from exc
    except OSError as exc:
        raise IconError('process_error', 'Cannot run the bundled icon query engine') from exc


def _query(root, command, argument, provider=None, variant=None, limit=None):
    process = _run_engine(root, command, argument, provider, variant, limit)
    if process.returncode not in (0, 1, 2):
        raise IconError('process_error', f'Icon query engine exited with code {process.returncode}')
    try:
        if len(process.stdout) > MAX_RESPONSE_BYTES:
            raise ValueError('Engine response exceeds limit')
        result = _library.strict_json(process.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IconError('schema_error', 'Engine did not return bounded valid JSON') from exc
    if not isinstance(result, dict) or type(result.get('contract_version')) is not int or result['contract_version'] != CONTRACT_VERSION:
        raise IconError('contract_error', 'Expected icon engine contract_version 2')
    if 'error' in result:
        branch = 'error'
    elif command == 'resolve':
        branch = 'resolved' if result.get('status') == 'resolved' else 'fallback'
    else:
        branch = command
    try:
        schema = _library.strict_json(SCHEMA_PATH.read_bytes())
        errors = _library.check_schema(result, schema)
        errors += _library.check_schema(result, {'$defs': schema['$defs'], '$ref': '#/$defs/' + branch})
        if errors:
            raise ValueError('; '.join(errors[:4]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise IconError('schema_error', 'Engine response failed bundled schema validation: ' + str(exc)) from exc
    expected = 1 if branch == 'error' else 2 if branch == 'fallback' else 0
    if process.returncode != expected:
        raise IconError('process_error', f'Engine exit code {process.returncode} disagrees with {branch} response')
    if branch == 'error':
        code = result.get('error_code')
        if not isinstance(code, str) or code not in {'path_error', 'hash_mismatch', 'asset_missing', 'size_limit', 'catalog_error', 'not_found'}:
            code = 'engine_error'
        raise IconError(code, result['error'] or 'Icon query engine reported an error')
    # Project before using extension fields: schema allows additional properties,
    # but they must not influence binding semantics or supply prepared SVG.
    result = {key: result[key] for key in schema['$defs'][branch]['properties'] if key in result}
    if command == 'resolve':
        if result['status'] == 'resolved':
            if provider is not None and result['provider'] != provider:
                raise IconError('provider_mismatch', 'Engine result violates the requested provider')
            if variant is not None and result['variant_id'] != variant:
                raise IconError('variant_mismatch', 'Engine result violates the requested variant')
        elif result['status'] == 'asset_integrity_error':
            raise IconError('hash_mismatch', result['diagnostic']['message'])
        if provider is not None and any(p['provider'] != provider for p in result.get('candidates', [])):
            raise IconError('provider_mismatch', 'Engine candidates violate the requested provider')
    elif command == 'search':
        if result['query'] != argument or (provider is not None and any(p['provider'] != provider for p in result['matches'])):
            raise IconError('provider_mismatch', 'Engine search response violates query/provider constraints')
        if len(result['matches']) > limit:
            raise IconError('schema_error', 'Engine exceeded search limit')
    elif result['product']['id'] != argument or any(v['product_id'] != argument for v in result['variants']):
        raise IconError('schema_error', 'Engine show response has inconsistent product identities')
    return result


def _error_card(binding, error, row=None):
    card = row if row is not None else _base_card(binding)
    card.update(status='error', fallback='card', diagnostic={
        'code': error.code, 'message': str(error),
        'action': '保留节点标签并使用状态卡片；修复资源或契约后重试，勿嵌入该 SVG。',
    })
    return card


def _base_card(binding):
    return {'node_id': binding['node_id'], 'query': binding['query'], 'status': 'error',
            'product_id': None, 'name': None, 'provider': binding.get('provider'),
            'variant_id': binding.get('variant'), 'source_sha256': None, 'svg_sha256': None}


def resolve_bindings(bindings, root=None) -> dict:
    """Resolve nodes with per-node fallback cards; invalid bindings raise ValueError."""
    _validate_bindings(bindings)
    output = {'contract_version': CONTRACT_VERSION, 'icons': [], 'prepared': {}}
    if not bindings:
        return output
    try:
        resource_root = _resource_root(root)
    except IconError as exc:
        output['icons'] = [_error_card(binding, exc) for binding in bindings]
        return output
    for binding in bindings:
        row = _base_card(binding)
        try:
            result = _query(resource_root, 'resolve', binding['query'], binding.get('provider'), binding.get('variant'))
            row.update({key: value for key, value in result.items() if key not in ('contract_version', 'sha256')})
            if result['status'] == 'resolved':
                raw = _library.asset_bytes(resource_root, result['asset_relative_path'], result['sha256'], result['asset_path'])
                row['source_sha256'] = _library.sha(raw)
                safe = _library.safe_svg(raw)
                if len(safe) > MAX_PREPARED_SVG_BYTES:
                    raise IconError('size_limit', f'Serialized SVG exceeds {MAX_PREPARED_SVG_BYTES}-byte image limit')
                row['svg_sha256'] = _library.sha(safe)
                output['prepared'][binding['node_id']] = {
                    'svg_data_uri': 'data:image/svg+xml;base64,' + base64.b64encode(safe).decode('ascii'),
                    'svg_sha256': row['svg_sha256'], 'source_sha256': row['source_sha256'],
                    'product_id': result['product_id'], 'variant_id': result['variant_id'], 'theme': result['theme'],
                }
        except IconError as exc:
            row = _error_card(binding, exc, row)
        output['icons'].append(row)
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', help='Local resource pack; never a Python engine directory')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('search', 'resolve'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('query')
        command.add_argument('--provider')
        if name == 'search':
            command.add_argument('--limit', type=int, default=20)
        else:
            command.add_argument('--variant')
            command.add_argument('--node-id', default='icon')
    commands.add_parser('show', allow_abbrev=False).add_argument('id')
    commands.add_parser('bind', allow_abbrev=False).add_argument('file', help='UTF-8 JSON bindings list')
    args = parser.parse_args(argv)
    try:
        if args.command in ('resolve', 'bind'):
            if args.command == 'bind':
                with Path(args.file).expanduser().open('rb') as stream:
                    raw = stream.read(_library.MAX_JSON_BYTES + 1)
                if len(raw) > _library.MAX_JSON_BYTES:
                    raise ValueError('Bindings file exceeds byte limit')
                bindings = _library.strict_json(raw)
            else:
                binding = {'node_id': args.node_id, 'query': args.query}
                if args.provider is not None:
                    binding['provider'] = args.provider
                if args.variant is not None:
                    binding['variant'] = args.variant
                bindings = [binding]
            result = resolve_bindings(bindings, args.root)
            code = 1 if any(row['status'] == 'error' for row in result['icons']) else 2 if any(row['status'] != 'resolved' for row in result['icons']) else 0
        else:
            if args.command == 'search' and not 1 <= args.limit <= 1000:
                raise ValueError('limit must be between 1 and 1000')
            result = _query(_resource_root(args.root), args.command,
                            args.query if args.command == 'search' else args.id,
                            getattr(args, 'provider', None), limit=getattr(args, 'limit', None))
            code = 0
    except (ValueError, OSError, RecursionError) as exc:
        result = {'contract_version': CONTRACT_VERSION, 'status': 'error', 'error': str(exc),
                  'diagnostic': {'code': exc.code if isinstance(exc, IconError) else 'input_error',
                                 'message': str(exc), 'action': '检查输入和资源后重试。'}}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
