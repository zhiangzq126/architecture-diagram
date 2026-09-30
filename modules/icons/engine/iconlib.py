#!/usr/bin/env python3
"""Private read-only query engine. Resource roots contain data, never Python code."""
from pathlib import Path
import argparse
import json
import re
import sys

# Also keep direct private-engine invocations read-only; the public caller uses -B.
sys.dont_write_bytecode = True
import library
from library import IconError, asset_bytes, load_catalog, norm, read_json, selected_variant

ROOT = library.ROOT
CONTRACT_VERSION = 2
INDEPENDENT_PROVIDERS = {'opensource', 'generic', 'status'}


def component_for(query):
    config = read_json(ROOT / 'catalog/component-mappings.json', {'schema_version': 1, 'components': []})
    if config.get('schema_version') != 1:
        raise IconError('catalog_error', 'Unsupported component mappings version')
    return next((x for x in config['components'] if norm(query) in {norm(t) for t in [x['name'], *x['aliases']]}), None)


def search(query, provider=None, include_pending=False, limit=20, catalog=None):
    catalog = catalog if catalog is not None else load_catalog()
    q, output, component = norm(query), [], component_for(query)
    for product in catalog['products']:
        if provider and product['provider'] != provider:
            continue
        if not include_pending and product['status'] != 'ready':
            continue
        normalized = [norm(x) for x in [product['id'], product['name'], *product['aliases']]]
        if component:
            independent = product['provider'] in INDEPENDENT_PROVIDERS and (
                product['id'] == component['id'] or bool(set(normalized) & {norm(t) for t in [component['name'], *component['aliases']]}))
            if independent or product['id'] in component['vendor_product_ids']:
                output.append({**product, 'score': 800 if independent else 700, 'component_name': component['name'],
                               'variant_count': sum(v['product_id'] == product['id'] for v in catalog['variants'])})
            continue
        score = (1000 if query.casefold() == product['id'].casefold() else
                 800 if q and q in normalized else 400 if q and any(q in x for x in normalized) else 0)
        if not query.strip():
            score = 1
        if not score:
            tokens = [norm(t) for t in re.split(r'\s+', query.strip()) if norm(t)]
            if tokens and all(any(t in x for x in normalized) for t in tokens):
                score = 200
        if score:
            output.append({**product, 'score': score,
                           'variant_count': sum(v['product_id'] == product['id'] for v in catalog['variants'])})
    output.sort(key=lambda p: (-p['score'], p['id']))
    return output[:limit]


def resolve(query, provider=None, catalog=None, preferences=None, variant_id=None):
    catalog = catalog if catalog is not None else load_catalog()
    preferences = preferences if preferences is not None else read_json(ROOT / 'catalog/preferences.json', {'defaults': {}})
    explicit = next((p for p in catalog['products'] if p['id'].casefold() == query.casefold() and (not provider or p['provider'] == provider)), None)
    found = [explicit] if explicit else search(query, provider, True, len(catalog['products']), catalog)
    exact = [p for p in found if explicit or p.get('score', 0) >= 800]
    messages = {
        'not_found': '当前查询范围内未找到图标。',
        'no_exact_match': '找到了相关素材，但尚未确定准确的产品身份。',
        'vendor_only': '仅有厂商版本，无可自动复用的组件 SVG。',
        'needs_provider_context': '需要明确厂商或组件映射。',
        'ambiguous': '存在歧义：匹配到多个产品身份。',
        'pending_review': '条目或变体仍待审核。',
        'invalid_variant': '指定变体不存在或不属于所选产品。',
        'unusable_variant': '所选变体缺失或不可用。',
    }

    def failure(status, choices=None):
        return {'status': status, 'fallback': 'card',
                'candidates': [{k: p[k] for k in ('id', 'name', 'provider', 'status')} for p in (choices or [])[:8]],
                'diagnostic': {'code': status, 'message': messages[status], 'action': '保留节点标签并使用状态卡片；确认产品、厂商或变体后重试。'}}

    component = component_for(query) if not explicit else None
    reuse = False
    independent = [p for p in exact if p['provider'] in INDEPENDENT_PROVIDERS]
    if not provider and not explicit and independent:
        exact = independent
    elif component and not explicit:
        if provider:
            exact = [p for p in found if p['id'] in component['vendor_product_ids'] or p['id'] == component['id'] or p['provider'] in INDEPENDENT_PROVIDERS]
        else:
            preferred = [p for p in found if p['id'] == component['preferred_svg_product_id'] and p['provider'] == 'aliyun']
            if not preferred:
                if found and all(p['status'] != 'ready' for p in found):
                    return failure('pending_review', found)
                return failure('vendor_only' if found else 'not_found', found)
            exact, reuse = preferred, True
    if not exact:
        return failure('no_exact_match' if found else 'not_found', found)
    if len(exact) > 1:
        return failure('ambiguous', exact)
    product = exact[0]
    if product['status'] != 'ready':
        return failure('pending_review', [product])
    if (not reuse and not provider and not explicit and product['provider'] not in INDEPENDENT_PROVIDERS
            and norm(query) in {'redis', 'mongodb', 'postgresql', 'postgres', 'mysql', 'elasticsearch', 'flink', 'kafka', 'kubernetes'}):
        return failure('needs_provider_context', [product])
    if variant_id is not None:
        variant = next((v for v in catalog['variants'] if v['id'] == variant_id and v['product_id'] == product['id']), None)
        if not variant:
            return failure('invalid_variant', [product])
    else:
        variant = selected_variant(product, catalog, preferences)
    if variant and variant['status'] == 'pending':
        return failure('pending_review', [product])
    if not variant or variant['status'] != 'ready' or variant['asset']['representation'] != 'vector':
        return failure('unusable_variant', [product])
    asset = variant['asset']
    if asset.get('format') != 'svg':
        raise IconError('catalog_error', 'Resolved asset must be SVG')
    # Validate before touching an asset; the public caller independently rechecks it.
    asset_bytes(ROOT, asset['path'], asset['sha256'])
    result = {
        'status': 'resolved', 'product_id': product['id'], 'name': product['name'], 'provider': product['provider'],
        'variant_id': variant['id'], 'style': variant['style'], 'asset_path': str(ROOT / asset['path']),
        'asset_relative_path': asset['path'], 'sha256': asset['sha256'], 'representation': 'vector',
        'theme': variant['theme'], 'kind': product['kind'],
        'selection': 'explicit_variant' if variant_id is not None else 'user_preference' if product['id'] in preferences.get('defaults', {}) else 'catalog_default',
        'display_name': component['name'] if component and not provider else product['name'],
        'match_type': 'vendor_svg_reuse' if reuse else 'independent_component' if product['provider'] in INDEPENDENT_PROVIDERS else 'exact_product',
    }
    if component and not provider:
        result['requested_component'] = {'id': component['id'], 'name': component['name']}
    result['diagnostic'] = {
        'code': result['match_type'],
        'message': f"缺少独立组件图标，已复用阿里云 {product['name']} 的 SVG；节点仍表示 {component['name']}，不表示部署在阿里云。" if reuse else '已找到可用图标。',
        'action': '保留用户原标签或使用 display_name；素材所属厂商不改变节点部署语义。',
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--root', type=Path, default=ROOT)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('search', 'resolve'):
        command = commands.add_parser(name, allow_abbrev=False)
        command.add_argument('query')
        command.add_argument('--provider')
        if name == 'search':
            command.add_argument('--limit', type=int, default=20)
        else:
            command.add_argument('--variant')
    commands.add_parser('show', allow_abbrev=False).add_argument('id')
    args = parser.parse_args()
    try:
        global_root = args.root.expanduser().resolve(strict=True)
        library.ROOT = global_root
        globals()['ROOT'] = global_root
        if args.command == 'search':
            if not 1 <= args.limit <= 1000:
                raise ValueError('limit must be between 1 and 1000')
            result = {'query': args.query, 'matches': search(args.query, args.provider, limit=args.limit)}
        elif args.command == 'resolve':
            result = resolve(args.query, args.provider, variant_id=args.variant)
        else:
            catalog = load_catalog()
            product = next((p for p in catalog['products'] if p['id'] == args.id), None)
            if product is None:
                raise IconError('not_found', 'Unknown product ID')
            result = {'product': product, 'variants': [v for v in catalog['variants'] if v['product_id'] == args.id]}
        result = {'contract_version': CONTRACT_VERSION, **result}
        code = 2 if args.command == 'resolve' and result['status'] != 'resolved' else 0
    except Exception as exc:
        result = {'contract_version': CONTRACT_VERSION, 'error': str(exc),
                  'error_code': exc.code if isinstance(exc, IconError) else 'catalog_error'}
        code = 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
