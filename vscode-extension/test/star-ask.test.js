const test = require('node:test');
const assert = require('node:assert');

// Compiled by `npm test` (tsc) before this runs; the module imports nothing from vscode.
const {
  askForStar, scheduleStarAsk, REPO_URL, STAR_FLAG, STAR_MESSAGE, STAR_ACTION, LATER_ACTION,
} = require('../out/starAsk.js');

function fakeHost({ choice, flag = false, failOn } = {}) {
  const state = { flag, opened: [], shown: [] };
  return {
    state,
    getFlag: (key) => { assert.strictEqual(key, STAR_FLAG); return state.flag; },
    setFlag: async (key, value) => {
      if (failOn === 'setFlag') { throw new Error('storage'); }
      assert.strictEqual(key, STAR_FLAG); state.flag = value;
    },
    showMessage: async (message, ...actions) => { state.shown.push({ message, actions }); return choice; },
    openUrl: async (url) => { state.opened.push(url); },
  };
}

test('the message and actions are the agreed copy', async () => {
  const host = fakeHost({ choice: undefined });
  assert.strictEqual(await askForStar(host), true);
  assert.deepStrictEqual(host.state.shown, [
    { message: 'Is SuperLocalMemory useful? A GitHub star helps other developers find it.',
      actions: ['Star on GitHub', 'Not now'] },
  ]);
  assert.strictEqual(STAR_MESSAGE, host.state.shown[0].message);
});

test('Star on GitHub opens the repository and sets the flag', async () => {
  const host = fakeHost({ choice: STAR_ACTION });
  await askForStar(host);
  assert.deepStrictEqual(host.state.opened, ['https://github.com/qualixar/superlocalmemory']);
  assert.strictEqual(REPO_URL, host.state.opened[0]);
  assert.strictEqual(host.state.flag, true);
});

test('Not now sets the flag and opens nothing', async () => {
  const host = fakeHost({ choice: LATER_ACTION });
  await askForStar(host);
  assert.deepStrictEqual(host.state.opened, []);
  assert.strictEqual(host.state.flag, true);
});

test('closing the message without a choice also counts, so it is asked once ever', async () => {
  const host = fakeHost({ choice: undefined });
  await askForStar(host);
  assert.strictEqual(host.state.flag, true);
  assert.strictEqual(await askForStar(host), false);
  assert.strictEqual(host.state.shown.length, 1);
});

test('an existing flag means no message at all', async () => {
  const host = fakeHost({ flag: true });
  assert.strictEqual(await askForStar(host), false);
  assert.strictEqual(host.state.shown.length, 0);
});

test('storage failure never throws and shows nothing', async () => {
  const host = fakeHost({ failOn: 'setFlag' });
  assert.strictEqual(await askForStar(host), false);
  assert.strictEqual(host.state.shown.length, 0);
});

test('the ask is delayed and can be cancelled', async () => {
  const host = fakeHost({ choice: LATER_ACTION });
  const cancel = scheduleStarAsk(host, 20);
  assert.strictEqual(host.state.shown.length, 0, 'not shown immediately');
  cancel();
  await new Promise((r) => setTimeout(r, 60));
  assert.strictEqual(host.state.shown.length, 0, 'cancelled before it fired');
  scheduleStarAsk(host, 10);
  await new Promise((r) => setTimeout(r, 60));
  assert.strictEqual(host.state.shown.length, 1);
});
