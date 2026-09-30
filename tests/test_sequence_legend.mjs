import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, sep } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { translateMessage } from '../modules/archify/renderers/shared/i18n.mjs';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const RENDERER = fileURLToPath(new URL('../modules/archify/renderers/sequence/render-sequence.mjs', import.meta.url));

function render(diagram) {
  const parent = realpathSync(tmpdir());
  const fromSkill = relative(realpathSync(ROOT), parent);
  assert.ok(isAbsolute(fromSkill) || fromSkill === '..' || fromSkill.startsWith(`..${sep}`),
    'renderer inputs and outputs must stay outside the skill');
  const directory = mkdtempSync(join(parent, 'architecture-diagram-sequence-legend-'));
  try {
    const input = join(directory, 'input.sequence.json');
    const output = join(directory, 'output.html');
    writeFileSync(input, JSON.stringify(diagram));
    // Direct local renderer only: no browser, inherited preload, icon registry,
    // repository evidence root, package manager, or wrapper side effects.
    const env = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !/^(?:NODE_|ARCHIFY_|ARCHITECTURE_DIAGRAM_|ICON_PERSONAL_ROOT$|ARCH_ICONS_ROOT$)/.test(key)));
    const result = spawnSync(process.execPath, [RENDERER, input, output], {
      cwd: directory, env, encoding: 'utf8', timeout: 30_000, maxBuffer: 4 * 1024 * 1024,
    });
    assert.ifError(result.error);
    assert.equal(result.status, 0, `renderer rejected fixture:\n${result.stdout}\n${result.stderr}`);
    assert.equal(result.stdout.trim(), output, 'CLI must honor the explicit temporary output');
    const html = readFileSync(output, 'utf8');
    assert.match(html, /<!doctype html>/i);
    // These built-ins have no nested SVGs/brand images. Ignore toolbar SVGs
    // and runtime JS; inspect only the canonical, renderer-produced diagram.
    const svgs = [...html.matchAll(/<svg\b[^>]*\baria-labelledby="archify-diagram-title archify-diagram-description"[^>]*>[\s\S]*?<\/svg>/g)];
    assert.equal(svgs.length, 1);
    return svgs[0][0];
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

function attributes(tag) {
  return Object.fromEntries([...tag.matchAll(/\s([\w:-]+)="([^"]*)"/g)].map((match) => [match[1], match[2]]));
}

function tags(svg, name) {
  return [...svg.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g'))].map((match) => attributes(match[0]));
}

function texts(svg) {
  return [...svg.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/g)]
    .map((match) => ({ ...attributes(match[1]), text: match[2] }));
}

function section(svg, name) {
  const matches = [...svg.matchAll(new RegExp(`<!-- ${name} -->([\\s\\S]*?)(?=<!--|</svg>)`, 'g'))];
  assert.equal(matches.length, 1, `expected one ${name} section`);
  return matches[0][1];
}

function assertOriginalGeometry(svg, diagram, fixture) {
  const [width, height] = fixture.viewBox;
  assert.deepEqual(diagram.meta.viewBox, fixture.viewBox);
  assert.equal(tags(svg, 'svg')[0].viewBox, `0 0 ${width} ${height}`);
  const centers = diagram.participants.map((_, index) => fixture.leftX + index * fixture.colGap);
  const lifelines = tags(section(svg, 'Lifelines'), 'path');
  assert.deepEqual(lifelines.map((line) => line.d), centers.map((x) => `M ${x} 142 L ${x} ${height - 65}`),
    'every lifeline retains its original start, end and column');
  for (const line of lifelines) {
    assert.equal(line['stroke-width'], '0.8');
    assert.equal(line['stroke-dasharray'], '3,7');
  }

  const messages = section(svg, 'Messages');
  const routes = tags(messages, 'path');
  assert.equal(routes.length, diagram.messages.length);
  const labels = texts(messages).filter((label) => label['font-size'] === '9');
  assert.equal(labels.length, diagram.messages.length);
  diagram.messages.forEach((message, index) => {
    const from = centers[diagram.participants.findIndex(({ id }) => id === message.from)];
    const to = centers[diagram.participants.findIndex(({ id }) => id === message.to)];
    const direction = to > from ? 1 : -1;
    const start = from + direction * 7;
    const end = to - direction * 7;
    assert.equal(routes[index].d, `M ${start} ${message.y} L ${end} ${message.y}`, message.label);
    assert.equal(routes[index]['data-composition-edge-from'], message.from);
    assert.equal(routes[index]['data-composition-edge-to'], message.to);
    assert.equal(routes[index]['data-composition-edge-id'], message.id);
    assert.equal(labels[index].text, message.label);
    assert.ok(Math.abs(Number(labels[index].x) - (start + end) / 2) < 1e-9);
    assert.equal(Number(labels[index].y), message.y - 10, 'authored message label baseline');
  });

  assert.deepEqual(tags(section(svg, 'Time Segments'), 'rect').map((rect) =>
    [Number(rect.x), Number(rect.y), Number(rect.width), Number(rect.height)]),
  diagram.segments.map((segment) => [48, segment.from, width - 96, segment.to - segment.from]),
  'authored segment frames must not move or shrink');
  const participants = section(svg, 'Participants');
  assert.deepEqual(tags(participants, 'rect').filter((rect) => rect.class === 'c-mask').map((rect) =>
    [Number(rect.x), Number(rect.y), Number(rect.width), Number(rect.height)]),
  centers.map((x) => [x - fixture.participantW / 2, 72, fixture.participantW, 54]));
  assert.deepEqual(texts(participants).map((text) => [text.text, text['font-size'], Number(text.y)]),
    diagram.participants.flatMap((participant) => [[participant.label, '11', 99], [participant.sublabel, '7', 111]]),
    'participant labels and sublabels retain their original font sizes and baselines');
  return lifelines;
}

function assertLegend(svg, diagram, lifelines, locale) {
  const height = diagram.meta.viewBox[1];
  const legend = section(svg, 'Legend');
  assert.deepEqual(tags(legend, 'g')[0], { 'data-legend': '' }, 'visible legend root must remain');
  const kinds = ['emphasis', 'return', 'security', 'dashed', 'default']
    .filter((kind) => diagram.messages.some((message) => (message.variant || 'default') === kind));
  const entries = tags(legend, 'g').filter((group) => Object.hasOwn(group, 'data-legend-semantic-kind'));
  assert.deepEqual(entries.map((entry) => entry['data-legend-semantic-kind']), kinds, 'retain every original legend kind');
  assert.equal(tags(legend, 'path').length, kinds.length, 'retain every legend swatch');
  const labels = texts(legend);
  assert.deepEqual(labels.map((label) => label.text), [translateMessage(locale, 'legend.title'),
    ...kinds.map((kind) => translateMessage(locale, `legend.sequence.${kind}`))]);
  assert.deepEqual(labels.map((label) => label['font-size']), ['12', ...kinds.map(() => '10')]);
  for (const attrs of [...tags(legend, 'g'), ...tags(legend, 'path'), ...labels]) {
    for (const hidden of ['hidden', 'display', 'visibility', 'opacity', 'style', 'transform']) {
      assert.equal(attrs[hidden], undefined, `legend must not bypass clearance with ${hidden}`);
    }
  }

  // Shared legend measurement uses baseline - 10 through baseline + 4 for
  // text bounds; its title baseline is 20px above the item baseline.
  const top = Math.min(...labels.map((label) => Number(label.y) - 10));
  for (const line of lifelines) {
    const bottom = Number(line.d.trim().split(/\s+/).at(-1));
    assert.ok(top > bottom, `legend top ${top} must clear lifeline bottom ${bottom}`);
    assert.equal(top - bottom, 11, 'preserve the 11px timeline/legend gap');
  }
  assert.equal(top, height - 54);
  assert.equal(Number(labels[0].y), height - 44);
  for (const label of labels.slice(1)) assert.equal(Number(label.y), height - 24);
  for (const entry of entries) assert.equal(Number(entry['data-legend-baseline']), height - 24);
  assert.ok(labels.every((label) => Number(label.y) + 4 <= height), 'bottom text estimate stays inside the viewBox');
}

// Pin the existing spread/fixed column geometry independently of the renderer.
const fixtures = [
  { name: 'cache-miss-request', viewBox: [1080, 560], participantW: 113, leftX: 118.5, colGap: 865 / 6 },
  { name: 'async-job-roundtrip', viewBox: [820, 920], participantW: 86, leftX: 62, colGap: 108 },
];
for (const fixture of fixtures) {
  for (const locale of ['en', 'zh-CN']) {
    test(`${fixture.name} (${locale}): visible legend clears unchanged timeline and fits viewBox`, () => {
      const diagram = JSON.parse(readFileSync(new URL(`../modules/archify/examples/${fixture.name}.sequence.json`, import.meta.url), 'utf8'));
      // Keep the default English built-in untouched; localize only the in-memory copy.
      if (locale !== 'en') diagram.meta.locale = locale;
      const svg = render(diagram);
      assert.equal(tags(svg, 'svg')[0].lang, locale);
      assertLegend(svg, diagram, assertOriginalGeometry(svg, diagram, fixture), locale);
    });
  }
}
