// Knowledge graph: the layout settles, inspect a person, inspect a cluster, zoom.
import { record } from './record-lib.mjs';
const out = process.argv[2] || 'docs/assets/gifs';

// The graph is a canvas, so nodes are found by colour: centroid of the first blob of a colour.
const findBlobs = (page) => page.evaluate(() => {
  const cv = document.querySelector('#graph-pane canvas');
  const r = cv.getBoundingClientRect();
  const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height;
  const d = ctx.getImageData(0, 0, W, H).data;
  const sx = W / r.width, sy = H / r.height;
  const seen = new Uint8Array(W * H);
  const near = (i, c) => Math.abs(d[i] - c[0]) < 28 && Math.abs(d[i + 1] - c[1]) < 28
    && Math.abs(d[i + 2] - c[2]) < 28 && d[i + 3] > 200;
  const targets = { gold: [232, 193, 58], person: [74, 111, 165] };
  const found = [];
  for (const [name, c] of Object.entries(targets)) {
    for (let y = 0; y < H; y += 2) for (let x = 0; x < W; x += 2) {
      if (seen[y * W + x] || !near((y * W + x) * 4, c)) continue;
      let sxm = 0, sym = 0, n = 0;
      const st = [[x, y]]; seen[y * W + x] = 1;
      while (st.length) {
        const [cx, cy] = st.pop(); sxm += cx; sym += cy; n++;
        for (const [dx, dy] of [[2, 0], [-2, 0], [0, 2], [0, -2]]) {
          const nx = cx + dx, ny = cy + dy;
          if (nx < 0 || ny < 0 || nx >= W || ny >= H || seen[ny * W + nx]) continue;
          if (near((ny * W + nx) * 4, c)) { seen[ny * W + nx] = 1; st.push([nx, ny]); }
        }
      }
      if (n > 20) found.push({ name, x: r.left + sxm / n / sx, y: r.top + sym / n / sy });
    }
  }
  return found;
});

async function clickAt(page, p) {
  await page.mouse.move(p.x, p.y, { steps: 14 });
  await page.waitForTimeout(150);
  await page.mouse.click(p.x, p.y);
}

const pickPerson = (nodes) => nodes.find((n) => n.name === 'person' && n.x > 440 && n.x < 900 && n.y > 220 && n.y < 640);

const gif = await record('knowledge-graph', out, async (page, begin) => {
  await page.click('.nav-link[data-tab="graph-pane"]');
  await page.waitForSelector('text=/\\d+ nodes/');
  begin();                                           // recording starts as the nodes appear
  await page.waitForTimeout(2600);                   // the layout settles
  let person = pickPerson(await findBlobs(page));
  if (person) {
    await page.mouse.move(person.x, person.y, { steps: 14 });
    person = pickPerson(await findBlobs(page)) || person;   // it may have drifted while the cursor travelled
    await page.mouse.click(person.x, person.y);
    await page.waitForTimeout(2200);
  }
  await page.mouse.move(600, 420, { steps: 6 });
  for (let i = 0; i < 3; i++) { await page.mouse.wheel(0, -220); await page.waitForTimeout(260); }
  await page.waitForTimeout(700);
}, { seconds: 8, fps: 8, colors: 64, dither: 'bayer:bayer_scale=3' });
console.log(gif);
