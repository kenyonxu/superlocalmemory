// Shared helpers for the README GIFs. Run against a SANDBOX daemon only
// (see scripts/media/README.md): never the daemon that holds your own memories.
import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';

export const BASE = process.env.SLM_DEMO_URL || 'http://127.0.0.1:18765';
const exe = process.env.CHROME || path.join(process.env.HOME,
  'Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing');

if (/:(8765|8767)\b/.test(BASE)) throw new Error('Refusing to record against the default SLM ports.');

export async function record(name, outDir, flow, { seconds = 8, fps = 10, colors = 96, dither = 'bayer:bayer_scale=5', clip = null } = {}) {
  const tmp = path.resolve(outDir, `.rec-${name}`);
  rmSync(tmp, { recursive: true, force: true });
  mkdirSync(tmp, { recursive: true });
  const browser = await chromium.launch({ executablePath: exe });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  await page.addStyleTag({ content: '*{caret-color:#c4b5fd}' });
  // Frames are screenshots with their own timestamps, so the GIF keeps real timing.
  const frames = [];
  let recording = false;
  const grab = (async () => {
    while (!recording) await new Promise((r) => setTimeout(r, 5));
    while (recording) {
      const t = Date.now();
      const file = path.join(tmp, `f${String(frames.length).padStart(5, '0')}.jpg`);
      await page.screenshot({ path: file, type: 'jpeg', quality: 88, ...(clip ? { clip } : {}) });
      frames.push({ t, file });
    }
  })();
  const flowDone = (async () => {
    // The flow prepares the page (not recorded), calls begin(), then plays the story.
    await flow(page, () => { recording = true; });
    await page.waitForTimeout(400);
    recording = false;
  })();
  await Promise.all([flowDone, grab]);
  await browser.close();
  const list = frames.map((f, i) => {
    const next = frames[i + 1]?.t ?? f.t + 100;
    return `file '${f.file}'\nduration ${((next - f.t) / 1000).toFixed(3)}`;
  }).join('\n') + `\nfile '${frames[frames.length - 1].file}'\n`;
  const listFile = path.join(tmp, 'frames.txt');
  writeFileSync(listFile, list);
  const gif = path.resolve(outDir, `${name}.gif`);
  const vf = `fps=${fps},scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=${colors}:stats_mode=diff[p];[b][p]paletteuse=dither=${dither}:diff_mode=rectangle`;
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', listFile, '-t', String(seconds), '-vf', vf, '-loop', '0', gif]);
  rmSync(tmp, { recursive: true, force: true });
  return gif;
}

export async function typeInto(page, selector, text, delay = 28) {
  await page.click(selector, { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type(selector, text, { delay });
  await page.keyboard.press('Enter');
}
