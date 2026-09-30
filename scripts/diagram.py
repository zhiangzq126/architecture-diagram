#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = {'architecture': 'components', 'workflow': 'nodes', 'sequence': 'participants', 'dataflow': 'nodes', 'lifecycle': 'states'}
FORMATS = {'html': 'archify', 'drawio': 'drawio', 'gif': 'gif'}


def read_json(path):
    def object_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON field: {key}')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f'Invalid JSON constant: {value}')

    path = Path(path)
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON input exceeds 16 MiB')
    try:
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=object_fields,
                          parse_constant=invalid_constant)
    except RecursionError as error:
        raise ValueError('JSON input is nested too deeply') from error


def request_path(value, base, label):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError(f'{label} must be a non-empty path string without NUL')
    try:
        return (base / Path(value).expanduser()).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f'Invalid {label}: {error}') from error


def dump(path, payload):
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    data = Path(path).read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def load_request(path):
    path = Path(path).expanduser().resolve()
    request = read_json(path)
    allowed = {'contract_version', 'name', 'type', 'targets', 'output_dir', 'quality', 'theme', 'icons'}
    if (not isinstance(request, dict) or set(request) - allowed
            or type(request.get('contract_version')) is not int or request['contract_version'] != 1):
        raise ValueError('Expected task contract_version=1 and documented fields')
    if not isinstance(request.get('name'), str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}', request['name']):
        raise ValueError('name must be a simple ASCII artifact stem')
    if not isinstance(request.get('type'), str) or request['type'] not in {*COLLECTIONS, 'uml', 'network', 'template'}:
        raise ValueError('Unsupported diagram type')
    for key, default, choices in (('theme', 'light', {'light', 'dark'}),
                                  ('quality', 'showcase', {'showcase', 'standard'})):
        if not isinstance(request.get(key, default), str) or request.get(key, default) not in choices:
            raise ValueError(f'Invalid {key}')
    targets = request.get('targets')
    if not isinstance(targets, list) or not targets:
        raise ValueError('targets must be a non-empty list')
    formats = set()
    for target in targets:
        if not isinstance(target, dict) or set(target) - {'format', 'source', 'preview', 'derive', 'geometry'}:
            raise ValueError('Target supports format, source, preview, derive and geometry only')
        fmt = target.get('format')
        if not isinstance(fmt, str) or fmt not in FORMATS or fmt in formats:
            raise ValueError('Unsupported or duplicate target format')
        formats.add(fmt)
        geometry = target.get('geometry')
        if geometry is not None and (fmt != 'drawio' or geometry not in {'shared', 'independent'}):
            raise ValueError('geometry is shared or independent for DrawIO targets only')
        derive = target.get('derive')
        if geometry == 'shared' and derive is None:
            derive = request['type']
            target['derive'] = derive
        if derive is not None:
            if fmt not in {'drawio', 'gif'} or not isinstance(derive, str) or derive not in {'architecture', 'workflow'} or request['type'] != derive:
                raise ValueError('derive supports matching architecture or workflow models delivered as DrawIO or topology GIF')
            if geometry == 'independent':
                raise ValueError('independent geometry cannot use derive')
            if fmt == 'drawio':
                target['geometry'] = 'shared'
        elif fmt == 'drawio':
            target['geometry'] = geometry or 'independent'
        if fmt == 'html' and request['type'] not in COLLECTIONS:
            raise ValueError('HTML supports five typed diagram models; no silent conversion')
        if fmt == 'gif' and request['type'] != 'template' and derive is None:
            raise ValueError('Topology GIF requires explicit derive=architecture or workflow; template GIF uses its dedicated model')
        if fmt == 'drawio' and request['type'] == 'template':
            raise ValueError('Template GIF does not convert to editable DrawIO')
        if 'preview' in target and (fmt != 'drawio' or not isinstance(target['preview'], bool)):
            raise ValueError('preview is a boolean option for DrawIO only')
        target['source'] = str(request_path(target.get('source'), path.parent, 'source'))
        source_path = Path(target['source'])
        if not source_path.is_file():
            raise ValueError(f'Source is not a file: {target["source"]}')
        if fmt == 'drawio' and target['geometry'] == 'shared' and source_path.suffix.lower() != '.json':
            raise ValueError('Shared DrawIO geometry requires a typed JSON source')
        if fmt == 'drawio' and target['geometry'] == 'independent' and source_path.suffix.lower() != '.drawio':
            raise ValueError('Independent DrawIO geometry requires an original .drawio source')
        if fmt == 'gif' and derive and source_path.suffix.lower() != '.json':
            raise ValueError('Topology GIF requires a typed JSON source')
    icons = request.setdefault('icons', {'bindings': []})
    if not isinstance(icons, dict) or set(icons) - {'root', 'bindings'} or not isinstance(icons.get('bindings', []), list):
        raise ValueError('icons supports root and bindings only')
    icons.setdefault('bindings', [])
    from icons import _validate_bindings
    _validate_bindings(icons['bindings'])
    if 'gif' in formats and request['type'] == 'template' and icons['bindings']:
        raise ValueError('Template GIF supports its own built-in symbols, not SVG bindings')
    if 'root' in icons:
        icons['root'] = str(request_path(icons['root'], path.parent, 'icons.root'))
    request['output_dir'] = str(request_path(request.get('output_dir'), path.parent, 'output_dir'))
    if Path(request['output_dir']).is_relative_to(ROOT):
        raise ValueError('Delivery must not write into the installed skill')
    return request


def route(request):
    return [{'format': t['format'], 'backend': FORMATS[t['format']], 'source': t['source'],
             **({'geometry': t['geometry'], 'derive': t.get('derive')} if t['format'] == 'drawio' else {}),
             **({'mode': 'topology' if t.get('derive') else 'template', 'derive': t.get('derive')} if t['format'] == 'gif' else {}),
             'reference': f'references/{FORMATS[t["format"]]}.md'} for t in request['targets']]


def run_process(args, env=None, timeout=180):
    result = subprocess.run([str(x) for x in args], env=env, capture_output=True, text=True, timeout=timeout)
    output = result.stdout.strip()
    try:
        payload = json.loads(output)
    except ValueError:
        payload = {'stdout': output[-6000:], 'stderr': result.stderr.strip()[-3000:]}
    if result.returncode:
        raise RuntimeError(json.dumps({'exit_code': result.returncode, 'diagnostic': payload}, ensure_ascii=False))
    return payload


def environment_for(registry=None):
    from environment import _child_env
    env = _child_env()
    if registry is not None:
        env['ARCHITECTURE_DIAGRAM_ICONS'] = str(registry)
    return env


def prepare_archify(source, request, resolved):
    spec = read_json(source)
    if not isinstance(spec, dict):
        raise ValueError('Archify source must be a typed JSON object')
    if (spec.get('diagram_type') != request['type']
            or type(spec.get('schema_version')) is not int
            or spec['schema_version'] not in ((1, 2) if request['type'] == 'workflow' else (1,))):
        raise ValueError('Archify source has an invalid schema_version or diagram_type')
    if not isinstance(spec.get('meta'), dict):
        raise ValueError('Archify meta must be an object')
    collection = COLLECTIONS[request['type']]
    nodes = spec.get(collection)
    if not isinstance(nodes, list):
        raise ValueError(f'Archify {collection} must be a list of node objects')
    mapping = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f'Archify {collection}[{index}] must be a node object')
        node_id = node.get('id')
        if not isinstance(node_id, str) or not node_id.strip() or '\x00' in node_id:
            raise ValueError(f'Archify {collection}[{index}].id must be a non-empty string without NUL')
        if node_id in mapping:
            raise ValueError(f'Duplicate Archify node ID: {node_id}')
        mapping[node_id] = node
        brand = node.get('brand')
        if brand is not None and (not isinstance(brand, str) or re.match(r'^(?:https?:|ad-icon:)', brand, re.I)):
            raise ValueError('Source brands must be built-in IDs; use icon bindings for local SVG')
    registry = {}
    for binding in request['icons']['bindings']:
        node_id = binding['node_id']
        if node_id not in mapping:
            raise ValueError(f'Unknown icon node: {node_id}')
        mapping[node_id].pop('brand', None)
        icon = resolved['prepared'].get(node_id)
        if icon:
            identity = [icon['svg_sha256'], icon['source_sha256'], icon['product_id'],
                        icon['variant_id'], icon['theme']['dark_backdrop']]
            key = 'ad-icon:' + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
            registry[key] = icon
            mapping[node_id]['brand'] = key
    meta = spec.setdefault('meta', {})
    meta['quality_profile'] = request.get('quality', 'showcase')
    meta.setdefault('locale', 'zh-CN')
    meta.setdefault('animation', 'none')
    return spec, registry


def render_archify(target, request, resolved, stage, tools):
    spec, registry = prepare_archify(target['source'], request, resolved)
    model = stage / 'model.json'
    registry_path = stage / 'icons.registry.json'
    dump(model, spec)
    dump(registry_path, registry)
    env = environment_for(registry_path)
    env['ARCHITECTURE_DIAGRAM_THEME'] = request.get('theme', 'light')
    cli = ROOT / 'modules/archify/bin/archify.mjs'
    quality = request.get('quality', 'showcase')
    validation = run_process([tools['node'], cli, 'validate', request['type'], model, '--quality', quality, '--json'], env)
    output = stage / (request['name'] + '.html')
    delivery = run_process([tools['node'], cli, 'deliver', request['type'], model, output, '--quality', quality, '--json'], env)
    if not output.is_file():
        raise RuntimeError('Archify returned no artifact')
    return {'validation': validation, 'delivery': delivery, 'visual_review': 'pending',
            'theme': request.get('theme', 'light')}, [output, model, registry_path]


def render_drawio(target, request, resolved, stage, tools):
    from drawio_adapter import attach_icons
    files = []
    derive = target.get('derive')
    if derive:
        from architecture_drawio import build_mxfile
        spec, registry = prepare_archify(target['source'], request, resolved)
        model = stage / 'model.json'
        registry_path = stage / 'icons.registry.json'
        dump(model, spec)
        dump(registry_path, registry)
        env = environment_for(registry_path)
        env['ARCHITECTURE_DIAGRAM_THEME'] = request.get('theme', 'light')
        cli = ROOT / 'modules/archify/bin/archify.mjs'
        if derive == 'architecture':
            layout = run_process([tools['node'], cli, 'inspect', 'architecture', model], env)
        else:
            layout = run_process([tools['node'], cli, 'validate', 'workflow', model, '--layout-json'], env)
        layout_path = stage / 'layout.json'
        dump(layout_path, layout)
        source_bytes = build_mxfile(layout, title=spec['meta'].get('title'), theme=request.get('theme', 'light'))
        files += [model, layout_path, registry_path]
    else:
        source_bytes = Path(target['source']).read_bytes()
    xml, pages = attach_icons(source_bytes, resolved['prepared'], request['icons']['bindings'])
    output = stage / (request['name'] + '.drawio')
    output.write_bytes(xml)
    validation = run_process([tools['node'], ROOT / 'modules/drawio/scripts/validate.mjs', output], environment_for())
    if '⚠' in validation.get('stdout', '') or '✗' in validation.get('stdout', ''):
        raise ValueError('DrawIO validation contains warnings: ' + validation['stdout'])
    files.insert(0, output)
    border = '0' if derive else '24'
    if target.get('preview', True):
        for page in range(pages):
            image = stage / (request['name'] + (f'-{page + 1:02d}' if pages > 1 else '') + '.png')
            run_process([tools['drawio'], '--export', '--format', 'png', '--scale', '2', '--border', border, '--page-index', str(page), '--output', image, output], environment_for())
            if not image.is_file() or image.read_bytes()[:8] != b'\x89PNG\r\n\x1a\n':
                raise RuntimeError('DrawIO export did not produce a PNG')
            files.append(image)
    return {'xml': 'passed', 'format': validation, 'pages': pages,
            'geometry': target.get('geometry', 'independent'), 'derived': derive or 'none',
            'render': 'passed' if target.get('preview', True) else 'not_requested', 'visual_review': 'pending'}, files


def render_gif(target, request, resolved, stage, tools):
    if target.get('derive'):
        return render_topology_gif(target, request, resolved, stage, tools)
    renderer = ROOT / 'modules/drawio/scripts/render_animated_diagram.py'
    env = environment_for()
    env['ARCHITECTURE_DIAGRAM_FONT'] = tools['font']
    result = run_process([tools['python'], '-I', '-B', renderer, '--spec', target['source'], '--outdir', stage,
                          '--basename', request['name'], '--theme', request.get('theme', 'dark'), '--verify', '--check'], env, timeout=300)
    files = [stage / (request['name'] + ext) for ext in ('.gif', '.png')]
    if not all(p.is_file() and p.stat().st_size for p in files):
        raise RuntimeError('Animation did not produce GIF and PNG')
    return {'animation': result, 'visual_review': 'pending'}, files


def render_topology_gif(target, request, resolved, stage, tools):
    spec, registry = prepare_archify(target['source'], request, resolved)
    kind = request['type']
    nodes = spec[COLLECTIONS[kind]]
    edges = spec.get('connections' if kind == 'architecture' else 'edges', [])
    if not 2 <= len(nodes) <= 12 or not isinstance(edges, list) or not 1 <= len(edges) <= 30:
        raise ValueError('Topology GIF supports 2–12 nodes and 1–30 edges')
    spec['meta']['animation'] = 'none'
    model, registry_path = stage / 'model.json', stage / 'icons.registry.json'
    dump(model, spec)
    dump(registry_path, registry)
    env = environment_for(registry_path)
    theme = request.get('theme', 'light')
    env['ARCHITECTURE_DIAGRAM_THEME'] = theme
    cli = ROOT / 'modules/archify/bin/archify.mjs'
    quality = request.get('quality', 'showcase')
    validation = run_process([tools['node'], cli, 'validate', kind, model, '--quality', quality, '--json'], env)
    layout = run_process([tools['node'], cli, 'inspect', kind, model] if kind == 'architecture' else
                         [tools['node'], cli, 'validate', kind, model, '--layout-json'], env)
    layout_path = stage / 'layout.json'
    dump(layout_path, layout)
    html = stage / 'base.html'
    delivery = run_process([tools['node'], cli, 'deliver', kind, model, html, '--quality', quality, '--json'], env)
    image, scene, output = stage / (request['name'] + '.png'), stage / 'scene.json', stage / (request['name'] + '.gif')
    snapshot = run_process([tools['node'], ROOT / 'scripts/topology_snapshot.mjs', '--artifact', html,
                            '--model', model, '--layout', layout_path, '--font', tools['font'],
                            '--png', image, '--scene', scene, '--theme', theme], env)
    animation = run_process([tools['python'], '-I', '-B', ROOT / 'scripts/topology_gif.py',
                             '--scene', scene, '--base', image, '--output', output], env, timeout=300)
    if not animation.get('ok') or not output.is_file() or not image.is_file():
        raise RuntimeError('Topology animation verification failed')
    files = [output, image, model, registry_path, layout_path, scene, html]
    return {'mode': 'topology', 'validation': validation, 'delivery': delivery, 'snapshot': snapshot,
            'animation': animation, 'visual_review': 'pending'}, files


def publish(stage, dest):
    # Reserve without clobbering; publish the completion receipt last.
    dest.mkdir()
    complete = False
    try:
        for child in sorted(stage.iterdir(), key=lambda p: p.name == 'receipt.json'):
            child.rename(dest / child.name)
        complete = True
    finally:
        if not complete:
            shutil.rmtree(dest)


def execute(request):
    from environment import doctor
    from icons import resolve_bindings
    preflight = [doctor('topology-gif' if t['format'] == 'gif' and t.get('derive') else FORMATS[t['format']],
                        require_preview=t.get('preview', True)) for t in request['targets']]
    print(json.dumps({'phase': 'preflight', 'checks': preflight}, ensure_ascii=False), file=sys.stderr)
    resolved = resolve_bindings(request['icons']['bindings'], root=request['icons'].get('root'))
    outdir = Path(request['output_dir'])
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for target, check in zip(request['targets'], preflight):
        fmt = target['format']
        item = {'format': fmt, 'backend': FORMATS[fmt], 'status': 'blocked' if check['status'] != 'ready' else 'pending', 'preflight': check}
        results.append(item)
        if item['status'] == 'blocked':
            continue
        dest = outdir / (request['name'] + '-' + fmt)
        if os.path.lexists(dest):
            item.update(status='failed', diagnostic=f'Output exists; choose a new name: {dest}')
            continue
        try:
            with tempfile.TemporaryDirectory(prefix='.architecture-diagram-', dir=outdir) as temporary:
                stage = Path(temporary)
                renderer = {'html': render_archify, 'drawio': render_drawio, 'gif': render_gif}[fmt]
                checks, files = renderer(target, request, resolved, stage, check['executables'])
                source = stage / ('source' + Path(target['source']).suffix)
                shutil.copyfile(target['source'], source)
                dump(stage / 'task.json', request)
                dump(stage / 'icons.lock.json', {'contract_version': 2, 'icons': resolved['icons']})
                files += [source, stage / 'task.json', stage / 'icons.lock.json']
                manifest = read_json(ROOT / 'manifest.json')
                if not isinstance(manifest, dict) or not isinstance(manifest.get('version'), str) or not manifest['version'].strip():
                    raise ValueError('Skill manifest must contain a non-empty version string')
                receipt = {'contract_version': 1, 'status': 'generated', 'backend': FORMATS[fmt],
                           'skill_version': manifest['version'], 'module_manifest': digest(ROOT / 'manifest.json'),
                           'runtime': check, 'checks': checks,
                           **({'geometry': target.get('geometry', 'independent')} if fmt == 'drawio' else {}),
                           'artifacts': [{'path': p.name, **digest(p)} for p in files],
                           'icons': resolved['icons'], 'visual_review': 'pending'}
                dump(stage / 'receipt.json', receipt)
                publish(stage, dest)
                item.update(status='generated', receipt=str(dest / 'receipt.json'),
                            artifacts=[str(dest / p.name) for p in files if p != source and p.suffix in {'.html', '.drawio', '.gif', '.png'}],
                            visual_review='pending', icon_fallbacks=sum(i['status'] != 'resolved' for i in resolved['icons']))
        except Exception as error:
            # Isolate backend failures without swallowing process interrupts.
            item.update(status='failed', diagnostic=f'{type(error).__name__}: {error}')
    count = sum(item['status'] == 'generated' for item in results)
    return {'contract_version': 1, 'status': 'generated' if count == len(results) else 'partial' if count else 'failed',
            'targets': results, 'visual_review': 'pending' if count else 'not_run'}


def main():
    parser = argparse.ArgumentParser(description='architecture-diagram: route, preflight, render and verify')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('route', 'run'):
        command = commands.add_parser(name)
        command.add_argument('task')
    command = commands.add_parser('doctor')
    command.add_argument('backend', choices=['archify', 'drawio', 'gif', 'topology-gif', 'icons', 'all'], default='all', nargs='?')
    command.add_argument('--no-preview', action='store_true')
    for name in ('install-plan', 'install'):
        command = commands.add_parser(name)
        command.add_argument('dependency', choices=['node', 'python', 'drawio', 'pillow', 'font', 'browser'])
        if name == 'install':
            command.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    try:
        if args.command in ('route', 'run'):
            request = load_request(args.task)
            result = {'routes': route(request)} if args.command == 'route' else execute(request)
        elif args.command == 'doctor':
            from environment import doctor
            result = doctor(args.backend, require_preview=not args.no_preview)
        else:
            from environment import install_plan, install
            result = install_plan(args.dependency) if args.command == 'install-plan' else install(args.dependency, confirmed=args.confirm)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('status') in {'failed', 'blocked', 'partial', 'refused', 'unsupported', 'confirmation_required', 'manual_required'} else 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({'status': 'failed', 'diagnostic': str(error)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
