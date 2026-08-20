/**
 * Acceptance gates — responsive layout + integration visibility.
 * Authored by the release coordinator, NOT by implementers.
 * Implementation agents may NOT modify this file.
 *
 * OWNER REQUIREMENT (verbatim):
 *   "the whole UI of the dashboard of super local memory should be responsive
 *    everywhere, whether people open it on any kind of screen (mobile, tablet,
 *    and desktop view). All three sections, column-wise, the left column, right
 *    column, and the thing in between, should be so much right and flexible."
 * Reported symptom: "where is the chat gone? It is gone on the right side."
 *
 * MEASURED BASELINE — taken in a real browser before any Wave 6 work.
 * Do NOT re-derive these; they are the ground truth this wave is built on.
 *
 *   1920 CSS px  .app = 248px + 1672px   .graph-shell = 1168px + 340px
 *                right panel x=1442 w=340 right=1782      -> CORRECT
 *   1365 CSS px  .graph-shell = 725px + 340px             -> CORRECT
 *   1000 CSS px  .graph-shell collapses to ONE column, rows 640px + 410px
 *                right panel stackedBelow=true, y > 100   -> THE DEFECT
 *                .graph-shell keeps height:calc(100vh - topbar), so the stage
 *                alone consumes the viewport and the chat/inspector sits ~640px
 *                below the fold. Nothing is clipped and there is no horizontal
 *                overflow — the panel is simply unreachable without scrolling
 *                past a full-screen canvas. That is what "the chat is gone"
 *                actually is.
 *    375 CSS px  .app = single column, sidebar off-canvas at x=-264,
 *                hamburger present, no horizontal overflow -> ALREADY CORRECT
 *
 * SO: mobile is largely handled already. This wave is NOT a responsive
 * rebuild. Do not rewrite the working mobile behaviour. The failure is the
 * middle band — roughly 640px to 1100px — where the third column becomes
 * unreachable rather than adapting.
 *
 * WHY THIS IS NOT COSMETIC: the right column carries "Ask your memory", the
 * node inspector and Quick Insights. On a tablet the product silently loses
 * its question-answering surface. 75% of SLM users are non-technical and will
 * conclude the feature does not exist.
 *
 * These are STATIC checks over CSS/markup — they cannot prove pixels. Real
 * layout verification is done by driving a browser at each breakpoint and is
 * recorded in the release notes. This file is the regression net.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI = join(__dirname, '../../src/superlocalmemory/ui');
const read = (rel) => readFileSync(join(UI, rel), 'utf8');
const designCss = () => read('css/design-system.css');

// ─────────────────────────────────────────────────────────────────────────────
// R1 — the third column must be DISCOVERABLE, not merely present
// ─────────────────────────────────────────────────────────────────────────────
//
// CORRECTION TO MY OWN FIRST DRAFT OF THIS FILE. I initially asserted that the
// stacked layout failed to release its height and lacked breakpoints. Both
// assertions passed immediately, because both were already true:
//
//   @media (max-width: 1100px) {
//     .graph-shell { grid-template-columns: 1fr; grid-template-rows: 1fr auto;
//                    height: auto; }
//     .graph-stage { height: 60vh; }
//     .inspector-scroll { max-height: 320px; }
//   }
//
// and breakpoints already exist at 1100 / 860 / 768 / 640 / 400. I then swept
// all ten panes in a real browser at 1000px AND at 375px: ZERO horizontal
// overflow anywhere. The responsive system works.
//
// The real defect is narrower and is a UX one: below 1100px the stage takes
// 60vh, so "Ask your memory", the node inspector and Quick Insights sit a full
// screen-height below the fold with NOTHING indicating they exist. The owner's
// report — "where is the chat gone? It is gone on the right side" — is a
// discoverability failure, not a layout failure. Fixing it by rewriting the
// working breakpoints would be the wrong repair and would risk the parts that
// are already correct.
describe('R1 third column discoverability below 1100px', function () {
  // A source regex CANNOT express "a user can discover this panel". My first
  // two attempts at a general affordance check both passed vacuously — one
  // matched an unrelated `.drawer` rule at design-system.css:609. So this gate
  // pins a SPECIFIC, NAMED control instead, and the behaviour it implies is
  // verified separately by driving a real browser at 1000px. A named artifact
  // is checkable; "good UX" is not.
  const CONTROL_ID = 'odg-panel-toggle';

  it(`ships a named control (#${CONTROL_ID}) to reach the stacked panel`, function () {
    const js = read('js/od-graph.js');
    assert.ok(js.includes(CONTROL_ID),
      `no #${CONTROL_ID} control exists. Below 1100px the graph stage takes ` +
      '60vh and the inspector stacks beneath it, so "Ask your memory", the ' +
      'node inspector and Quick Insights sit a full screen-height below the ' +
      'fold with nothing indicating they are there. Measured at 1000px: rows ' +
      '640px + 410px, panel y > 100. Ship a visible control that reveals the ' +
      'panel (toggle, tab, or sticky peek — mechanism is your call, but it ' +
      `must carry the id ${CONTROL_ID} so this gate and the browser check ` +
      'agree on what they are testing).\n\n' +
      'DO NOT rewrite the 1100/860/768/640 breakpoint ladder. I swept all ten ' +
      'panes in a real browser at 1000px AND 375px: zero horizontal overflow ' +
      'anywhere, sidebar correctly off-canvas at -264px on phones. That system ' +
      'works. This is the one gap in it.');
  });

  it(`only shows #${CONTROL_ID} where it is needed`, function () {
    const css = designCss();
    if (!read('js/od-graph.js').includes(CONTROL_ID)) return; // covered above
    assert.match(css, new RegExp(`${CONTROL_ID}`),
      `#${CONTROL_ID} has no styling rule. It must be hidden on wide viewports ` +
      'where the panel is already visible beside the stage — a permanent ' +
      'toggle on a 1920px desktop is clutter that solves nothing.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// R3 — floor: the parts that already work must not regress
// ─────────────────────────────────────────────────────────────────────────────
describe('R3 responsive floor (already correct — do not break)', function () {
  it('keeps the sidebar off-canvas with a toggle on phones', function () {
    const css = designCss();
    // Measured at 375px: sidebar x=-264 (off-canvas), hamburger present.
    const offCanvas = /transform:\s*translateX\(\s*-|left:\s*-|margin-left:\s*-/.test(css);
    assert.ok(offCanvas,
      'the phone layout moved the sidebar off-canvas (measured x=-264 at ' +
      '375px). That behaviour is correct and must survive this wave.');
  });

  it('keeps the app shell free of horizontal overflow', function () {
    const css = designCss();
    assert.match(css, /\.app\s*\{[^}]*grid/,
      '.app must remain a grid — it is what keeps sidebar and content from ' +
      'overflowing. Measured: no horizontal overflow at 1920, 1365, 1000 or ' +
      '375px. Do not regress that.');
  });

  it('does not reintroduce a retired implementation while restyling', function () {
    const html = read('index.html');
    assert.ok(!/<script[^>]+js\/brain\.js/.test(html),
      'js/brain.js was retired in Wave 5 — do not re-add it.');
    assert.ok(!/<script[^>]+js\/knowledge-graph\.js/.test(html),
      'js/knowledge-graph.js was retired in Wave 5 — do not re-add it.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// R4 — integration visibility (the original Wave 6 scope)
// ─────────────────────────────────────────────────────────────────────────────
describe('R4 connected integrations', function () {
  it('can report Codex as an integration', function () {
    // Measured on the live Connected clients tab: antigravity, claude code,
    // cli, copilot, cursor, mcp. Codex is absent, and the owner runs Codex.
    const js = read('js/od-brain.js');
    const py = readFileSync(
      join(UI, '../server/routes/brain.py'), 'utf8',
    );
    assert.ok(/codex/i.test(js) || /codex/i.test(py),
      'neither the Connected clients pane nor its read model knows about ' +
      'Codex, yet it is a configured MCP client on this machine.');
  });

  it('can detect bounded-loops rather than assuming absence', function () {
    const py = readFileSync(
      join(UI, '../server/routes/brain.py'), 'utf8',
    );
    assert.ok(/bounded[_-]?loop/i.test(py),
      'the brain read model never mentions bounded-loops. The owner requires ' +
      'that when Bounded Loops is installed the dashboard detects it and ' +
      'surfaces it, instead of the section silently reading as empty.');
  });
});
