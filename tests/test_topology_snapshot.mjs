import assert from 'node:assert/strict';
import test from 'node:test';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { snapshot, topologyModel, writeSnapshot } from '../scripts/topology_snapshot.mjs';

const model = { diagram_type: 'architecture', components: [{ id: 'a', label: '入口 & A' }, { id: 'b', label: '结果' }], connections: [{ id: 'a-b', from: 'a', to: 'b', label: '传递' }] };
const layout = { diagram_type: 'architecture', viewBox: [240, 120], components: [{ id: 'a', x: 10, y: 40, width: 70, height: 50 }, { id: 'b', x: 160, y: 40, width: 70, height: 50 }] };
const html = `<!doctype html><html><head><style>:root{--bg:#fff}text{font-size:12px}</style><script>throw new Error('Must not execute artifact scripts')</script></head><body><svg viewBox="0 0 240 120" aria-labelledby="archify-diagram-title archify-diagram-description">
<path data-edge-from="a" data-edge-to="b" data-edge-key="0" data-edge-id="a-b" data-edge-label="传递" d="M80 65 Q120 0 160 65" fill="none" stroke="black"/>
<g data-node-id="a" data-node-label="入口 &amp; A"><rect x="10" y="40" width="70" height="50" fill="lightblue"/><text x="12" y="67">入口 &amp; A</text></g>
<g data-node-id="b" data-node-label="结果"><rect x="160" y="40" width="70" height="50" fill="lightblue"/><text x="175" y="67">结果</text></g></svg></body></html>`;

test('topology model keeps native ordering, limits and supported layouts', () => {
  assert.deepEqual(topologyModel(model).edges, model.connections);
  const workflow = { diagram_type: 'workflow', nodes: model.components, edges: model.connections };
  assert.equal(topologyModel(workflow).kind, 'workflow');
  for (const bad of [null, { ...model, diagram_type: 'sequence' }, { ...model, components: [] }, { ...model, connections: [] }, { ...model, components: Array(13).fill(model.components[0]) }, { ...model, connections: Array(31).fill(model.connections[0]) }, { ...model, connections: [{ from: 'missing', to: 'b' }] }, { ...model, layout: { mode: 'auto-unknown' } }]) {
    assert.throws(() => topologyModel(bad));
  }
});

test('snapshot output pair rejects collisions and cleans only owned partial files', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'topology-output-'));
  try {
    const png = path.join(directory, 'base.png'), scene = path.join(directory, 'scene.json');
    const result = { png: Buffer.from('PNG'), scene: { width: 1 } };
    fs.writeFileSync(scene, 'keep');
    assert.throws(() => writeSnapshot(png, scene, result), /EEXIST/);
    assert.equal(fs.existsSync(png), false);
    assert.equal(fs.readFileSync(scene, 'utf8'), 'keep');
    assert.throws(() => writeSnapshot(png, png, result), /EEXIST/);
    assert.equal(fs.existsSync(png), false);
    fs.unlinkSync(scene);
    writeSnapshot(png, scene, result);
    assert.equal(fs.readFileSync(png, 'utf8'), 'PNG');
    assert.deepEqual(JSON.parse(fs.readFileSync(scene)), result.scene);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test('offline SVG snapshot preserves labels, samples the curve and blocks artifact scripts/resources', { skip: process.env.ARCHITECTURE_TOPOLOGY_BROWSER_TEST !== '1' }, async () => {
  let requests = 0;
  const server = http.createServer((req, res) => { requests++; res.end(''); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/forbidden`;
    const hostileCss = html.replace('<style>', `<style>@import url('${url}');`);
    const font = process.env.ARCHITECTURE_DIAGRAM_FONT ? fs.readFileSync(process.env.ARCHITECTURE_DIAGRAM_FONT) : undefined;
    const { scene, png } = await snapshot({ html: hostileCss, model, layout, theme: 'light', font });
    assert.deepEqual(scene.nodes.map(({ id, label }) => ({ id, label })), model.components);
    assert.equal(png.readUInt32BE(16), scene.width);
    assert.equal(png.readUInt32BE(20), scene.height);
    const points = scene.edges[0].points;
    assert.deepEqual(points[0], [80, 65]);
    assert.deepEqual(points.at(-1), [160, 65]);
    assert.ok(Math.min(...points.map(p => p[1])) < 40, 'actual quadratic bend, not straight interpolation');
    assert.equal(requests, 0);
    assert.equal(scene.text_boxes.length, 2);
    assert.ok(scene.text_boxes.every(box => box.width > 0 && box.height > 0));
    const reverse = { id: 'b-a', from: 'b', to: 'a', label: '返回' };
    const reorderedModel = { ...model, connections: [reverse, ...model.connections] };
    const reorderedHtml = html.replace('</svg>', '<path data-edge-from="b" data-edge-to="a" data-edge-key="1" data-edge-id="b-a" data-edge-label="返回" d="M160 65 L80 65"/></svg>');
    const reordered = await snapshot({ html: reorderedHtml, model: reorderedModel, layout, theme: 'light' });
    assert.deepEqual(reordered.scene.edges.map(e => e.id), ['b-a', 'a-b']);
    assert.deepEqual(reordered.scene.edges[0].points[0], [160, 65]);
    assert.deepEqual(reordered.scene.edges[1].points[0], [80, 65]);
    await assert.rejects(snapshot({ html: html.replace('<svg ', '<svg onload="fetch(\'' + url + '\')" '), model, layout, theme: 'light' }), /Unsafe SVG/);
    await assert.rejects(snapshot({ html: html.replace('</svg>', `<image href="${url}"/></svg>`), model, layout, theme: 'dark' }), /Unsafe SVG/);
    await assert.rejects(snapshot({ html, model, layout: { ...layout, viewBox: [400, 120] }, theme: 'light' }), /viewBox disagree/);
    await assert.rejects(snapshot({ html, model, layout, theme: 'light', font: Buffer.from('invalid font') }), /SyntaxError|font/i);
    const oversizedPath = html.replace('font-size:12px', 'font-size:2px').replace('M80 65 Q120 0 160 65', 'M80 65 L15080 65');
    await assert.rejects(snapshot({ html: oversizedPath, model, layout, theme: 'light' }), /Sampled path exceeds/);
    assert.equal(requests, 0);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
