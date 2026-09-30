#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { ChromeVisualBrowser, findChrome } from '../modules/archify/bin/visual-check.mjs';

const CSP = "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";

export function topologyModel(model) {
  const kind = model?.diagram_type;
  if (!['architecture', 'workflow'].includes(kind)) throw new Error('Topology GIF requires architecture or workflow');
  const nodes = model[kind === 'architecture' ? 'components' : 'nodes'];
  const edges = model[kind === 'architecture' ? 'connections' : 'edges'];
  if (!Array.isArray(nodes) || nodes.length < 2 || nodes.length > 12 || !Array.isArray(edges) || edges.length < 1 || edges.length > 30) {
    throw new Error('Topology GIF supports 2–12 nodes and 1–30 edges');
  }
  if (kind === 'architecture' && !['free', 'grid'].includes(model.layout?.mode || 'free')) throw new Error('Topology GIF supports free/grid architecture layouts');
  const ids = new Set(nodes.map(n => n.id));
  if (ids.size !== nodes.length || edges.some(e => !ids.has(e.from) || !ids.has(e.to))) throw new Error('Invalid topology endpoints or duplicate nodes');
  return { kind, nodes, edges };
}

export async function snapshot({ html, model, layout, theme, font, chromePath = findChrome() }) {
  const expected = topologyModel(model);
  if (font && font.length > 64 * 1024 * 1024) throw new Error('CJK font exceeds 64 MiB');
  if (!chromePath) throw new Error('Topology GIF requires an existing Chrome/Chromium; no browser will be installed');
  if (!['light', 'dark'].includes(theme)) throw new Error('Invalid theme');
  const browser = new ChromeVisualBrowser(chromePath);
  try {
    const session = await browser.sessionPromise;
    const send = (method, params = {}) => browser.cdp.send(method, params, session, 60000);
    const evaluate = async (expression) => {
      const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
      return result.result.value;
    };
    await send('Network.enable');
    await send('Network.setBlockedURLs', { urls: ['http://*', 'https://*', 'file://*', 'ftp://*', 'ws://*', 'wss://*'] });
    await browser.cdp.send('Browser.setDownloadBehavior', { behavior: 'deny' });
    const { frameTree } = await send('Page.getFrameTree');
    await send('Page.setDocumentContent', { frameId: frameTree.frame.id,
      html: `<!doctype html><html><head><meta charset="UTF-8"><meta http-equiv="Content-Security-Policy" content="${CSP}"></head><body></body></html>` });
    const prepared = await evaluate(`(async () => {
      const parsed = new DOMParser().parseFromString(${JSON.stringify(html)}, 'text/html');
      const original = parsed.querySelector('svg[aria-labelledby="archify-diagram-title archify-diagram-description"]');
      if (!original || original.querySelector('script, foreignObject, iframe, animate, animateMotion, set')) throw new Error('Expected a static canonical Archify SVG');
      for (const el of [original, ...original.querySelectorAll('*')]) {
        for (const attr of el.attributes) {
          if (/^on/i.test(attr.name) || (/href$/i.test(attr.name) && !/^(#|data:)/i.test(attr.value))) throw new Error('Unsafe SVG resource or event attribute');
        }
      }
      for (const style of parsed.querySelectorAll('style')) document.head.append(document.importNode(style, true));
      document.documentElement.dataset.theme = ${JSON.stringify(theme)};
      document.documentElement.dataset.preset = original.dataset.preset || 'classic';
      document.documentElement.dataset.motion = 'still';
      const svg = document.importNode(original, true);
      svg.removeAttribute('data-animation');
      for (const el of svg.querySelectorAll('[data-animate]')) el.removeAttribute('data-animate');
      document.body.append(svg);
      const fontUri=${JSON.stringify(font ? 'data:font/collection;base64,' + font.toString('base64') : null)};
      if (fontUri) {
        const face=new FontFace('TopologyCJK', 'url('+fontUri+')', {unicodeRange:'U+2E80-9FFF,U+F900-FAFF,U+FE30-FE4F,U+FF00-FFEF,U+20000-3134F'});
        document.fonts.add(await face.load().catch(error => {throw new Error('CJK font failed to decode: '+error.message);}));
        for (const text of svg.querySelectorAll('text')) text.style.fontFamily="'TopologyCJK',"+getComputedStyle(text).fontFamily;
      }
      const override = document.createElement('style');
      override.textContent = 'html,body{margin:0!important;padding:0!important;display:block!important;overflow:hidden!important;min-width:0!important;min-height:0!important;background:var(--bg)!important} svg{display:block!important;max-width:none!important;max-height:none!important;min-width:0!important;transform:none!important;transition:none!important} *,*::before,*::after{animation:none!important;transition:none!important}';
      document.head.append(override);
      const vb = svg.viewBox.baseVal;
      if (![vb.x,vb.y,vb.width,vb.height].every(Number.isFinite) || vb.width<=0 || vb.height<=0) throw new Error('Invalid SVG viewBox');
      svg.style.width = vb.width + 'px'; svg.style.height = vb.height + 'px';
      await document.fonts.ready;
      const sizes = [...svg.querySelectorAll('text')].filter(e => e.textContent.trim()).map(e => parseFloat(getComputedStyle(e).fontSize));
      if (!sizes.length || sizes.some(s => !Number.isFinite(s) || s<=0)) throw new Error('Missing readable SVG text');
      const minFont = Math.min(...sizes);
      const scale = Math.max(1, 8/minFont);
      const width = Math.ceil(vb.width*scale), height = Math.ceil(vb.height*scale);
      if (width*height>3000000 || width*height*(${expected.edges.length}*12+2)>180000000) throw new Error('Topology GIF readability requires too many pixels; simplify the diagram instead of shrinking its text');
      svg.style.width = width + 'px'; svg.style.height = height + 'px';
      return {width,height,viewBox:[vb.x,vb.y,vb.width,vb.height],min_text_px:minFont*scale};
    })()`);
    if (layout.diagram_type !== expected.kind || layout.viewBox?.length !== 2 || layout.viewBox.some((n, i) => Math.abs(n - prepared.viewBox[i + 2]) > 0.01)) {
      throw new Error('SVG and layout report viewBox disagree');
    }
    await send('Emulation.setDeviceMetricsOverride', { width: prepared.width, height: prepared.height, deviceScaleFactor: 1, mobile: false });
    const scene = await evaluate(`(async () => {
      await document.fonts.ready;
      const svg = document.querySelector('svg');
      await Promise.all([...svg.querySelectorAll('image')].map(el => new Promise((resolve,reject) => {
        const image = new Image(); image.onload=resolve; image.onerror=()=>reject(new Error('Embedded icon failed to decode'));
        image.src=el.getAttribute('href') || el.getAttribute('xlink:href');
      })));
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const expected = ${JSON.stringify(expected)};
      const placed = ${JSON.stringify(layout.components || layout.nodes)};
      const vb = svg.viewBox.baseVal, matrix = svg.getScreenCTM();
      const point = (x,y) => { const p=new DOMPoint(x,y).matrixTransform(matrix); return [p.x,p.y]; };
      const nodeElements = [...svg.querySelectorAll('[data-node-id]')];
      if (nodeElements.length !== expected.nodes.length) throw new Error('Missing business nodes');
      const nodes = expected.nodes.map(n => {
        const el=nodeElements.find(e=>e.dataset.nodeId===n.id), box=placed.find(p=>p.id===n.id);
        if (!el || !box || el.dataset.nodeLabel !== n.label) throw new Error('Business label or geometry changed: '+n.id);
        const [x,y]=point(box.x,box.y), [right,bottom]=point(box.x+box.width,box.y+box.height);
        if (x<0 || y<0 || right>${prepared.width}+0.1 || bottom>${prepared.height}+0.1) throw new Error('Node clipped: '+n.id);
        return {id:n.id,label:n.label,x,y,width:right-x,height:bottom-y};
      });
      const paths=[...svg.querySelectorAll('path[data-edge-from][data-edge-to]')];
      if (paths.length!==expected.edges.length) throw new Error('Missing relationship paths');
      const usedPaths=new Set();
      const edges=expected.edges.map(e=>{
        const p=paths.find(p=>!usedPaths.has(p) && (!e.id || p.dataset.edgeId===e.id) && p.dataset.edgeFrom===e.from && p.dataset.edgeTo===e.to && (p.dataset.edgeLabel||'')===(e.label||''));
        if (!p) throw new Error('Relationship semantics changed');
        usedPaths.add(p);
        const length=p.getTotalLength();
        if (!Number.isFinite(length) || length<=0 || length>20000) throw new Error('Invalid animation path length');
        const local=p.getScreenCTM();
        const count=Math.ceil(length*Math.max(Math.hypot(local.a,local.b),Math.hypot(local.c,local.d))/2);
        if (!Number.isFinite(count) || count<1 || count>20000) throw new Error('Sampled path exceeds 20001 points; simplify the diagram');
        const points=Array.from({length:count+1},(_,i)=>{const v=p.getPointAtLength(length*i/count).matrixTransform(local);return [v.x,v.y];});
        return {from:e.from,to:e.to,label:e.label||'',...(e.id?{id:e.id}:{}),points};
      });
      const texts=[...svg.querySelectorAll('text')].filter(e=>e.textContent.trim());
      const text_boxes=texts.map(text => {
        const r=text.getBoundingClientRect(), s=getComputedStyle(text);
        if (s.display==='none' || s.visibility==='hidden' || Number(s.opacity)===0 || r.width<=0 || r.height<=0 || r.left < -0.5 || r.top < -0.5 || r.right > ${prepared.width}+0.5 || r.bottom > ${prepared.height}+0.5) throw new Error('Hidden or clipped text: '+text.textContent);
        const x=Math.max(0,r.left), y=Math.max(0,r.top);
        return {x,y,width:Math.min(${prepared.width},r.right)-x,height:Math.min(${prepared.height},r.bottom)-y};
      });
      return {width:${prepared.width},height:${prepared.height},theme:${JSON.stringify(theme)},nodes,edges,text_boxes,min_text_px:${prepared.min_text_px},icons:svg.querySelectorAll('image').length};
    })()`);
    const image = await send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false,
      clip: { x: 0, y: 0, width: scene.width, height: scene.height, scale: 1 } });
    return { scene, png: Buffer.from(image.data, 'base64') };
  } finally {
    await browser.close();
  }
}

export function writeSnapshot(pngPath, scenePath, result) {
  const scene = JSON.stringify(result.scene, null, 2) + '\n';
  if (Buffer.byteLength(scene) > 32 * 1024 * 1024) throw new Error('Scene exceeds 32 MiB');
  const owned = [];
  try {
    for (const [file, data] of [[pngPath, result.png], [scenePath, scene]]) {
      const fd = fs.openSync(file, 'wx');
      try {
        owned.push({ file, stat: fs.fstatSync(fd) });
        fs.writeFileSync(fd, data);
      } finally {
        fs.closeSync(fd);
      }
    }
  } catch (error) {
    for (const { file, stat } of owned) {
      try {
        const current = fs.lstatSync(file);
        if (stat.ino === current.ino && stat.dev === current.dev) fs.unlinkSync(file);
      } catch {}
    }
    throw error;
  }
}

async function main() {
  const { values } = parseArgs({ options: { doctor: { type: 'boolean' }, artifact: { type: 'string' }, model: { type: 'string' }, layout: { type: 'string' }, font: { type: 'string' }, png: { type: 'string' }, scene: { type: 'string' }, theme: { type: 'string', default: 'light' } }, strict: true });
  if (values.doctor) {
    const executable = findChrome();
    console.log(JSON.stringify({ ok: Boolean(executable), path: executable, version: null }));
    process.exitCode = executable ? 0 : 1;
    return;
  }
  for (const key of ['artifact', 'model', 'layout', 'font', 'png', 'scene']) {
    if (!values[key] || !path.isAbsolute(values[key])) throw new Error(`${key} must be an absolute path`);
  }
  if (path.resolve(values.png) === path.resolve(values.scene)) throw new Error('PNG and scene must have distinct output paths');
  for (const key of ['artifact', 'model', 'layout']) if (fs.statSync(values[key]).size > 32*1024*1024) throw new Error('Input exceeds 32 MiB');
  if (fs.statSync(values.font).size > 64*1024*1024) throw new Error('CJK font exceeds 64 MiB');
  for (const key of ['png', 'scene']) if (fs.existsSync(values[key])) throw new Error('Output already exists');
  const result = await snapshot({ html: fs.readFileSync(values.artifact, 'utf8'), model: JSON.parse(fs.readFileSync(values.model, 'utf8')), layout: JSON.parse(fs.readFileSync(values.layout, 'utf8')), theme: values.theme, font: fs.readFileSync(values.font) });
  writeSnapshot(values.png, values.scene, result);
  console.log(JSON.stringify({ ok: true, width: result.scene.width, height: result.scene.height, nodes: result.scene.nodes.length, edges: result.scene.edges.length, icons: result.scene.icons, min_text_px: result.scene.min_text_px, network: 'No page network access; CSP and protocol blocklist; isolated Chrome profile' }));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.log(JSON.stringify({ ok: false, diagnostic: error.message.slice(0, 3000) })); process.exitCode = 1; });
}
