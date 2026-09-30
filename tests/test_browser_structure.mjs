import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { parseArgs } from 'node:util';
import { fileURLToPath } from 'node:url';
import { ChromeVisualBrowser, findChrome } from '../modules/archify/bin/visual-check.mjs';
import { compileWorkflow } from '../modules/archify/renderers/workflow/workflow-compiler.mjs';
import { expectedStructure, installPageTools, installObserver, observePipe, OFFLINE_CSP, argumentsFor } from './browser_acceptance.mjs';

// Explicit read-only deliveries and a fresh external output directory are required.
// Run directly with node (node:test still emits TAP); no renderer CLI or packages.
const { values } = parseArgs({ options: { fixtures: { type: 'string' }, outdir: { type: 'string' } } });
values.fixtures ??= process.env.ARCHIFY_BROWSER_STRUCTURE_FIXTURES;
values.outdir ??= process.env.ARCHIFY_BROWSER_STRUCTURE_OUTDIR;
const hash = (bytes) => createHash('sha256').update(bytes).digest('hex');
const skill = fs.realpathSync(fileURLToPath(new URL('..', import.meta.url)));
const cli = fileURLToPath(new URL('./browser_acceptance.mjs', import.meta.url));

test('native-model structure in offline Chrome', {
  timeout: 240000,
  skip: !values.fixtures && !values.outdir ? 'Opt in with --fixtures/--outdir or ARCHIFY_BROWSER_STRUCTURE_FIXTURES/OUTDIR.' : false,
}, async (t) => {
  assert.ok(path.isAbsolute(values.fixtures || ''), '--fixtures must be an absolute delivery directory');
  assert.ok(path.isAbsolute(values.outdir || ''), '--outdir must be a fresh absolute directory');
  const parent = fs.realpathSync(path.dirname(values.outdir));
  assert.ok(parent !== skill && !parent.startsWith(skill + path.sep));
  const outdir = path.join(parent, path.basename(values.outdir));
  fs.mkdirSync(outdir);
  const save = (name, content) => fs.writeFileSync(path.join(outdir, name), content, { flag: 'wx' });
  const evidence = { cases: [], commands: [], inputs: [], external: [], policy: [], errors: [], requests: [] };
  const fixtures = new Map();
  for (const name of ['sequence', 'workflow', 'order-platform', 'dataflow', 'lifecycle']) {
    const directory = path.join(values.fixtures, `${name}-html`);
    const artifact = path.join(directory, `${name}.html`), modelPath = path.join(directory, 'model.json');
    const html = fs.readFileSync(artifact, 'utf8'), bytes = fs.readFileSync(modelPath);
    const model = JSON.parse(bytes);
    fixtures.set(name, { artifact, modelPath, html, model, expected: expectedStructure(model) });
    evidence.inputs.push({ path: artifact, sha256: hash(html) }, { path: modelPath, bytes: bytes.length, sha256: hash(bytes) });
  }
  const sequence = fixtures.get('sequence'), workflow = fixtures.get('workflow');
  const chrome = findChrome();
  assert.ok(chrome, 'Existing Chrome required; no installation attempted');
  let served = '', browser, detach, closing = false;
  const pending = new Set();
  const oldTmp = process.env.TMPDIR;
  process.env.TMPDIR = outdir; // Ephemeral Chrome profile stays in the new test directory.
  let origin;
  const server = http.createServer((req, res) => {
    const allowed = req.method === 'GET' && req.headers.host === new URL(origin).host && req.url === '/artifact.html';
    evidence.requests.push({ url: req.url, allowed });
    res.writeHead(allowed ? 200 : 404, { 'Content-Type': 'text/html; charset=utf-8', 'Content-Security-Policy': OFFLINE_CSP, 'Cache-Control': 'no-store' });
    res.end(allowed ? served : '');
  });
  try {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    origin = `http://127.0.0.1:${server.address().port}`;
    browser = new ChromeVisualBrowser(chrome);
    const session = await browser.sessionPromise;
    const send = (method, params = {}) => browser.cdp.send(method, params, session);
    const evaluate = async (expression) => {
      const response = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text);
      return response.result?.value;
    };
    const handle = (promise) => {
      pending.add(promise);
      promise.catch((error) => { if (!closing) evidence.errors.push(String(error)); }).finally(() => pending.delete(promise));
    };
    detach = observePipe(browser, ({ method, params, sessionId }) => {
      if (sessionId !== session || closing) return;
      if (method === 'Fetch.requestPaused') {
        const url = params.request.url;
        if (url === `${origin}/favicon.ico`) return handle(send('Fetch.fulfillRequest', { requestId: params.requestId, responseCode: 204, body: '' }));
        const allowed = url === `${origin}/artifact.html` || /^(data:|blob:|about:blank$)/i.test(url);
        if (!allowed) evidence.external.push(url);
        handle(send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', { requestId: params.requestId, ...(allowed ? {} : { errorReason: 'BlockedByClient' }) }));
      } else if (method === 'Runtime.bindingCalled' && params.name === '__acceptanceEvent') evidence.policy.push(JSON.parse(params.payload));
      else if (method === 'Runtime.exceptionThrown') evidence.errors.push(params.exceptionDetails);
      else if (method === 'Runtime.consoleAPICalled' && params.type === 'error') evidence.errors.push(params.args);
      else if (method === 'Log.entryAdded' && params.entry.level === 'error') evidence.errors.push(params.entry);
      else if (method === 'Network.requestWillBeSent' && /^(https?|wss?|ftp|file):/i.test(params.request.url) && !params.request.url.startsWith(origin + '/')) evidence.external.push(params.request.url);
      else if (method === 'Page.javascriptDialogOpening') {
        evidence.errors.push(params.message);
        handle(send('Page.handleJavaScriptDialog', { accept: false }));
      }
    });
    await send('Network.enable');
    await send('Network.setBlockedURLs', { urls: ['https://*', 'ws://*', 'wss://*', 'ftp://*', 'file://*'] });
    await send('Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Request' }] });
    await send('Log.enable');
    await send('Runtime.addBinding', { name: '__acceptanceEvent' });
    await send('Page.addScriptToEvaluateOnNewDocument', { source: `(${installObserver.toString()})()` });
    await browser.cdp.send('Browser.setDownloadBehavior', { behavior: 'deny' });
    await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    await send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
    evidence.chrome = await browser.cdp.send('Browser.getVersion');
    const settle = () => evaluate(`(async () => {
      await document.fonts.ready;
      for (let i = 0; i < 2; i++) {
        await window.Archify?.readerLayout?.whenStable?.();
        await window.Archify?.viewerChromeLayout?.whenStable?.();
      }
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    })()`);
    const load = async (fixture) => {
      served = fixture.html;
      const loaded = browser.cdp.waitFor('Page.loadEventFired', session);
      const navigation = await send('Page.navigate', { url: `${origin}/artifact.html` });
      assert.ok(!navigation.errorText, navigation.errorText);
      await loaded;
      await evaluate(`(${installPageTools.toString()})(${JSON.stringify(fixture.expected)})`);
      await settle();
    };
    const runCase = async (name, fixture, mutation = '', pass = true, detail = null) => t.test(name, async () => {
      await load(fixture);
      if (mutation) await evaluate(`(() => { ${mutation} })()`);
      const result = await evaluate('__acceptance.structuralClearance()');
      evidence.cases.push({ name, expected: pass, actual: result.ok, mutation, result });
      if (pass || result.ok !== pass) {
        const shot = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false });
        save(`${name}.png`, Buffer.from(shot.data, 'base64'));
      }
      assert.equal(result.ok, pass, JSON.stringify(result));
      if (pass && !fixture.expected.story) {
        const layout = await evaluate('__acceptance.layout()');
        assert.ok(layout.surfaces.every((surface) => surface.ok), JSON.stringify(layout.surfaces));
        assert.ok(!layout.surfaces.some((surface) => surface.element === 'guided-views'));
      }
      if (detail) detail(result);
    });

    for (const [name, fixture] of fixtures) await runCase(`${name}-baseline`, fixture, '', true, (result) => {
      if (name !== 'sequence') assert.equal(result.sequence, null);
      else {
        assert.ok(result.sequence.lifelines.every((line) => line.rect.width === 0 && line.ok), 'vertical lifelines must pass with zero bbox width');
        assert.ok(result.sequence.legend.gap > 0);
      }
    });
    const L = `document.querySelector('[data-legend]')`;
    const P = `document.querySelector('path[stroke-dasharray="3,7"]')`;
    const G = `document.querySelector('[data-composition-frame-kind="group"]')`;
    const LABEL = `document.querySelector('[data-group-label]')`;
    const hiddenModel = structuredClone(sequence.model);
    hiddenModel.meta.legend = { mode: 'hidden' };
    const hidden = { ...sequence, expected: expectedStructure(hiddenModel) };
    await runCase('sequence-explicit-hidden-absent', hidden, `${L}.remove()`);
    await runCase('sequence-explicit-hidden-style', hidden, `${L}.style.display = 'none'`);
    const autoModel = structuredClone(sequence.model);
    autoModel.meta.legend = { mode: 'auto' };
    await runCase('sequence-explicit-auto', { ...sequence, expected: expectedStructure(autoModel) });
    // mode:all requires even kinds absent from messages; no renderer invocation.
    const allModel = structuredClone(sequence.model);
    allModel.meta.legend = { mode: 'all' };
    const allFixture = { ...sequence, expected: expectedStructure(allModel) };
    await runCase('sequence-all-missing-kind', allFixture, '', false, (result) => {
      assert.equal(result.sequence.legend.ok, false);
    });
    await runCase('sequence-all-complete', allFixture, `
      const entry = ${L}.querySelector('[data-legend-semantic-kind]').cloneNode(true);
      entry.dataset.legendSemanticKind = 'security'; entry.setAttribute('transform', 'translate(600 0)');
      entry.querySelector('text').textContent = '安全'; ${L}.append(entry);
    `);
    const defaultVariant = structuredClone(sequence.model);
    defaultVariant.messages.filter(message => message.variant === 'default').forEach(message => delete message.variant);
    await runCase('sequence-implicit-default-variant', { ...sequence, expected: expectedStructure(defaultVariant) });
    const overrideModel = structuredClone(sequence.model);
    overrideModel.meta.legend = { mode: 'auto', entries: { default: { label: 'Custom message' } } };
    await runCase('sequence-legend-label-override', { ...sequence, expected: expectedStructure(overrideModel) },
      `document.querySelector('[data-legend-semantic-kind="default"] text').textContent = 'Custom message'`);
    for (const [name, mutation] of [
      ['legend-delete', `${L}.remove()`],
      ['legend-display-none', `${L}.style.display = 'none'`],
      ['legend-opacity', `${L}.style.opacity = '0'`],
      ['legend-parent-hidden', `${L}.parentElement.style.visibility = 'hidden'`],
      ['legend-title-delete', `${L}.querySelector(':scope > text').remove()`],
      ['legend-title-hidden', `${L}.querySelector(':scope > text').style.visibility = 'hidden'`],
      ['legend-entry-delete', `${L}.querySelector('[data-legend-semantic-kind]').remove()`],
      ['legend-entry-text-hidden', `${L}.querySelector('[data-legend-semantic-kind] text').style.opacity = '0'`],
      ['legend-text-wrong', `${L}.querySelector('[data-legend-semantic-kind] text').textContent = 'wrong'`],
      ['legend-duplicate', `${L}.after(${L}.cloneNode(true))`],
      ['legend-below-viewbox', `${L}.setAttribute('transform', 'translate(0 200)')`],
      ['legend-timeline-overlap', `${L}.setAttribute('transform', 'translate(0 -40)')`],
      ['lifeline-delete', `${P}.remove()`],
      ['lifeline-all-delete', `document.querySelectorAll('path[stroke-dasharray="3,7"]').forEach(el => el.remove())`],
      ['lifeline-display-none', `${P}.style.display = 'none'`],
      ['lifeline-visibility', `${P}.style.visibility = 'hidden'`],
      ['lifeline-opacity', `${P}.style.opacity = '0'`],
      ['lifeline-hidden-attribute', `${P}.setAttribute('hidden', '')`],
      ['lifeline-parent-hidden', `const line = ${P}, parent = document.createElementNS('http://www.w3.org/2000/svg', 'g'); line.before(parent); parent.append(line); parent.style.opacity = '0'`],
      ['lifeline-no-stroke', `${P}.style.stroke = 'none'`],
      ['lifeline-transparent-stroke', `${P}.style.stroke = 'transparent'`],
      ['lifeline-stroke-opacity', `${P}.style.strokeOpacity = '0'`],
      ['lifeline-zero-stroke-width', `${P}.style.strokeWidth = '0'`],
      ['lifeline-zero-length', `${P}.setAttribute('d', 'M 62 142 L 62 142')`],
      ['lifeline-duplicate', `${P}.after(${P}.cloneNode(true))`],
      ['lifeline-same-count-wrong-column', `const lines = document.querySelectorAll('path[stroke-dasharray="3,7"]'); lines[1].setAttribute('d', lines[0].getAttribute('d'))`],
      ['lifeline-wrong-order', `const lines = document.querySelectorAll('path[stroke-dasharray="3,7"]'); lines[0].before(lines[1])`],
      ['legend-and-lifelines-delete', `${L}.remove(); document.querySelectorAll('path[stroke-dasharray="3,7"]').forEach(el => el.remove())`],
    ]) await runCase(`sequence-${name}`, sequence, mutation, false, (result) => assert.equal(result.sequence.ok, false));

    for (const [name, mutation] of [
      ['group-delete', `${G}.remove()`], ['group-hidden', `${G}.style.display = 'none'`],
      ['group-opacity', `${G}.style.opacity = '0'`], ['group-no-stroke', `${G}.style.stroke = 'none'`],
      ['group-move-away', `${G}.setAttribute('x', '10000')`],
      ['group-shrink', `${G}.setAttribute('width', '1')`],
      ['group-duplicate', `${G}.after(${G}.cloneNode(true))`],
      ['group-unexpected', `const el = ${G}.cloneNode(true); el.dataset.compositionFrameId = 'unexpected'; ${G}.after(el)`],
      ['group-wrong-id', `${G}.dataset.compositionFrameId = 'wrong'`],
      ['member-delete', `document.querySelector('[data-node-id="page"]').remove()`],
      ['member-hidden', `document.querySelector('[data-node-id="page"]').style.visibility = 'hidden'`],
      ['member-mask-delete', `document.querySelector('[data-node-id="page"] > rect.c-mask').remove()`],
      ['member-move-out', `document.querySelector('[data-node-id="page"]').setAttribute('transform', 'translate(350 0)')`],
      ['member-duplicate', `const el = document.querySelector('[data-node-id="page"]'); el.after(el.cloneNode(true))`],
      ['member-label-outside-frame', `document.querySelector('[data-node-id="page"] text[data-node-label]').setAttribute('transform', 'translate(350 0)')`],
      ['label-delete', `${LABEL}.remove()`], ['label-hidden', `${LABEL}.style.display = 'none'`],
      ['label-wrong-text', `${LABEL}.textContent = 'wrong'`], ['label-wrong-id', `${LABEL}.dataset.groupLabel = 'wrong'`],
      ['label-duplicate', `${LABEL}.after(${LABEL}.cloneNode(true))`],
      ['all-groups-and-labels-delete', `document.querySelectorAll('[data-composition-frame-kind="group"], [data-group-label]').forEach(el => el.remove())`],
    ]) await runCase(`workflow-${name}`, workflow, mutation, false, (result) => {
      assert.ok(!result.groupCountOK || result.groups.some((group) => !group.ok));
    });
    for (const [name, mutation] of [
      ['toolbar-delete', `document.querySelector('.toolbar').remove()`],
      ['toolbar-hidden', `document.querySelector('.toolbar').style.display = 'none'`],
      ['story-delete', `document.querySelector('.guided-views').remove()`],
      ['story-hidden', `document.querySelector('.guided-views').style.display = 'none'`],
      ['toolbar-story-overlap', `const el = document.querySelector('.toolbar'), r = document.querySelector('.guided-views').getBoundingClientRect(); Object.assign(el.style, {position:'fixed', left:r.left+'px', top:r.top+'px', right:'auto'})`],
    ]) await runCase(name, sequence, mutation, false);
    await runCase('embed-ui-hidden-legally', sequence, `document.documentElement.dataset.embed = 'true'`);

    const v2 = JSON.parse(fs.readFileSync(new URL('../modules/archify/examples/agent-tool-call.workflow.json', import.meta.url), 'utf8'));
    const compiled = compileWorkflow({ workflow: v2 });
    assert.equal(compiled.ok, true, JSON.stringify(compiled.diagnostics));
    const v2Fixture = { model: v2, expected: expectedStructure(v2), html: workflow.html
      .replace(/<svg\b[^>]*aria-labelledby="archify-diagram-title archify-diagram-description"[\s\S]*?<\/svg>/, () => compiled.svg)
      .replace(/(<script\b[^>]*id="archify-guided-views-data"[^>]*>)[\s\S]*?(<\/script>)/, (_, open, close) => open + JSON.stringify(v2.meta.views) + close) };
    await runCase('workflow-v2-grouped', v2Fixture);
    await runCase('workflow-v2-missing-member', v2Fixture, `document.querySelector('[data-node-id="planner"]').remove()`, false);
    await runCase('workflow-v2-group-move-away', v2Fixture, `${G}.setAttribute('x', '10000')`, false);
    const reordered = structuredClone(v2);
    reordered.groups.reverse();
    await runCase('workflow-v2-canonical-group-order', { ...v2Fixture, expected: expectedStructure(reordered) });
    const noViews = structuredClone(v2);
    noViews.groups = []; noViews.meta.views = [];
    const noGroups = compileWorkflow({ workflow: noViews });
    assert.equal(noGroups.ok, true);
    await runCase('workflow-no-groups-no-story', { expected: expectedStructure(noViews), html: v2Fixture.html
      .replace(/<svg\b[^>]*aria-labelledby="archify-diagram-title archify-diagram-description"[\s\S]*?<\/svg>/, () => noGroups.svg)
      .replace(/(<script\b[^>]*id="archify-guided-views-data"[^>]*>)[\s\S]*?(<\/script>)/, '$1[]$2') });
    for (const name of ['order-platform', 'dataflow', 'lifecycle']) {
      await runCase(`${name}-missing-native-node`, fixtures.get(name), `document.querySelector('.diagram-container [data-node-id]').remove()`, false);
      await runCase(`${name}-unexpected-group`, fixtures.get(name), `
        const frame = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
        frame.dataset.compositionFrameKind = 'group'; frame.dataset.compositionFrameId = 'unexpected';
        __acceptance.svg().append(frame);
      `, false);
    }

    await t.test('model validation and CLI fail closed before Chrome', () => {
      const valid = sequence.model;
      const missingCol = structuredClone(v2);
      delete missingCol.nodes[0].col;
      // The installed compiler/schema currently rejects omitted columns even in
      // v2. Pin fail-closed behavior rather than authoring a fallback layout.
      const invalidCases = [ ['missing', undefined], ['malformed', '{'], ['null', 'null'], ['invalid-schema', '{}'],
        ['v2-missing-col', JSON.stringify(missingCol)],
        ['unknown-type', JSON.stringify({ ...valid, diagram_type: 'unknown' })],
        ['duplicate-participant', JSON.stringify({ ...valid, participants: [...valid.participants, valid.participants[0]] })],
        ['unknown-endpoint', JSON.stringify({ ...valid, messages: [{ ...valid.messages[0], from: 'absent' }] })] ];
      for (const [name, content] of invalidCases) {
        const modelPath = path.join(outdir, `${name}.json`), output = path.join(outdir, `cli-${name}`);
        if (content !== undefined) fs.writeFileSync(modelPath, content, { flag: 'wx' });
        const args = [cli, '--artifact', sequence.artifact, '--model', modelPath, '--outdir', output];
        const result = spawnSync(process.execPath, args, { encoding: 'utf8', timeout: 20000, env: { ...process.env, NODE_OPTIONS: '', ARCHIFY_DIAGNOSTIC_FORMAT: '' } });
        evidence.commands.push({ args, status: result.status, stdout: result.stdout, stderr: result.stderr });
        assert.ifError(result.error);
        assert.equal(result.status, 1);
        const report = JSON.parse(fs.readFileSync(path.join(output, 'report.json'), 'utf8'));
        assert.equal(report.automated_status, 'fail');
        assert.equal(report.checks.find((check) => check.name === 'native-model-valid').status, 'fail');
        assert.equal(report.chrome, undefined);
        assert.equal(report.model.path, modelPath);
        if (content !== undefined) { assert.equal(report.model.sha256, hash(content)); assert.equal(report.model.bytes, Buffer.byteLength(content)); }
      }
      const args = ['--artifact', sequence.artifact, '--outdir', path.join(outdir, 'parse-default')];
      assert.equal(argumentsFor(args).model, sequence.modelPath);
      for (const suffix of [['--model', 'relative.json'], ['--model'], ['--model', sequence.modelPath, '--model', sequence.modelPath], ['--unexpected', 'x']]) {
        assert.throws(() => argumentsFor([...args, ...suffix]));
      }
      const standalone = path.join(outdir, 'standalone.html');
      fs.writeFileSync(standalone, sequence.html, { flag: 'wx' });
      const result = spawnSync(process.execPath, [cli, '--artifact', standalone, '--outdir', path.join(outdir, 'cli-standalone')], { encoding: 'utf8', timeout: 20000, env: { ...process.env, NODE_OPTIONS: '' } });
      evidence.commands.push({ name: 'standalone-no-model', status: result.status, stdout: result.stdout, stderr: result.stderr });
      assert.equal(result.status, 1);
      assert.ok(JSON.parse(fs.readFileSync(path.join(outdir, 'cli-standalone/report.json'))).harness_errors.some((message) => message.includes('no DOM fallback')));
      assert.throws(() => expectedStructure({ ...valid, diagram_type: 'workflow' }));
    });
    await t.test('offline and immutable inputs', () => {
      assert.deepEqual(evidence.external, []);
      assert.deepEqual(evidence.policy, []);
      assert.deepEqual(evidence.errors, []);
      assert.ok(evidence.requests.every((request) => request.allowed));
      for (const input of evidence.inputs) assert.equal(hash(fs.readFileSync(input.path)), input.sha256, input.path);
    });
  } finally {
    await Promise.allSettled([...pending]);
    closing = true;
    try { if (browser) await browser.close(); }
    finally {
      detach?.();
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      if (oldTmp === undefined) delete process.env.TMPDIR; else process.env.TMPDIR = oldTmp;
      evidence.profileRemoved = !browser || !fs.existsSync(browser.profileRoot);
      save('structure-report.json', JSON.stringify(evidence, null, 2) + '\n');
    }
    assert.equal(evidence.profileRemoved, true);
  }
});
