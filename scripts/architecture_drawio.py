"""Emit uncompressed DrawIO XML from Archify architecture or workflow layouts."""
import math
import xml.etree.ElementTree as ET


MARGIN = 40
TITLE_H = 56
ICON_SLOT = 44  # keep in sync with drawio_adapter icon spacingRight

COMPONENT_TYPES = {'frontend', 'backend', 'database', 'cloud', 'security', 'messagebus', 'external'}

# Per-type fill/stroke tuned to echo the Archify HTML palette. Same-source parity
# is defined by IDs/labels/edges/geometry, not pixel-identical colour.
_LIGHT_TYPES = {
    'frontend': ('#EFF6FF', '#5B7FA5'),
    'backend': ('#EEF2FF', '#5566A6'),
    'database': ('#ECFDF5', '#40866B'),
    'cloud': ('#F5F3FF', '#7A5EA6'),
    'security': ('#FEF2F2', '#B04A4A'),
    'messagebus': ('#FFF7ED', '#B5793A'),
    'external': ('#F3F4F6', '#6B7280'),
}
_DARK_TYPES = {
    'frontend': ('#12263A', '#7FA8D6'),
    'backend': ('#1B2140', '#8C9CE0'),
    'database': ('#0C2A20', '#5FC79E'),
    'cloud': ('#231633', '#B693E6'),
    'security': ('#331617', '#E08a8a'),
    'messagebus': ('#2C1E0E', '#E0A868'),
    'external': ('#26282E', '#A6ADBB'),
}
_THEME = {
    'light': {
        'background': '#FFFFFF',
        'font': '#172B4D',
        'edge': '#5B7FA5',
        'label_bg': '#FFFFFF',
        'boundary': '#8895A7',
        'types': _LIGHT_TYPES,
    },
    'dark': {
        'background': '#1E1E1E',
        'font': '#E6E6E6',
        'edge': '#9BB4D0',
        'label_bg': '#1E1E1E',
        'boundary': '#7A8699',
        'types': _DARK_TYPES,
    },
}


def _number(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Layout report contains a non-finite coordinate')
    return number


def _box(item):
    x, y = _number(item['x']), _number(item['y'])
    width, height = _number(item['width']), _number(item['height'])
    if width <= 0 or height <= 0:
        raise ValueError('Layout report box has a non-positive dimension')
    return x, y, width, height


def _anchor(box, point):
    x, y, width, height = box
    ax = min(1.0, max(0.0, (_number(point[0]) - x) / width))
    ay = min(1.0, max(0.0, (_number(point[1]) - y) / height))
    return round(ax, 4), round(ay, 4)


def _fmt(number):
    return f'{round(number):g}'


def build_mxfile(layout, title=None, theme='light'):
    """Return uncompressed mxfile bytes for an Archify layout report."""
    if isinstance(layout, dict) and layout.get('diagram_type') == 'workflow':
        return _build_workflow_mxfile(layout, title=title, theme=theme)
    if not isinstance(layout, dict) or layout.get('diagram_type') != 'architecture':
        raise ValueError('Expected an architecture or workflow layout report')
    palette = _THEME.get(theme)
    if palette is None:
        raise ValueError('theme must be light or dark')

    components = layout.get('components') or []
    boundaries = layout.get('boundaries') or []
    connections = layout.get('connections') or []
    if not isinstance(components, list) or not components:
        raise ValueError('Layout report has no components to emit')

    boxes = {}
    for comp in components:
        node_id = comp.get('id')
        if not isinstance(node_id, str) or not node_id.strip() or '\x00' in node_id:
            raise ValueError('Component id must be a non-empty string without NUL')
        if node_id in boxes:
            raise ValueError(f'Duplicate component id: {node_id}')
        boxes[node_id] = _box(comp)

    # Translate every coordinate so the drawing sits inside a titled, margined page.
    xs, ys = [], []
    for x, y, width, height in boxes.values():
        xs += [x, x + width]
        ys += [y, y + height]
    for boundary in boundaries:
        bx, by, bw, bh = _box(boundary)
        xs += [bx, bx + bw]
        ys += [by, by + bh]
    for conn in connections:
        for px, py in conn.get('points', []):
            xs.append(_number(px))
            ys.append(_number(py))
    title_band = TITLE_H if title else 0
    dx = MARGIN - min(xs)
    dy = MARGIN + title_band - min(ys)
    page_width = math.ceil((max(xs) - min(xs)) + 2 * MARGIN)
    page_height = math.ceil((max(ys) - min(ys)) + 2 * MARGIN + title_band)

    mxfile = ET.Element('mxfile', {'host': 'architecture-diagram', 'compressed': 'false'})
    diagram = ET.SubElement(mxfile, 'diagram', {'name': title or 'architecture', 'id': 'architecture'})
    model = ET.SubElement(diagram, 'mxGraphModel', {
        'grid': '1', 'gridSize': '10', 'page': '1',
        'pageWidth': str(page_width), 'pageHeight': str(page_height),
        'background': palette['background'],
    })
    root = ET.SubElement(model, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})

    def vertex(cell_id, value, style, x, y, width, height):
        cell = ET.SubElement(root, 'mxCell', {
            'id': cell_id, 'value': value, 'style': style, 'vertex': '1', 'parent': '1',
        })
        ET.SubElement(cell, 'mxGeometry', {
            'x': _fmt(x), 'y': _fmt(y), 'width': _fmt(width), 'height': _fmt(height), 'as': 'geometry',
        })
        return cell

    # A full-page fill guarantees the exported PNG shows the theme canvas: the
    # draw.io CLI does not honour the mxGraphModel background on export.
    vertex('canvas', '',
           f'rounded=0;html=1;strokeColor=none;fillColor={palette["background"]}',
           0, 0, page_width, page_height)

    if title:
        vertex('title', title,
               f'text;html=1;fontSize=24;fontStyle=1;align=left;verticalAlign=middle;fontColor={palette["font"]}',
               MARGIN, MARGIN - title_band + 8, page_width - 2 * MARGIN, 40)

    # Boundaries first so their fill sits behind the component nodes.
    used = set(boxes) | {'0', '1', 'title', 'canvas'}
    for index, boundary in enumerate(boundaries):
        bx, by, bw, bh = _box(boundary)
        cell_id = f'boundary_{index}'
        while cell_id in used:
            cell_id += '_'
        used.add(cell_id)
        label = boundary.get('label') or ''
        style = ';'.join([
            'rounded=0', 'html=1', 'dashed=1', 'fillColor=none',
            f'strokeColor={palette["boundary"]}', f'fontColor={palette["font"]}',
            'verticalAlign=top', 'align=left', 'fontStyle=2', 'spacing=8',
        ])
        vertex(cell_id, label, style, bx + dx, by + dy, bw, bh)

    for comp in components:
        node_id = comp['id']
        x, y, width, height = boxes[node_id]
        kind = comp.get('type') if comp.get('type') in COMPONENT_TYPES else 'external'
        fill, stroke = palette['types'][kind]
        label = comp.get('label')
        if not isinstance(label, str):
            label = node_id
        style = ';'.join([
            'rounded=1', 'html=1', 'whiteSpace=wrap', f'spacingRight={ICON_SLOT}',
            f'fillColor={fill}', f'strokeColor={stroke}', f'fontColor={palette["font"]}',
            'fontSize=18', 'verticalAlign=middle', 'align=center',
        ])
        vertex(node_id, label, style, x + dx, y + dy, width, height)

    for index, conn in enumerate(connections):
        source, target = conn.get('from'), conn.get('to')
        if source not in boxes or target not in boxes:
            raise ValueError(f'Connection references unknown component: {source} -> {target}')
        points = [[_number(px) + dx, _number(py) + dy] for px, py in conn.get('points', [])]
        if len(points) < 2:
            raise ValueError('Connection is missing routed endpoints')
        exit_x, exit_y = _anchor((boxes[source][0] + dx, boxes[source][1] + dy, boxes[source][2], boxes[source][3]), points[0])
        entry_x, entry_y = _anchor((boxes[target][0] + dx, boxes[target][1] + dy, boxes[target][2], boxes[target][3]), points[-1])
        variant = conn.get('variant') or 'default'
        parts = [
            'edgeStyle=orthogonalEdgeStyle', 'html=1', 'rounded=0', 'endArrow=block',
            f'strokeColor={palette["edge"]}', f'fontColor={palette["font"]}',
            f'labelBackgroundColor={palette["label_bg"]}',
            f'exitX={exit_x:g}', f'exitY={exit_y:g}', 'exitDx=0', 'exitDy=0',
            f'entryX={entry_x:g}', f'entryY={entry_y:g}', 'entryDx=0', 'entryDy=0',
        ]
        if variant in {'security', 'dashed'}:
            parts.append('dashed=1')
        if variant == 'emphasis':
            parts.append('strokeWidth=2')
        edge_id = f'e{index}_{source}__{target}'
        while edge_id in used:
            edge_id += '_'
        used.add(edge_id)
        cell = ET.SubElement(root, 'mxCell', {
            'id': edge_id, 'value': conn.get('label') or '', 'style': ';'.join(parts),
            'edge': '1', 'parent': '1', 'source': source, 'target': target,
        })
        geometry = ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
        interior = points[1:-1]
        if not interior:
            # validate.mjs warns on edges with no waypoint; a midpoint on the
            # straight run is harmless and keeps the check clean.
            interior = [[(points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2]]
        array = ET.SubElement(geometry, 'Array', {'as': 'points'})
        for px, py in interior:
            ET.SubElement(array, 'mxPoint', {'x': _fmt(px), 'y': _fmt(py)})

    ET.indent(mxfile, space='  ')
    xml = ET.tostring(mxfile, encoding='unicode', short_empty_elements=True).replace(' />', '/>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml + '\n').encode('utf-8')


def _build_workflow_mxfile(layout, title=None, theme='light'):
    palette = _THEME.get(theme)
    if palette is None:
        raise ValueError('theme must be light or dark')
    phases = layout.get('phases') or []
    lanes = layout.get('lanes') or []
    groups = layout.get('groups') or []
    nodes = layout.get('nodes') or []
    edges = layout.get('edges') or []
    if not isinstance(lanes, list) or not lanes or not isinstance(nodes, list) or not nodes:
        raise ValueError('Workflow layout report has no lanes or nodes to emit')

    node_boxes = {}
    for node in nodes:
        node_id = node.get('id')
        if not isinstance(node_id, str) or not node_id.strip() or '\x00' in node_id:
            raise ValueError('Workflow node id must be a non-empty string without NUL')
        if node_id in node_boxes:
            raise ValueError(f'Duplicate workflow node id: {node_id}')
        node_boxes[node_id] = _box(node)

    lane_boxes = {}
    for lane in lanes:
        lane_id = lane.get('id')
        if not isinstance(lane_id, str) or not lane_id.strip() or '\x00' in lane_id:
            raise ValueError('Workflow lane id must be a non-empty string without NUL')
        if lane_id in lane_boxes:
            raise ValueError(f'Duplicate workflow lane id: {lane_id}')
        lane_boxes[lane_id] = _box(lane)

    xs, ys = [], []
    for item in [*phases, *lanes, *groups, *nodes]:
        x, y, width, height = _box(item)
        xs.extend((x, x + width))
        ys.extend((y, y + height))
    for edge in edges:
        for px, py in edge.get('points', []):
            xs.append(_number(px))
            ys.append(_number(py))
    title_band = TITLE_H if title else 0
    dx = MARGIN - min(xs)
    dy = MARGIN + title_band - min(ys)
    page_width = math.ceil(max(xs) - min(xs) + 2 * MARGIN)
    page_height = math.ceil(max(ys) - min(ys) + 2 * MARGIN + title_band)

    mxfile = ET.Element('mxfile', {'host': 'architecture-diagram', 'compressed': 'false'})
    diagram = ET.SubElement(mxfile, 'diagram', {'name': title or 'workflow', 'id': 'workflow'})
    model = ET.SubElement(diagram, 'mxGraphModel', {
        'grid': '1', 'gridSize': '10', 'page': '1',
        'pageWidth': str(page_width), 'pageHeight': str(page_height),
        'background': palette['background'],
    })
    root = ET.SubElement(model, 'root')
    ET.SubElement(root, 'mxCell', {'id': '0'})
    ET.SubElement(root, 'mxCell', {'id': '1', 'parent': '0'})

    def vertex(cell_id, value, style, x, y, width, height, parent='1'):
        cell = ET.SubElement(root, 'mxCell', {
            'id': cell_id, 'value': value, 'style': style, 'vertex': '1', 'parent': parent,
        })
        ET.SubElement(cell, 'mxGeometry', {
            'x': _fmt(x), 'y': _fmt(y), 'width': _fmt(width), 'height': _fmt(height), 'as': 'geometry',
        })
        return cell

    vertex('canvas', '',
           f'rounded=0;html=1;strokeColor=none;fillColor={palette["background"]}',
           0, 0, page_width, page_height)
    if title:
        vertex('title', str(title),
               f'text;html=1;fontSize=24;fontStyle=1;align=left;verticalAlign=middle;fontColor={palette["font"]}',
               MARGIN, MARGIN - title_band + 8, page_width - 2 * MARGIN, 40)

    used = set(node_boxes) | {'0', '1', 'title', 'canvas'}
    for index, phase in enumerate(phases):
        x, y, width, height = _box(phase)
        cell_id = f'phase_{phase.get("id") or index}'
        while cell_id in used:
            cell_id += '_'
        used.add(cell_id)
        variant = phase.get('variant') or 'default'
        phase_color = palette['types']['security'][1] if variant == 'security' else palette['edge']
        vertex(cell_id, str(phase.get('label') or ''),
               f'rounded=1;html=1;fillColor={palette["label_bg"]};strokeColor={phase_color};fontColor={palette["font"]};fontStyle=1;fontSize=12;align=center;verticalAlign=middle',
               x + dx, y + dy, width, height)

    lane_cell_ids = {}
    for index, lane in enumerate(lanes):
        lane_id = lane['id']
        x, y, width, height = lane_boxes[lane_id]
        cell_id = f'lane_{lane_id}'
        while cell_id in used:
            cell_id += '_'
        used.add(cell_id)
        lane_cell_ids[lane_id] = cell_id
        exception = lane.get('variant') == 'exception'
        stroke = palette['types']['security'][1] if exception else palette['boundary']
        prefix = 'EX' if exception else f'{index + 1:02d}'
        vertex(cell_id, f'{prefix} / {lane.get("label") or lane_id}',
               f'swimlane;html=1;horizontal=1;startSize=32;rounded=1;fillColor=none;swimlaneFillColor=none;strokeColor={stroke};fontColor={palette["font"]};fontStyle=1;fontSize=13;align=left;verticalAlign=top;spacingLeft=10',
               x + dx, y + dy, width, height)

    for index, group in enumerate(groups):
        lane_id = group.get('lane')
        if lane_id not in lane_boxes:
            raise ValueError(f'Workflow group references unknown lane: {lane_id}')
        x, y, width, height = _box(group)
        lane_x, lane_y, _, _ = lane_boxes[lane_id]
        cell_id = f'group_{group.get("id") or index}'
        while cell_id in used:
            cell_id += '_'
        used.add(cell_id)
        variant = group.get('variant') or 'default'
        stroke = palette['types']['security'][1] if variant == 'security' else palette['boundary']
        vertex(cell_id, str(group.get('label') or ''),
               f'rounded=1;html=1;dashed=1;fillColor=none;strokeColor={stroke};fontColor={palette["font"]};fontStyle=2;fontSize=11;align=left;verticalAlign=top;spacing=6',
               x - lane_x, y - lane_y, width, height, lane_cell_ids[lane_id])

    for node in nodes:
        node_id = node['id']
        lane_id = node.get('lane')
        if lane_id not in lane_boxes:
            raise ValueError(f'Workflow node references unknown lane: {lane_id}')
        x, y, width, height = node_boxes[node_id]
        lane_x, lane_y, _, _ = lane_boxes[lane_id]
        kind = node.get('type') if node.get('type') in COMPONENT_TYPES else 'external'
        fill, stroke = palette['types'][kind]
        value = str(node.get('label') or node_id)
        style = ';'.join([
            'rounded=1', 'html=1', 'whiteSpace=wrap', f'spacingRight={ICON_SLOT}',
            f'fillColor={fill}', f'strokeColor={stroke}', f'fontColor={palette["font"]}',
            'fontSize=14', 'verticalAlign=middle', 'align=center',
        ])
        vertex(node_id, value, style, x - lane_x, y - lane_y, width, height, lane_cell_ids[lane_id])

    for index, edge in enumerate(edges):
        source, target = edge.get('from'), edge.get('to')
        if source not in node_boxes or target not in node_boxes:
            raise ValueError(f'Workflow edge references unknown node: {source} -> {target}')
        points = [[_number(px) + dx, _number(py) + dy] for px, py in edge.get('points', [])]
        if len(points) < 2:
            raise ValueError('Workflow edge is missing routed endpoints')
        source_box = node_boxes[source]
        target_box = node_boxes[target]
        exit_x, exit_y = _anchor((source_box[0] + dx, source_box[1] + dy, source_box[2], source_box[3]), points[0])
        entry_x, entry_y = _anchor((target_box[0] + dx, target_box[1] + dy, target_box[2], target_box[3]), points[-1])
        variant = edge.get('variant') or 'default'
        parts = [
            'edgeStyle=orthogonalEdgeStyle', 'html=1', 'rounded=0', 'endArrow=block',
            f'strokeColor={palette["edge"]}', f'fontColor={palette["font"]}',
            f'labelBackgroundColor={palette["label_bg"]}',
            f'exitX={exit_x:g}', f'exitY={exit_y:g}', 'exitDx=0', 'exitDy=0',
            f'entryX={entry_x:g}', f'entryY={entry_y:g}', 'entryDx=0', 'entryDy=0',
        ]
        if variant in {'security', 'dashed'}:
            parts.append('dashed=1')
        if variant == 'emphasis':
            parts.append('strokeWidth=2')
        preferred_id = edge.get('id')
        edge_id = preferred_id if isinstance(preferred_id, str) and preferred_id else f'e{index}_{source}__{target}'
        while edge_id in used:
            edge_id += '_'
        used.add(edge_id)
        cell = ET.SubElement(root, 'mxCell', {
            'id': edge_id, 'value': str(edge.get('label') or ''), 'style': ';'.join(parts),
            'edge': '1', 'parent': '1', 'source': source, 'target': target,
        })
        geometry = ET.SubElement(cell, 'mxGeometry', {'relative': '1', 'as': 'geometry'})
        interior = points[1:-1]
        if not interior:
            interior = [[(points[0][0] + points[-1][0]) / 2, (points[0][1] + points[-1][1]) / 2]]
        array = ET.SubElement(geometry, 'Array', {'as': 'points'})
        for px, py in interior:
            ET.SubElement(array, 'mxPoint', {'x': _fmt(px), 'y': _fmt(py)})

    ET.indent(mxfile, space='  ')
    xml = ET.tostring(mxfile, encoding='unicode', short_empty_elements=True).replace(' />', '/>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml + '\n').encode('utf-8')
