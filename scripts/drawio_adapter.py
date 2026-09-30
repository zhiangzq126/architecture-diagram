import base64
from html.parser import HTMLParser
import math
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote


_COLOR = (
    r'(?:#[0-9a-f]{3}|#[0-9a-f]{4}|#[0-9a-f]{6}|#[0-9a-f]{8}|'
    r'black|silver|gray|white|maroon|red|purple|fuchsia|green|lime|olive|yellow|'
    r'navy|blue|teal|aqua|orange|transparent|currentcolor|'
    r'rgb\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*\)|'
    r'rgba\(\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*\d{1,3}%?\s*,\s*(?:0|1|0?\.\d+)\s*\))'
)
_LENGTH = r'(?:0|(?:\d+(?:\.\d+)?|\.\d+)(?:px|pt|em|rem|%))'
_FAMILY_NAME = r'[\w -]+'
_FONT_FAMILY = rf'''(?:{_FAMILY_NAME}|"{_FAMILY_NAME}"|'{_FAMILY_NAME}')(?:\s*,\s*(?:{_FAMILY_NAME}|"{_FAMILY_NAME}"|'{_FAMILY_NAME}'))*'''
_CSS_FORMATTING = {
    'color': _COLOR,
    'background-color': _COLOR,
    'font-family': _FONT_FAMILY,
    'font-size': rf'(?:{_LENGTH}|xx-small|x-small|small|medium|large|x-large|xx-large|smaller|larger)',
    'font-weight': r'(?:normal|bold|bolder|lighter|[1-9]00)',
    'font-style': r'(?:normal|italic|oblique)',
    'text-decoration': r'(?:none|(?:underline|overline|line-through)(?:\s+(?:underline|overline|line-through))*)',
    'text-align': r'(?:left|right|center|justify|start|end)',
    'vertical-align': rf'(?:baseline|sub|super|top|text-top|middle|bottom|text-bottom|{_LENGTH})',
    'white-space': r'(?:normal|nowrap|pre|pre-wrap|pre-line|break-spaces)',
    'line-height': rf'(?:normal|\d+(?:\.\d+)?|\.\d+|{_LENGTH})',
    'letter-spacing': rf'(?:normal|-?{_LENGTH})',
}


def _validate_css(style):
    for declaration in style.split(';'):
        if not declaration.strip():
            continue
        name, separator, value = declaration.partition(':')
        pattern = _CSS_FORMATTING.get(name.strip().lower())
        if not separator or pattern is None or not re.fullmatch(pattern, value.strip(), re.I):
            raise ValueError('Unsupported HTML label CSS formatting')


class _LabelParser(HTMLParser):
    tags = {'br', 'div', 'span', 'p', 'b', 'strong', 'i', 'em', 'u', 's', 'font', 'sup', 'sub', 'center'}

    def handle_starttag(self, tag, attrs):
        if tag not in self.tags:
            raise ValueError(f'Unsupported HTML label tag: {tag}')
        seen = set()
        for name, value in attrs:
            if name in seen or value is None:
                raise ValueError(f'Invalid HTML label attribute: {name}')
            seen.add(name)
            if name == 'style':
                _validate_css(value)
                continue
            pattern = None
            if name == 'dir':
                pattern = r'(?:ltr|rtl|auto)'
            elif tag in {'div', 'p', 'center'} and name == 'align':
                pattern = r'(?:left|right|center|justify)'
            elif tag == 'font':
                pattern = {'color': _COLOR, 'face': _FONT_FAMILY, 'size': r'[+-]?[1-7]'}.get(name)
            if pattern is None or not re.fullmatch(pattern, value.strip(), re.I):
                raise ValueError(f'Unsupported HTML label attribute: {name}')

    def handle_endtag(self, tag):
        if tag not in self.tags:
            raise ValueError(f'Unsupported HTML label tag: {tag}')

    def handle_comment(self, data):
        raise ValueError('HTML label comments are not supported')

    def handle_decl(self, decl):
        raise ValueError('HTML label declarations are not supported')

    def unknown_decl(self, data):
        raise ValueError('HTML label declarations are not supported')

    def handle_pi(self, data):
        raise ValueError('HTML label processing instructions are not supported')


def parse_diagram(raw):
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('DrawIO XML is too large')
    try:
        text = raw.decode('utf-8', errors='strict')
    except UnicodeDecodeError as exc:
        raise ValueError('DrawIO XML must be UTF-8 encoded') from exc
    # BOM-less UTF-16 ASCII can decode as UTF-8 but still be sniffed as UTF-16 by XML parsers.
    if '\x00' in text:
        raise ValueError('DrawIO XML must be UTF-8 encoded without NUL characters')
    if re.search(r'<!\s*(DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('DrawIO XML contains DTD/entities')
    try:
        tree = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f'Invalid DrawIO XML: {exc}') from exc
    if tree.tag != 'mxfile' or not tree.findall('diagram'):
        raise ValueError('Expected an uncompressed mxfile with diagram pages')
    pages = []
    for page in tree.findall('diagram'):
        root = page.find('mxGraphModel/root')
        if root is None:
            raise ValueError('Compressed pages are not supported; save as uncompressed XML first')
        cells = list(root)
        for cell in cells:
            if cell.tag != 'mxCell':
                raise ValueError(f'Unsupported DrawIO root element: {cell.tag}; wrappers are not supported')
        mapping = {c.get('id'): c for c in cells}
        if len(mapping) != len(cells) or None in mapping or not {'0', '1'} <= mapping.keys():
            raise ValueError('Missing or duplicate DrawIO cell IDs')
        if mapping['1'].get('parent') != '0':
            raise ValueError('Invalid root layer')
        for cell in cells:
            cid = cell.get('id')
            try:
                parser = _LabelParser(convert_charrefs=True)
                parser.feed(cell.get('value', ''))
                parser.close()
            except (ValueError, AssertionError) as exc:
                raise ValueError(f'Unsafe or external HTML content in {cid}: {exc}') from exc
            for property_ in cell.get('style', '').split(';'):
                key, _, value = property_.partition('=')
                if key.strip().lower() in {'image', 'backgroundimage'} and value.strip().lower() != 'none':
                    raise ValueError(f'Unverified image in {cid}; use icon bindings instead')
            if cid in {'0', '1'}:
                continue
            if cell.get('parent') not in mapping or cell.get('parent') == '0':
                raise ValueError(f'Invalid parent for {cid}')
            visited = {cid}
            parent = cell.get('parent')
            while parent and parent != '0':
                if parent in visited or parent not in mapping:
                    raise ValueError(f'Cyclic or invalid parent for {cid}')
                visited.add(parent)
                parent = mapping[parent].get('parent')
            if cell.get('vertex') == '1':
                geo = cell.find('mxGeometry')
                if geo is None:
                    raise ValueError(f'Missing geometry for {cid}')
                for key in ('x', 'y', 'width', 'height'):
                    number = float(geo.get(key, '0'))
                    if not math.isfinite(number) or (key in ('width', 'height') and number <= 0):
                        raise ValueError(f'Invalid {key} for {cid}')
            if cell.get('edge') == '1':
                for endpoint in ('source', 'target'):
                    if cell.get(endpoint) not in mapping or mapping[cell.get(endpoint)].get('vertex') != '1':
                        raise ValueError(f'Invalid edge {endpoint} for {cid}')
        pages.append((page, root, mapping))
    return tree, pages


def attach_icons(raw, prepared, bindings):
    tree, pages = parse_diagram(raw)
    for binding in bindings:
        node_id = binding['node_id']
        found = [(root, cells[node_id]) for _, root, cells in pages
                 if node_id in cells and cells[node_id].get('vertex') == '1']
        if len(found) != 1:
            raise ValueError(f'Icon node {node_id} must identify exactly one vertex across all pages')
        if node_id not in prepared:
            continue
        root, cell = found[0]
        geo = cell.find('mxGeometry')
        width, height = float(geo.get('width')), float(geo.get('height'))
        if width < 100 or height < 48:
            raise ValueError(f'Node {node_id} needs width >=100 and height >=48 for its icon slot')
        child_id = f'{node_id}__ad_icon'
        if any(c.get('id') == child_id for c in root.findall('mxCell')):
            raise ValueError(f'Reserved icon cell already exists: {child_id}')
        icon = prepared[node_id]
        raw_svg = base64.b64decode(icon['svg_data_uri'].split(',', 1)[1], validate=True)
        image = 'data:image/svg+xml,' + quote(raw_svg.decode('utf-8'), safe='')
        style = [s for s in cell.get('style', '').split(';') if s and not s.startswith('spacingRight=')]
        previous = re.search(r'(?:^|;)spacingRight=([^;]+)', cell.get('style', ''))
        spacing = max(44, float(previous.group(1)) if previous else 0)
        cell.set('style', ';'.join([*style, f'spacingRight={spacing:g}']))
        fill = '#FFFFFF' if icon.get('theme', {}).get('dark_backdrop') else 'none'
        child = ET.SubElement(root, 'mxCell', {
            'id': child_id, 'value': '', 'vertex': '1', 'parent': node_id,
            'connectable': '0',
            'style': f'shape=image;html=1;imageAspect=1;aspect=fixed;image={image};fillColor={fill};strokeColor=none;movable=0;resizable=0;pointerEvents=0',
        })
        ET.SubElement(child, 'mxGeometry', {'x': f'{width - 36:g}', 'y': '8', 'width': '28', 'height': '28', 'as': 'geometry'})
    ET.indent(tree, space='  ')
    xml = ET.tostring(tree, encoding='unicode', short_empty_elements=True).replace(' />', '/>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n' + xml + '\n').encode('utf-8'), len(pages)
