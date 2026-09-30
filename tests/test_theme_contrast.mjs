import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const TEMPLATE = new URL('../modules/archify/assets/template.html', import.meta.url);
const MIN_CONTRAST = 4.5;
const NODE_TYPES = ['frontend', 'backend', 'database', 'cloud', 'security', 'messagebus', 'external'];
const REQUIRED = ['bg', 'mask', 'lane-fill', 'text-dim', 'text-muted', ...NODE_TYPES.map((type) => `${type}-fill`)];
const html = readFileSync(TEMPLATE, 'utf8');
const css = [...html.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)]
  .map((match) => match[1]).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');

function block(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\s+/g, '\\s*');
  const matches = [...source.matchAll(new RegExp(`(?:^|[{}])\\s*${escaped}\\s*\\{`, 'g'))];
  assert.equal(matches.length, 1, `expected one CSS block for ${selector}`);
  const start = matches[0].index + matches[0][0].indexOf(selector.split(/\s/)[0]);
  const opening = matches[0].index + matches[0][0].length - 1;
  let depth = 1;
  let end = opening + 1;
  for (; end < source.length && depth; end += 1) {
    if (source[end] === '{') depth += 1;
    if (source[end] === '}') depth -= 1;
  }
  assert.equal(depth, 0, `unclosed CSS block for ${selector}`);
  return { body: source.slice(opening + 1, end - 1), start, end };
}

function palette(source, selector) {
  const declarations = Object.fromEntries([...block(source, selector).body
    .matchAll(/--([\w-]+)\s*:\s*([^;{}]+);/g)].map((match) => [match[1], match[2].trim()]));
  return Object.fromEntries(REQUIRED.map((name) => {
    assert.ok(Object.hasOwn(declarations, name), `${selector} is missing --${name}`);
    return [name, color(declarations[name])];
  }));
}

function color(value) {
  const hex = /^#([\da-f]{3}|[\da-f]{6})$/i.exec(value);
  if (hex) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit.repeat(2)).join('') : hex[1];
    return [0, 2, 4].map((offset) => parseInt(digits.slice(offset, offset + 2), 16)).concat(1);
  }
  const rgba = /^rgba\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*\)$/i.exec(value);
  assert.ok(rgba, `unsupported CSS color: ${value}`);
  const channels = rgba.slice(1).map(Number);
  assert.ok(channels.every((channel, index) => Number.isFinite(channel)
    && channel >= 0 && channel <= (index === 3 ? 1 : 255)), `invalid CSS color: ${value}`);
  return channels;
}

function over(foreground, background) {
  const alpha = foreground[3] + background[3] * (1 - foreground[3]);
  if (alpha === 0) return [0, 0, 0, 0];
  return foreground.slice(0, 3).map((channel, index) =>
    (channel * foreground[3] + background[index] * background[3] * (1 - foreground[3])) / alpha).concat(alpha);
}

function luminance(rgba) {
  assert.equal(rgba[3], 1, 'resolve transparency before calculating luminance');
  const linear = rgba.slice(0, 3).map((channel) => {
    const srgb = channel / 255;
    return srgb <= 0.04045 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function contrast(foreground, background) {
  const a = luminance(over(foreground, background));
  const b = luminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function dimSurfaces(p) {
  assert.equal(p.bg[3], 1, '--bg must be opaque');
  assert.equal(p.mask[3], 1, '--mask must hide underlying geometry');
  return [
    ['bg', p.bg],
    ['mask over bg', over(p.mask, p.bg)],
    ['lane-fill over bg', over(p['lane-fill'], p.bg)],
  ];
}

function mutedSurfaces(p) {
  assert.equal(p.mask[3], 1, 'node fills must composite over an opaque mask');
  return NODE_TYPES.map((type) => [`${type}-fill over mask`, over(p[`${type}-fill`], p.mask)]);
}

function measurements(foreground, surfaces) {
  return surfaces.map(([surface, background]) => ({ surface, ratio: contrast(foreground, background) }));
}

function enforce(label, values) {
  const failures = values.filter(({ ratio }) => !(ratio >= MIN_CONTRAST));
  assert.equal(failures.length, 0, `${label}: contrast below ${MIN_CONTRAST}: ${failures
    .map(({ surface, ratio }) => `${surface} = ${ratio.toFixed(6)}:1`).join(', ')}`);
}

function reportMinimum(t, label, values) {
  const minimum = values.reduce((a, b) => a.ratio < b.ratio ? a : b);
  t.diagnostic(`${label}: minimum ${minimum.ratio.toFixed(6)}:1 on ${minimum.surface}`);
}

const printBlock = block(css, '@media print');
const screenCss = css.slice(0, printBlock.start) + css.slice(printBlock.end);
const palettes = [
  ['classic dark', palette(screenCss, ':root, [data-theme="dark"]'), '#475569'],
  ['classic light', palette(screenCss, '[data-theme="light"]'), '#94a3b8'],
  ['signal-flow dark', palette(screenCss, '[data-preset="signal-flow"][data-theme="dark"]'), '#52667f'],
  ['signal-flow light', palette(screenCss, '[data-preset="signal-flow"][data-theme="light"]'), '#8aa2b4'],
  ['print palette', palette(printBlock.body, ':root, [data-theme="dark"], [data-theme="light"]')],
];

function close(actual, expected) {
  assert.ok(Math.abs(actual - expected) < 1e-12, `${actual} != ${expected}`);
}

function closeColor(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((channel, index) => close(channel, expected[index]));
}

test('known black/white contrast is 21:1 in either direction', () => {
  assert.equal(contrast(color('#000000'), color('#ffffff')), 21);
  assert.equal(contrast(color('#fff'), color('#000')), 21);
});

test('identical opaque colors have contrast 1:1', () => {
  for (const value of ['#000000', '#ffffff', '#567084', '#808080']) {
    assert.equal(contrast(color(value), color(value)), 1);
  }
});

test('sRGB luminance uses channel weights and both transfer-function branches', () => {
  close(luminance(color('#ff0000')), 0.2126);
  close(luminance(color('#00ff00')), 0.7152);
  close(luminance(color('#0000ff')), 0.0722);
  close(luminance(color('#0a0a0a')), (10 / 255) / 12.92);
  close(luminance(color('#0b0b0b')), 0.003346535763899161);
  close(contrast(color('#808080'), color('#000000')), 5.317210002277984);
});

test('rgba alpha composites in sRGB without rounding or treating RGB as opaque', () => {
  const black = color('#000000');
  const white = color('#ffffff');
  closeColor(over(color('rgba(0, 0, 0, 0.5)'), white), [127.5, 127.5, 127.5, 1]);
  closeColor(over(color('rgba(255, 0, 0, 0.5)'), color('#0000ff')), [127.5, 0, 127.5, 1]);
  closeColor(over(color('rgba(255, 0, 0, 0)'), black), black);
  closeColor(over(color('rgba(255, 0, 0, 1)'), black), color('#ff0000'));
  close(contrast(color('rgba(0, 0, 0, 0.5)'), white), 3.976653024912438);
  assert.equal(contrast(color('rgba(0, 0, 0, 0)'), white), 1);
});

test('source-over preserves alpha for translucent layers until an opaque backdrop is supplied', () => {
  const red = color('rgba(255, 0, 0, 0.5)');
  const blue = color('rgba(0, 0, 255, 0.5)');
  const composite = over(red, blue);
  closeColor(composite, [170, 0, 85, 0.75]);
  closeColor(over(composite, color('#ffffff')), [191.25, 63.75, 127.5, 1]);
  closeColor(over(red, over(blue, color('#ffffff'))), over(composite, color('#ffffff')));
  closeColor(over(color('rgba(1, 2, 3, 0)'), color('rgba(4, 5, 6, 0)')), [0, 0, 0, 0]);
  assert.throws(() => luminance(composite), /resolve transparency/);
});

test('CSS extraction separates screen and print palettes and rejects missing selectors or values', (t) => {
  t.diagnostic('Palette-only coverage, not global WCAG compliance: excludes font sizes, full gradient sampling, blueprint/editorial and browser print cascade.');
  const fixture = ':root, [data-theme="dark"] { --bg: #000000; }\n'
    + '@media print { :root, [data-theme="dark"] { --bg: #ffffff; } }';
  const print = block(fixture, '@media print');
  const screen = fixture.slice(0, print.start) + fixture.slice(print.end);
  assert.match(block(screen, ':root, [data-theme="dark"]').body, /#000000/);
  assert.match(block(print.body, ':root, [data-theme="dark"]').body, /#ffffff/);
  assert.throws(() => block(screen, '[data-theme="light"]'), /expected one CSS block/);
  assert.throws(() => palette(screen, ':root, [data-theme="dark"]'), /missing --mask/);
  assert.throws(() => color('rgba(1, 2, 3, 2)'), /invalid CSS color/);
});

for (const [name, p, oldDim] of palettes) {
  test(`${name}: dim meets 4.5:1 on bg, opaque mask and composited lane`, (t) => {
    const values = measurements(p['text-dim'], dimSurfaces(p));
    assert.equal(values.length, 3);
    reportMinimum(t, `${name} dim`, values);
    enforce(`${name} dim`, values);
  });

  test(`${name}: muted meets 4.5:1 on all seven node fills composited over mask`, (t) => {
    const values = measurements(p['text-muted'], mutedSurfaces(p));
    assert.equal(values.length, 7);
    reportMinimum(t, `${name} muted`, values);
    enforce(`${name} muted`, values);
  });

  if (oldDim) {
    test(`${name}: old dim ${oldDim} fails the same gate on every actual dim surface`, (t) => {
      const values = measurements(color(oldDim), dimSurfaces(p));
      for (const value of values) {
        assert.ok(value.ratio < MIN_CONTRAST, `${name} old dim on ${value.surface}: ${value.ratio}:1`);
        assert.throws(() => enforce(`${name} old dim`, [value]), /contrast below 4\.5/);
      }
      t.diagnostic(`${name} old dim: ${values.map(({ surface, ratio }) => `${surface} ${ratio.toFixed(6)}:1`).join(', ')}`);
    });
  }
}
