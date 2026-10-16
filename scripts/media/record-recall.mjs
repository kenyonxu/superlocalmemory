// Recall in the dashboard: a real answer, a nonsense question, then the closest memories.
import { record, typeInto } from './record-lib.mjs';
const out = process.argv[2] || 'docs/assets/gifs';
const box = 'input[placeholder^="Search all"]';
const steps = [
  ['how do we roll back the API', 'FIRST:blue-green'],
  ['zxqv blorptangle quux', "That isn't in SuperLocalMemory."],
  ['What was the revenue last quarter', 'closest memories'],
];
const gif = await record('recall-tiers', out, async (page, begin) => {
  await page.click('.nav-link[data-tab="memories-pane"]');
  await page.waitForTimeout(700);
  await page.click('text=All memories');
  await page.waitForSelector(box);
  await page.waitForTimeout(900);
  begin();
  for (const [q, expect] of steps) {
    await typeInto(page, box, q, 18);
    await page.waitForFunction((t) => t.startsWith('FIRST:')
      ? ([...document.querySelectorAll('[id$="-tbl-wrap"]')].find((e) => e.offsetParent)?.querySelectorAll('tbody tr')[0]?.innerText || '').includes(t.slice(6))
      : document.body.innerText.includes(t), expect, { timeout: 8000 });
    await page.waitForTimeout(1050);
  }
}, { seconds: 8 });
console.log(gif);
