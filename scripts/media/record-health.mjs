// Memory health card: a repair that needs your say-so, Fix, then Undo.
import { record } from './record-lib.mjs';
const out = process.argv[2] || 'docs/assets/gifs';
const gif = await record('memory-health', out, async (page, begin) => {
  await page.click('.nav-link[data-tab="health-pane"]');
  await page.waitForSelector('#memory-health-card');
  await page.waitForSelector('text=/Fix 4 hidden/');
  await page.waitForTimeout(500);
  begin();
  await page.waitForTimeout(1500);
  await page.hover('text=/Fix 4 hidden/');
  await page.waitForTimeout(500);
  await page.click('text=/Fix 4 hidden/');
  await page.waitForSelector('text=/Undo the last change/');
  await page.waitForTimeout(1700);
  await page.hover('text=/Undo the last change/');
  await page.waitForTimeout(500);
  await page.click('text=/Undo the last change/');
  await page.waitForSelector('text=/Undone:/');
  await page.waitForTimeout(1600);
}, { seconds: 8, clip: { x: 248, y: 76, width: 1032, height: 256 } });
console.log(gif);
