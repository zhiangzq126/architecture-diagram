import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const template = fs.readFileSync(new URL('../modules/archify/assets/template.html', import.meta.url), 'utf8');
const view = template.slice(template.indexOf('    Archify.view = (function () {'), template.indexOf('    Archify.radar = (function () {'));
const cap = view.match(/var maxManualScale = (\d+);/);
assert.ok(cap, 'Manual zoom limit must be declared');
const extract = (name, next) => {
  const start = view.indexOf(`      function ${name}(`);
  const end = view.indexOf(`      function ${next}(`, start);
  assert.ok(start >= 0 && end > start, `Missing ${name} source`);
  return view.slice(start, end);
};

function camera() {
  const context = vm.createContext({});
  vm.runInContext(`
    var state = { scale: 1, x: 0, y: 0, mode: 'overview' };
    var maxManualScale = ${cap[1]};
    var outBtn = {}, inBtn = {}, Archify = {};
    var attributes = {};
    var svg = { clientWidth: 260, clientHeight: 400, style: {},
      setAttribute: function (key, value) { attributes[key] = value; } };
    var container = { classList: { toggle: function () {} } };
    function syncViewportClip() {}
    function renderControls() {}
    function interruptCamera() { state.mode = 'manual'; }
    function stopCameraMotion() {}
    ${extract('clamp', 'reducedMotion')}
    ${extract('apply', 'sampleRenderedState')}
    ${extract('zoom', 'reset')}
    ${extract('reset', 'centerAt')}
    apply();
  `, context);
  return { context, run: (code) => vm.runInContext(code, context) };
}

test('manual zoom can reach 800% while preserving the centered content point', () => {
  const { context, run } = camera();
  for (let step = 1; step <= 28; step++) {
    run('zoom(state.scale + 0.25)');
    assert.equal(context.state.scale, 1 + step / 4);
    assert.equal((130 - context.state.x) / context.state.scale, 130);
    assert.equal((200 - context.state.y) / context.state.scale, 200);
    assert.equal(context.inBtn.disabled, step === 28);
  }
  assert.equal(context.attributes['data-view-scale'], '8');
  run('zoom(state.scale + 0.25)');
  assert.equal(context.state.scale, 8);
  assert.equal(context.inBtn.disabled, true);
});

test('zooming out re-enables plus and stops at the unchanged overview scale', () => {
  const { context, run } = camera();
  run('zoom(8); zoom(state.scale - 0.25)');
  assert.equal(context.state.scale, 7.75);
  assert.equal(context.inBtn.disabled, false);
  for (let step = 0; step < 30; step++) run('zoom(state.scale - 0.25)');
  assert.equal(context.state.scale, 1);
  assert.equal(context.state.x, 0);
  assert.equal(context.state.y, 0);
  assert.equal(context.outBtn.disabled, true);
});

test('reset restores overview transform after maximum zoom and pan', () => {
  const { context, run } = camera();
  const original = context.svg.style.transform;
  run('zoom(8); state.x = -900; state.y = -1800; apply(); reset()');
  assert.equal(JSON.stringify(context.state), JSON.stringify({ scale: 1, x: 0, y: 0, mode: 'overview' }));
  assert.equal(context.svg.style.transform, original);
  assert.equal(context.inBtn.disabled, false);
  assert.equal(context.outBtn.disabled, true);
});

test('pointer and keyboard actions keep the same quarter-step zoom path', () => {
  assert.match(view, /inBtn\.addEventListener\('click', function \(\) \{ zoom\(state\.scale \+ 0\.25\); \}\)/);
  assert.match(view, /outBtn\.addEventListener\('click', function \(\) \{ zoom\(state\.scale - 0\.25\); \}\)/);
  assert.match(view, /zoomIn: function \(\) \{ zoom\(state\.scale \+ 0\.25\); \}/);
  assert.match(view, /zoomOut: function \(\) \{ zoom\(state\.scale - 0\.25\); \}/);
});

test('viewer stage clips transformed overflow outside the canonical SVG slot', () => {
  assert.match(template, /<div class="diagram-stage">\s*<!-- ARCHIFY:SVG_SLOT_START -->/);
  assert.match(template, /<!-- ARCHIFY:SVG_SLOT_END -->\s*<\/div>/);
  assert.match(template, /\.diagram-stage\s*\{\s*width: 100%;\s*overflow: clip;\s*\}/);
  assert.match(template, /\.diagram-container\[data-wide-diagram="true"\] > \.diagram-stage\s*\{\s*min-width: 720px;/);
});

test('viewer modules address the canonical SVG through the clipping stage', () => {
  assert.doesNotMatch(template, /querySelector\(':scope > svg'\)/);
  assert.doesNotMatch(template, /\.diagram-container(?:\[[^\]]+\])? > svg/);
  assert.equal([...template.matchAll(/querySelector\(':scope > \.diagram-stage > svg'\)/g)].length, 7);
});

test('presentation constrains both the flex stage and its SVG to the available height', () => {
  assert.match(template, /html\[data-present="true"\]:not\(\[data-embed="true"\]\) \.diagram-container > \.diagram-stage,\s*html\[data-present="true"\]:not\(\[data-embed="true"\]\) \.diagram-container > \.diagram-stage > svg\s*\{\s*flex: 1 1 auto;\s*height: 100%;\s*min-height: 0;\s*width: 100%;\s*min-width: 0;/);
});
