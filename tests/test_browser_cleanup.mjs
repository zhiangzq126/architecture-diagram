import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { ChromeVisualBrowser } from '../modules/archify/bin/visual-check.mjs';

for (const exited of [false, true]) {
  test(`close releases inherited pipes when Chrome ${exited ? 'has exited' : 'is running'}`, async () => {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'archify-cleanup-test-'));
    const child = new EventEmitter();
    child.exitCode = exited ? 0 : null;
    child.signalCode = null;
    child.stdio = [null, null, new PassThrough(), new PassThrough(), new PassThrough()];
    const signals = [];
    child.kill = signal => {
      signals.push(signal);
      queueMicrotask(() => { child.signalCode = signal; child.emit('exit', null, signal); });
    };
    let failure;
    const browser = Object.assign(Object.create(ChromeVisualBrowser.prototype), {
      child, profileRoot: profile, cdp: { failAll(error) { failure = error; } },
    });
    try {
      await browser.close();
      assert.equal(failure.message, 'visual-check finished');
      assert.deepEqual(signals, exited ? [] : ['SIGTERM']);
      assert.ok(child.stdio.filter(Boolean).every(stream => stream.destroyed));
      assert.equal(fs.existsSync(profile), false);
      await browser.close();
      assert.deepEqual(signals, exited ? [] : ['SIGTERM']);
    } finally {
      for (const stream of child.stdio) stream?.destroy();
      fs.rmSync(profile, { recursive: true, force: true });
    }
  });
}
