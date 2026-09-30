#!/usr/bin/env node
// No dependencies. Uses the shipped Chrome/CDP transport; never calls private export APIs.
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { ChromeVisualBrowser, findChrome } from '../modules/archify/bin/visual-check.mjs';
import { validateSchema } from '../modules/archify/renderers/shared/validator.mjs';
import { validateGuidedViews, validateRelationshipIds } from '../modules/archify/renderers/shared/cli.mjs';
import { compileWorkflow } from '../modules/archify/renderers/workflow/workflow-compiler.mjs';
import { resolveLegend } from '../modules/archify/renderers/shared/legend.mjs';
import { translateMessage } from '../modules/archify/renderers/shared/i18n.mjs';

const ROOT = fs.realpathSync(fileURLToPath(new URL('..', import.meta.url)));
const USAGE = 'node tests/browser_acceptance.mjs --artifact /abs/delivered.html --outdir /abs/newdir [--model /abs/native.json (default: sibling model.json; standalone HTML requires --model)] [--expect-icons N] [--require-chinese true|false] [--profile desktop|responsive] [--shard I/N]';
const OFFLINE_CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline' data:; img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'self' data: blob:; worker-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; sandbox allow-scripts allow-same-origin allow-downloads";
const VIEWPORTS = [[1440, 900], [1600, 1000], [1920, 1080]];
const RESPONSIVE_VIEWPORTS = [[320, 568], [360, 640], [390, 844], [414, 896], [768, 1024], [820, 1180], [1023, 768], [1024, 768], [1024, 900], [1280, 720], [1280, 800], [1366, 768], [1440, 900], [1600, 1000], [1920, 1080], [2560, 1440], [3840, 2160]];
const sha256 = (bytes) => createHash('sha256').update(bytes).digest('hex');
const errorText = (error) => error?.message || String(error);

function argumentsFor(argv) {
  if (argv.length === 1 && argv[0] === '--help') return null;
  const result = {};
  for (let i = 0; i < argv.length; i += 2) {
    const key = argv[i];
    if (!['--artifact', '--model', '--outdir', '--expect-icons', '--require-chinese', '--profile', '--shard'].includes(key) || result[key] !== undefined || !argv[i + 1] || argv[i + 1].startsWith('--')) {
      throw new Error(`Invalid or duplicate argument: ${key}. ${USAGE}`);
    }
    result[key] = argv[i + 1];
  }
  for (const key of ['--artifact', '--outdir']) {
    if (!result[key] || !path.isAbsolute(result[key])) throw new Error(`${key} must be an absolute local path. ${USAGE}`);
  }
  if (!/\.html?$/i.test(result['--artifact']) || !fs.statSync(result['--artifact']).isFile()) throw new Error('Artifact must be a local HTML file.');
  if (result['--model'] !== undefined && !path.isAbsolute(result['--model'])) throw new Error('--model must be an absolute local path.');
  const model = result['--model'] || path.join(path.dirname(fs.realpathSync(result['--artifact'])), 'model.json');
  const expected = result['--expect-icons'];
  if (expected !== undefined && (!/^\d+$/.test(expected) || !Number.isSafeInteger(Number(expected)))) throw new Error('--expect-icons must be a nonnegative safe integer.');
  const requireChinese = result['--require-chinese'] ?? 'false';
  if (!['true', 'false'].includes(requireChinese)) throw new Error('--require-chinese must be true or false.');
  const profile = result['--profile'] ?? 'desktop';
  if (!['desktop', 'responsive'].includes(profile)) throw new Error('--profile must be desktop or responsive.');
  const shardText = result['--shard'] ?? '1/1';
  if (!/^[1-9]\d*\/[1-9]\d*$/.test(shardText) || (result['--shard'] && profile !== 'responsive')) throw new Error('--shard requires responsive profile and I/N format.');
  const [shardIndex, shardCount] = shardText.split('/').map(Number);
  if (shardIndex > shardCount || shardCount > RESPONSIVE_VIEWPORTS.length + 1) throw new Error('Shard must satisfy 1 <= I <= N <= 18.');
  const requested = path.resolve(result['--outdir']);
  const parent = fs.realpathSync(path.dirname(requested));
  if (!fs.statSync(parent).isDirectory()) throw new Error('The output parent directory must already exist.');
  const outdir = path.join(parent, path.basename(requested));
  if (outdir === ROOT || outdir.startsWith(ROOT + path.sep)) throw new Error('Acceptance output must be outside the skill.');
  // Exclusive mkdir rejects existing paths, including dangling symlinks.
  fs.mkdirSync(outdir);
  return { artifact: fs.realpathSync(result['--artifact']), model, outdir, profile, shardIndex, shardCount, expectIcons: expected === undefined ? null : Number(expected), requireChinese: requireChinese === 'true' };
}

// JSON data only: never import/evaluate model content, run a renderer CLI, or infer
// expectations from HTML. The pure workflow receipt supplies resolved columns.
function expectedStructure(model) {
  const collections = {
    architecture: ['components', 'connections'], workflow: ['nodes', 'edges'],
    sequence: ['participants', 'messages'], dataflow: ['nodes', 'flows'], lifecycle: ['states', 'transitions'],
  };
  const type = model?.diagram_type;
  if (!Object.hasOwn(collections, type)) throw new Error('Native model must declare one of the five supported diagram_type values.');
  // Workflow owns its schema validation/normalization. Do not invent columns or
  // pre-empt a compiler-supported automatic layout with a DOM/schema fallback.
  const compiled = type === 'workflow' ? compileWorkflow({ workflow: model }) : null;
  if (compiled && !compiled.ok) throw new Error(`Invalid native workflow: ${compiled.error || JSON.stringify(compiled.diagnostics)}`);
  if (!compiled) validateSchema(type, model);
  validateGuidedViews(type, model);
  validateRelationshipIds(type, model);
  const [nodeCollection, edgeCollection] = collections[type];
  const sourceNodes = model[nodeCollection];
  const unique = (items, name) => {
    const ids = items.map((item) => item.id);
    if (new Set(ids).size !== ids.length) throw new Error(`Native model has duplicate ${name} IDs.`);
    return new Set(ids);
  };
  const ids = unique(sourceNodes, nodeCollection);
  if (!ids.size) throw new Error('Native model must contain business nodes.');
  for (const edge of model[edgeCollection] || []) {
    if (!ids.has(edge.from) || !ids.has(edge.to)) throw new Error(`Native model has unknown ${edgeCollection} endpoint.`);
  }
  const nodes = sourceNodes.map(({ id, label }) => ({ id, label }));
  const expected = { type, nodes, groups: [], sequence: null, story: (model.meta.views || []).length > 0 };
  if (type === 'workflow') {
    unique(model.groups || [], 'group');
    unique(model.lanes, 'lane');
    const positions = new Map(compiled.receipt.nodes.map((node) => [node.id, node]));
    const groups = [...(model.groups || [])];
    if (model.schema_version === 2) {
      // readable-v2 canonicalizes group rendering order before assigning frame
      // IDs. Reproduce that source-only order; never inspect compiled SVG/DOM.
      const lanes = new Map(model.lanes.map((lane, index) => [lane.id, index]));
      groups.sort((a, b) => lanes.get(a.lane) - lanes.get(b.lane) || a.fromCol - b.fromCol
        || a.toCol - b.toCol || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
    }
    expected.groups = groups.map((group, index) => ({
      id: `group-${index}`, labelId: group.id ?? `group-${index}`, label: group.label,
      members: sourceNodes.filter((node) => {
        const placed = positions.get(node.id);
        const col = node.col ?? placed?.col, lane = node.lane ?? placed?.lane;
        if (!Number.isFinite(col) || !lane) throw new Error(`No compiled lane/column for ${node.id}.`);
        return lane === group.lane && col >= group.fromCol && col <= group.toCol;
      }).map(({ id }) => id),
    }));
  }
  if (type === 'sequence') {
    for (const activation of model.activations || []) {
      if (!ids.has(activation.participant) || activation.to <= activation.from) throw new Error('Invalid native sequence activation.');
    }
    const catalog = ['emphasis', 'return', 'security', 'dashed', 'default'].map((kind) => ({
      kind, label: translateMessage(model.meta.locale, `legend.sequence.${kind}`), interactive: false,
    }));
    expected.sequence = {
      participants: nodes,
      legend: {
        mode: model.meta.legend?.mode || 'auto', title: translateMessage(model.meta.locale, 'legend.title'),
        entries: resolveLegend(model.meta.legend, catalog, model.messages.map((message) => message.variant || 'default'))
          .map(({ kind, label }) => ({ kind, label })),
      },
    };
  }
  return expected;
}

// A passive event tap avoids losing multiple events delivered in one CDP pipe chunk.
function observePipe(browser, receive) {
  let buffer = '';
  const listener = (chunk) => {
    buffer += chunk.toString();
    let boundary;
    while ((boundary = buffer.indexOf('\0')) !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 1);
      if (!raw) continue;
      const message = JSON.parse(raw);
      if (message.method) receive(message);
    }
  };
  browser.child.stdio[4].on('data', listener);
  return () => browser.child.stdio[4].off('data', listener);
}

// Install before artifact scripts to observe downloads and errors without changing feature state.
function installObserver() {
  const notify = (value) => window.__acceptanceEvent(JSON.stringify(value));
  document.addEventListener('securitypolicyviolation', (event) => notify({
    kind: 'policy', url: event.blockedURI, directive: event.effectiveDirective,
  }));
  window.__acceptanceDownloads = [];
  const originalClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    if (!this.download || !this.href.startsWith('blob:')) return originalClick.call(this);
    const entry = { filename: this.download, ready: false };
    window.__acceptanceDownloads.push(entry);
    // Start reading immediately, before the template revokes its object URL.
    fetch(this.href).then((response) => response.blob()).then((blob) => {
      entry.type = blob.type;
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
      });
    }).then((data) => { entry.data = data; entry.ready = true; })
      .catch((error) => { entry.error = String(error); entry.ready = true; });
  };
}

// DOM selectors and state attributes are from modules/archify/assets/template.html.
function installPageTools(expected) {
  if (!expected?.nodes?.length || !Array.isArray(expected.groups)) throw new Error('Validated native structure is required.');
  const svg = () => document.querySelector('.diagram-container > svg, .diagram-container > .diagram-stage > svg');
  const rect = (element) => {
    const r = element.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  };
  const inside = (a, b) => a.left >= b.left - 1 && a.top >= b.top - 1 && a.right <= b.right + 1 && a.bottom <= b.bottom + 1;
  const shown = (element) => {
    if (!element || !element.getClientRects().length) return false;
    let opacity = 1;
    for (let p = element; p; p = p.parentElement) {
      const style = getComputedStyle(p);
      opacity *= Number(style.opacity);
      if (style.display === 'none' || /^(hidden|collapse)$/.test(style.visibility) || opacity <= 0.01 || p.hasAttribute('hidden')) return false;
    }
    return true;
  };
  const visible = (element) => {
    if (!shown(element)) return false;
    const r = rect(element);
    return r.width > 0 && r.height > 0;
  };
  const paint = (element, property) => {
    if (!shown(element)) return false;
    const style = getComputedStyle(element), color = style[property];
    return color !== 'none' && color !== 'transparent' && !/rgba\([^)]*,\s*0(?:\.0+)?\)$/.test(color)
      && Number(style[`${property}Opacity`]) > 0.01 && (property !== 'stroke' || parseFloat(style.strokeWidth) > 0);
  };
  const textVisible = (element) => visible(element) && (paint(element, 'fill') || paint(element, 'stroke'));
  // Zero-width vertical SVG paths are valid ink, unlike two-dimensional boxes.
  const strokeVisible = (element) => paint(element, 'stroke') && typeof element.getTotalLength === 'function'
    && element.getTotalLength() > 0 && (rect(element).height > 0 || rect(element).width > 0);
  const inViewBox = (element) => {
    if (!element) return false;
    const root = svg(), m = root.getScreenCTM()?.inverse(), vb = root.viewBox.baseVal;
    if (!m || vb.width <= 0 || vb.height <= 0) return false;
    const r = rect(element);
    return [[r.left, r.top], [r.right, r.top], [r.left, r.bottom], [r.right, r.bottom]].every(([x, y]) => {
      const p = new DOMPoint(x, y).matrixTransform(m);
      return p.x >= vb.x - 0.05 && p.y >= vb.y - 0.05 && p.x <= vb.x + vb.width + 0.05 && p.y <= vb.y + vb.height + 0.05;
    });
  };
  const decodeImage = async (src) => {
    const image = new Image();
    image.src = src;
    await image.decode();
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = 64;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    context.drawImage(image, 0, 0, 64, 64);
    const pixels = context.getImageData(0, 0, 64, 64).data;
    let painted = 0;
    const colors = new Set();
    for (let i = 0; i < pixels.length; i += 4) {
      if (pixels[i + 3]) painted++;
      colors.add(`${pixels[i]},${pixels[i + 1]},${pixels[i + 2]},${pixels[i + 3]}`);
    }
    return { width: image.naturalWidth, height: image.naturalHeight, painted, colors: colors.size };
  };
  const inspectIcons = async (root, checkVisibility) => Promise.all([...root.querySelectorAll('image')].map(async (image) => {
    const href = image.getAttribute('href') || image.getAttributeNS('http://www.w3.org/1999/xlink', 'href') || '';
    const result = { node: image.closest('[data-node-id]')?.getAttribute('data-node-id') || null, embedded: /^data:image\/svg\+xml[;,]/i.test(href) };
    try {
      if (!result.embedded) throw new Error('Image is not an embedded data SVG');
      const comma = href.indexOf(',');
      const bytes = /;base64/i.test(href.slice(0, comma))
        ? Uint8Array.from(atob(href.slice(comma + 1)), (c) => c.charCodeAt(0))
        : new TextEncoder().encode(decodeURIComponent(href.slice(comma + 1)));
      const xml = new DOMParser().parseFromString(new TextDecoder().decode(bytes), 'image/svg+xml');
      if (xml.querySelector('parsererror') || xml.documentElement.localName !== 'svg') throw new Error('Invalid SVG XML');
      result.sha256 = [...new Uint8Array(await crypto.subtle.digest('SHA-256', bytes))].map((v) => v.toString(16).padStart(2, '0')).join('');
      result.decoded = await decodeImage(href);
      if (checkVisibility) result.visible = visible(image) && inside(rect(image), rect(svg()));
      result.ok = result.decoded.width > 0 && result.decoded.height > 0 && result.decoded.painted > 0 && (!checkVisibility || result.visible);
    } catch (error) { result.ok = false; result.error = String(error); }
    return result;
  }));
  const viewportRect = () => ({ left: 0, top: 0, right: innerWidth, bottom: innerHeight });
  const intersection = (a, b) => ({ left: Math.max(a.left, b.left), top: Math.max(a.top, b.top), right: Math.min(a.right, b.right), bottom: Math.min(a.bottom, b.bottom) });
  const area = (r) => Math.max(0, r.right - r.left) * Math.max(0, r.bottom - r.top);
  const unclipped = (element, bounds = rect(element)) => {
    if (!inside(bounds, viewportRect())) return false;
    for (let p = element.parentElement; p; p = p.parentElement) {
      const style = getComputedStyle(p), r = rect(p);
      if (/(hidden|clip|auto|scroll)/.test(style.overflowX) && (bounds.left < r.left - 1 || bounds.right > r.right + 1)) return false;
      if (/(hidden|clip|auto|scroll)/.test(style.overflowY) && (bounds.top < r.top - 1 || bounds.bottom > r.bottom + 1)) return false;
    }
    return true;
  };
  const surfaces = () => {
    const elements = [document.querySelector('.header'), document.querySelector('.header h1')];
    elements.push(...document.querySelectorAll('.header .subtitle'));
    if (expected.story) {
      elements.push(document.querySelector('.guided-views'));
      elements.push(...document.querySelectorAll('.guided-view-copy, .guided-view-actions, .guided-view-index'));
    }
    const cards = [...document.querySelectorAll('.cards .card')];
    if (cards.length) elements.push(document.querySelector('.cards'), ...cards);
    return elements.map((element) => {
      if (!element) return { ok: false, error: 'Required reader surface missing' };
      const shown = visible(element);
      // Chapter lists intentionally scroll horizontally; their containing panel must not clip.
      const contentFits = element.scrollWidth <= element.clientWidth + 1 && element.scrollHeight <= element.clientHeight + 1;
      const text = [...element.querySelectorAll('h1, .subtitle, h3, li')].filter((el) => !el.closest('.guided-views')).map((el) => {
        const range = document.createRange(); range.selectNodeContents(el);
        return { text: el.textContent.trim(), ok: visible(el) && unclipped(el, rectFromRange(range)) };
      });
      return { element: element.id || element.className || element.tagName, rect: rect(element), visible: shown,
        contentFits, text, ok: shown && unclipped(element) && contentFits && text.every((item) => item.ok) };
    });
  };
  const rectFromRange = (range) => {
    const r = range.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  };
  const chrome = () => {
    const root = svg(), nav = document.querySelector('.diagram-nav'), legend = root.querySelector('[data-legend]');
    const receipt = window.Archify?.viewerChromeLayout?.receipt?.() || null;
    // Undo the camera's root transform, not its authored viewBox scale. A wrapper, when present, owns clipping.
    const transform = getComputedStyle(root).transform;
    const r = rect(root), matrix = new DOMMatrixReadOnly(transform === 'none' ? undefined : transform);
    const stage = root.parentElement.matches('.diagram-stage') ? rect(root.parentElement) : {
      left: r.left - matrix.e, top: r.top - matrix.f,
      right: r.left - matrix.e + r.width / Math.hypot(matrix.a, matrix.b),
      bottom: r.top - matrix.f + r.height / Math.hypot(matrix.c, matrix.d),
    };
    const dock = nav && rect(nav), legendBounds = visible(legend) ? rect(legend) : null;
    const stageGap = dock ? dock.top - stage.bottom : null;
    const stageIntersectionArea = dock ? area(intersection(dock, stage)) : null;
    const legendIntersectionArea = dock && legendBounds ? area(intersection(dock, intersection(legendBounds, stage))) : 0;
    const receiptOK = !receipt || (receipt.eligible === true && Number.isFinite(receipt.gap) && receipt.gap >= 10 &&
      Number.isFinite(receipt.stageGap) && receipt.stageGap >= receipt.gap - 1 && receipt.stageIntersectionArea === 0 && receipt.intersectionArea === 0);
    return { receipt, stage, dock, legend: legendBounds, stageGap, stageIntersectionArea, legendIntersectionArea,
      ok: visible(nav) && inside(stage, viewportRect()) && inside(stage, rect(document.querySelector('.diagram-container'))) &&
        unclipped(nav) && stageGap >= 9 && stageIntersectionArea === 0 && legendIntersectionArea === 0 && receiptOK };
  };
  const readerSpacing = () => {
    const active = document.documentElement.dataset.readerSidebar === 'true';
    if (!active) return { active, ok: true };
    const actual = rect(document.querySelector('.header')).left - rect(document.querySelector('.diagram-container')).right;
    const expected = parseFloat(getComputedStyle(document.querySelector('.container')).columnGap);
    return { active, actual, expected, ok: Number.isFinite(actual) && Math.abs(actual - expected) <= 1 };
  };
  const nodeInk = () => [...svg().querySelectorAll('[data-node-id]')].map((node) => {
    const matrix = node.getScreenCTM();
    const scale = Math.hypot(matrix.a, matrix.b);
    const texts = [...node.querySelectorAll('text')].filter(visible);
    const marks = [...node.querySelectorAll('[data-semantic-sigil], .brand-mark')].filter(visible);
    const clearance = (a, b) => Math.hypot(Math.max(a.left - b.right, b.left - a.right, 0), Math.max(a.top - b.bottom, b.top - a.bottom, 0));
    const pairs = marks.flatMap((mark) => {
      const bounds = [...mark.querySelectorAll('path, rect, circle, ellipse, polygon, image')].filter(visible).map((shape) => {
        const style = getComputedStyle(shape), m = shape.getScreenCTM();
        const stroke = style.stroke === 'none' ? 0 : parseFloat(style.strokeWidth) / 2 * (style.vectorEffect === 'non-scaling-stroke' ? 1 : Math.max(Math.hypot(m.a, m.b), Math.hypot(m.c, m.d)));
        const r = rect(shape);
        return { left: r.left - stroke, top: r.top - stroke, right: r.right + stroke, bottom: r.bottom + stroke };
      });
      return texts.map((text) => {
        const gap = Math.min(...bounds.map((bound) => clearance(bound, rect(text))));
        return { mark: mark.dataset.semanticSigil || 'brand', text: text.textContent, gap, minimum: 2 * scale, ok: bounds.length > 0 && gap >= 2 * scale - 0.05 };
      });
    });
    const overlaps = texts.flatMap((text, index) => texts.slice(index + 1).map((other) => ({
      texts: [text.textContent, other.textContent], area: area(intersection(rect(text), rect(other))),
    }))).filter((pair) => pair.area > 0.05);
    return { id: node.dataset.nodeId, pairs, overlaps, ok: pairs.every((pair) => pair.ok) && overlaps.length === 0 };
  });
  const groupLabelInk = () => [...svg().querySelectorAll('[data-composition-frame-kind="group"]')].map((frame) => {
    const label = frame.nextElementSibling;
    if (label?.tagName.toLowerCase() !== 'text' || !visible(label)) {
      return { id: frame.dataset.compositionFrameId, ok: false, error: 'Group label missing or hidden' };
    }
    const obstacles = [...svg().querySelectorAll('[data-node-id] > rect.c-mask, text')]
      .filter((element) => element !== label && (!element.closest('[data-node-id]') || element.matches('[data-node-id] > rect.c-mask')))
      .filter(visible);
    const overlaps = obstacles.map((element) => ({
      subject: element.closest('[data-node-id]')?.dataset.nodeId || element.textContent,
      area: area(intersection(rect(label), rect(element))),
    })).filter((entry) => entry.area > 0.05);
    return { id: frame.dataset.compositionFrameId, label: label.textContent, overlaps, ok: overlaps.length === 0 };
  });
  const structuralClearance = () => {
    const root = svg();
    if (!root) return { ok: false, error: 'Canonical diagram SVG missing' };
    const matching = (scope, selector, attribute, value) => [...scope.querySelectorAll(selector)]
      .filter((element) => element.getAttribute(attribute) === value);
    const scale = Math.hypot(root.getScreenCTM().c, root.getScreenCTM().d);
    const contained = (a, b) => a.left >= b.left - 0.05 && a.top >= b.top - 0.05
      && a.right <= b.right + 0.05 && a.bottom <= b.bottom + 0.05;
    const domNodes = [...root.querySelectorAll('[data-node-id]')];
    const nodes = expected.nodes.map((node) => {
      const matches = domNodes.filter((element) => element.dataset.nodeId === node.id), element = matches[0];
      const boxes = element ? [...element.querySelectorAll(':scope > rect.c-mask')] : [];
      const labels = element ? [...element.querySelectorAll('text[data-node-label]')] : [];
      return { ...node, count: matches.length, boxCount: boxes.length, labelCount: labels.length,
        ok: matches.length === 1 && visible(element) && element.id === `node-${node.id}` && element.dataset.nodeLabel === node.label
          && boxes.length === 1 && visible(boxes[0]) && inViewBox(boxes[0])
          && labels.length === 1 && textVisible(labels[0]) && labels[0].textContent === node.label };
    });
    const frames = [...root.querySelectorAll('[data-composition-frame-kind="group"]')];
    const labels = [...root.querySelectorAll('[data-group-label]')];
    const groups = expected.groups.map((group) => {
      const matches = frames.filter((frame) => frame.dataset.compositionFrameId === group.id), frame = matches[0];
      const labelMatches = labels.filter((label) => label.dataset.groupLabel === group.labelId), label = labelMatches[0];
      const bounds = frame && rect(frame), labelBounds = label && rect(label);
      const members = group.members.map((id) => {
        const node = domNodes.find((element) => element.dataset.nodeId === id);
        const box = node?.querySelector(':scope > rect.c-mask');
        return { id, ok: nodes.find((entry) => entry.id === id)?.ok === true && !!bounds && !!box
          && contained(rect(box), bounds) && contained(rect(node), bounds) };
      });
      // Labels are deliberately above (not inside) workflow frames. Check actual
      // ink bounds and adjacency as well as the source label ID and literal text.
      const labelOK = labelMatches.length === 1 && label.localName === 'text' && textVisible(label)
        && label.textContent === group.label && frame?.nextElementSibling === label && inViewBox(label)
        && !!bounds && labelBounds.left >= bounds.left - 0.05 && labelBounds.right <= bounds.right + 0.05
        && labelBounds.bottom <= bounds.top + 2 * scale && labelBounds.bottom >= bounds.top - 24 * scale;
      return { id: group.id, labelId: group.labelId, count: matches.length, labelCount: labelMatches.length,
        labelOK, members, overflow: members.filter((member) => !member.ok).map((member) => member.id),
        ok: matches.length === 1 && frame.localName === 'rect' && visible(frame) && paint(frame, 'stroke')
          && inViewBox(frame) && labelOK && members.every((member) => member.ok) };
    });
    const unexpectedGroups = frames.filter((frame) => !expected.groups.some((group) => group.id === frame.dataset.compositionFrameId))
      .map((frame) => frame.dataset.compositionFrameId ?? null);
    const groupCountOK = frames.length === expected.groups.length && labels.length === expected.groups.length;
    const toolbars = [...document.querySelectorAll('.toolbar')], stories = [...document.querySelectorAll('.guided-views')];
    const toolbar = toolbars[0], story = stories[0];
    const normalUI = document.documentElement.dataset.embed !== 'true' && !matchMedia('print').matches;
    const ui = { toolbarExpected: normalUI, storyExpected: normalUI && expected.story,
      toolbarCount: toolbars.length, storyCount: stories.length,
      toolbarVisible: visible(toolbar), storyVisible: visible(story) };
    ui.ok = toolbars.length === 1 && stories.length === 1
      && ui.toolbarVisible === ui.toolbarExpected && ui.storyVisible === ui.storyExpected;
    const toolbarStoryOverlap = scrollY === 0 && visible(toolbar) && visible(story)
      ? area(intersection(rect(toolbar), rect(story))) : 0;
    let sequence = null;
    const lifelineOverlaps = [];
    if (expected.sequence) {
      const lines = [...root.querySelectorAll('path[stroke-dasharray="3,7"]')];
      const lifelines = expected.sequence.participants.map((participant, index) => {
        const line = lines[index], node = domNodes.find((element) => element.dataset.nodeId === participant.id);
        const box = node?.querySelector(':scope > rect.c-mask');
        const bounds = line && rect(line), centerX = box && (rect(box).left + rect(box).right) / 2;
        return { id: participant.id, index, rect: bounds, centerX,
          ok: nodes.find((entry) => entry.id === participant.id)?.ok === true && !!line && strokeVisible(line)
            && bounds.height > 0 && bounds.width <= 0.05 && inViewBox(line)
            && Math.abs(bounds.left - centerX) <= 0.05 };
      });
      const wanted = expected.sequence.legend, roots = [...root.querySelectorAll('[data-legend]')], legend = roots[0];
      const required = wanted.mode !== 'hidden' && wanted.entries.length > 0;
      const entryElements = legend ? [...legend.querySelectorAll('[data-legend-semantic-kind]')] : [];
      const entries = wanted.entries.map((entry) => {
        const matches = legend ? matching(legend, '[data-legend-semantic-kind]', 'data-legend-semantic-kind', entry.kind) : [];
        const texts = matches[0] ? [...matches[0].querySelectorAll('text')] : [];
        const swatches = matches[0] ? [...matches[0].querySelectorAll('path')] : [];
        return { ...entry, count: matches.length,
          ok: matches.length === 1 && visible(matches[0]) && inViewBox(matches[0])
            && texts.length === 1 && textVisible(texts[0]) && texts[0].textContent === entry.label
            && swatches.length === 1 && strokeVisible(swatches[0]) };
      });
      const titles = legend ? [...legend.querySelectorAll(':scope > text')] : [];
      const band = visible(legend) ? rect(legend) : null;
      // Measure the bottom timeline-to-legend gap from real Chrome ink, not
      // renderer estimates; compare in SVG units across reader/camera scales.
      const gap = band && lines.length ? (band.top - Math.max(...lines.map((line) => rect(line).bottom))) / scale : null;
      if (band) for (const line of lines) {
        const box = rect(line);
        if (box.right >= band.left && box.left <= band.right && box.bottom > band.top && box.top < band.bottom) {
          lifelineOverlaps.push({ x: box.left, bottom: box.bottom, legendTop: band.top });
        }
      }
      const legendOK = required
        ? roots.length === 1 && visible(legend) && inViewBox(legend) && entryElements.length === wanted.entries.length
          && titles.length === 1 && textVisible(titles[0]) && titles[0].textContent === wanted.title
          && entries.every((entry) => entry.ok) && Number.isFinite(gap) && gap > 0
        : roots.length === 0 || (wanted.mode === 'hidden' && roots.length === 1 && !shown(legend)); // Source-authorized only.
      sequence = { lifelines, lifelineCount: lines.length, expectedLifelines: expected.sequence.participants.length,
        legend: { required, mode: wanted.mode, count: roots.length, entries, gap, ok: legendOK },
        ok: lines.length === expected.sequence.participants.length && lifelines.every((line) => line.ok) && legendOK };
    }
    return { type: expected.type, ui, nodes, nodeCount: domNodes.length, toolbarStoryOverlap, groups, groupCountOK,
      unexpectedGroups, sequence, lifelineOverlaps,
      ok: ui.ok && toolbarStoryOverlap === 0 && domNodes.length === expected.nodes.length && nodes.every((node) => node.ok)
        && groupCountOK && !unexpectedGroups.length && groups.every((group) => group.ok)
        && (!sequence || sequence.ok) && lifelineOverlaps.length === 0 };
  };
  window.__acceptance = {
    svg, visible, decodeImage, inspectIcons, nodeInk, groupLabelInk, structuralClearance,
    responsive() {
      const html = document.documentElement;
      const documentBounds = { left: -scrollX, top: -scrollY, right: innerWidth - scrollX, bottom: Math.max(html.scrollHeight, innerHeight) - scrollY };
      const reachable = (element, bounds) => {
        if (!inside(bounds, documentBounds)) return false;
        for (let p = element; p && p !== document.body && p !== html; p = p.parentElement) {
          const style = getComputedStyle(p), r = rect(p);
          if (/(hidden|clip)/.test(style.overflowX) && (bounds.left < r.left - 1 || bounds.right > r.right + 1)) return false;
          if (/(hidden|clip)/.test(style.overflowY) && (bounds.top < r.top - 1 || bounds.bottom > r.bottom + 1)) return false;
        }
        return true;
      };
      const caption = document.querySelector('#guided-story-caption');
      const elements = [...document.querySelectorAll('.header, .header h1, .guided-views, .guided-view-copy, .guided-view-actions, .guided-view-index, .cards .card, .toolbar, .toolbar button, .diagram-nav, #guided-story-caption:not([hidden])')].filter(visible).map((element) => {
        const bounds = rect(element);
        const text = [...element.querySelectorAll('h1, h3, li, #guided-story-caption-route, #guided-story-caption-detail, #guided-story-caption-next-label')]
          .filter((el) => !el.closest('.guided-view-chapters') && (visible(el) ||
            (visible(caption) && ['guided-story-caption-route', 'guided-story-caption-detail'].includes(el.id)))).map((el) => {
            const range = document.createRange(); range.selectNodeContents(el);
            return { text: el.textContent.trim(), ok: visible(el) && reachable(el, rectFromRange(range)) };
          });
        const scrollableDock = element.matches('.diagram-nav') && /auto|scroll/.test(getComputedStyle(element).overflowX);
        return { element: element.id || element.className || element.tagName, rect: bounds,
          scrollableDock, clientWidth: element.clientWidth, scrollWidth: element.scrollWidth,
          ok: reachable(element, bounds) && (scrollableDock || element.scrollWidth <= element.clientWidth + 1) && text.every((item) => item.ok), text };
      });
      const toolbar = document.querySelector('.toolbar');
      const overlaps = visible(toolbar) ? [...document.querySelectorAll('.header h1, .header .subtitle')].filter(visible).reduce((sum, element) => {
        const range = document.createRange(); range.selectNodeContents(element);
        return sum + area(intersection(rectFromRange(range), rect(toolbar)));
      }, 0) : 0;
      return { width: innerWidth, height: innerHeight, scrollX, scrollY, scrollWidth: html.scrollWidth, scrollHeight: html.scrollHeight,
        sidebar: html.dataset.readerSidebar || null, reader: html.dataset.readerLayout || null,
        stageWidth: html.style.getPropertyValue('--archify-stage-width'), sidebarWidth: html.style.getPropertyValue('--archify-sidebar-width'),
        activeView: document.querySelector('#guided-views')?.dataset.activeView,
        playing: document.querySelector('#guided-views')?.dataset.playing, beat: document.querySelector('#guided-views')?.dataset.storyBeat,
        captionVisible: visible(document.querySelector('#guided-story-caption')), elements, toolbarHeaderOverlap: overlaps,
        nodeInk: nodeInk(), groupLabelInk: groupLabelInk(), structuralClearance: structuralClearance(), readerSpacing: readerSpacing(),
        nodes: [...svg().querySelectorAll('[data-node-id]')].map((node) => ({ id: node.dataset.nodeId, label: node.dataset.nodeLabel })),
        ok: html.scrollWidth <= innerWidth + 1 && elements.every((item) => item.ok) && (scrollY !== 0 || overlaps === 0) };
    },
    mode() {
      const html = document.documentElement;
      const visibility = Object.fromEntries(['.toolbar', '.header', '.cards', '.diagram-nav', '.guided-views', '.overview-map', '.focus-chip', '.route-probe', '.semantic-lens', '.diagram-guide', '.node-finder', '.diagram-container', '#btn-present'].map((selector) => [selector, visible(document.querySelector(selector))]));
      return { width: innerWidth, height: innerHeight, present: html.getAttribute('data-present'), embed: html.getAttribute('data-embed'),
        print: matchMedia('print').matches, pressed: document.querySelector('#btn-present')?.getAttribute('aria-pressed'),
        reader: html.getAttribute('data-reader-layout'), readerOverflow: html.getAttribute('data-reader-overflow'),
        readerWidth: html.style.getPropertyValue('--archify-reader-width'), sidebar: html.getAttribute('data-reader-sidebar'),
        stageWidth: html.style.getPropertyValue('--archify-stage-width'), sidebarWidth: html.style.getPropertyValue('--archify-sidebar-width'),
        rail: html.getAttribute('data-nav-stage-rail'), chrome: window.Archify?.viewerChromeLayout?.receipt?.() || null,
        guided: document.querySelector('#guided-views')?.dataset.activeView,
        cardCount: document.querySelectorAll('.cards .card').length, visibility,
        svgVisible: visible(svg()), background: getComputedStyle(document.body).backgroundColor };
    },
    async layout() {
      const root = svg();
      if (!root) throw new Error('Canonical diagram SVG missing');
      const viewport = viewportRect();
      const containers = [document.body, document.documentElement, document.querySelector('.container'), document.querySelector('.diagram-container'), root.parentElement].filter((value, index, all) => value && all.indexOf(value) === index).map((element) => {
        const style = getComputedStyle(element), bounds = rect(element);
        const clippedStage = element === root.parentElement && element.matches('.diagram-stage') && style.overflowX === 'clip' && style.overflowY === 'clip';
        // A clip stage may retain internal overflow dimensions without exposing a scrollable area.
        const contentFits = clippedStage
          ? element.scrollLeft === 0 && element.scrollTop === 0 && inside(bounds, rect(document.querySelector('.diagram-container')))
          : element.scrollWidth <= element.clientWidth + 1 && element.scrollHeight <= element.clientHeight + 1;
        return {
          element: element.tagName + (element.className.baseVal ?? element.className ? '.' + (element.className.baseVal ?? element.className) : ''),
          clientWidth: element.clientWidth, clientHeight: element.clientHeight,
          scrollWidth: element.scrollWidth, scrollHeight: element.scrollHeight, rect: bounds, clippedStage,
          ok: contentFits && bounds.width > 0 && bounds.height > 0 && inside(bounds, viewport),
        };
      });
      const nodes = [...root.querySelectorAll('[data-node-id]')].map((node) => {
        const label = node.querySelector('text[data-node-label]') || node.querySelector('text');
        const bounds = rect(node);
        const labelBounds = label && rect(label);
        const center = labelBounds || bounds;
        const hit = document.elementFromPoint((center.left + center.right) / 2, (center.top + center.bottom) / 2);
        return { id: node.getAttribute('data-node-id'), label: node.getAttribute('data-node-label'), rect: bounds,
          labelVisible: !!label && visible(label) && label.getComputedTextLength() > 0,
          unobscured: !!hit && node.contains(hit),
          unclipped: unclipped(node) && (!root.parentElement.matches('.diagram-stage') || inside(bounds, rect(root.parentElement))),
          visible: visible(node) && inside(bounds, viewport) && inside(bounds, rect(root)) && inside(bounds, rect(document.querySelector('.diagram-container'))) };
      });
      const businessText = [...root.querySelectorAll('[data-node-id] text, [data-node-id] tspan')]
        .filter((element) => visible(element) && element.textContent.trim() && !element.querySelector('tspan'))
        .map((element) => {
          const matrix = element.getScreenCTM(), fontSize = parseFloat(getComputedStyle(element).fontSize);
          const projectedPx = matrix ? fontSize * Math.hypot(matrix.c, matrix.d) : 0;
          return { node: element.closest('[data-node-id]').dataset.nodeId, text: element.textContent.trim(), fontSize,
            projectedPx, ok: Number.isFinite(projectedPx) && projectedPx >= 6 };
        });
      return { width: innerWidth, height: innerHeight, theme: document.documentElement.dataset.theme,
        background: getComputedStyle(document.body).backgroundColor,
        lang: document.documentElement.lang, locale: window.Archify?.locale,
        surfaces: surfaces(), chrome: chrome(), businessText, nodeInk: nodeInk(), groupLabelInk: groupLabelInk(), structuralClearance: structuralClearance(), readerSpacing: readerSpacing(), mode: this.mode(),
        containers, nodes, icons: await inspectIcons(root, true) };
    },
    finder() {
      return { open: !document.querySelector('#node-finder').hidden, query: document.querySelector('#node-finder-input').value,
        ids: [...document.querySelectorAll('#node-finder-results .node-finder-result')].map((node) => node.dataset.nodeId),
        empty: !document.querySelector('#node-finder-empty').hidden, status: document.querySelector('#node-finder-status').textContent };
    },
    focus() {
      const root = svg();
      return { active: root.getAttribute('data-focus-active'), chip: !document.querySelector('#focus-chip').hidden,
        selected: [...root.querySelectorAll('[data-node-id][data-focus-selected]')].map((node) => node.dataset.nodeId),
        nodes: [...root.querySelectorAll('[data-node-id]')].map((node) => ({ id: node.dataset.nodeId, match: node.hasAttribute('data-focus-match'), pressed: node.getAttribute('aria-pressed') })),
        edges: [...root.querySelectorAll('[data-edge-from][data-edge-to]')].map((edge) => ({ from: edge.dataset.edgeFrom, to: edge.dataset.edgeTo, match: edge.hasAttribute('data-focus-match') })) };
    },
    zoom() {
      return { transform: getComputedStyle(svg()).transform, percent: document.querySelector('[data-view-percent]')?.textContent };
    },
    async exported(data, format) {
      const result = { decoded: await decodeImage(data) };
      if (format === 'svg') {
        const xml = await (await fetch(data)).text();
        const root = new DOMParser().parseFromString(xml, 'image/svg+xml');
        result.validXML = !root.querySelector('parsererror') && root.documentElement.localName === 'svg';
        result.nodes = [...root.querySelectorAll('[data-node-id]')].map((node) => ({ id: node.getAttribute('data-node-id'), label: node.getAttribute('data-node-label') }));
        result.icons = await inspectIcons(root, false);
        result.focusClean = !root.querySelector('[data-focus-active], [data-focus-selected], [data-focus-match]');
      }
      return result;
    },
  };
}

async function run(options) {
  const bytes = fs.readFileSync(options.artifact);
  const report = {
    schema_version: 1, automated_status: 'fail', visual_review: 'pending', profile: options.profile,
    started_at: new Date().toISOString(), artifact: { path: options.artifact, bytes: bytes.length, sha256: sha256(bytes) },
    model: { path: options.model || path.join(path.dirname(options.artifact), 'model.json'), bytes: null, sha256: null },
    expect_icons: options.expectIcons, require_chinese: options.requireChinese, checks: [], screenshots: [], exports: [],
    console_errors: [], external_requests: [], rejected_local_requests: [], policy_violations: [], server_requests: [], harness_errors: [],
    limitations: ['Geometry, XML/image decoding and nonblank pixels are automatic checks, not human visual approval.',
      'Chinese UI is checked separately; Chinese business labels are required only with --require-chinese true. Glyph aesthetics/tofu require manual screenshot review.',
      'Downloads are captured at a.click after real export UI clicks; OS download dialogs are not tested.',
      'Offline CSP disables workers, frames, popups and remote resources; external attempts fail acceptance.'],
  };
  let browser, server, removeObserver, closing = false, aborted = false;
  const pending = new Set();
  let send, evaluate;
  const record = (name, ok, evidence) => {
    report.checks.push({ name, status: ok ? 'pass' : 'fail', evidence });
    return ok;
  };
  const check = async (name, operation) => {
    if (aborted) throw new Error('Interrupted');
    try { await operation(); } catch (error) {
      const evidence = { error: errorText(error) };
      if (options.profile === 'responsive' && evaluate) {
        try {
          evidence.geometry = await evaluate('window.__acceptance?.responsive()');
          const image = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
          report.screenshots.push({ ...persist(`failure-${report.checks.length}.png`, Buffer.from(image.data, 'base64')), key: name });
        } catch (captureError) { evidence.captureError = errorText(captureError); }
      }
      record(name, false, evidence);
    }
  };
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const persist = (name, contents) => {
    const file = path.join(options.outdir, name);
    fs.writeFileSync(file, contents, { flag: 'wx' });
    const actual = fs.readFileSync(file);
    return { path: file, bytes: actual.length, sha256: sha256(actual) };
  };
  const onSignal = () => { aborted = true; if (browser && !closing) browser.cdp.failAll(new Error('Interrupted')); };
  process.on('SIGINT', onSignal);
  process.on('SIGTERM', onSignal);
  try {
    let expected;
    try {
      assert(path.isAbsolute(report.model.path), '--model must be an absolute local path.');
      report.model.path = fs.realpathSync(report.model.path);
      assert(fs.statSync(report.model.path).isFile(), 'Native model must be a local JSON file.');
      const modelBytes = fs.readFileSync(report.model.path);
      Object.assign(report.model, { bytes: modelBytes.length, sha256: sha256(modelBytes) });
      expected = expectedStructure(JSON.parse(modelBytes.toString('utf8')));
      report.expected_structure = expected;
      record('native-model-valid', true, report.model);
    } catch (error) {
      record('native-model-valid', false, { ...report.model, error: errorText(error) });
      throw new Error(`Native model unavailable or invalid; no DOM fallback. Standalone HTML requires --model: ${errorText(error)}`);
    }
    report.chrome = findChrome();
    assert(report.chrome, 'Chrome unavailable; no installation attempted. Set ARCHIFY_CHROME to an existing executable.');
    let origin;
    server = http.createServer((request, response) => {
      const allowed = request.method === 'GET' && request.headers.host === new URL(origin).host && /^\/artifact\.html(?:\?[^#]*)?$/.test(request.url);
      report.server_requests.push({ method: request.method, url: request.url, status: allowed ? 200 : 404 });
      if (!allowed) {
        // Chrome's implicit favicon request is not an artifact dependency.
        if (request.url !== '/favicon.ico') report.rejected_local_requests.push({ source: 'server', url: request.url });
        response.writeHead(404); response.end(); return;
      }
      response.writeHead(200, {
        'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
        'Content-Security-Policy': OFFLINE_CSP,
      });
      response.end(bytes);
    });
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    origin = `http://127.0.0.1:${server.address().port}`;
    report.server = { origin, whitelist: ['/artifact.html'] };
    browser = new ChromeVisualBrowser(report.chrome);
    const session = await browser.sessionPromise;
    send = (method, params = {}) => browser.cdp.send(method, params, session);
    evaluate = async (expression) => {
      const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
      return response.result?.value;
    };
    const isExternal = (url) => /^(?:https?|wss?|ftp|file):/i.test(url) && !url.startsWith(origin + '/');
    const handle = (promise) => {
      pending.add(promise);
      promise.catch((error) => { if (!closing) report.harness_errors.push(errorText(error)); }).finally(() => pending.delete(promise));
    };
    removeObserver = observePipe(browser, ({ method, params, sessionId }) => {
      if (sessionId !== session || closing) return;
      if (method === 'Fetch.requestPaused') {
        const url = params.request.url;
        const local = url.startsWith(origin + '/');
        const allowed = (local && /^\/artifact\.html(?:\?[^#]*)?$/.test(new URL(url).pathname + new URL(url).search)) || /^(data:|blob:|about:blank$)/i.test(url);
        if (!allowed && local && new URL(url).pathname !== '/favicon.ico') report.rejected_local_requests.push({ source: 'Fetch', url });
        if (!allowed && !local) report.external_requests.push({ source: 'Fetch', url, blocked: true });
        if (local && new URL(url).pathname === '/favicon.ico') {
          handle(send('Fetch.fulfillRequest', { requestId: params.requestId, responseCode: 204, body: '' }));
        } else {
          handle(send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', { requestId: params.requestId, ...(allowed ? {} : { errorReason: 'BlockedByClient' }) }));
        }
      } else if (method === 'Network.requestWillBeSent' && isExternal(params.request.url)) {
        report.external_requests.push({ source: 'Network', url: params.request.url, type: params.type });
      } else if (method === 'Runtime.consoleAPICalled' && params.type === 'error') {
        report.console_errors.push({ source: 'console', args: params.args.map((arg) => arg.value ?? arg.description ?? arg.type), stack: params.stackTrace });
      } else if (method === 'Runtime.exceptionThrown') {
        report.console_errors.push({ source: 'exception', details: params.exceptionDetails });
      } else if (method === 'Log.entryAdded' && params.entry.level === 'error') {
        report.console_errors.push({ source: 'browser', ...params.entry });
      } else if (method === 'Runtime.bindingCalled' && params.name === '__acceptanceEvent') {
        const value = JSON.parse(params.payload);
        report.policy_violations.push(value);
        if (isExternal(value.url)) report.external_requests.push({ source: 'CSP', ...value, blocked: true });
      } else if (method === 'Page.javascriptDialogOpening') {
        report.console_errors.push({ source: 'dialog', message: params.message });
        handle(send('Page.handleJavaScriptDialog', { accept: false }));
      }
    });
    await send('Network.enable');
    // Keep loopback HTTP; Fetch enforces the exact-origin allowlist for HTTP and redirects.
    await send('Network.setBlockedURLs', { urls: ['https://*', 'ws://*', 'wss://*', 'ftp://*', 'file://*'] });
    await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await send('Log.enable');
    await send('Runtime.addBinding', { name: '__acceptanceEvent' });
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `(${installObserver.toString()})()` });
    await browser.cdp.send('Browser.setDownloadBehavior', { behavior: 'deny' });
    report.browser_version = await browser.cdp.send('Browser.getVersion');
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: options.profile === 'responsive' ? 'no-preference' : 'reduce' }] });

    const wait = async (expression, timeout = 10000) => evaluate(`new Promise((resolve, reject) => {
      const deadline = performance.now() + ${timeout};
      function poll() { try { if (${expression}) return resolve(true); } catch (_) {}
        if (performance.now() > deadline) return reject(new Error('Condition timed out: ' + ${JSON.stringify(expression)}));
        requestAnimationFrame(poll); } poll(); })`);
    const settle = async () => {
      await evaluate(`(async () => { await document.fonts.ready;
        for (let i = 0; i < 2; i++) {
          if (window.Archify?.readerLayout?.whenStable) await Archify.readerLayout.whenStable();
          if (window.Archify?.viewerChromeLayout?.whenStable) await Archify.viewerChromeLayout.whenStable();
        }
      })()`);
      // Wait for measured layout/transform quiescence, not a blind fixed sleep.
      await evaluate(`new Promise((resolve, reject) => { let prior = '', stable = 0; const deadline = performance.now() + 10000;
        function tick() {
          const elements = [...document.querySelectorAll('.diagram-container, .diagram-stage, .diagram-container > svg, .diagram-stage > svg, .diagram-nav, .guided-story-caption, .guided-view-actions, #guided-view-play')];
          const value = JSON.stringify(elements.map(el => { const r = el.getBoundingClientRect(), style = getComputedStyle(el);
            return [r.x, r.y, r.width, r.height, style.transform, el.scrollLeft, el.scrollWidth, style.clipPath]; }));
          stable = value === prior ? stable + 1 : 0; prior = value;
          if (stable >= 8 && !document.querySelector('.is-camera-moving, .is-camera-transaction')) return resolve(true);
          if (performance.now() > deadline) return reject(new Error('Layout did not settle'));
          requestAnimationFrame(tick);
        } tick(); })`);
    };
    const click = async (selector) => {
      const target = JSON.stringify(selector);
      const locate = () => evaluate(`(() => {
        const el = document.querySelector(${target});
        if (!el || !__acceptance.visible(el) || el.disabled) throw new Error('UI missing, hidden or disabled: ' + ${target});
        const r = el.getBoundingClientRect();
        for (const [fx, fy] of [[.5,.5],[.25,.5],[.75,.5],[.5,.25],[.5,.75]]) {
          const x = r.left + r.width * fx, y = r.top + r.height * fy;
          const hit = document.elementFromPoint(x, y);
          if (hit && el.contains(hit)) return { x, y };
        } throw new Error('UI is obscured: ' + ${target});
      })()`);
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...await locate() });
      await evaluate(`(() => {
        const el = document.querySelector(${target});
        window.__acceptanceLastClick = null;
        document.addEventListener('click', event => {
          window.__acceptanceLastClick = { matched: el.contains(event.target), trusted: event.isTrusted,
            target: event.target.id || event.target.tagName, buttonRect: el.getBoundingClientRect().toJSON() };
        }, { once: true, capture: true });
      })()`);
      const point = await locate();
      await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
      await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
      const received = await evaluate('window.__acceptanceLastClick');
      assert(received?.matched && received?.trusted, `UI click missed ${selector}: ${JSON.stringify({ point, received })}`);
      await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: 1, y: 1 });
      await settle();
      return { point, ...received };
    };
    const typeSearch = async (text) => {
      await click('#node-finder-input');
      await evaluate("document.querySelector('#node-finder-input').select()");
      await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
      await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 });
      if (text) await send('Input.insertText', { text });
      await wait(`document.querySelector('#node-finder-input').value === ${JSON.stringify(text)}`);
      return evaluate('__acceptance.finder()');
    };
    const cleared = async () => {
      const state = await evaluate('__acceptance.focus()');
      return { state, ok: !state.active && !state.chip && !state.selected.length && state.nodes.every((n) => !n.match && n.pressed !== 'true') && state.edges.every((e) => !e.match) };
    };
    const clearFocus = async () => {
      if (await evaluate("!document.querySelector('#focus-chip').hidden")) await click('#btn-focus-clear');
    };
    const iconsSignature = (icons) => icons.map((icon) => `${icon.node}:${icon.sha256}`).sort().join('\n');
    const nodeSignature = (nodes) => nodes.map((node) => `${node.id}:${node.label}`).sort().join('\n');
    let baselineSignature;
    const readerChecks = (key, layout) => {
      record(`${key}/projected-business-text`, layout.businessText.length > 0 && layout.businessText.every((text) => text.ok), layout.businessText);
      record(`${key}/node-ink-clearance`, layout.nodeInk.length > 0 && layout.nodeInk.every((node) => node.ok), layout.nodeInk);
      record(`${key}/group-label-clearance`, layout.groupLabelInk.every((label) => label.ok), layout.groupLabelInk);
      record(`${key}/structural-clearance`, layout.structuralClearance.ok, layout.structuralClearance);
      record(`${key}/reader-column-gap`, layout.readerSpacing.ok, layout.readerSpacing);
      record(`${key}/reader-surfaces`, layout.surfaces.every((surface) => surface.ok), layout.surfaces);
      record(`${key}/viewer-chrome-clearance`, layout.chrome.ok, layout.chrome);
    };
    const restored = (key, before, after) => {
      const near = (a, b) => ['left', 'top', 'right', 'bottom'].every((axis) => Number.isFinite(a?.[axis]) && Number.isFinite(b?.[axis]) && Math.abs(a[axis] - b[axis]) <= 1);
      const sameBoxes = (a, b) => a.length === b.length && a.every((item, index) => item.element === b[index].element && near(item.rect, b[index].rect));
      record(`${key}/restored`, after.mode.present !== 'true' && after.mode.embed !== 'true' && !after.mode.print &&
        ['reader', 'readerWidth', 'sidebar', 'stageWidth', 'sidebarWidth'].every((field) => after.mode[field] === before.mode[field]) &&
        (expected.story ? after.mode.guided === 'all' : !after.mode.guided) &&
        after.containers.every((c) => c.ok) && sameBoxes(before.containers, after.containers) && sameBoxes(before.surfaces, after.surfaces) &&
        nodeSignature(after.nodes) === baselineSignature && after.nodes.every((n) => n.visible && n.labelVisible && n.unobscured && n.unclipped) &&
        iconsSignature(after.icons) === iconsSignature(before.icons) && after.icons.every((icon) => icon.ok), { before, after });
      readerChecks(key, after);
    };
    const navigate = async (query) => {
      const navigation = await send('Page.navigate', { url: `${origin}/artifact.html${query}` });
      assert(!navigation.errorText, navigation.errorText);
      await wait("document.readyState === 'complete' && document.querySelector('.diagram-container svg') && window.Archify?.finder");
      await evaluate(`(${installPageTools.toString()})(${JSON.stringify(expected)})`);
      await settle();
    };
    for (const [width, height] of options.profile === 'desktop' ? VIEWPORTS : []) {
      for (const theme of ['light', 'dark']) {
        const key = `${width}x${height}-${theme}`;
        await check(`${key}/case`, async () => {
          await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
          await navigate(`?theme=${theme === 'light' ? 'dark' : 'light'}`);
          const beforeTheme = await evaluate('document.documentElement.dataset.theme');
          await click('#btn-theme');
          const layout = await evaluate('__acceptance.layout()');
          record(`${key}/theme-click`, beforeTheme !== theme && layout.theme === theme && await evaluate(`document.querySelector('#btn-theme').getAttribute('aria-pressed') === ${JSON.stringify(theme === 'light' ? 'true' : 'false')}`), { before: beforeTheme, after: layout.theme });
          const capture = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
          const screenshot = persist(`${key}.png`, Buffer.from(capture.data, 'base64'));
          const decoded = await evaluate(`__acceptance.decodeImage(${JSON.stringify('data:image/png;base64,' + capture.data)})`);
          report.screenshots.push({ ...screenshot, width, height, theme, decoded });
          record(`${key}/screenshot`, decoded.width === width && decoded.height === height && decoded.colors > 1, screenshot);
          record(`${key}/containment`, layout.width === width && layout.height === height && layout.containers.every((c) => c.ok), layout.containers);
          const signature = nodeSignature(layout.nodes);
          baselineSignature ??= signature;
          record(`${key}/business-nodes`, layout.nodes.length > 0 && signature === baselineSignature && layout.nodes.every((n) => n.visible && n.labelVisible && n.unobscured && n.unclipped), layout.nodes);
          readerChecks(key, layout);
          const chinese = layout.nodes.find((n) => /[\u3400-\u9fff]/.test(n.label || ''));
          record(`${key}/zh-ui`, /^zh\b/i.test(layout.lang) && /^zh\b/i.test(layout.locale), { lang: layout.lang, locale: layout.locale });
          if (options.requireChinese) record(`${key}/chinese`, !!chinese, { required: true, example: chinese?.label });
          record(`${key}/embedded-icons`, layout.icons.every((icon) => icon.ok) && (options.expectIcons === null || layout.icons.length === options.expectIcons), { count: layout.icons.length, expected: options.expectIcons, icons: layout.icons });

          await check(`${key}/search`, async () => {
            const searchable = chinese || layout.nodes.find((n) => n.id && n.label?.trim());
            assert(searchable, 'No valid business label to search');
            const query = chinese ? chinese.label.match(/[\u3400-\u9fff]+/)[0] : searchable.label.trim();
            await click('#btn-node-finder');
            const hit = await typeSearch(query);
            record(`${key}/search-hit`, hit.open && !hit.empty && hit.ids.includes(searchable.id), { query, ...hit });
            const missingQuery = '__不存在的验收节点_9f17d7__';
            const missing = await typeSearch(missingQuery);
            record(`${key}/search-no-results`, missing.open && missing.empty && missing.ids.length === 0, missing);
            const empty = await typeSearch('');
            record(`${key}/search-clear`, !empty.empty && empty.ids.length === layout.nodes.length && layout.nodes.every((n) => empty.ids.includes(n.id)), empty);
            await typeSearch(query);
            await click(`#node-finder-results .node-finder-result[data-node-id=${JSON.stringify(searchable.id)}]`);
            const selected = await evaluate('__acceptance.focus()');
            record(`${key}/search-select-focus`, selected.active === searchable.id && selected.selected.includes(searchable.id) && !(await evaluate('__acceptance.finder()')).open, selected);
            await clearFocus();
            const clear = await cleared();
            record(`${key}/search-focus-clear`, clear.ok, clear.state);
          });
          if (await evaluate("!document.querySelector('#node-finder').hidden")) await click('#node-finder-close');
          await clearFocus();
          await check(`${key}/relationships`, async () => {
            const before = await evaluate('__acceptance.focus()');
            const edge = before.edges.find((e) => layout.nodes.some((n) => n.id === e.from) && layout.nodes.some((n) => n.id === e.to));
            assert(edge, 'No relationship between business nodes to test');
            const id = edge.from;
            await click(`.diagram-container svg [data-node-id=${JSON.stringify(id)}]`);
            const focused = await evaluate('__acceptance.focus()');
            const expected = new Set([id]);
            before.edges.filter((e) => e.from === id || e.to === id).forEach((e) => { expected.add(e.from); expected.add(e.to); });
            record(`${key}/relationship-focus`, focused.active === id && focused.chip && focused.selected.length === 1 && focused.selected[0] === id && focused.nodes.every((n) => n.match === expected.has(n.id)) && focused.edges.every((e) => e.match === (e.from === id || e.to === id)), { expected_nodes: [...expected], ...focused });
            await click('#btn-focus-clear');
            const clear = await cleared();
            record(`${key}/relationship-clear`, clear.ok, clear.state);
          });
          await clearFocus();
          await check(`${key}/zoom`, async () => {
            const count = await evaluate("document.querySelectorAll('.diagram-nav [data-view]').length");
            if (!count) { report.checks.push({ name: `${key}/zoom`, status: 'not_supported', evidence: 'No zoom UI in artifact' }); return; }
            await click('.diagram-nav [data-view="reset"]');
            const before = await evaluate('__acceptance.zoom()');
            await click('.diagram-nav [data-view="in"]');
            const zoomed = await evaluate('__acceptance.zoom()');
            await click('.diagram-nav [data-view="out"]');
            const zoomedOut = await evaluate('__acceptance.zoom()');
            await click('.diagram-nav [data-view="in"]');
            await click('.diagram-nav [data-view="reset"]');
            const reset = await evaluate('__acceptance.zoom()');
            record(`${key}/zoom-reset`, Number.parseInt(zoomed.percent) > Number.parseInt(before.percent) && zoomed.transform !== before.transform && zoomedOut.percent === before.percent && reset.percent === before.percent && reset.transform === before.transform, { before, zoomed, zoomedOut, reset });
          });
          await check(`${key}/guided`, async () => {
            if (!expected.story) {
              record(`${key}/guided-not-authored`, !layout.structuralClearance.ui.storyVisible, 'Native model has no guided views.');
              return;
            }
            const selector = '#guided-view-chapters [data-guided-view-id]';
            const id = await evaluate(`document.querySelector(${JSON.stringify(selector)})?.dataset.guidedViewId`);
            assert(id, 'No guided chapter UI to test');
            const chapters = await evaluate(`[...document.querySelectorAll(${JSON.stringify(selector)})].map(el => el.dataset.guidedViewId)`);
            if (chapters.length > 1) {
              await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus()`);
              const vertical = layout.mode.sidebar === 'true';
              for (const [key, expected] of [[vertical ? 'ArrowDown' : 'ArrowRight', chapters[1]], [vertical ? 'ArrowUp' : 'ArrowLeft', chapters[0]]]) {
                await send('Input.dispatchKeyEvent', { type: 'keyDown', key });
                await send('Input.dispatchKeyEvent', { type: 'keyUp', key });
                record(`${width}x${height}-${theme}/guided-keyboard-${key}`, await evaluate(`document.activeElement?.dataset.guidedViewId === ${JSON.stringify(expected)}`), { expected });
              }
            }
            try {
              await click(selector);
              const chapter = await evaluate('__acceptance.layout()');
              const focus = await evaluate('__acceptance.focus()');
              record(`${key}/guided-chapter-click`, chapter.mode.guided === id && focus.selected.length > 0 &&
                focus.selected.every((selected) => chapter.nodes.some((n) => n.id === selected && n.visible && n.labelVisible && n.unobscured && n.unclipped)), { id, focus, nodes: chapter.nodes });
              // Unselected nodes may intentionally lie outside a chapter camera; its reader surfaces and dock may not.
              readerChecks(`${key}/guided-chapter`, chapter);
            } finally {
              if (await evaluate("document.querySelector('#guided-views').dataset.activeView !== 'all'")) await click('#guided-view-all');
            }
            const overview = await evaluate('__acceptance.layout()');
            const clear = await cleared();
            record(`${key}/guided-all-focus-clear`, clear.ok, clear.state);
            restored(`${key}/guided-all`, layout, overview);
          });
          for (const format of ['svg', 'png']) {
            await check(`${key}/export-${format}`, async () => {
              const index = await evaluate('__acceptanceDownloads.length');
              await click('#btn-export');
              assert(await evaluate("document.querySelector('#btn-export').getAttribute('aria-expanded') === 'true'"), 'Export menu did not open');
              await click(`#export-menu button[data-format="${format}"]`);
              await wait(`__acceptanceDownloads[${index}]?.ready`, 12000);
              const download = await evaluate(`__acceptanceDownloads[${index}]`);
              assert(!download.error && download.data, download.error || 'No blob captured');
              const output = persist(`${key}.export.${format}`, Buffer.from(download.data.slice(download.data.indexOf(',') + 1), 'base64'));
              const inspection = await evaluate(`__acceptance.exported(${JSON.stringify(download.data)}, ${JSON.stringify(format)})`);
              report.exports.push({ ...output, width, height, theme, format, download_name: download.filename, mime: download.type, inspection });
              const valid = output.bytes > 0 && download.filename.toLowerCase().endsWith('.' + format) && download.type.startsWith(format === 'svg' ? 'image/svg+xml' : 'image/png') && inspection.decoded.width > 0 && inspection.decoded.height > 0 && inspection.decoded.colors > 1;
              record(`${key}/export-${format}-decode`, valid, { ...output, decoded: inspection.decoded });
              if (format === 'svg') record(`${key}/export-svg-preserves-content`, inspection.validXML && inspection.focusClean && nodeSignature(inspection.nodes) === signature && inspection.icons.every((icon) => icon.ok) && iconsSignature(inspection.icons) === iconsSignature(layout.icons), inspection);
            });
          }
        });
      }
    }
    if (options.profile === 'desktop') record('six-desktop-theme-captures', report.screenshots.length === 6, { count: report.screenshots.length });
    if (options.profile === 'desktop') record('twelve-real-ui-exports', report.exports.length === 12, { count: report.exports.length });
    if (options.profile === 'desktop') await check('boundary/case', async () => {
      const [width, height] = VIEWPORTS[0];
      const readerCleared = (mode) => ['reader', 'readerWidth', 'readerOverflow', 'sidebar', 'stageWidth', 'sidebarWidth'].every((field) => !mode[field]);
      const resize = async (w, h, mobile = false) => {
        await send('Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile });
        await settle();
      };
      await resize(width, height);
      await navigate('?theme=dark');
      const baseline = await evaluate('__acceptance.layout()');
      await check('boundary/mobile', async () => {
        try {
          // This must cross the breakpoint in the same document, not load a fresh mobile page.
          await resize(390, 844, true);
          const mobile = await evaluate('__acceptance.mode()');
          record('boundary/mobile-reader-cleared', mobile.width === 390 && mobile.height === 844 && readerCleared(mobile) &&
            !mobile.rail && mobile.svgVisible && mobile.visibility['.header'] &&
            (!mobile.cardCount || mobile.visibility['.cards']) && (!mobile.chrome || (!mobile.chrome.eligible && mobile.chrome.reserve === 0)), mobile);
          // Mobile intentionally scrolls; desktop containment and the 6px desktop text budget do not apply.
        } finally { await resize(width, height); }
        restored('boundary/mobile-to-desktop', baseline, await evaluate('__acceptance.layout()'));
      });
      await check('boundary/present', async () => {
        try {
          await click('#btn-present');
          const present = await evaluate('__acceptance.mode()');
          record('boundary/present-enter', present.present === 'true' && present.pressed === 'true' && readerCleared(present) &&
            present.svgVisible && present.visibility['.toolbar'] && present.visibility['.header'] &&
            present.visibility['.guided-views'] === expected.story && !present.visibility['.cards'], present);
          const layout = await evaluate('__acceptance.layout()');
          record('boundary/present-chrome-clearance', layout.chrome.ok, layout.chrome);
        } finally {
          if (await evaluate("document.documentElement.getAttribute('data-present') === 'true'")) await click('#btn-present');
        }
        const normal = await evaluate('__acceptance.layout()');
        record('boundary/present-exit-button', normal.mode.pressed === 'false', normal.mode);
        restored('boundary/present-to-normal', baseline, normal);
      });
      await check('boundary/embed', async () => {
        try {
          await navigate('?theme=dark&embed=1');
          const embed = await evaluate('__acceptance.mode()');
          record('boundary/embed-contract', embed.embed === 'true' && readerCleared(embed) && !embed.rail &&
            embed.svgVisible && Object.entries(embed.visibility).every(([selector, shown]) => selector === '.diagram-container' ? shown : !shown) &&
            (!embed.chrome || (!embed.chrome.eligible && embed.chrome.reserve === 0)), embed);
        } finally { await navigate('?theme=dark'); }
        restored('boundary/embed-to-normal', baseline, await evaluate('__acceptance.layout()'));
      });
      await check('boundary/print', async () => {
        const features = [{ name: 'prefers-reduced-motion', value: 'reduce' }];
        try {
          await send('Emulation.setEmulatedMedia', { media: 'print', features });
          await settle();
          const print = await evaluate('__acceptance.mode()');
          record('boundary/print-contract', print.print && readerCleared(print) && !print.rail && print.svgVisible &&
            print.visibility['.header'] && (!print.cardCount || print.visibility['.cards']) && print.background === 'rgb(255, 255, 255)' &&
            ['.toolbar', '.diagram-nav', '.guided-views', '.focus-chip'].every((selector) => !print.visibility[selector]) &&
            (!print.chrome || (!print.chrome.eligible && print.chrome.reserve === 0)), print);
        } finally {
          await send('Emulation.setEmulatedMedia', { media: '', features });
          await settle();
        }
        restored('boundary/print-to-normal', baseline, await evaluate('__acceptance.layout()'));
      });
    });
    if (options.profile === 'responsive') {
      report.limitations.push('Finite CSS-pixel matrix, not every possible resolution; small screens may scroll vertically. Chrome emulation is not a physical-device or other-engine test.');
      const viewports = RESPONSIVE_VIEWPORTS.filter((_, index) => index % options.shardCount === options.shardIndex - 1);
      const sweep = RESPONSIVE_VIEWPORTS.length % options.shardCount === options.shardIndex - 1;
      report.shard = { index: options.shardIndex, count: options.shardCount, includes_breakpoint_sweep: sweep };
      report.responsive_viewports = viewports;
      const scrollClick = async (selector) => {
        const scrolled = await evaluate(`(() => {
          const el = document.querySelector(${JSON.stringify(selector)}), r = el.getBoundingClientRect();
          if (r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight) return false;
          el.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
          return true;
        })()`);
        if (scrolled) await settle();
        return click(selector);
      };
      const capture = async (key) => {
        const image = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
        report.screenshots.push({ ...persist(`${key}.png`, Buffer.from(image.data, 'base64')), key });
      };
      const inspect = async (key) => {
        const state = await evaluate('__acceptance.responsive()');
        const inkFailures = state.nodeInk.filter((node) => !node.ok);
        record(`${key}/node-ink-clearance`, state.nodeInk.length > 0 && inkFailures.length === 0, { nodes: state.nodeInk.length, failures: inkFailures });
        record(`${key}/group-label-clearance`, state.groupLabelInk.every((label) => label.ok), state.groupLabelInk);
        record(`${key}/structural-clearance`, state.structuralClearance.ok, state.structuralClearance);
        record(`${key}/reader-column-gap`, state.readerSpacing.ok, state.readerSpacing);
        const signature = nodeSignature(state.nodes);
        baselineSignature ??= signature;
        const cleared = state.width >= 1024 || (!state.sidebar && !state.reader && !state.stageWidth && !state.sidebarWidth);
        record(key, state.ok && cleared && signature === baselineSignature, state.ok ? {
          width: state.width, height: state.height, scrollHeight: state.scrollHeight, sidebar: state.sidebar,
          activeView: state.activeView, playing: state.playing, beat: state.beat, captionVisible: state.captionVisible,
          nodes: state.nodes.length, cleared, signatureMatches: signature === baselineSignature,
        } : state);
        return state;
      };
      for (const [width, height] of viewports) for (const theme of ['light', 'dark']) {
        const key = `responsive/${width}x${height}-${theme}`;
        await check(key, async () => {
          await send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 768 });
          await navigate(`?theme=${theme === 'light' ? 'dark' : 'light'}`);
          await click('#btn-theme');
          await evaluate('scrollTo(0, 0)');
          await settle();
          const baseline = await inspect(`${key}/overview`);
          const mode = await evaluate('__acceptance.mode()');
          record(`${key}/theme-ui`, await evaluate(`document.documentElement.dataset.theme === ${JSON.stringify(theme)} && /^zh/.test(document.documentElement.lang)`), mode);
          await capture(`${width}x${height}-${theme}-overview`);
          const dockControls = await evaluate(`(async () => {
            const nav = document.querySelector('.diagram-nav'), results = [];
            for (const button of [...nav.querySelectorAll('button')].filter(__acceptance.visible)) {
              button.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
              await new Promise(resolve => requestAnimationFrame(resolve));
              const r = button.getBoundingClientRect(), hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              results.push({ control: button.id || button.dataset.view, reachable: !!hit && button.contains(hit) });
            }
            nav.scrollLeft = 0; scrollTo(0, 0);
            return results;
          })()`);
          record(`${key}/scrollable-dock-controls`, dockControls.length > 0 && dockControls.every(control => control.reachable), dockControls);
          await settle();
          if (width === 320) await check(`${key}/manual-zoom`, async () => {
            const before = await evaluate('__acceptance.zoom()');
            let naturalBefore, nativeScroll = false;
            const scrollState = () => evaluate(`(() => {
              const container = document.querySelector('.diagram-container'), stage = container.querySelector(':scope > .diagram-stage');
              if (!stage) throw new Error('Natural scroll measurement requires .diagram-stage');
              const box = el => { const style = getComputedStyle(el); return {
                rect: el.getBoundingClientRect().toJSON(), clientWidth: el.clientWidth, clientHeight: el.clientHeight,
                scrollLeft: el.scrollLeft, scrollWidth: el.scrollWidth, overflowX: style.overflowX,
                transform: style.transform, clipPath: style.clipPath,
              }; };
              const c = box(container), s = box(stage), style = getComputedStyle(container);
              const paddingLeft = parseFloat(style.paddingLeft), paddingRight = parseFloat(style.paddingRight);
              // Layout stage width, never its transformed SVG/overflow width, defines the natural range.
              const naturalTotalWidth = s.rect.width + paddingLeft + paddingRight;
              const left = Math.max(0, c.rect.left + container.clientLeft, s.rect.left);
              const right = Math.min(innerWidth, c.rect.left + container.clientLeft + c.clientWidth, s.rect.right);
              const top = Math.max(0, c.rect.top + container.clientTop, s.rect.top);
              const bottom = Math.min(innerHeight, c.rect.top + container.clientTop + c.clientHeight, s.rect.bottom);
              const point = { x: (left + right) / 2, y: (top + bottom) / 2 };
              return { ...__acceptance.zoom(), container: c, stage: s, svg: box(__acceptance.svg()),
                paddingLeft, paddingRight, naturalTotalWidth, naturalScrollWidth: Math.max(c.clientWidth, naturalTotalWidth),
                naturalMax: Math.max(0, naturalTotalWidth - c.clientWidth), actualMax: c.scrollWidth - c.clientWidth,
                intersection: { width: Math.max(0, right - left), height: Math.max(0, bottom - top) }, point,
                hitStage: stage.contains(document.elementFromPoint(point.x, point.y)),
              };
            })()`);
            const naturalRangeOK = state => state.stage.rect.width > 0
              && Math.abs(state.container.scrollWidth - state.naturalScrollWidth) <= 1
              && Math.abs(state.naturalTotalWidth - naturalBefore.naturalTotalWidth) <= 1
              && Math.abs(state.naturalMax - naturalBefore.naturalMax) <= 1;
            const wheelTo = async (phase, deltaX) => {
              // Move only the page vertically; horizontal positioning must come from native wheel input.
              await evaluate(`(() => { const r = document.querySelector('.diagram-stage').getBoundingClientRect();
                scrollBy({ top: r.top - Math.max(0, (innerHeight - r.height) / 2), behavior: 'instant' }); })()`);
              await settle();
              const start = await scrollState(), name = `${key}/native-wheel-${phase}-${deltaX > 0 ? 'right' : 'left'}`;
              if (!(start.intersection.width > 0 && start.intersection.height > 0 && start.hitStage)) {
                record(name, false, { before: start, deltaX, error: 'On-screen stage center is not reachable' });
                return;
              }
              await send('Input.dispatchMouseEvent', { type: 'mouseMoved', ...start.point });
              await send('Input.dispatchMouseEvent', { type: 'mouseWheel', ...start.point, deltaX, deltaY: 0, modifiers: 0 });
              await settle();
              const after = await scrollState(), target = deltaX > 0 ? after.naturalMax : 0;
              record(name, naturalRangeOK(start) && naturalRangeOK(after)
                && after.container.scrollLeft >= 0 && after.container.scrollLeft <= after.naturalMax + 1
                && Math.abs(after.container.scrollLeft - target) <= 1
                && after.intersection.width > 0 && after.intersection.height > 0
                && after.percent === start.percent && after.transform === start.transform,
              { deltaX, target, before: start, after });
            };
            const measureZoomScroll = async percent => {
              const state = await scrollState();
              record(`${key}/natural-scroll-${percent}`, state.percent === `${percent}%` && naturalRangeOK(state), state);
              if (nativeScroll) {
                for (const deltaX of [10000, -10000]) await wheelTo(`${percent}`, deltaX);
              }
            };
            const panAcross = async direction => {
              const before = await scrollState();
              const point = await evaluate(`(() => {
                const container = document.querySelector('.diagram-container'), stage = container.querySelector('.diagram-stage');
                const c = container.getBoundingClientRect(), s = stage.getBoundingClientRect();
                const left = Math.max(0, c.left + container.clientLeft, s.left) + 4;
                const right = Math.min(innerWidth, c.left + container.clientLeft + container.clientWidth, s.right) - 4;
                const top = Math.max(0, c.top + container.clientTop, s.top) + 4;
                const bottom = Math.min(innerHeight, c.top + container.clientTop + container.clientHeight, s.bottom) - 4;
                const dx = (right - left) * .6 * ${direction};
                const x = ${direction} > 0 ? left + (right - left) * .1 : right - (right - left) * .1;
                for (let y = top; y < bottom; y += 8) {
                  const hit = document.elementFromPoint(x, y);
                  if (hit && stage.contains(hit) && !hit.closest('.diagram-nav, .focus-chip, .node-finder, .diagram-guide, .overview-map, .route-probe, .semantic-lens, [data-node-id], [data-relationship-hit-key]')) return { x, y, dx };
                }
                throw new Error('No visible blank stage surface for native pan');
              })()`);
              const name = direction > 0 ? 'right' : 'left';
              await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y });
              await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, x: point.x, y: point.y });
              const captured = await evaluate("document.querySelector('.diagram-container').classList.contains('is-panning')");
              try {
                for (let step = 1; step <= 6; step++) await send('Input.dispatchMouseEvent', {
                  type: 'mouseMoved', button: 'left', buttons: 1, x: point.x + point.dx * step / 6, y: point.y,
                });
              } finally {
                await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', buttons: 0, clickCount: 1, x: point.x + point.dx, y: point.y });
              }
              await settle();
              const after = await scrollState();
              record(`${key}/native-pan-${name}`, captured && after.percent === before.percent && after.transform !== before.transform
                && naturalRangeOK(after) && after.intersection.width > 0 && after.intersection.height > 0,
              { captured, point, before, after });
              for (const deltaX of [10000, -10000]) await wheelTo(`after-pan-${name}`, deltaX);
            };
            try {
              naturalBefore = await scrollState();
              nativeScroll = /^(auto|scroll)$/.test(naturalBefore.container.overflowX) && naturalBefore.naturalMax > 0;
              await measureZoomScroll(100);
              for (let step = 1; step <= 28; step++) {
                await scrollClick('.diagram-nav [data-view="in"]');
                if (step === 8 || step === 28) await measureZoomScroll(step === 8 ? 300 : 800);
              }
              if (nativeScroll) for (const direction of [1, -1]) await panAcross(direction);
              const maximum = await evaluate(`(() => {
                const texts = [...__acceptance.svg().querySelectorAll('[data-node-id] text')].filter(__acceptance.visible);
                return { ...__acceptance.zoom(), disabled: document.querySelector('[data-view="in"]').disabled,
                  text: texts.map(el => { const matrix = el.getScreenCTM(); return {
                    node: el.closest('[data-node-id]').dataset.nodeId, text: el.textContent,
                    pixels: parseFloat(getComputedStyle(el).fontSize) * Math.hypot(matrix.c, matrix.d),
                  }; }) };
              })()`);
              record(`${key}/maximum-zoom-readable`, maximum.percent === '800%' && maximum.disabled
                && maximum.text.length > 0 && maximum.text.every(text => text.pixels >= 10), maximum);
              await capture(`${width}x${height}-${theme}-maximum-zoom`);
              if (nativeScroll) {
                const actions = [
                  { name: 'button-out', view: 'out', percent: '775%' },
                  { name: 'button-in', view: 'in', percent: '800%' },
                  { name: 'button-reset', view: 'reset', percent: '100%' },
                  { name: 'key-equals', key: '=', code: 'Equal', vk: 187, percent: '125%' },
                  { name: 'key-plus', key: '+', code: 'Equal', vk: 187, modifiers: 8, percent: '150%' },
                  { name: 'key-zero', key: '0', code: 'Digit0', vk: 48, percent: '100%' },
                ];
                for (const action of actions) {
                  await wheelTo(`before-${action.name}`, 10000);
                  const start = await scrollState();
                  let received;
                  if (action.view) received = await scrollClick(`.diagram-nav [data-view="${action.view}"]`);
                  else {
                    const event = { key: action.key, code: action.code, windowsVirtualKeyCode: action.vk, modifiers: action.modifiers || 0 };
                    await send('Input.dispatchKeyEvent', { type: 'keyDown', ...event, text: action.key });
                    await send('Input.dispatchKeyEvent', { type: 'keyUp', ...event });
                    await settle();
                  }
                  const after = await scrollState();
                  record(`${key}/right-edge-${action.name}`, Math.abs(start.container.scrollLeft - start.naturalMax) <= 1
                    && after.percent === action.percent && naturalRangeOK(after)
                    && after.container.scrollLeft >= 0 && after.container.scrollLeft <= after.naturalMax + 1
                    && after.intersection.width > 0 && (action.percent !== '100%' || after.transform === before.transform),
                  { before: start, after, click: received, key: action.key });
                  if (action.view === 'out') record(`${key}/maximum-zoom-out`, after.percent === '775%', after);
                }
              } else {
                await scrollClick('.diagram-nav [data-view="out"]');
                record(`${key}/maximum-zoom-out`, (await evaluate('__acceptance.zoom()')).percent === '775%', {});
              }
            } finally {
              await scrollClick('.diagram-nav [data-view="reset"]');
              if (naturalBefore) {
                const resetScroll = await scrollState();
                record(`${key}/natural-scroll-after-reset`, resetScroll.percent === '100%' && naturalRangeOK(resetScroll), { before: naturalBefore, after: resetScroll });
              }
              if (nativeScroll) await wheelTo('reset-restore', -10000);
              await evaluate("document.querySelector('.diagram-nav').scrollLeft = 0; scrollTo(0, 0)");
              await settle();
            }
            const reset = await evaluate('__acceptance.zoom()');
            record(`${key}/maximum-zoom-reset`, reset.transform === before.transform && reset.percent === before.percent, { before, reset });
          });
          const chapters = await evaluate("[...document.querySelectorAll('#guided-view-chapters [data-guided-view-id]')].map(el => el.dataset.guidedViewId)");
          assert(expected.story ? chapters.length > 0 : chapters.length === 0, 'Chapter UI does not match native guided-view presence');
          for (const [index, id] of chapters.entries()) await check(`${key}/${id}`, async () => {
            try {
              await scrollClick(`#guided-view-chapters [data-guided-view-id=${JSON.stringify(id)}]`);
              record(`${key}/${id}/chapter-click`, (await inspect(`${key}/${id}/chapter-layout`)).activeView === id, { id });
              await scrollClick('#guided-view-play');
              await wait("document.querySelector('#guided-views').dataset.playing === 'true' && !!document.querySelector('#guided-views').dataset.storyBeat");
              const playing = await inspect(`${key}/${id}/playing-layout`);
              record(`${key}/${id}/play-start`, playing.playing === 'true' && playing.captionVisible,
                { playing: playing.playing, captionVisible: playing.captionVisible, beat: playing.beat });
              const beforeBeat = await evaluate("document.querySelector('#guided-view-play').getBoundingClientRect().toJSON()");
              await wait(`document.querySelector('#guided-views').dataset.storyBeat !== ${JSON.stringify(playing.beat)}`);
              await settle();
              const afterBeat = await evaluate("document.querySelector('#guided-view-play').getBoundingClientRect().toJSON()");
              record(`${key}/${id}/stable-play-control`, Math.abs(beforeBeat.x - afterBeat.x) <= 1 && Math.abs(beforeBeat.y - afterBeat.y) <= 1,
                { beforeBeat, afterBeat });
              const pauseClick = await scrollClick('#guided-view-play');
              const paused = await inspect(`${key}/${id}/paused-layout`);
              record(`${key}/${id}/pause`, paused.playing === 'false' && paused.captionVisible,
                { playing: paused.playing, captionVisible: paused.captionVisible, beat: paused.beat, click: pauseClick });
              await evaluate('new Promise(resolve => setTimeout(resolve, 1600))');
              const held = await inspect(`${key}/${id}/paused-held-layout`);
              record(`${key}/${id}/pause-held`, held.playing === 'false' && held.captionVisible && held.beat === paused.beat,
                { before: paused.beat, after: held.beat, playing: held.playing });
              if (index === 0) await capture(`${width}x${height}-${theme}-story`);
            } finally {
              if (await evaluate("document.querySelector('#guided-views').dataset.playing === 'true'")) await scrollClick('#guided-view-play');
              if (await evaluate("document.querySelector('#guided-views').dataset.activeView !== 'all'")) await scrollClick('#guided-view-all');
              await evaluate('scrollTo(0, 0)');
              await settle();
            }
            const after = await inspect(`${key}/${id}/restored-layout`);
            record(`${key}/${id}/restored`, after.activeView === 'all' && !after.captionVisible &&
              after.sidebar === baseline.sidebar && after.stageWidth === baseline.stageWidth && after.sidebarWidth === baseline.sidebarWidth &&
              Math.abs(after.scrollHeight - baseline.scrollHeight) <= 1, { before: baseline.scrollHeight, after: after.scrollHeight });
          });
        });
      }
      if (sweep) await check('responsive/breakpoint-sweep', async () => {
        await navigate('?theme=light');
        const breakpoints = [360, 420, 720, 768, 1024];
        const widths = breakpoints.flatMap((point) => Array.from({ length: 33 }, (_, i) => point - 16 + i));
        report.responsive_breakpoints = breakpoints;
        for (const width of [...widths, ...[...widths].reverse()]) {
          await send('Emulation.setDeviceMetricsOverride', { width, height: 900, deviceScaleFactor: 1, mobile: false });
          await evaluate('scrollTo(0, 0)');
          await settle();
          await inspect(`sweep/${width}x900/${report.checks.length}`);
        }
        for (const height of [568, 640, 720, 768, 800, 899, 900, 901, 920, 921, 1000, 1080, 1100, 1101]) {
          await send('Emulation.setDeviceMetricsOverride', { width: 1440, height, deviceScaleFactor: 1, mobile: false });
          await evaluate('scrollTo(0, 0)');
          await settle();
          await inspect(`sweep/1440x${height}`);
        }
      });
      record('responsive-matrix-complete', report.screenshots.filter((image) => image.key?.endsWith('-overview')).length === viewports.length * 2,
        { expected: viewports.length * 2, shard: report.shard });
    }
    record('local-http-artifact-served', report.server_requests.some((r) => r.status === 200), report.server);
  } catch (error) {
    report.harness_errors.push(errorText(error));
  } finally {
    try {
      await Promise.allSettled([...pending]);
      closing = true;
      if (browser) await browser.close();
    } catch (error) { report.harness_errors.push(`Chrome cleanup: ${errorText(error)}`); }
    finally {
      removeObserver?.();
      if (server) {
        server.closeAllConnections();
        await new Promise((resolve) => server.close(resolve));
      }
      process.off('SIGINT', onSignal);
      process.off('SIGTERM', onSignal);
    }
    try {
      const after = fs.readFileSync(options.artifact);
      record('artifact-unchanged', sha256(after) === report.artifact.sha256, { sha256_after: sha256(after) });
    } catch (error) { record('artifact-unchanged', false, errorText(error)); }
    if (report.model.sha256) {
      try {
        const after = sha256(fs.readFileSync(report.model.path));
        record('model-unchanged', after === report.model.sha256, { sha256_after: after });
      } catch (error) { record('model-unchanged', false, errorText(error)); }
    }
    record('no-external-requests', report.external_requests.length === 0, { count: report.external_requests.length });
    record('no-rejected-dependencies', report.rejected_local_requests.length === 0 && report.policy_violations.length === 0, { local: report.rejected_local_requests.length, policy: report.policy_violations.length });
    record('no-console-errors', report.console_errors.length === 0, { count: report.console_errors.length });
    record('harness-completed', !aborted && report.harness_errors.length === 0, report.harness_errors);
    report.automated_status = report.checks.some((c) => c.status === 'fail') ? 'fail' : 'pass';
    report.finished_at = new Date().toISOString();
    persist('report.json', JSON.stringify(report, null, 2) + '\n');
  }
  console.log(JSON.stringify({ automated_status: report.automated_status, visual_review: report.visual_review, checks: report.checks.length, failures: report.checks.filter((c) => c.status === 'fail').map((c) => c.name), report: path.join(options.outdir, 'report.json') }));
  return report.automated_status === 'pass' ? 0 : 1;
}

export { argumentsFor, expectedStructure, installPageTools, installObserver, observePipe, OFFLINE_CSP, run };

if (process.argv[1] && fs.realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const options = argumentsFor(process.argv.slice(2));
    if (!options) console.log(USAGE);
    else process.exitCode = await run(options);
  } catch (error) {
    console.error(`browser_acceptance: ${errorText(error)}`);
    process.exitCode = 1;
  }
}
