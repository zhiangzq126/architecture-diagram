import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { isAbsolute, join, relative, sep } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const ROOT = fileURLToPath(new URL('../', import.meta.url));
const RENDERER = fileURLToPath(new URL('../modules/archify/renderers/lifecycle/render-lifecycle.mjs', import.meta.url));

function example(name) {
  return JSON.parse(readFileSync(new URL(`../modules/archify/examples/${name}.lifecycle.json`, import.meta.url), 'utf8'));
}

function render(diagram) {
  const parent = realpathSync(tmpdir());
  const fromSkill = relative(realpathSync(ROOT), parent);
  assert.ok(isAbsolute(fromSkill) || fromSkill === '..' || fromSkill.startsWith(`..${sep}`),
    'renderer inputs and outputs must stay outside the skill');
  const directory = mkdtempSync(join(parent, 'architecture-diagram-lifecycle-rail-'));
  try {
    const input = join(directory, 'input.lifecycle.json');
    const output = join(directory, 'output.html');
    writeFileSync(input, JSON.stringify(diagram));
    // Direct renderer CLI: schema/layout validation is real, with no browser,
    // package manager, inherited preload, external icon registry, or repo root.
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
    // Restrict inspection to the canonical diagram, not toolbar icons or JS
    // strings. These fixtures contain no nested SVG/brand images.
    const svgs = [...html.matchAll(/<svg\b[^>]*\baria-labelledby="archify-diagram-title archify-diagram-description"[^>]*>[\s\S]*?<\/svg>/g)];
    assert.equal(svgs.length, 1, 'expected one generated lifecycle SVG');
    return svgs[0][0];
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

// The renderer emits double-quoted attributes; no DOM/browser dependency is
// needed to inspect its controlled SVG markup.
function attributes(tag) {
  return Object.fromEntries([...tag.matchAll(/\s([\w:-]+)="([^"]*)"/g)].map((match) => [match[1], match[2]]));
}

function tags(svg, name) {
  return [...svg.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g'))].map((match) => attributes(match[0]));
}

function stateRects(svg, diagram) {
  const rects = new Map();
  for (const match of svg.matchAll(/<g\b([^>]*\bdata-node-id="[^"]+"[^>]*)>([\s\S]*?)<\/g>/g)) {
    const id = attributes(match[1])['data-node-id'];
    const masks = tags(match[2], 'rect').filter((rect) => rect.class === 'c-mask');
    assert.equal(masks.length, 1, `state ${id} needs its actual masking rectangle`);
    const rect = Object.fromEntries(['x', 'y', 'width', 'height'].map((key) => [key, Number(masks[0][key])]));
    assert.ok(Object.values(rect).every(Number.isFinite), `finite bounds for ${id}`);
    assert.ok(!rects.has(id), `state ${id} must not be duplicated`);
    rects.set(id, rect);
  }
  assert.deepEqual([...rects.keys()].sort(), diagram.states.map((state) => state.id).sort());
  return rects;
}

function pathCommands(d) {
  assert.equal(typeof d, 'string', 'rail has path geometry');
  const tokens = d.trim().split(/[\s,]+/);
  const commands = [];
  while (tokens.length) {
    const command = tokens.shift();
    assert.ok(['M', 'L', 'Q'].includes(command), `unexpected rail command ${command}: ${d}`);
    const count = command === 'Q' ? 4 : 2;
    const numbers = tokens.splice(0, count).map(Number);
    assert.equal(numbers.length, count, `incomplete path command: ${d}`);
    assert.ok(numbers.every(Number.isFinite), `non-finite path: ${d}`);
    const points = [];
    for (let i = 0; i < numbers.length; i += 2) points.push(numbers.slice(i, i + 2));
    commands.push({ command, points });
  }
  assert.equal(commands[0]?.command, 'M');
  assert.equal(commands.filter(({ command }) => command === 'M').length, 1, 'each rail is one path');
  assert.equal(commands.at(-1)?.command, 'L', 'rail ends in an explicit line segment');
  return commands;
}

function assertTransitions(svg, diagram) {
  const hasEdgeAttrs = (attrs) => Object.keys(attrs).some((key) => key.startsWith('data-edge-'));
  const identity = (attrs) => ({
    id: attrs['data-edge-id'], key: attrs['data-edge-key'],
    from: attrs['data-edge-from'], to: attrs['data-edge-to'], label: attrs['data-edge-label'],
  });
  const expected = diagram.transitions.map((transition, index) => ({
    id: transition.id, key: String(index), from: transition.from, to: transition.to,
    label: transition.label || undefined,
  }));
  const paths = tags(svg, 'path').filter(hasEdgeAttrs);
  assert.equal(paths.length, diagram.transitions.length, 'rails must not add business transitions');
  assert.deepEqual(paths.map(identity), expected, 'preserve transition IDs, keys, endpoints and order');
  assert.deepEqual(tags(svg, 'g').filter(hasEdgeAttrs).map(identity), expected.filter(({ label }) => label),
    'label groups retain the same transition identities');
  return paths;
}

function assertRails(svg, diagram) {
  const phases = diagram.states.filter((state) => state.lane === 'main').sort((a, b) => a.col - b.col);
  const rails = tags(svg, 'path').filter((attrs) => Object.hasOwn(attrs, 'data-lifecycle-rail'));
  assert.equal(rails.length, Math.max(0, phases.length - 1), 'exactly N-1 separate main-phase rails');
  const rects = stateRects(svg, diagram);
  assertTransitions(svg, diagram);
  if (!rails.length) return rails;

  const markers = [...svg.matchAll(/<marker\b([^>]*)>([\s\S]*?)<\/marker>/g)]
    .filter((match) => attributes(match[1]).id === 'arrowhead-emphasis');
  assert.equal(markers.length, 1, 'emphasis marker must resolve inside the generated SVG');
  const marker = attributes(markers[0][1]);
  assert.equal(marker.orient, 'auto');
  assert.equal(marker.markerUnits || 'strokeWidth', 'strokeWidth');
  assert.equal(marker.viewBox, undefined, 'marker coordinates are unscaled viewport units');
  const polygons = tags(markers[0][2], 'polygon');
  assert.equal(polygons.length, 1);
  assert.equal(polygons[0].class, 'm-emphasis');
  const polygon = polygons[0].points.trim().split(/[\s,]+/).map(Number);
  assert.deepEqual(polygon, [0, 0, 10, 3.5, 0, 7], 'marker triangle points forward');
  const tipX = polygon[2];
  assert.equal(Number(marker.refY), polygon[3], 'tip lies on the path centerline');
  assert.ok(tipX > Number(marker.refX));
  assert.ok(Number(marker.markerWidth) >= tipX && Number(marker.markerHeight) >= polygon[5],
    'marker viewport must not clip the arrowhead');

  rails.forEach((rail, index) => {
    const from = rects.get(phases[index].id);
    const to = rects.get(phases[index + 1].id);
    const pair = `${phases[index].id} -> ${phases[index + 1].id}`;
    assert.equal(rail.class, 'a-emphasis', pair);
    assert.equal(rail['stroke-width'], '2.2', pair);
    assert.equal(rail['marker-end'], 'url(#arrowhead-emphasis)', pair);
    assert.equal(rail['marker-start'], undefined, pair);
    assert.ok(!Object.keys(rail).some((key) => key.startsWith('data-edge-')), `${pair}: decorative, not a business edge`);
    assert.equal(rail['data-composition-points'], undefined, pair);
    assert.equal(rail['data-animate'], undefined, `${pair}: not a transition animation step`);

    const commands = pathCommands(rail.d);
    const points = commands.flatMap(({ points }) => points);
    const start = points[0];
    const end = points.at(-1);
    const previous = commands.at(-2).points.at(-1);
    const horizontal = from.x + from.width < to.x;
    const down = to.y > from.y;
    const axis = horizontal ? 0 : 1;
    const direction = horizontal || down ? 1 : -1;
    const destination = horizontal ? to.x : down ? to.y : to.y + to.height;
    const expectedStart = horizontal ? [from.x + from.width, from.y + from.height / 2]
      : [from.x + from.width / 2, down ? from.y + from.height : from.y];
    const expectedEnd = horizontal ? [to.x - 3, to.y + to.height / 2]
      : [to.x + to.width / 2, destination - 3 * direction];
    assert.deepEqual(start, expectedStart, `${pair}: source anchor faces the gap`);
    assert.deepEqual(end, expectedEnd, `${pair}: destination anchor leaves 3px for the marker tip`);
    assert.ok(direction * (end[axis] - start[axis]) > 0, `${pair}: rail must advance toward destination, got ${rail.d}`);
    assert.equal(previous[1 - axis], end[1 - axis], `${pair}: final segment is orthogonal`);
    assert.ok(direction * (end[axis] - previous[axis]) > 0, `${pair}: final tangent points into destination`);
    assert.ok(points.every((point) => direction * (point[axis] - start[axis]) >= 0 && direction * (end[axis] - point[axis]) >= 0),
      `${pair}: curve control points remain in the inter-node gap`);
    for (let i = 1; i < points.length; i += 1) {
      assert.ok(direction * (points[i][axis] - points[i - 1][axis]) >= 0, `${pair}: path never doubles back`);
    }
    const renderedTip = end[axis] + direction * (tipX - Number(marker.refX)) * Number(rail['stroke-width']);
    assert.ok(direction * (destination - renderedTip) > 0, `${pair}: actual arrowhead tip remains outside the destination mask`);
  });
  return rails;
}

function fixture(states, transitions = []) {
  return {
    schema_version: 1, diagram_type: 'lifecycle', meta: { title: 'Lifecycle rail regression' },
    lanes: [{ id: 'main', label: 'Phases' }, { id: 'waiting', label: 'Waits' }, { id: 'terminal', label: 'Outcomes' }],
    states, transitions,
  };
}

function phase(id, col, geometry = {}) {
  return { id, type: 'active', label: id, lane: 'main', col, ...geometry };
}

for (const name of ['agent-run', 'deployment-release']) {
  test(`${name} example renders four visible forward rails without changing its six transitions`, () => {
    const diagram = example(name);
    assert.equal(diagram.states.filter((state) => state.lane === 'main').length, 5);
    assert.equal(diagram.transitions.length, 6);
    assert.equal(assertRails(render(diagram), diagram).length, 4);
  });
}

for (const mainCount of [0, 1]) {
  test(`${mainCount} main phase emits no decorative rail and preserves other lanes and transitions`, () => {
    // states.minItems is 2 and the renderer requires the main lane to exist;
    // zero/one main phase does not mean an invalid zero/one-state document.
    const diagram = fixture([
      ...(mainCount ? [phase('phase', 2)] : []),
      { id: 'wait', type: 'waiting', label: 'Wait', lane: 'waiting', col: 0 },
      { id: 'done', type: 'success', label: 'Done', lane: 'terminal', col: 0 },
    ], [
      ...(mainCount ? [{ id: 'phase-wait', from: 'phase', to: 'wait', fromSide: 'bottom', toSide: 'top' }] : []),
      { id: 'wait-done', from: 'wait', to: 'done', label: 'Finish', labelAt: [450, 394], fromSide: 'bottom', toSide: 'top' },
    ]);
    assert.equal(assertRails(render(diagram), diagram).length, 0);
  });
}

test('unsorted, interleaved states connect adjacent columns without reordering business transitions', () => {
  const diagram = example('agent-run');
  const ordered = render(diagram);
  const byId = new Map(diagram.states.map((state) => [state.id, state]));
  diagram.states = ['completed', 'approval', 'executing', 'cancelled', 'queued', 'failed', 'reviewing', 'expired', 'planning', 'blocked']
    .map((id) => byId.get(id));
  const shuffled = render(diagram);
  assert.deepEqual(assertRails(shuffled, diagram), assertRails(ordered, diagram), 'phase order comes from col, not input order');
  assert.deepEqual(assertTransitions(shuffled, diagram), assertTransitions(ordered, diagram),
    'transition geometry and attributes are unchanged by phase sorting');
});

test('two phases in nonconsecutive columns produce one rail and no synthetic transition', () => {
  const diagram = fixture([phase('last', 4), phase('first', 1)]);
  assert.equal(assertRails(render(diagram), diagram).length, 1);
});

test('valid custom widths, heights and signed fractional yOffsets use actual anchors and rounded bends', () => {
  const diagram = fixture([
    phase('A', 0, { width: 48, height: 36, yOffset: 0 }),
    phase('B', 2, { width: 180.5, height: 72.5, yOffset: 36.25 }),
    phase('C', 4, { width: 140, height: 96, yOffset: -20 }),
  ]);
  const svg = render(diagram);
  const rects = stateRects(svg, diagram);
  for (const state of diagram.states) {
    const rect = rects.get(state.id);
    assert.equal(rect.width, state.width);
    assert.equal(rect.height, state.height);
    assert.equal(rect.y, 126 + state.yOffset);
  }
  const rails = assertRails(svg, diagram);
  for (const rail of rails) {
    assert.equal(pathCommands(rail.d).filter(({ command }) => command === 'Q').length, 2,
      'both upward and downward doglegs retain rounded corners');
  }
});

for (const offsets of [[0, 100], [100, 0]]) {
  test(`wide phases with yOffsets ${offsets} have forward, outside-destination rails`, () => {
    const diagram = fixture([
      phase('wideA', 1, { width: 200, height: 62, yOffset: offsets[0] }),
      phase('wideB', 2, { width: 200, height: 62, yOffset: offsets[1] }),
    ]);
    assertRails(render(diagram), diagram);
  });
}
