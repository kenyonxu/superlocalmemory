// Entity Explorer: people, tools and projects SLM resolved from the memories, with their facts.
import { record } from './record-lib.mjs';
const out = process.argv[2] || 'docs/assets/gifs';
const gif = await record('entities', out, async (page, begin) => {
  await page.click('.nav-link[data-tab="entities-pane"]');
  await page.waitForSelector('text=Canonical entities');
  await page.waitForTimeout(1200);
  begin();
  await page.waitForTimeout(1300);
  await page.click('text=Lumen >> nth=0');
  await page.waitForTimeout(1500);
  await page.click('text=Postgres 16 >> nth=0');
  await page.waitForTimeout(1400);
  await page.click('input[placeholder^="Search entities"]');
  await page.keyboard.type('Priya', { delay: 90 });
  await page.waitForTimeout(700);
  await page.click('text=Priya Raman >> nth=0');
  await page.waitForTimeout(1800);
}, { seconds: 8 });
console.log(gif);
