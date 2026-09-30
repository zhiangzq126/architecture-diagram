import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import dns from 'node:dns';
import dnsPromises from 'node:dns/promises';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import https from 'node:https';
import { syncBuiltinESMExports } from 'node:module';
import net from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import {
  brandLabelFitWidth, brandMarkFor, brandMetadataFor, brandTopRailProblem,
  captureBrandReference, findBrandMark, listBrandMarks,
  prepareDiagramBrandMarks, renderBrandMark,
} from '../modules/archify/renderers/shared/brand-marks.mjs';
import { focusNodeAttrs } from '../modules/archify/renderers/shared/cli.mjs';

const COLLECTIONS = {
  architecture: 'components', workflow: 'nodes', sequence: 'participants',
  dataflow: 'nodes', lifecycle: 'states',
};
const MAX_IMAGE_BYTES = 1024 * 1024;
const DATA_PREFIX = 'data:image/svg+xml;base64,';
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><defs><linearGradient id="paint"><stop stop-color="#f60"/><stop offset="1" stop-color="#08f"/></linearGradient></defs><path fill="url(#paint)" d="M0 0h32v32H0z"/></svg>';
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const SOURCE_HASH = sha256('original local SVG before Python sanitization');

function fixture(bytes = SVG, overrides = {}) {
  const buffer = Buffer.from(bytes);
  const entry = {
    svg_data_uri: `${DATA_PREFIX}${buffer.toString('base64')}`,
    svg_sha256: sha256(buffer),
    source_sha256: SOURCE_HASH,
    product_id: 'fixture/product',
    variant_id: 'fixture/product@color',
    theme: { preserve_colors: true, dark_backdrop: true },
    ...overrides,
  };
  const identityHash = sha256(Buffer.from(JSON.stringify([
    entry.svg_sha256, entry.source_sha256, entry.product_id, entry.variant_id, entry.theme.dark_backdrop,
  ]), 'utf8'));
  return { id: `ad-icon:${identityHash}`, entry };
}

async function withEnv(name, value, run) {
  const previous = process.env[name];
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
  try {
    return await run();
  } finally {
    if (previous === undefined) delete process.env[name];
    else process.env[name] = previous;
  }
}

async function withRegistry(registry, run, { raw = false } = {}) {
  const directory = mkdtempSync(join(tmpdir(), 'architecture-diagram-icons-'));
  const path = join(directory, 'registry.json');
  try {
    writeFileSync(path, raw ? registry : JSON.stringify(registry));
    return await withEnv('ARCHITECTURE_DIAGRAM_ICONS', path, () => run(path));
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

function diagnostic(code, pattern) {
  return (error) => {
    assert.ok(error.archifyDiagnostics?.some((item) => item.code === code && item.severity === 'error'), error.message);
    if (pattern) assert.match(error.message, pattern);
    return true;
  };
}

async function prepareOne(brand) {
  const node = { id: 'service', label: 'Service', brand };
  await prepareDiagramBrandMarks('architecture', { components: [node] });
  return node;
}

test('all five collections prepare private icons in existing slots and retain metadata/layout checks', async (t) => {
  const { id, entry } = fixture();
  assert.equal(id.length, 72);
  assert.notEqual(entry.source_sha256, entry.svg_sha256);
  await withRegistry({ [id]: entry }, async () => {
    for (const [kind, collection] of Object.entries(COLLECTIONS)) {
      await t.test(kind, async () => {
        const node = { id: 'service', label: 'Service', brand: id };
        const builtin = { id: 'db', label: 'Database', brand: 'postgresql' };
        const plain = { id: 'plain', label: 'Plain' };
        const diagram = { [collection]: [node, builtin, plain] };
        const original = JSON.stringify(diagram);
        await prepareDiagramBrandMarks(kind, diagram);
        assert.equal(JSON.stringify(diagram), original, 'authored node data must not change');
        assert.equal(brandMarkFor(node).kind, 'local');
        assert.equal(brandMarkFor(node).dataUrl, entry.svg_data_uri);
        assert.equal(brandMarkFor(builtin).kind, 'preset');
        assert.equal(brandMarkFor(plain), null);
        const metadata = brandMetadataFor(node);
        assert.equal(metadata.brandId, id);
        assert.equal(metadata.brandSource, `sha256:${SOURCE_HASH}`);
        assert.equal(metadata.brandSourceSha256, SOURCE_HASH);
        assert.equal(metadata.brandSha256, entry.svg_sha256);
        assert.equal(metadata.brandProductId, entry.product_id);
        assert.equal(metadata.brandVariantId, entry.variant_id);
        const nodeAttrs = focusNodeAttrs(node.id, node.label, metadata, 'en');
        assert.ok(nodeAttrs.includes(`data-node-brand-id="${id}"`));
        assert.ok(nodeAttrs.includes(`data-node-brand-source="sha256:${SOURCE_HASH}"`));
        assert.equal(brandLabelFitWidth(node, 200), 152);
        assert.equal(brandLabelFitWidth(node, 30), 1);
        assert.equal(brandLabelFitWidth(plain, 200), 200);
        assert.equal(brandTopRailProblem(node, 200, 12), null);
        assert.match(brandTopRailProblem(node, 50, 12), /brand top rail leaves 2px/);
        assert.equal(brandTopRailProblem(plain, 1, 12), null);
        const html = renderBrandMark(node, { x: 12, y: 20 });
        assert.ok(html.includes(`href="${entry.svg_data_uri}"`));
        assert.equal((html.match(/<image\b/g) || []).length, 1);
        assert.doesNotMatch(html, /<svg\b|<path\b|<defs\b|<linearGradient\b/);
        assert.match(html, /transform="translate\(12 20\)"/);
        assert.match(html, /x="3" y="3" width="10" height="10"/);
        assert.match(html, /class="brand-mark-badge" style="fill:#fff"/);
        assert.ok(html.includes(`data-brand-source-sha256="${SOURCE_HASH}"`));
        assert.ok(html.includes(`data-brand-sha256="${entry.svg_sha256}"`));
        assert.match(renderBrandMark(builtin, { x: 0, y: 0 }), /<path\b/);
        assert.equal(renderBrandMark(plain), '');
      });
    }
  });
});

test('identical SVG bytes retain distinct identities and exact metadata in all five collections', async (t) => {
  const fixtures = [
    fixture(),
    fixture(SVG, { product_id: 'fixture/产品' }),
    fixture(SVG, { variant_id: 'fixture/product@彩色' }),
    fixture(SVG, { source_sha256: sha256('another original local SVG') }),
    fixture(SVG, { theme: { preserve_colors: true, dark_backdrop: false } }),
  ];
  assert.equal(new Set(fixtures.map(({ id }) => id)).size, fixtures.length);
  assert.equal(new Set(fixtures.map(({ entry }) => entry.svg_sha256)).size, 1);
  assert.equal(new Set(fixtures.map(({ entry }) => entry.svg_data_uri)).size, 1);
  const registry = Object.fromEntries(fixtures.map(({ id, entry }) => [id, entry]));
  await withRegistry(registry, async () => {
    for (const [kind, collection] of Object.entries(COLLECTIONS)) {
      await t.test(kind, async () => {
        const nodes = fixtures.map(({ id }, index) => ({ id: `node-${index}`, label: 'Service', brand: id }));
        const diagram = { [collection]: nodes };
        const original = JSON.stringify(diagram);
        await prepareDiagramBrandMarks(kind, diagram);
        assert.equal(JSON.stringify(diagram), original);
        for (const [index, node] of nodes.entries()) {
          const { id, entry } = fixtures[index];
          const mark = brandMarkFor(node);
          assert.equal(mark.id, id);
          assert.equal(mark.dataUrl, entry.svg_data_uri);
          assert.deepEqual(mark.theme, entry.theme);
          assert.deepEqual(brandMetadataFor(node), {
            brand: entry.product_id,
            brandId: id,
            brandStatus: 'local',
            brandSource: `sha256:${entry.source_sha256}`,
            brandSha256: entry.svg_sha256,
            brandSourceSha256: entry.source_sha256,
            brandProductId: entry.product_id,
            brandVariantId: entry.variant_id,
          });
          const html = renderBrandMark(node, { x: 0, y: 0 });
          for (const [attribute, value] of Object.entries({
            'data-brand-mark': id,
            'data-brand-sha256': entry.svg_sha256,
            'data-brand-source-sha256': entry.source_sha256,
            'data-brand-product-id': entry.product_id,
            'data-brand-variant-id': entry.variant_id,
            href: entry.svg_data_uri,
          })) assert.ok(html.includes(`${attribute}="${value}"`));
          assert.ok(html.includes(`class="brand-mark-badge" style="fill:${entry.theme.dark_backdrop ? '#fff' : 'none'}"`));
        }
      });
    }
  });
});

test('a packaged local SVG fixture round-trips unchanged with original colors', async () => {
  const assetHash = '2ee193029bfdb2fc75804ba07530c2a9cfb5fd3350f8239c3f3fec2f4c4bfd0b';
  const bytes = readFileSync(new URL(`../assets/icon-pack/assets/2e/${assetHash}.svg`, import.meta.url));
  assert.equal(sha256(bytes), assetHash);
  const { id, entry } = fixture(bytes, {
    product_id: 'aliyun/ams-cloud-app',
    variant_id: 'aliyun/ams-cloud-app@orange-glyph-2ee193029b',
  });
  await withRegistry({ [id]: entry }, async () => {
    const html = renderBrandMark(await prepareOne(id), { x: 0, y: 0, size: 24 });
    const uri = html.match(/<image href="([^"]+)"/)[1];
    const decoded = Buffer.from(uri.slice(DATA_PREFIX.length), 'base64');
    assert.deepEqual(decoded, bytes);
    assert.match(decoded.toString('utf8'), /fill="#FF6A00"/);
    assert.doesNotMatch(html, /<svg\b/);
  });
});

test('dark_backdrop=false is transparent and product/variant attributes are escaped', async () => {
  const { id, entry } = fixture(SVG, {
    product_id: 'fixture/"<color>&', variant_id: 'variant/"<&',
    theme: { preserve_colors: true, dark_backdrop: false },
  });
  await withRegistry({ [id]: entry }, async () => {
    const html = renderBrandMark(await prepareOne(id), { x: 0, y: 0 });
    assert.match(html, /class="brand-mark-badge" style="fill:none"/);
    assert.match(html, /data-brand-product-id="fixture\/&quot;&lt;color&gt;&amp;"/);
    assert.match(html, /data-brand-variant-id="variant\/&quot;&lt;&amp;"/);
    assert.ok(html.includes(`href="${entry.svg_data_uri}"`));
  });
});

test('registry path must be absolute, readable local JSON; no registry is needed for built-ins', async () => {
  const { id } = fixture();
  for (const path of [undefined, '', 'registry.json', 'https://example.invalid/icons.json']) {
    await withEnv('ARCHITECTURE_DIAGRAM_ICONS', path, async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry', /absolute local/));
      assert.equal(brandMarkFor(await prepareOne('postgresql')).kind, 'preset');
    });
  }
  await withRegistry({}, async (path) => {
    await withEnv('ARCHITECTURE_DIAGRAM_ICONS', `${path}.missing`, async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry', /cannot read or parse/));
    });
    await withEnv('ARCHITECTURE_DIAGRAM_ICONS', join(path, '..'), async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry', /cannot read or parse/));
    });
  });
  await withRegistry('{not-json', async () => {
    await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry', /cannot read or parse/));
  }, { raw: true });
});

test('registry validates all entries, keys, required fields, hashes, and theme types', async (t) => {
  const { id, entry } = fixture();
  const invalid = [
    ['array registry', []], ['null registry', null], ['string registry', 'bad'],
    ['non-private key', { postgresql: entry }],
    ['uppercase key', { [id.toUpperCase()]: entry }],
    ['key with trailing newline', { [`${id}\n`]: entry }],
    ['null entry', { [id]: null }], ['array entry', { [id]: [] }],
    ...[
      ['SVG hash missing', { svg_sha256: undefined }],
      ['source hash missing', { source_sha256: undefined }],
      ['source hash invalid', { source_sha256: 'g'.repeat(64) }],
      ['source hash newline', { source_sha256: `${SOURCE_HASH}\n` }],
      ['product missing', { product_id: undefined }],
      ['product wrong type', { product_id: {} }],
      ['variant empty', { variant_id: ' ' }],
      ['theme missing', { theme: undefined }], ['theme array', { theme: [] }],
      ['recoloring requested', { theme: { preserve_colors: false, dark_backdrop: true } }],
      ['backdrop missing', { theme: { preserve_colors: true } }],
      ['backdrop wrong type', { theme: { preserve_colors: true, dark_backdrop: 'true' } }],
    ].map(([name, patch]) => [name, { [id]: { ...entry, ...patch } }]),
    ['invalid unused entry', { [id]: entry, [`ad-icon:${'0'.repeat(64)}`]: null }],
  ];
  for (const [name, registry] of invalid) {
    await t.test(name, () => withRegistry(registry, async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry'));
    }));
  }
});

test('data URI requires exact SVG MIME and canonical base64', async (t) => {
  const { id, entry } = fixture();
  const invalid = [
    undefined, 'https://example.invalid/icon.svg', SVG,
    'data:image/png;base64,PHN2Zy8+', 'data:image/svg+xml,<svg/>',
    'data:image/svg+xml;charset=utf-8;base64,PHN2Zy8+',
    'DATA:image/svg+xml;base64,PHN2Zy8+', `${DATA_PREFIX}`,
    `${DATA_PREFIX}!!!!`, `${DATA_PREFIX}====`, `${DATA_PREFIX}AA=A`,
    `${DATA_PREFIX}PHN2Zy8+\n`, `${DATA_PREFIX}PHN2Zy8-`,
    `${DATA_PREFIX}Zg`, `${DATA_PREFIX}Zh==`, `${DATA_PREFIX}Zg===`,
  ];
  for (const [index, uri] of invalid.entries()) {
    await t.test(`invalid URI ${index}`, () => withRegistry({ [id]: { ...entry, svg_data_uri: uri } }, async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry'));
    }));
  }
});

test('SVG bytes and registry identity digests are independently verified', async () => {
  const { id, entry } = fixture();
  const wrongHash = '0'.repeat(64);
  const wrongSvgHash = fixture(SVG, { svg_sha256: wrongHash });
  for (const [brand, registry, message] of [
    [id, { [id]: { ...entry, svg_sha256: wrongHash } }, /SVG digest/],
    [wrongSvgHash.id, { [wrongSvgHash.id]: wrongSvgHash.entry }, /SVG digest/],
    [`ad-icon:${wrongHash}`, { [`ad-icon:${wrongHash}`]: entry }, /identity digest/],
    [`ad-icon:${entry.svg_sha256}`, { [`ad-icon:${entry.svg_sha256}`]: entry }, /identity digest/],
    [id, { [id]: { ...entry, svg_data_uri: fixture(`${SVG}\n`).entry.svg_data_uri } }, /SVG digest/],
    ...[
      { source_sha256: sha256('changed source') },
      { product_id: 'changed/product' },
      { variant_id: 'changed/variant' },
      { theme: { preserve_colors: true, dark_backdrop: false } },
      { svg_sha256: fixture(`${SVG}\n`).entry.svg_sha256, svg_data_uri: fixture(`${SVG}\n`).entry.svg_data_uri },
    ].map((patch) => [id, { [id]: { ...entry, ...patch } }, /identity digest/]),
  ]) {
    await withRegistry(registry, async () => {
      await assert.rejects(prepareOne(brand), diagnostic('brand/digest-mismatch', message));
    });
  }
});

test('non-SVG and invalid UTF-8 bytes fail even when their digest matches', async () => {
  for (const bytes of ['<html/>', Buffer.from([0xff, 0xfe]), Buffer.concat([Buffer.from('<svg>'), Buffer.from([0xff]), Buffer.from('</svg>')])]) {
    const { id, entry } = fixture(bytes);
    await withRegistry({ [id]: entry }, async () => {
      await assert.rejects(prepareOne(id), diagnostic('brand/icon-registry'));
    });
  }
});

test('decoded SVG size limit is inclusive at 1 MiB, including the base64 rounding boundary', async () => {
  const prefix = '<svg xmlns="http://www.w3.org/2000/svg">';
  const suffix = '</svg>';
  for (const size of [MAX_IMAGE_BYTES, MAX_IMAGE_BYTES + 1, MAX_IMAGE_BYTES + 3]) {
    const svg = prefix + ' '.repeat(size - prefix.length - suffix.length) + suffix;
    assert.equal(Buffer.byteLength(svg), size);
    const { id, entry } = fixture(svg);
    await withRegistry({ [id]: entry }, async () => {
      if (size === MAX_IMAGE_BYTES) assert.equal(brandMarkFor(await prepareOne(id)).sha256, entry.svg_sha256);
      else await assert.rejects(prepareOne(id), diagnostic('brand/icon-too-large', /1 MiB/));
    });
  }
});

test('unknown and malformed IDs fail without normalizing or falling back', async () => {
  const { id, entry } = fixture();
  await withRegistry({ [id]: entry }, async () => {
    await assert.rejects(prepareOne(`ad-icon:${'0'.repeat(64)}`), diagnostic('brand/unknown', /not in the local icon registry/));
    for (const bad of [`ad-icon:${id.slice('ad-icon:'.length).toUpperCase()}`, `${id}\n`, `${id} `, `${id}/extra`, id.slice(0, -1)]) {
      await assert.rejects(prepareOne(bad), diagnostic('brand/icon-id'));
    }
    for (const bad of [` ${id}`, id.toUpperCase(), id.replace(':', '_'), 'unknown-brand-id']) {
      await assert.rejects(prepareOne(bad), (error) => {
        assert.ok(error.archifyDiagnostics?.length);
        return true;
      });
    }
    assert.equal(findBrandMark(id), null, 'private IDs must not resolve as built-in aliases');
  });
});

test('re-preparing nodes clears stale icons and never caches an earlier registry', async () => {
  const { id, entry } = fixture();
  const node = { id: 'service', label: 'Service', brand: id };
  const diagram = { components: [node] };
  await withRegistry({ [id]: entry }, async (path) => {
    await prepareDiagramBrandMarks('architecture', diagram);
    assert.ok(brandMarkFor(node));
    writeFileSync(path, '{}');
    await assert.rejects(prepareDiagramBrandMarks('architecture', diagram), diagnostic('brand/unknown'));
    assert.equal(brandMarkFor(node), null);
    writeFileSync(path, JSON.stringify({ [id]: entry }));
    await prepareDiagramBrandMarks('architecture', diagram);
    delete node.brand;
    await prepareDiagramBrandMarks('architecture', diagram);
    assert.equal(brandMarkFor(node), null);
    assert.equal(brandLabelFitWidth(node, 200), 200);
    node.brand = id;
    const invalid = { id: 'unknown', brand: 'not-a-known-brand' };
    await assert.rejects(prepareDiagramBrandMarks('architecture', { components: [node, invalid] }), diagnostic('brand/unknown'));
    assert.equal(brandMarkFor(node), null, 'failed preparations must not leave partial resolved marks');
  });
});

test('all diagram URL/object brands, URL lookup, and capture fail without DNS, HTTP, sockets, or fetch', async (t) => {
  const mocks = [];
  const block = (object, method) => mocks.push(t.mock.method(object, method, () => {
    throw new Error(`unexpected network call: ${method}`);
  }));
  for (const transport of [http, https]) for (const method of ['request', 'get']) block(transport, method);
  for (const resolver of [dns, dnsPromises]) for (const method of ['lookup', 'resolve', 'resolve4', 'resolve6']) block(resolver, method);
  for (const method of ['connect', 'createConnection']) block(net, method);
  block(net.Socket.prototype, 'connect');
  block(globalThis, 'fetch');
  syncBuiltinESMExports();
  try {
    await withEnv('ARCHIFY_BRAND_ALLOW_PRIVATE', '1', async () => {
      for (const [kind, collection] of Object.entries(COLLECTIONS)) {
        for (const brand of [
          'https://github.com', 'http://127.0.0.1:1234/icon.svg', 'https://example.invalid/icon.svg',
          '//example.invalid/icon.svg', 'file:///tmp/icon.svg', `${DATA_PREFIX}PHN2Zy8+`,
          { url: 'https://github.com', sha256: SOURCE_HASH },
          { url: 'http://127.0.0.1:1234', sha256: SOURCE_HASH },
          { url: 'https://example.invalid/icon.svg', sha256: SOURCE_HASH },
        ]) {
          await assert.rejects(prepareDiagramBrandMarks(kind, { [collection]: [{ id: 'remote', brand }] }), (error) => {
            diagnostic('brand/offline', /disabled.*offline/)(error);
            assert.equal(error.archifyDiagnostics[0].subject.path, `/${collection}/0/brand`);
            return true;
          });
        }
      }
      assert.throws(() => findBrandMark('https://github.com'), diagnostic('brand/offline'));
      assert.throws(() => findBrandMark({ url: 'https://github.com' }), diagnostic('brand/offline'));
      await assert.rejects(captureBrandReference('https://example.invalid'), diagnostic('brand/offline'));
      await assert.rejects(captureBrandReference('https://github.com'), diagnostic('brand/offline'));
      const builtin = await prepareOne('postgresql');
      assert.equal(brandMarkFor(builtin).kind, 'preset');
      assert.equal(findBrandMark('PostgreSQL').id, 'postgresql');
      assert.ok(listBrandMarks('postgresql').length);
      const { id, entry } = fixture();
      await withRegistry({ [id]: entry }, async () => {
        assert.match(renderBrandMark(await prepareOne(id), { x: 0, y: 0 }), /<image\b/);
      });
    });
    for (const mocked of mocks) assert.equal(mocked.mock.callCount(), 0);
  } finally {
    t.mock.restoreAll();
    syncBuiltinESMExports();
  }
});
