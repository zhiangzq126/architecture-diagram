import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from environment import doctor


def digest(path):
    data = path.read_bytes()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def command(argv, env, log):
    result = subprocess.run([str(x) for x in argv], capture_output=True, text=True, env=env, timeout=600)
    save(log, {'argv': [str(x) for x in argv], 'returncode': result.returncode,
               'stdout': result.stdout[-60000:], 'stderr': result.stderr[-20000:]})
    require(result.returncode == 0, f'Command failed; see {log}')
    return json.loads(result.stdout)


def check_drawio(source, output, case):
    require(case in {'order-platform', 'multipage'}, 'Unknown DrawIO acceptance case')
    original = ET.parse(source).getroot().findall('diagram')
    rendered = ET.parse(output).getroot().findall('diagram')
    require(len(original) == len(rendered) == 2, 'Expected two DrawIO pages')
    icon_parents = []
    for before, after in zip(original, rendered):
        original_cells = {c.get('id'): c for c in before.findall('./mxGraphModel/root/mxCell')}
        cells = {c.get('id'): c for c in after.findall('./mxGraphModel/root/mxCell')}
        for cell in original_cells.values():
            actual = cells[cell.get('id')]
            for attribute in ('value', 'source', 'target', 'parent'):
                require(cell.get(attribute) == actual.get(attribute), f'DrawIO changed {cell.get("id")} {attribute}')
        for cid, cell in cells.items():
            if cid not in original_cells:
                require(cell.get('connectable') == '0', 'Icon became an edge endpoint')
                require(cell.get('vertex') == '1' and 'shape=image;' in cell.get('style', '')
                        and 'image=data:image/svg+xml,' in cell.get('style', ''), 'Expected an embedded SVG icon cell')
                parent = cell.get('parent')
                require(parent in original_cells and original_cells[parent].get('vertex') == '1', 'Icon lost its business parent')
                icon_parents.append(parent)
    for node_id in ('primary', 'archive'):
        require(icon_parents.count(node_id) == 1, f'Expected one DrawIO icon for {node_id}')
    require('orders' not in icon_parents, 'Missing orders icon did not fall back')
    if case == 'multipage':
        require(icon_parents.count('commit') == 1, 'Expected one DrawIO icon for commit')
        require('manual' not in icon_parents, 'Missing manual icon did not fall back')
    else:
        require('commit' not in icon_parents, 'Unbound commit received an icon')


def check_shared_workflow(source, output, layout_path):
    authored = json.loads(source.read_text())
    layout = json.loads(layout_path.read_text())
    rendered = ET.parse(output).getroot().findall('diagram')
    require(len(rendered) == 1, 'Shared Workflow DrawIO should be single-page')
    cells = {cell.get('id'): cell for cell in rendered[0].findall('./mxGraphModel/root/mxCell')}
    lanes = {lane['id']: lane for lane in layout['lanes']}
    first_lane = layout['lanes'][0]
    first_geo = cells[f'lane_{first_lane["id"]}'].find('mxGeometry')
    dx = float(first_geo.get('x')) - first_lane['x']
    dy = float(first_geo.get('y')) - first_lane['y']
    for lane in layout['lanes']:
        actual = cells.get(f'lane_{lane["id"]}')
        require(actual is not None and actual.get('vertex') == '1', f'Missing Workflow lane {lane["id"]}')
        require(lane['label'] in actual.get('value', ''), f'Workflow lane label drift on {lane["id"]}')
        geo = actual.find('mxGeometry')
        require(float(geo.get('x')) == round(lane['x'] + dx) and float(geo.get('y')) == round(lane['y'] + dy),
                f'Workflow lane position drift on {lane["id"]}')
        require(float(geo.get('width')) == round(lane['width']) and float(geo.get('height')) == round(lane['height']),
                f'Workflow lane size drift on {lane["id"]}')
    layout_nodes = {node['id']: node for node in layout['nodes']}
    for node in authored['nodes']:
        actual = cells.get(node['id'])
        placed = layout_nodes[node['id']]
        lane = lanes[node['lane']]
        require(actual is not None and actual.get('vertex') == '1', f'Missing Workflow node {node["id"]}')
        require(actual.get('value') == node['label'], f'Workflow node label drift on {node["id"]}')
        require(actual.get('parent') == f'lane_{node["lane"]}', f'Workflow node parent drift on {node["id"]}')
        geo = actual.find('mxGeometry')
        require(float(geo.get('x')) == round(placed['x'] - lane['x']) and float(geo.get('y')) == round(placed['y'] - lane['y']),
                f'Workflow node position drift on {node["id"]}')
        require(float(geo.get('width')) == round(placed['width']) and float(geo.get('height')) == round(placed['height']),
                f'Workflow node size drift on {node["id"]}')
    for edge in authored['edges']:
        matches = [cell for cell in cells.values() if cell.get('edge') == '1'
                   and cell.get('source') == edge['from'] and cell.get('target') == edge['to']]
        if edge.get('id'):
            matches = [cell for cell in matches if cell.get('id') == edge['id']]
        subject = edge.get('id') or f'{edge["from"]}->{edge["to"]}'
        require(len(matches) == 1, f'Missing or duplicate Workflow edge {subject}')
        require(matches[0].get('value') == edge.get('label', ''), f'Workflow edge label drift on {subject}')


def run(out):
    require(not out.exists() and not out.is_symlink(), 'Output directory already exists')
    require(not out.resolve().is_relative_to(ROOT), 'Acceptance output must be outside the skill')
    require(out.parent.is_dir(), 'Output parent must exist')
    out.mkdir()
    report = {'status': 'running', 'visual_review': 'pending', 'cases': [], 'network': 'No downloads or installation; each HTML browser check enforces a loopback-only resource allowlist'}
    try:
        runtime = doctor('all')
        save(out / 'preflight.json', runtime)
        require(runtime['status'] == 'ready', 'Required dependencies unavailable; see preflight.json')
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('PYTHON', 'PIP_')) and key not in {'NODE_OPTIONS', 'NODE_PATH'}}
        env.update(PYTHONDONTWRITEBYTECODE='1', ICON_PERSONAL_ROOT='/nonexistent/old-icons', ARCH_ICONS_ROOT='/nonexistent/arch-icons')
        examples = ROOT / 'examples'
        upstream = ROOT / 'modules/archify/examples'
        bindings = [{'node_id': 'primary', 'query': 'MySQL'}, {'node_id': 'archive', 'query': 'MySQL'},
                    {'node_id': 'orders', 'query': 'architecture-diagram-p0-missing-icon'}]
        cases = [
            ('order-platform', 'architecture', [{'format': 'html', 'source': str(examples / 'p0-order-platform.architecture.json')},
                                               {'format': 'drawio', 'geometry': 'independent', 'source': str(examples / 'p0-order-platform.drawio')}], bindings, 'light'),
            ('multipage', 'architecture', [{'format': 'drawio', 'source': str(examples / 'p0-order-platform.drawio')}],
             bindings + [{'node_id': 'commit', 'query': 'MySQL'}, {'node_id': 'manual', 'query': 'architecture-diagram-p0-missing-icon'}], 'light'),
            ('derived-architecture', 'architecture', [{'format': 'drawio', 'derive': 'architecture', 'source': str(examples / 'p0-order-platform.architecture.json')}], [], 'dark'),
            ('derived-workflow-v1', 'workflow', [
                {'format': 'html', 'source': str(upstream / 'incident-response.workflow.json')},
                {'format': 'drawio', 'geometry': 'shared', 'source': str(upstream / 'incident-response.workflow.json')},
            ], [], 'light'),
            ('derived-workflow-v2', 'workflow', [
                {'format': 'drawio', 'geometry': 'shared', 'source': str(upstream / 'agent-tool-call.workflow.json')},
            ], [], 'dark'),
        ]
        for kind, name in [('workflow', 'incident-response'), ('sequence', 'async-job-roundtrip'),
                           ('dataflow', 'event-stream'), ('lifecycle', 'deployment-release')]:
            cases.append((kind, kind, [{'format': 'html', 'source': str(upstream / f'{name}.{kind}.json')}], [], 'light'))
        gif_source = out / 'template.json'
        gif = json.loads((ROOT / 'modules/drawio/examples/05-animated-flow-spec.json').read_text())
        gif['signature'] = ''
        save(gif_source, gif)
        for theme in ('light', 'dark'):
            cases.append((f'template-{theme}', 'template', [{'format': 'gif', 'source': str(gif_source)}], [], theme))
            for label, kind, source, icons in [
                ('architecture', 'architecture', examples / 'p0-order-platform.architecture.json', bindings),
                ('workflow-v1', 'workflow', upstream / 'incident-response.workflow.json', []),
                ('workflow-v2', 'workflow', upstream / 'agent-tool-call.workflow.json', []),
            ]:
                cases.append((f'topology-{label}-{theme}', kind,
                              [{'format': 'gif', 'derive': kind, 'source': str(source)}], icons, theme))
        for name, kind, targets, icons, theme in cases:
            task = {'contract_version': 1, 'name': name, 'type': kind, 'targets': targets,
                    'icons': {'bindings': icons}, 'theme': theme, 'quality': 'showcase', 'output_dir': str(out / 'delivery')}
            task_path = out / f'{name}.task.json'
            save(task_path, task)
            result = command([sys.executable, '-B', ROOT / 'scripts/diagram.py', 'run', task_path], env, out / f'{name}.log.json')
            require(result['status'] == 'generated', f'{name} did not generate all targets')
            case = {'name': name, 'status': 'passed', 'targets': []}
            report['cases'].append(case)
            for target_index, target in enumerate(result['targets']):
                receipt_path = Path(target['receipt'])
                target_report = {'format': target['format'], 'receipt': str(receipt_path), 'artifacts': target['artifacts'], 'image_checks': []}
                case['targets'].append(target_report)
                receipt = json.loads(receipt_path.read_text())
                require(receipt['module_manifest'] == digest(ROOT / 'manifest.json'), 'Manifest receipt mismatch')
                require(receipt['skill_version'] == json.loads((ROOT / 'manifest.json').read_text())['version'], 'Version receipt mismatch')
                for artifact in receipt['artifacts']:
                    require(digest(receipt_path.parent / artifact['path']) == {key: artifact[key] for key in ('sha256', 'bytes')}, 'Artifact receipt mismatch')
                rows = {row['node_id']: row for row in receipt['icons']}
                for binding in icons:
                    expected = 'resolved' if binding['query'] == 'MySQL' else 'not_found'
                    require(rows[binding['node_id']]['status'] == expected, f'Unexpected icon state: {rows[binding["node_id"]]}')
                if target['format'] == 'html':
                    validation = receipt['checks']['validation']
                    require(len(validation['checks']) == 9 and all(check['ok'] for check in validation['checks']), 'Incomplete showcase checks')
                    require(validation['composition']['summary'] == {'errors': 0, 'warnings': 0}, 'Composition failed')
                    if name == 'order-platform':
                        model = json.loads((receipt_path.parent / 'model.json').read_text())
                        source = json.loads((examples / 'p0-order-platform.architecture.json').read_text())
                        require(model['connections'] == source['connections'], 'HTML relationships changed')
                        require([(n['id'], n['label']) for n in model['components']] == [(n['id'], n['label']) for n in source['components']], 'HTML business nodes changed')
                        require(model['components'][3]['brand'] == model['components'][6]['brand'], 'Repeated MySQL identity unexpectedly differs')
                        require('brand' not in model['components'][2], 'Missing icon did not fall back')
                    html = [Path(p) for p in target['artifacts'] if Path(p).suffix.lower() in {'.html', '.htm'}]
                    require(len(html) == 1, 'Expected one delivered HTML artifact per target')
                    browser_out = out / f'{name}-{target_index}-browser'
                    browser_report_path = browser_out / 'report.json'
                    browser_log = out / f'{name}-{target_index}-browser.log.json'
                    target_report.update(browser_report=str(browser_report_path), browser_log=str(browser_log), visual_review='pending')
                    try:
                        summary = command([runtime['executables']['node'], ROOT / 'tests/browser_acceptance.mjs',
                                           '--artifact', html[0].resolve(), '--outdir', browser_out,
                                           '--require-chinese', 'true' if name == 'order-platform' else 'false',
                                           '--expect-icons', '2' if name == 'order-platform' else '0'], env, browser_log)
                        browser_report = json.loads(browser_report_path.read_text())
                        require(summary['automated_status'] == browser_report['automated_status'] == 'pass', 'Browser acceptance failed')
                        require(not any(check['status'] == 'fail' for check in browser_report['checks']), 'Browser check failed')
                        require(browser_report['visual_review'] == 'pending', 'Browser cannot approve human visual review')
                        target_report['browser_status'] = 'passed'
                    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
                        # Keep receipts and browser evidence, and still exercise every remaining HTML target.
                        target_report.update(browser_status='failed', diagnostic=str(error))
                        case['status'] = 'failed'
                if target['format'] == 'drawio':
                    expected_geometry = 'shared' if name.startswith('derived-') else 'independent'
                    require(receipt['geometry'] == receipt['checks']['geometry'] == expected_geometry,
                            f'Unexpected DrawIO geometry mode for {name}')
                    if name == 'derived-architecture':
                        derived_source = json.loads((examples / 'p0-order-platform.architecture.json').read_text())
                        rendered = ET.parse(receipt_path.parent / f'{name}.drawio').getroot().findall('diagram')
                        require(len(rendered) == 1, 'Derived DrawIO should be single-page')
                        cells = {c.get('id'): c for c in rendered[0].findall('./mxGraphModel/root/mxCell')}
                        for component in derived_source['components']:
                            actual = cells.get(component['id'])
                            require(actual is not None and actual.get('vertex') == '1', f'Derived DrawIO missing component {component["id"]}')
                            require(actual.get('value') == component['label'], f'Derived DrawIO label drift on {component["id"]}')
                        for conn in derived_source['connections']:
                            match = [c for c in cells.values() if c.get('edge') == '1'
                                     and c.get('source') == conn['from'] and c.get('target') == conn['to']]
                            require(len(match) == 1, f'Derived DrawIO edge missing: {conn["from"]}->{conn["to"]}')
                            if conn.get('label'):
                                require(match[0].get('value') == conn['label'], f'Derived DrawIO edge label drift: {conn["from"]}->{conn["to"]}')
                        require(len([p for p in target['artifacts'] if p.endswith('.png')]) == 1, 'Missing derived PNG')
                    elif name.startswith('derived-workflow-'):
                        source_name = 'incident-response.workflow.json' if name.endswith('v1') else 'agent-tool-call.workflow.json'
                        check_shared_workflow(upstream / source_name, receipt_path.parent / f'{name}.drawio', receipt_path.parent / 'layout.json')
                        expected_contract = 'fixed-v1' if name.endswith('v1') else 'readable-v2'
                        require(json.loads((receipt_path.parent / 'layout.json').read_text())['contract'] == expected_contract,
                                f'Unexpected Workflow layout contract for {name}')
                        require(len([p for p in target['artifacts'] if p.endswith('.png')]) == 1, 'Missing Workflow PNG')
                    else:
                        check_drawio(examples / 'p0-order-platform.drawio', receipt_path.parent / f'{name}.drawio', name)
                        require(len([p for p in target['artifacts'] if p.endswith('.png')]) == 2, 'Missing page PNG')
                images = [p for p in target['artifacts'] if p.endswith(('.png', '.gif'))]
                image_checks = []
                if images:
                    code = "import json,sys; from PIL import Image,ImageChops; result=[]\nfor path in json.loads(sys.argv[1]):\n with Image.open(path) as im:\n  im.load(); frames=getattr(im,'n_frames',1); changed=False\n  if frames>1:\n   first=im.convert('RGB'); im.seek(frames//2); changed=ImageChops.difference(first,im.convert('RGB')).getbbox() is not None\n  result.append({'path':path,'width':im.width,'height':im.height,'frames':frames,'changed':changed})\nprint(json.dumps(result))"
                    image_checks = command([runtime['executables']['python'], '-I', '-B', '-c', code, json.dumps(images)], env, out / f'{name}-{target["format"]}-images.log.json')
                    for image in image_checks:
                        require(image['width'] > 100 and image['height'] > 100, 'Empty image')
                        if image['path'].endswith('.gif'):
                            topology = receipt['checks'].get('mode') == 'topology'
                            if topology:
                                scene = json.loads((receipt_path.parent / 'scene.json').read_text())
                                model = json.loads((receipt_path.parent / 'model.json').read_text())
                                expected_nodes = model['components' if kind == 'architecture' else 'nodes']
                                expected_edges = model['connections' if kind == 'architecture' else 'edges']
                                require([(n['id'], n['label']) for n in scene['nodes']] == [(n['id'], n['label']) for n in expected_nodes], 'Topology nodes changed')
                                require([(e['from'], e['to'], e.get('label', '')) for e in scene['edges']] ==
                                        [(e['from'], e['to'], e.get('label', '')) for e in expected_edges], 'Topology edges changed')
                                animation = receipt['checks']['animation']
                                require(animation['ok'] and animation['duration_ms'] == len(expected_edges) * 600 + 800, 'Topology animation duration mismatch')
                                require(scene['theme'] == theme and scene['min_text_px'] >= 8, 'Topology theme/readability mismatch')
                                require(scene['text_boxes'] and all(box['width'] > 0 and box['height'] > 0 for box in scene['text_boxes']), 'Topology text protection missing')
                                require(scene['icons'] == (2 if kind == 'architecture' else 0), 'Topology icons lost')
                                expected_frames = len(expected_edges) * 12 + 2
                            else:
                                expected_frames = 41
                            require(image['frames'] == expected_frames and image['changed'], 'GIF frame verification failed')
                target_report['image_checks'] = image_checks
        invalid_tasks = [
            {
                'contract_version': 1, 'name': 'invalid-independent-derive', 'type': 'workflow',
                'targets': [{'format': 'drawio', 'geometry': 'independent', 'derive': 'workflow',
                             'source': str(upstream / 'incident-response.workflow.json')}],
                'output_dir': str(out / 'delivery'),
            },
            {
                'contract_version': 1, 'name': 'invalid-html-geometry', 'type': 'workflow',
                'targets': [{'format': 'html', 'geometry': 'shared',
                             'source': str(upstream / 'incident-response.workflow.json')}],
                'output_dir': str(out / 'delivery'),
            },
        ]
        for index, invalid in enumerate(invalid_tasks):
            invalid_path = out / f'invalid-geometry-{index}.task.json'
            save(invalid_path, invalid)
            rejected = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/diagram.py'), 'route', str(invalid_path)],
                                      capture_output=True, text=True, env=env, timeout=120)
            require(rejected.returncode != 0 and json.loads(rejected.stdout)['status'] == 'failed',
                    f'Invalid geometry contract {index} was accepted')
        first_task = out / 'order-platform.task.json'
        snapshots = {str(p): digest(p) for folder in ('order-platform-html', 'order-platform-drawio')
                     for p in (out / 'delivery' / folder).iterdir() if p.is_file()}
        repeat = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/diagram.py'), 'run', str(first_task)], capture_output=True, text=True, env=env, timeout=120)
        require(repeat.returncode != 0 and json.loads(repeat.stdout)['status'] == 'failed', 'Existing delivery was not rejected')
        require(all(digest(Path(p)) == value for p, value in snapshots.items()), 'Existing output was modified')
        require(not list((out / 'delivery').glob('.architecture-diagram-*')), 'Staging directories leaked')
        failed = [case['name'] for case in report['cases'] if case['status'] == 'failed']
        report.update(status='failed' if failed else 'passed', output_protection='passed', poisoned_source_roots='passed', module_manifest=digest(ROOT / 'manifest.json'))
        if failed:
            report['diagnostic'] = 'Browser acceptance failed: ' + ', '.join(failed)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        report.update(status='failed', diagnostic=str(error))
    save(out / 'report.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Run real multi-format P0 acceptance without installing anything')
    parser.add_argument('--outdir', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = run(args.outdir.absolute())
        print(json.dumps({'status': result['status'], 'cases': len(result['cases']), 'report': str(args.outdir / 'report.json'), 'visual_review': result['visual_review']}, ensure_ascii=False))
        sys.exit(0 if result['status'] == 'passed' else 1)
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'failed', 'diagnostic': str(error)}, ensure_ascii=False))
        sys.exit(1)
