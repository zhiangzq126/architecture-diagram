import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import net from 'node:net';
import { isAbsolute } from 'node:path';
import { BRAND_MARKS } from './generated-brand-marks.mjs';
import { throwDiagnosticError } from './diagnostics.mjs';
import { esc, textUnits } from './utils.mjs';

const COLLECTIONS = Object.freeze({
  architecture: 'components',
  workflow: 'nodes',
  sequence: 'participants',
  dataflow: 'nodes',
  lifecycle: 'states',
});
const MARK_BY_LOOKUP = new Map();
const RESOLVED_BY_NODE = new WeakMap();
const RESOLVED_MARK = Symbol('archify.brandMark');
const ICON_PREFIX = 'ad-icon:';
const SVG_DATA_PREFIX = 'data:image/svg+xml;base64,';
const MAX_IMAGE_BYTES = 1024 * 1024;
const OFFLINE_MESSAGE = 'remote brand objects and URLs are disabled in the offline architecture-diagram engine; use a built-in brand ID or a registered ad-icon:<sha256> ID';

function lookupForms(value) {
  const raw = String(value ?? '').trim().toLocaleLowerCase('en-US');
  if (!raw) return [];
  const dashed = raw.replace(/[\s_]+/g, '-');
  const compact = raw.replace(/[\s_.-]+/g, '');
  return [...new Set([raw, dashed, compact])];
}

for (const mark of BRAND_MARKS) {
  for (const value of [mark.id, mark.title, ...mark.aliases]) {
    for (const form of lookupForms(value)) {
      if (!MARK_BY_LOOKUP.has(form)) MARK_BY_LOOKUP.set(form, mark);
    }
  }
}

function isUrlReference(value) {
  return typeof value === 'string'
    && /^(?:[a-z][a-z\d+.-]*:|\/\/)/i.test(value.trim());
}

function offlineError() {
  throwDiagnosticError(OFFLINE_MESSAGE, [{
    code: 'brand/offline',
    severity: 'error',
    message: OFFLINE_MESSAGE,
    supportedFixes: ['use a built-in brand ID or the local icon wrapper'],
  }]);
}

export function findBrandMark(value) {
  // Private IDs are exact registry keys, never normalized built-in aliases.
  if (typeof value === 'string' && value.startsWith(ICON_PREFIX)) return null;
  if ((value && typeof value === 'object') || isUrlReference(value)) offlineError();
  for (const form of lookupForms(value)) {
    const mark = MARK_BY_LOOKUP.get(form);
    if (mark) return mark;
  }
  return null;
}

export function listBrandMarks(query = '') {
  const needle = String(query).trim().toLocaleLowerCase('en-US');
  return BRAND_MARKS.filter((mark) => {
    if (!needle) return true;
    return [mark.id, mark.title, mark.category, ...mark.aliases, ...mark.domains]
      .some((value) => String(value).toLocaleLowerCase('en-US').includes(needle));
  }).map(({ path, ...mark }) => mark);
}

function ipv4Private(address) {
  const parts = address.split('.').map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return true;
  const [a, b, c] = parts;
  return a === 0 || a === 10 || a === 127 || a >= 224
    || (a === 100 && b >= 64 && b <= 127)
    || (a === 169 && b === 254)
    || (a === 172 && b >= 16 && b <= 31)
    || (a === 192 && b === 0 && (c === 0 || c === 2))
    || (a === 192 && b === 88 && c === 99)
    || (a === 192 && b === 168)
    || (a === 198 && (b === 18 || b === 19))
    || (a === 198 && b === 51 && c === 100)
    || (a === 203 && b === 0 && c === 113);
}

function ipv6Private(address) {
  const normalized = address.toLocaleLowerCase('en-US').split('%')[0];
  if (normalized === '::' || normalized === '::1') return true;
  if (normalized.startsWith('fc') || normalized.startsWith('fd') || normalized.startsWith('ff') || /^fe[89ab]/.test(normalized)) return true;
  if (normalized.startsWith('64:ff9b:') || normalized.startsWith('100:')
    || normalized.startsWith('2001:db8:') || normalized.startsWith('2002:')) return true;
  const mappedDotted = normalized.match(/::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (mappedDotted) return ipv4Private(mappedDotted[1]);
  const mappedHex = normalized.match(/::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
  if (mappedHex) {
    const high = Number.parseInt(mappedHex[1], 16);
    const low = Number.parseInt(mappedHex[2], 16);
    return ipv4Private(`${high >>> 8}.${high & 255}.${low >>> 8}.${low & 255}`);
  }
  const compatibleHex = normalized.match(/^::([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
  if (compatibleHex) {
    const high = Number.parseInt(compatibleHex[1], 16);
    const low = Number.parseInt(compatibleHex[2], 16);
    return ipv4Private(`${high >>> 8}.${high & 255}.${low >>> 8}.${low & 255}`);
  }
  return false;
}

export function isPrivateBrandAddress(address) {
  const family = net.isIP(address);
  return family === 4 ? ipv4Private(address) : (family === 6 ? ipv6Private(address) : true);
}

// Keep the public entry point, but never perform DNS, HTTP, or remote capture.
export async function captureBrandReference() {
  offlineError();
}

function isSha256(value) {
  return typeof value === 'string' && value.length === 64 && /^[a-f0-9]+$/.test(value);
}

function isIconId(value) {
  return typeof value === 'string' && value.startsWith(ICON_PREFIX) && isSha256(value.slice(ICON_PREFIX.length));
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function iconRegistryError(message, code = 'brand/icon-registry', evidence = {}) {
  throwDiagnosticError(`Local icon registry validation failed: ${message}`, [{
    code,
    severity: 'error',
    message,
    subject: { registry: process.env.ARCHITECTURE_DIAGRAM_ICONS },
    evidence,
    supportedFixes: ['regenerate the local icon registry with the architecture-diagram wrapper'],
  }]);
}

function loadIconRegistry() {
  const registryPath = process.env.ARCHITECTURE_DIAGRAM_ICONS;
  if (!registryPath || !isAbsolute(registryPath)) {
    iconRegistryError('ARCHITECTURE_DIAGRAM_ICONS must name an absolute local JSON registry path');
  }
  let registry;
  try {
    registry = JSON.parse(readFileSync(registryPath, 'utf8'));
  } catch (error) {
    iconRegistryError(`cannot read or parse local icon registry: ${error.message}`);
  }
  if (!isRecord(registry)) iconRegistryError('registry must be a JSON object keyed by exact ad-icon:<sha256> IDs');
  const marks = new Map();
  for (const [id, entry] of Object.entries(registry)) {
    if (!isIconId(id)) iconRegistryError(`invalid local icon registry key ${JSON.stringify(id)}`);
    if (!isRecord(entry)
      || !isSha256(entry.svg_sha256) || !isSha256(entry.source_sha256)
      || typeof entry.product_id !== 'string' || !entry.product_id.trim()
      || typeof entry.variant_id !== 'string' || !entry.variant_id.trim()
      || !isRecord(entry.theme) || entry.theme.preserve_colors !== true
      || typeof entry.theme.dark_backdrop !== 'boolean') {
      iconRegistryError(`${id} requires SVG/source SHA-256 hashes, product_id, variant_id, and theme {preserve_colors: true, dark_backdrop: boolean}`);
    }
    if (typeof entry.svg_data_uri !== 'string' || !entry.svg_data_uri.startsWith(SVG_DATA_PREFIX)) {
      iconRegistryError(`${id} must contain a data:image/svg+xml;base64, URI`);
    }
    const encoded = entry.svg_data_uri.slice(SVG_DATA_PREFIX.length);
    if (encoded.length > 4 * Math.ceil(MAX_IMAGE_BYTES / 3)) {
      iconRegistryError(`${id} SVG exceeds the 1 MiB limit`, 'brand/icon-too-large');
    }
    if (!encoded.length || encoded.length % 4 !== 0 || /[^A-Za-z0-9+/=]/.test(encoded)) {
      iconRegistryError(`${id} SVG data URI must use canonical base64`);
    }
    const bytes = Buffer.from(encoded, 'base64');
    if (bytes.toString('base64') !== encoded) iconRegistryError(`${id} SVG data URI must use canonical base64`);
    if (bytes.length > MAX_IMAGE_BYTES) iconRegistryError(`${id} SVG exceeds the 1 MiB limit`, 'brand/icon-too-large');
    const sha256 = createHash('sha256').update(bytes).digest('hex');
    if (sha256 !== entry.svg_sha256) {
      iconRegistryError(`${id} SVG digest does not match svg_sha256`, 'brand/digest-mismatch', {
        brandId: id, expected: entry.svg_sha256, actual: sha256,
      });
    }
    const identityHash = createHash('sha256').update(JSON.stringify([
      entry.svg_sha256, entry.source_sha256, entry.product_id, entry.variant_id, entry.theme.dark_backdrop,
    ]), 'utf8').digest('hex');
    if (id !== `${ICON_PREFIX}${identityHash}`) {
      iconRegistryError(`${id} brand ID does not match the icon identity digest`, 'brand/digest-mismatch', {
        brandId: id, expected: `${ICON_PREFIX}${identityHash}`, actual: id,
      });
    }
    let svg;
    try {
      svg = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      iconRegistryError(`${id} SVG must be UTF-8 text`);
    }
    if (!/^\s*(?:<\?xml\b[^?]*\?>\s*)?<svg[\s/>]/.test(svg)) {
      iconRegistryError(`${id} data URI must contain an SVG document`);
    }
    // This is a private handoff from this package's Python allowlist sanitizer,
    // not an SVG sanitization API. Verify its bytes/identity, then isolate the
    // unchanged document in an <image>; never interpolate its XML into HTML.
    marks.set(id, Object.freeze({
      id,
      title: entry.product_id,
      category: 'local-icon',
      kind: 'local',
      status: 'local',
      dataUrl: entry.svg_data_uri,
      sha256,
      sourceSha256: entry.source_sha256,
      productId: entry.product_id,
      variantId: entry.variant_id,
      theme: Object.freeze({ ...entry.theme }),
    }));
  }
  return marks;
}

function suggestions(value) {
  const needle = lookupForms(value)[0] || '';
  return BRAND_MARKS.map((mark) => ({
    id: mark.id,
    score: lookupForms(mark.id).some((form) => form.includes(needle) || needle.includes(form)) ? 0 : 1,
  })).sort((left, right) => left.score - right.score || left.id.localeCompare(right.id))
    .slice(0, 5)
    .map((entry) => entry.id);
}

export async function prepareDiagramBrandMarks(diagramType, diagram) {
  const collection = COLLECTIONS[diagramType];
  const nodes = collection && Array.isArray(diagram[collection]) ? diagram[collection] : [];
  const diagnostics = [];
  const prepared = [];
  let registry;
  // Do not retain a stale mark if a node or registry changed between renders.
  for (const node of nodes) {
    delete node[RESOLVED_MARK];
    RESOLVED_BY_NODE.delete(node);
  }
  for (const [index, node] of nodes.entries()) {
    if (!node.brand) continue;
    const problem = (code, detail) => diagnostics.push({
      code,
      severity: 'error',
      message: `/${collection}/${index}/brand ${detail}`,
      subject: { diagramType, collection, nodeId: node.id, path: `/${collection}/${index}/brand` },
      evidence: {},
      supportedFixes: ['choose an ID from `archify brands` or regenerate the local icon registry with the wrapper'],
    });
    if (typeof node.brand === 'string' && node.brand.startsWith(ICON_PREFIX)) {
      if (!isIconId(node.brand)) {
        problem('brand/icon-id', 'must exactly match ad-icon:<64 lowercase hex SHA-256>');
        continue;
      }
      registry ??= loadIconRegistry();
      const resolved = registry.get(node.brand);
      if (!resolved) problem('brand/unknown', `${JSON.stringify(node.brand)} is not in the local icon registry`);
      else prepared.push([node, resolved]);
      continue;
    }
    if (typeof node.brand === 'object' || isUrlReference(node.brand)) {
      problem('brand/offline', OFFLINE_MESSAGE);
      continue;
    }
    const preset = findBrandMark(node.brand);
    if (preset) {
      prepared.push([node, { ...preset, kind: 'preset', status: 'preset', sourceUrl: preset.provenance.source }]);
      continue;
    }
    problem('brand/unknown', `${JSON.stringify(node.brand)} is not a built-in brand; closest IDs: ${suggestions(node.brand).join(', ')}`);
  }
  if (diagnostics.length) {
    throwDiagnosticError(`Brand mark validation failed:\n- ${diagnostics.map(({ message }) => message).join('\n- ')}`, diagnostics);
  }
  for (const [node, resolved] of prepared) {
    node[RESOLVED_MARK] = resolved;
    RESOLVED_BY_NODE.set(node, resolved);
  }
}

export function brandMarkFor(node) {
  return node?.[RESOLVED_MARK] || RESOLVED_BY_NODE.get(node) || null;
}

export function brandMetadataFor(node) {
  const mark = brandMarkFor(node);
  return mark ? {
    brand: mark.title,
    brandId: mark.id,
    brandStatus: mark.status,
    brandSource: mark.sourceSha256 ? `sha256:${mark.sourceSha256}` : mark.sourceUrl,
    ...(mark.kind === 'local' ? {
      brandSha256: mark.sha256,
      brandSourceSha256: mark.sourceSha256,
      brandProductId: mark.productId,
      brandVariantId: mark.variantId,
    } : {}),
  } : {};
}

export function brandLabelFitWidth(node, width) {
  return brandMarkFor(node) ? Math.max(1, width - 48) : width;
}

export function brandTopRailProblem(node, width, minimumFontSize, subject = 'Node') {
  if (!brandMarkFor(node)) return null;
  const available = width - 48;
  const required = textUnits(node.label) * minimumFontSize * 0.6;
  if (available >= required) return null;
  return `${subject} "${node.id}" brand top rail leaves ${Math.max(0, available)}px for its label, but `
    + `"${node.label}" needs ~${Math.ceil(required)}px at the ${minimumFontSize}px legible minimum — widen the node or shorten the label.`;
}

function markAttrs(mark) {
  return [
    `data-brand-mark="${esc(mark.id)}"`,
    `data-brand-title="${esc(mark.title)}"`,
    `data-brand-status="${esc(mark.status)}"`,
    mark.sourceUrl ? `data-brand-source="${esc(mark.sourceUrl)}"` : '',
    mark.sha256 ? `data-brand-sha256="${esc(mark.sha256)}"` : '',
    mark.sourceSha256 ? `data-brand-source-sha256="${esc(mark.sourceSha256)}"` : '',
    mark.productId ? `data-brand-product-id="${esc(mark.productId)}"` : '',
    mark.variantId ? `data-brand-variant-id="${esc(mark.variantId)}"` : '',
  ].filter(Boolean).join(' ');
}

export function renderBrandMark(node, { x, y, size = 16 } = {}) {
  const mark = brandMarkFor(node);
  if (!mark) return '';
  const inset = 3;
  let content;
  if (mark.kind === 'preset') {
    const scale = (size - inset * 2) / mark.viewBox;
    content = `<path d="${esc(mark.path)}" transform="translate(${inset} ${inset}) scale(${scale})" fill="#${esc(mark.hex)}"/>`;
  } else if (mark.kind === 'local') {
    content = `<image href="${esc(mark.dataUrl)}" x="${inset}" y="${inset}" width="${size - inset * 2}" height="${size - inset * 2}" preserveAspectRatio="xMidYMid meet"/>`;
  } else {
    const scale = size / 20;
    content = `<g transform="scale(${scale})" class="brand-mark-fallback"><circle cx="10" cy="10" r="5.2"/><path d="M4.8 10h10.4M10 4.8c1.6 1.6 2.4 3.3 2.4 5.2s-.8 3.6-2.4 5.2M10 4.8C8.4 6.4 7.6 8.1 7.6 10s.8 3.6 2.4 5.2"/></g>`;
  }
  // Inline fill also survives standalone SVG export without the HTML stylesheet.
  const backdrop = mark.kind === 'local' ? ` style="fill:${mark.theme.dark_backdrop ? '#fff' : 'none'}"` : '';
  return `<g aria-hidden="true" ${markAttrs(mark)} class="brand-mark" transform="translate(${x} ${y})">
            <rect width="${size}" height="${size}" rx="4" class="brand-mark-badge"${backdrop}/>
            ${content}
            <rect width="${size}" height="${size}" rx="4" class="brand-mark-frame"/>
          </g>`;
}
