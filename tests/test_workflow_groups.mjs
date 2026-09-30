import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { compileWorkflow } from '../modules/archify/renderers/workflow/workflow-compiler.mjs';

function example(name) {
  return JSON.parse(readFileSync(new URL(`../modules/archify/examples/${name}.workflow.json`, import.meta.url), 'utf8'));
}

function compile(workflow) {
  const before = structuredClone(workflow);
  const result = compileWorkflow({ workflow });
  assert.equal(result.ok, true, result.error || JSON.stringify(result.diagnostics));
  assert.deepEqual(workflow, before, 'compilation must not rewrite the authored workflow');
  assert.deepEqual(result.receipt.diagnostics, []);
  assert.equal(typeof result.svg, 'string');
  return result;
}

function attributes(tag) {
  return Object.fromEntries([...tag.matchAll(/\s([\w:-]+)="([^"]*)"/g)].map((match) => [match[1], match[2]]));
}

function textElements(svg) {
  return [...svg.matchAll(/<text\b([^>]*)>([\s\S]*?)<\/text>/g)]
    .map((match) => ({ ...attributes(match[1]), text: match[2] }));
}

function groupLabels(svg) {
  return textElements(svg).filter((label) => Object.hasOwn(label, 'data-group-label'));
}

function groupFrames(svg) {
  return [...svg.matchAll(/<rect\b[^>]*>/g)]
    .map((match) => attributes(match[0]))
    .filter((rect) => rect['data-composition-frame-kind'] === 'group');
}

function point(label) {
  const coordinates = [Number(label.x), Number(label.y)];
  assert.ok(coordinates.every(Number.isFinite));
  return coordinates;
}

function overlaps(a, b) {
  return a.x < b.x + b.width && a.x + a.width > b.x
    && a.y < b.y + b.height && a.y + a.height > b.y;
}

function assertGroupText(svg, workflow) {
  const labels = groupLabels(svg);
  assert.equal(labels.length, workflow.groups.length, 'one marked text element per group');
  assert.deepEqual(labels.map((label) => label['data-group-label']), workflow.groups.map((group) => group.id));
  assert.deepEqual(labels.map((label) => label.text), workflow.groups.map((group) => group.label));
  for (const label of labels) {
    assert.equal(label['font-size'], '7');
    assert.equal(label['font-weight'], '600');
    assert.equal(label['text-anchor'] ?? 'start', 'start');
  }
  return labels;
}

test('incident groups retain both original labels at the fixed-v1 regression coordinates', () => {
  const workflow = example('incident-response');
  assert.equal(workflow.schema_version, 1);
  assert.equal(workflow.meta.quality_profile, 'showcase');
  assert.deepEqual(workflow.groups.map(({ id, label }) => ({ id, label })), [
    { id: 'command', label: 'Incident command' },
    { id: 'exception_actions', label: 'If impact persists' },
  ]);
  const { svg, receipt } = compile(workflow);
  assert.equal(receipt.contract, 'fixed-v1');
  const labels = assertGroupText(svg, workflow);
  assert.deepEqual(labels.map(point), [[232, 205], [442, 708]]);
  assert.deepEqual(groupFrames(svg).map((frame) => [Number(frame.x), Number(frame.y), Number(frame.width), Number(frame.height)]), [
    [170, 207, 310, 72],
    [380, 710, 295, 58],
  ]);
});

test('fixed-v1 group frames contain every member node with 2 SVG units of padding', () => {
  const workflow = example('incident-response');
  const { svg, receipt } = compile(workflow);
  const frames = groupFrames(svg);
  const labels = groupLabels(svg);
  for (const [index, group] of workflow.groups.entries()) {
    const frame = frames[index];
    const members = receipt.nodes.filter((node) => node.lane === group.lane
      && node.col >= group.fromCol && node.col <= group.toCol);
    assert.ok(members.length > 0);
    assert.equal(Number(labels[index].y), Number(frame.y) - 2);
    for (const node of members) {
      assert.ok(Number(frame.y) <= node.y - 2, `${group.id} must pad ${node.id} above`);
      assert.ok(Number(frame.y) + Number(frame.height) >= node.y + node.height + 2,
        `${group.id} must pad ${node.id} below`);
    }
  }
});

test('fixed-v1 frames use inclusive member columns and ignore other columns and lanes', () => {
  for (const [col, expected] of [[1, [170, 214, 100, 58]], [3, [380, 207, 100, 72]]]) {
    const workflow = example('incident-response');
    workflow.groups = [{ ...workflow.groups[0], fromCol: col, toCol: col }];
    const { svg } = compile(workflow);
    const [frame] = groupFrames(svg);
    assert.deepEqual([Number(frame.x), Number(frame.y), Number(frame.width), Number(frame.height)], expected);
    assert.deepEqual(point(groupLabels(svg)[0]), [expected[0] + 62, expected[1] - 2]);
  }
});

test('incident group label box estimates clear page, escalate and every other node', () => {
  const workflow = example('incident-response');
  const { svg, receipt } = compile(workflow);
  const labels = assertGroupText(svg, workflow);
  assert.equal(receipt.nodes.length, workflow.nodes.length);
  assert.ok(receipt.nodes.some(({ id }) => id === 'page'));
  assert.ok(receipt.nodes.some(({ id }) => id === 'escalate'));
  for (const label of labels) {
    assert.match(label.text, /^[\x20-\x7e]+$/, 'this estimate covers the original ASCII incident labels');
    const [x, y] = point(label);
    const box = { x, y: y - 10, width: label.text.length * 5.6, height: 14 };
    for (const node of receipt.nodes) {
      assert.equal(overlaps(box, node), false, `${label['data-group-label']} overlaps ${node.id}`);
    }
  }
});

test('incident groups do not move nodes, edges, edge labels or the viewBox', () => {
  const workflow = example('incident-response');
  const withGroups = compile(workflow);
  const withoutGroups = compile({ ...structuredClone(workflow), groups: [] });
  assert.equal(withoutGroups.receipt.contract, 'fixed-v1');
  assert.equal(groupLabels(withoutGroups.svg).length, 0);
  assert.equal(groupFrames(withoutGroups.svg).length, 0);
  assert.equal(withGroups.receipt.edges.length, workflow.edges.length);
  assert.equal(withGroups.receipt.labels.length, workflow.edges.filter(({ label }) => label).length);
  for (const key of ['nodes', 'edges', 'labels', 'viewBox']) {
    assert.deepEqual(withGroups.receipt[key], withoutGroups.receipt[key], `${key} must not drift when groups are present`);
  }
  assert.deepEqual(withGroups.receipt.viewBox, [720, 900]);
  assert.match(withGroups.svg, /<svg\b[^>]*\bviewBox="0 0 720 900"/);
});

test('Declare keeps its existing tag and 68px node height', () => {
  const workflow = example('incident-response');
  const authored = workflow.nodes.find(({ id }) => id === 'declare');
  assert.equal(authored.label, 'Declare');
  assert.equal(authored.tag, 'SEV-1/2');
  assert.equal(authored.height, undefined);
  const { svg, receipt } = compile(workflow);
  assert.deepEqual(receipt.nodes.find(({ id }) => id === 'declare'), {
    id: 'declare', lane: 'responders', col: 3, x: 384, y: 209, width: 92, height: 68,
    type: 'security', label: 'Declare', sublabel: 'assign commander', tag: 'SEV-1/2',
  });
  assert.match(svg, /<text\b[^>]*data-detail="fine"[^>]*\bx="430"[^>]*\by="265"[^>]*>SEV-1\/2<\/text>/);
});

test('node subtitles use 14 SVG units of leading without changing titles, fonts or tags', () => {
  for (const name of ['incident-response', 'agent-tool-call']) {
    const workflow = example(name);
    const { svg, receipt } = compile(workflow);
    const texts = textElements(svg);
    const titles = texts.filter((text) => Object.hasOwn(text, 'data-node-label'));
    const subtitles = texts.filter((text) => text['data-detail'] === 'context');
    const tags = texts.filter((text) => text['data-detail'] === 'fine');
    assert.equal(titles.length, workflow.nodes.length);
    assert.equal(subtitles.length, workflow.nodes.filter((node) => node.sublabel).length);
    assert.equal(tags.length, workflow.nodes.filter((node) => node.tag).length);
    for (const authored of workflow.nodes) {
      const node = receipt.nodes.find(({ id }) => id === authored.id);
      const title = titles.find((text) => text.text === authored.label);
      const subtitle = subtitles.find((text) => text.text === authored.sublabel);
      assert.ok(title && subtitle, `${name}/${node.id} retains both text lines`);
      assert.deepEqual(point(title), [node.x + node.width / 2, node.y + 27]);
      assert.deepEqual(point(subtitle), [node.x + node.width / 2, node.y + 41]);
      assert.equal(Number(subtitle.y) - Number(title.y), 14);
      assert.equal(title['font-size'], { page: '9.4', recover: '10.4', update: '10.7' }[node.id] ?? '11');
      assert.equal(subtitle['font-size'], node.id === 'triage' ? '7.7' : '8');
      assert.equal(title['font-weight'], '600');
      assert.equal(title['text-anchor'], 'middle');
      assert.equal(subtitle['text-anchor'], 'middle');
      if (authored.tag) {
        const tag = tags.find((text) => text.text === authored.tag);
        assert.ok(tag, `${name}/${node.id} retains its tag`);
        assert.deepEqual(point(tag), [node.x + node.width / 2, node.y + node.height - 12]);
        assert.equal(tag['font-size'], '7');
      }
    }
  }
});

test('built-in schema2 groups retain label positioning relative to their frames', () => {
  const workflow = example('agent-tool-call');
  assert.equal(workflow.schema_version, 2);
  assert.equal(workflow.meta.quality_profile, 'showcase');
  assert.equal(workflow.groups.length, 4);
  const { svg, receipt } = compile(workflow);
  assert.equal(receipt.contract, 'readable-v2');
  const labels = assertGroupText(svg, workflow);
  const frames = groupFrames(svg);
  assert.equal(frames.length, workflow.groups.length);
  assert.deepEqual(frames.map((frame) => [Number(frame.x), Number(frame.y), Number(frame.width), Number(frame.height)]), [
    [421.2, 216, 300.00000000000006, 72],
    [581.2, 350, 478.79999999999995, 72],
    [204, 484, 357.20000000000005, 72],
    [760, 484, 300, 72],
  ]);
  for (const [index, label] of labels.entries()) {
    const frame = frames.find((candidate) => candidate['data-composition-frame-id'] === `group-${index}`);
    assert.ok(frame, `group-${index} frame must exist`);
    assert.deepEqual(point(label), [Number(frame.x) + 10, Number(frame.y) - 2]);
  }
});

test('missing group IDs are rejected by the schema before renderer fallback is reachable', () => {
  const workflow = example('incident-response');
  delete workflow.groups[0].id;
  delete workflow.groups[1].id;
  const result = compileWorkflow({ workflow });
  assert.equal(result.ok, false);
  assert.equal(result.svg, undefined, 'invalid input must not bypass schema validation to exercise group-N fallback');
  for (const index of [0, 1]) {
    assert.ok(result.diagnostics.some((diagnostic) => diagnostic.code === 'schema/required'
      && diagnostic.subject.path === `/groups/${index}` && diagnostic.evidence.missingProperty === 'id'),
    `group-${index} rendering fallback is guarded by a required group id`);
  }
});

test('group label text XML-escapes markup, ampersands and both quote forms', () => {
  const workflow = example('incident-response');
  workflow.groups[0].label = `A < B & "C" > 'D'`;
  workflow.groups[1].label = '&lt;tag&gt;';
  const { svg } = compile(workflow);
  const labels = groupLabels(svg);
  assert.equal(labels.length, 2);
  assert.deepEqual(labels.map((label) => label['data-group-label']), ['command', 'exception_actions']);
  assert.deepEqual(labels.map((label) => label.text), [
    'A &lt; B &amp; &quot;C&quot; &gt; &#39;D&#39;',
    '&amp;lt;tag&amp;gt;',
  ]);
  assert.deepEqual(labels.map(point), [[232, 205], [442, 708]]);
});
