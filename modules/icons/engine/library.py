"""Private, standard-library-only, read-only icon catalog and SVG safety helpers."""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import math
import os
import re
import stat
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[3] / 'assets' / 'icon-pack'
SCHEMA_PATH = Path(__file__).resolve().parents[1] / 'schemas' / 'agent-response.schema.json'
SVG_NS = 'http://www.w3.org/2000/svg'
XLINK_NS = 'http://www.w3.org/1999/xlink'
XML_NS = 'http://www.w3.org/XML/1998/namespace'
MAX_SVG_BYTES = 4_000_000
MAX_SVG_DEPTH = 64
MAX_SVG_ELEMENTS = 20_000
MAX_JSON_BYTES = 16_000_000
ASSET_PATH = re.compile(r'assets/([0-9a-f]{2})/([0-9a-f]{64})\.svg\Z')
ET.register_namespace('', SVG_NS)
ET.register_namespace('xlink', XLINK_NS)


class IconError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('Non-finite JSON number: ' + value)

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def read_confined(root, relative, limit):
    """Open each component with dir_fd/O_NOFOLLOW, including the final file.

    No symlinks below the chosen canonical root are allowed (even in-root links).
    Descriptor-relative traversal also prevents a symlink swap between check/open.
    """
    if (not isinstance(relative, str) or not relative or '\\' in relative
            or '\x00' in relative or relative.startswith('/')
            or any(p in ('', '.', '..') for p in relative.split('/'))):
        raise IconError('path_error', 'Expected a canonical root-relative path')
    parts = PurePosixPath(relative).parts
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        os.close(fd)
        fd = child
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise IconError('path_error', 'Resource must be a regular file')
        if info.st_size > limit:
            raise IconError('size_limit', 'Resource exceeds byte limit')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise IconError('size_limit', 'Resource exceeds byte limit')
        return data
    except FileNotFoundError as exc:
        raise IconError('asset_missing', 'Resource does not exist: ' + relative) from exc
    except OSError as exc:
        raise IconError('path_error', 'Resource path is unreadable or contains a symlink') from exc
    finally:
        if fd is not None:
            os.close(fd)


def read_json(path, default=None):
    path = Path(path)
    relative = path.relative_to(ROOT).as_posix()
    try:
        raw = read_confined(ROOT, relative, MAX_JSON_BYTES)
    except IconError as exc:
        if exc.code == 'asset_missing' and default is not None:
            return default
        raise
    return strict_json(raw)


def load_catalog():
    catalog = read_json(ROOT / 'catalog/index.json')
    if not isinstance(catalog, dict) or type(catalog.get('schema_version')) is not int or catalog['schema_version'] != 1:
        raise IconError('catalog_error', 'Unsupported catalog version')
    for name in ('products', 'variants'):
        records = catalog.get(name)
        if not isinstance(records, list) or any(not isinstance(x, dict) or not isinstance(x.get('id'), str) for x in records):
            raise IconError('catalog_error', 'Invalid catalog ' + name)
        if len({x['id'] for x in records}) != len(records):
            raise IconError('catalog_error', 'Duplicate catalog IDs')
    return catalog


def asset_bytes(root, relative, digest, absolute=None):
    match = ASSET_PATH.fullmatch(relative) if isinstance(relative, str) else None
    if not match:
        raise IconError('path_error', 'SVG path must be assets/xx/<sha256>.svg')
    if absolute is not None and (not isinstance(absolute, str) or not Path(absolute).is_absolute()
                                 or absolute != str(root / relative)):
        raise IconError('path_error', 'Absolute and relative SVG paths disagree')
    if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise IconError('hash_mismatch', 'Invalid source SHA-256')
    if match[2] != digest or match[1] != digest[:2]:
        raise IconError('hash_mismatch', 'Content-addressed SVG path disagrees with SHA-256')
    raw = read_confined(root, relative, MAX_SVG_BYTES)
    if sha(raw) != digest:
        raise IconError('hash_mismatch', 'Original SVG SHA-256 mismatch')
    return raw


def clean(value):
    import html
    return ' '.join(html.unescape(value or '').split())


def norm(value):
    return re.sub(r'[^\w]+', '', clean(value).casefold())


def selected_variant(product, catalog, preferences=None):
    requested = (preferences or {}).get('defaults', {}).get(product['id'], product.get('default_variant'))
    return next((v for v in catalog['variants'] if v['product_id'] == product['id'] and v['id'] == requested), None)


def check_schema(value, schema):
    """Fail-closed validator for the bundled schema's bounded JSON Schema vocabulary."""
    errors = []
    allowed = {'$schema', '$defs', '$ref', 'oneOf', 'type', 'const', 'enum', 'properties',
               'required', 'additionalProperties', 'items', 'minItems', 'maxItems',
               'pattern', 'minLength', 'minimum', 'exclusiveMinimum'}

    def equal(a, b):
        if isinstance(a, bool) or isinstance(b, bool):
            return type(a) is type(b) and a == b
        return a == b

    def walk(x, rule, path='$'):
        unknown = set(rule) - allowed
        if unknown:
            raise ValueError('Unsupported schema keywords: ' + ','.join(sorted(unknown)))
        if 'oneOf' in rule:
            matches = 0
            for branch in rule['oneOf']:
                start = len(errors)
                walk(x, branch, path)
                matches += len(errors) == start
                del errors[start:]
            if matches != 1:
                errors.append(path + ': expected exactly one matching schema')
        if '$ref' in rule:
            ref = rule['$ref']
            if not ref.startswith('#/$defs/'):
                raise ValueError('Unsupported schema reference')
            walk(x, schema['$defs'][ref.split('/')[-1]], path)
        if 'type' in rule:
            types = rule['type'] if isinstance(rule['type'], list) else [rule['type']]
            checks = {'object': lambda: isinstance(x, dict), 'array': lambda: isinstance(x, list),
                      'string': lambda: isinstance(x, str), 'integer': lambda: type(x) is int,
                      'number': lambda: type(x) is int or (type(x) is float and math.isfinite(x)),
                      'null': lambda: x is None, 'boolean': lambda: type(x) is bool}
            if not any(checks[t]() for t in types):
                errors.append(path + ': incorrect type')
                return
        if 'const' in rule and not equal(x, rule['const']):
            errors.append(path + ': const mismatch')
        if 'enum' in rule and not any(equal(x, item) for item in rule['enum']):
            errors.append(path + ': invalid enum')
        if isinstance(x, dict):
            for key in rule.get('required', []):
                if key not in x:
                    errors.append(path + ': missing ' + key)
            for key, item in x.items():
                if key in rule.get('properties', {}):
                    walk(item, rule['properties'][key], path + '.' + key)
                elif rule.get('additionalProperties') is False:
                    errors.append(path + ': unexpected ' + key)
                elif isinstance(rule.get('additionalProperties'), dict):
                    walk(item, rule['additionalProperties'], path + '.' + key)
        if isinstance(x, list):
            if not rule.get('minItems', 0) <= len(x) <= rule.get('maxItems', float('inf')):
                errors.append(path + ': invalid array length')
            if 'items' in rule:
                for index, item in enumerate(x):
                    walk(item, rule['items'], path + '[' + str(index) + ']')
        if isinstance(x, str):
            if len(x) < rule.get('minLength', 0):
                errors.append(path + ': string too short')
            if 'pattern' in rule and not re.search(rule['pattern'], x):
                errors.append(path + ': pattern mismatch')
        if type(x) in (int, float):
            if type(x) is float and not math.isfinite(x):
                errors.append(path + ': non-finite number')
            if 'minimum' in rule and x < rule['minimum']:
                errors.append(path + ': below minimum')
            if 'exclusiveMinimum' in rule and x <= rule['exclusiveMinimum']:
                errors.append(path + ': below exclusive minimum')

    walk(value, schema)
    return errors


ELEMENTS = {'svg', 'g', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
            'defs', 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'mask', 'use',
            'symbol', 'title', 'desc', 'text', 'tspan'}
STYLE_PROPERTIES = {
    'fill', 'stroke', 'color', 'fill-opacity', 'stroke-opacity', 'opacity', 'fill-rule',
    'clip-rule', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
    'stroke-dasharray', 'stroke-dashoffset', 'stop-color', 'stop-opacity', 'clip-path',
    'mask', 'mask-type', 'paint-order', 'overflow', 'display', 'visibility', 'isolation',
    'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline',
}
ATTRIBUTES = STYLE_PROPERTIES | {
    'id', 'class', 'style', 'viewBox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2',
    'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'transform', 'preserveAspectRatio',
    'gradientUnits', 'gradientTransform', 'spreadMethod', 'offset', 'fx', 'fy', 'fr',
    'clipPathUnits', 'maskUnits', 'maskContentUnits', 'href', 'version',
    'dx', 'dy', 'rotate', 'textLength', 'lengthAdjust', 'role', 'aria-label',
    'aria-labelledby', 'aria-describedby', 'enable-background',
}
ID_PATTERN = re.compile(r'[A-Za-z_][A-Za-z0-9_.:-]*\Z')
URL_PATTERN = re.compile(r'url\(\s*(?:"#([A-Za-z_][A-Za-z0-9_.:-]*)"|\'#([A-Za-z_][A-Za-z0-9_.:-]*)\'|#([A-Za-z_][A-Za-z0-9_.:-]*))\s*\)', re.I)
URL_PROPERTIES = {'fill', 'stroke', 'clip-path', 'mask'}
COLOR_PROPERTIES = {'fill', 'stroke', 'color', 'stop-color'}
COLOR_PATTERN = re.compile(r'(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|(?:rgb|rgba|hsl|hsla)\([0-9.,%+\-\s]+\))\Z', re.I)


def safe_svg(raw):
    """Reject unsafe SVG and serialize without recoloring for image/data URI use only."""
    def reject(message):
        raise IconError('unsafe_svg', message)

    if len(raw) > MAX_SVG_BYTES:
        raise IconError('size_limit', 'SVG exceeds byte limit')
    try:
        text = raw.decode('utf-8-sig')
    except UnicodeDecodeError as exc:
        raise IconError('unsafe_svg', 'SVG must be UTF-8') from exc
    if '\x00' in text or re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', text, re.I):
        reject('DTD and entity declarations are forbidden')
    declaration = re.match(r'\s*<\?xml\s+[^?]*\?>', text)
    if declaration:
        encoding = re.search(r'encoding\s*=\s*[\'"]([^\'"]+)', declaration[0], re.I)
        if encoding and encoding[1].lower() not in ('utf-8', 'utf8'):
            reject('SVG declaration must specify UTF-8')
    depth = count = 0
    try:
        parser = ET.iterparse(io.StringIO(text), events=('start', 'end', 'pi', 'start-ns'))
        for event, element in parser:
            if event == 'pi':
                reject('Processing instructions are forbidden')
            if event == 'start-ns':
                if element[1] not in (SVG_NS, XLINK_NS, XML_NS):
                    reject('Unknown SVG namespace')
            elif event == 'start':
                depth += 1
                count += 1
                if depth > MAX_SVG_DEPTH or count > MAX_SVG_ELEMENTS:
                    raise IconError('svg_complexity_limit', 'SVG exceeds depth or element limit')
            elif event == 'end':
                depth -= 1
        root = parser.root
    except ET.ParseError as exc:
        raise IconError('unsafe_svg', 'Malformed SVG XML') from exc

    ids = {}
    references = []
    edges = {element: list(element) for element in root.iter()}

    def local_name(name):
        if name.startswith('{'):
            namespace, local = name[1:].split('}', 1)
            if namespace != SVG_NS:
                reject('Foreign SVG element namespace')
            return local
        return name

    def css_value(name, value, element):
        # Reject escape/comment tricks before interpreting URL tokens.
        if re.search(r'[\\\x00-\x1f\x7f<>@{};]|/\*|\*/|://|\b(?:javascript|data|file|vbscript)\s*:|expression\s*\(', value, re.I):
            reject('Unsafe SVG/CSS value: ' + name)
        urls = list(URL_PATTERN.finditer(value))
        remainder = URL_PATTERN.sub('', value)
        if re.search(r'url\s*\(', remainder, re.I):
            reject('Only literal local url(#id) references are allowed')
        if urls:
            if name not in URL_PROPERTIES or len(urls) != 1:
                reject('URL not allowed for property: ' + name)
            if remainder.strip() and (name not in ('fill', 'stroke') or not COLOR_PATTERN.fullmatch(remainder.strip())):
                reject('Unsupported URL fallback')
            references.append((element, next(x for x in urls[0].groups() if x), name))
        # Consume identifiers even without '(' to avoid rescanning their suffixes.
        functions = (match[1] for match in re.finditer(r'([a-zA-Z_-][a-zA-Z0-9_-]*)\s*(\()?', remainder)
                     if match[2])
        permitted = {'matrix', 'translate', 'scale', 'rotate', 'skewX', 'skewY'} if name in ('transform', 'gradientTransform') else set()
        if name in COLOR_PROPERTIES:
            permitted |= {'rgb', 'rgba', 'hsl', 'hsla'}
        if any(function not in permitted for function in functions):
            reject('Unsupported SVG/CSS function: ' + name)
        if name in COLOR_PROPERTIES and not urls and not COLOR_PATTERN.fullmatch(value):
            reject('Unsupported color value')
        if name in ('clip-path', 'mask') and not urls and value != 'none':
            reject('Expected none or a local SVG reference')
        return value

    if local_name(root.tag) != 'svg':
        reject('Root element must be svg')
    for element in root.iter():
        tag = local_name(element.tag)
        if tag not in ELEMENTS:
            reject('Unsupported SVG element: ' + tag)
        element.tag = '{' + SVG_NS + '}' + tag
        attrs = {}
        for key, value in element.attrib.items():
            local = key
            if key.startswith('{'):
                namespace, local = key[1:].split('}', 1)
                if namespace == XML_NS and local == 'space' and value in ('default', 'preserve'):
                    attrs[key] = value
                    continue
                if namespace != XLINK_NS or local != 'href':
                    reject('Unsupported namespaced attribute')
            if local.lower().startswith('on'):
                reject('SVG event handlers are forbidden')
            if local not in ATTRIBUTES:
                reject('Unsupported SVG attribute: ' + local)
            if local == 'id':
                if not ID_PATTERN.fullmatch(value) or value in ids:
                    reject('Invalid or duplicate SVG ID')
                ids[value] = element
            elif local == 'href':
                if tag not in ('use', 'linearGradient', 'radialGradient') or not value.startswith('#') or not ID_PATTERN.fullmatch(value[1:]):
                    reject('Only local use/gradient href references are allowed')
                references.append((element, value[1:], 'href'))
            elif local in ('aria-labelledby', 'aria-describedby'):
                for target in value.split():
                    references.append((element, target, 'aria'))
            elif local == 'style':
                declarations = []
                seen = set()
                for part in value.split(';'):
                    if not part.strip():
                        continue
                    if ':' not in part:
                        reject('Malformed inline CSS')
                    name, item = (piece.strip() for piece in part.split(':', 1))
                    name = name.lower()
                    if name not in STYLE_PROPERTIES or name in seen or not item:
                        reject('Unsupported or duplicate inline CSS property: ' + name)
                    seen.add(name)
                    declarations.append(name + ':' + css_value(name, item, element))
                value = ';'.join(declarations)
            elif local not in ('aria-label', 'role', 'class'):
                value = css_value(local, value, element)
            attrs[key] = value
        element.attrib.clear()
        element.attrib.update(sorted(attrs.items()))

    for element, target, kind in references:
        if target not in ids:
            reject('Dangling SVG reference: ' + target)
        destination = ids[target]
        dest_tag = local_name(destination.tag)
        if kind in ('fill', 'stroke') and dest_tag not in ('linearGradient', 'radialGradient'):
            reject('Paint references must target gradients')
        if kind == 'clip-path' and dest_tag != 'clipPath':
            reject('clip-path must reference clipPath')
        if kind == 'mask' and dest_tag != 'mask':
            reject('mask must reference mask')
        if kind == 'href' and local_name(element.tag) in ('linearGradient', 'radialGradient') and dest_tag not in ('linearGradient', 'radialGradient'):
            reject('Gradient href must target a gradient')
        if kind != 'aria':
            edges[element].append(destination)

    # Bound the expansion DAG, not only XML size: nested <use> may expand exponentially.
    costs, heights, visiting = {}, {}, set()
    for start in edges:
        stack = [(start, False)]
        while stack:
            element, done = stack.pop()
            if done:
                costs[element] = 1 + sum(costs[child] for child in edges[element])
                heights[element] = 1 + max((heights[child] for child in edges[element]), default=0)
                visiting.remove(element)
                if costs[element] > MAX_SVG_ELEMENTS or heights[element] > MAX_SVG_DEPTH:
                    raise IconError('svg_complexity_limit', 'SVG reference expansion exceeds depth or element limit')
            elif element not in costs:
                if element in visiting:
                    reject('Cyclic SVG references')
                if len(visiting) >= MAX_SVG_DEPTH:
                    raise IconError('svg_complexity_limit', 'SVG reference depth exceeds limit')
                visiting.add(element)
                stack.append((element, True))
                stack.extend((child, False) for child in reversed(edges[element]))

    view_box = root.get('viewBox')
    if view_box:
        try:
            numbers = [float(x) for x in re.split(r'[\s,]+', view_box.strip())]
        except ValueError:
            reject('Invalid SVG viewBox')
        if len(numbers) != 4 or not all(math.isfinite(x) for x in numbers) or min(numbers[2:]) <= 0:
            reject('Invalid SVG viewBox')
    else:
        dimensions = []
        for name in ('width', 'height'):
            value = root.get(name, '')
            if not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:px)?', value):
                reject('SVG requires viewBox or positive numeric dimensions')
            number = float(value.removesuffix('px'))
            if not math.isfinite(number) or number <= 0:
                reject('Invalid SVG dimensions')
            dimensions.append(number)
        root.set('viewBox', '0 0 ' + ' '.join(format(x, 'g') for x in dimensions))
    safe = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if len(safe) > MAX_SVG_BYTES:
        raise IconError('size_limit', 'Serialized SVG exceeds byte limit')
    return safe
