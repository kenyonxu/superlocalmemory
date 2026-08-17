/**
 * Wave 5 acceptance gates — Living Brain UI + Knowledge Graph.
 * Authored by the release coordinator, NOT by implementers.
 * Implementation agents may NOT modify this file.
 *
 * WHAT THIS FILE PROVES, AND WHAT IT DOES NOT
 * -------------------------------------------
 * These are static/source and jsdom-level gates. They CANNOT prove that pixels
 * appear on a canvas — jsdom has no WebGL and no real layout. Proving the graph
 * actually renders is done separately, by driving the real browser against a
 * repo-served daemon, and that evidence is recorded in the release notes.
 *
 * So: this file is the REGRESSION net (cheap, CI-able, catches re-breakage).
 * The live browser run is the PROOF. Both are required. Neither substitutes.
 *
 * MEASURED BASELINE (live 4.0.5 daemon on :8765, captured before any Wave 5 work)
 * ------------------------------------------------------------------------------
 * Knowledge Graph, cold load into #graph-pane:
 *   - canvas exists, 1280x1320 backing / 640x660 CSS, and EVERY PIXEL IS ZERO
 *   - node budget slider reads 120; header reads "181 nodes . 373 edges"
 *   - legend populated ("Cluster 0 (120)", "concept (68)", ...) => data DID load
 *   - window.sigmaInstance === null, window.sigmaGraph === null  (dead legacy path)
 *   - #graph-tab (legacy container) is 0x0, offsetParent null, 0 children
 *   => the data loads and the renderer draws nothing. Owner-reported workaround
 *      was to drag the budget slider to 20, which re-triggers load() and paints.
 *
 * Living Brain, Overview tab:
 *   - "Adaptive ranking progress ... 5,339 / 200 signals ... 100%"   (past denominator)
 *   - "Ranking phase: Ml-Model"                                     (mangled jargon)
 *   - "Feedback signals 5,339" beside "3 unique queries"
 * Reward signal tab:
 *   - "Average settled reward 0.500", distribution 100% Neutral / 0% / 0%
 *     => every label sits on the untrained prior, rendered as if it were a finding
 * Behaviour tab:
 *   - "Layer 1 . confidence-weighted", "Layer 3 . sequence & temporal"
 * Source quality tab:
 *   - 18 rows, every one exactly 0.50, each NAMED BY A 64-HEX INTERNAL ID, e.g.
 *     "http:daemon-capability:062b81f8fe47a22ebba2fd6334dd0eec93565b3f9..."
 *
 * DESIGN PRINCIPLE THIS WAVE ENFORCES
 * -----------------------------------
 * 75% of SLM users are non-technical. The brain UI must distinguish MEASURED
 * from NOT-YET-MEASURED, and must never render an uninformative prior as a
 * finding. An honest "nothing learned yet, here is what would produce this" is
 * strictly better than 18 rows of 0.50 under a heading that says "quality".
 * This is the same fail-closed honesty Wave 4 put into brain/truth.py, applied
 * to the pixels.
 *
 * LESSON APPLIED FROM WAVE 2: a gate that asserts internal shape pushes bad
 * implementations (a previous gate asserted attribute mutation and a frozen
 * config got un-frozen to satisfy it). Assert user-visible outcomes and
 * structural facts, not internals.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const UI = join(__dirname, '../../src/superlocalmemory/ui');

const read = (rel) => readFileSync(join(UI, rel), 'utf8');
const indexHtml = () => read('index.html');
const odGraph = () => read('js/od-graph.js');
const odBrain = () => read('js/od-brain.js');

/** Strip block and line comments so copy gates gauge SHIPPED STRINGS, not prose.
 *  (Wave 4 lesson: my own jargon gate matched its own explanatory comment.) */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ');
}

// ─────────────────────────────────────────────────────────────────────────────
// G1 — ONE implementation per surface
// ─────────────────────────────────────────────────────────────────────────────
describe('G1 single implementation per surface', function () {
  it('does not ship two brain implementations', function () {
    const html = indexHtml();
    const legacy = /<script[^>]+js\/brain\.js/.test(html);
    const od = /<script[^>]+js\/od-brain\.js/.test(html);
    assert.ok(!(legacy && od),
      'index.html loads BOTH js/brain.js (1400 lines, legacy) and js/od-brain.js. ' +
      'Two brain implementations execute on every page load. Retire the legacy one.');
  });

  it('does not ship two knowledge-graph implementations', function () {
    const html = indexHtml();
    const legacy = /<script[^>]+js\/knowledge-graph\.js/.test(html);
    const od = /<script[^>]+js\/od-graph\.js/.test(html);
    assert.ok(!(legacy && od),
      'index.html loads BOTH js/knowledge-graph.js (Sigma-based, its container ' +
      '#graph-tab measured 0x0 with 0 children on the live daemon) and ' +
      'js/od-graph.js (canvas-2D, the one that actually owns #odg-stage). ' +
      'The dead Sigma path still pulls in graphology + sigma vendor bundles.');
  });

  it('does not load vendor bundles that no live module uses', function () {
    const html = indexHtml();
    const loadsSigma = /<script[^>]+vendor\/sigma\.min\.js/.test(html);
    if (!loadsSigma) return; // already retired
    const liveUsesSigma = /new\s+Sigma\s*\(/.test(odGraph());
    assert.ok(liveUsesSigma,
      'index.html still loads vendor/sigma.min.js + graphology, but the live ' +
      'renderer od-graph.js draws with canvas 2D and never constructs Sigma. ' +
      'Dead vendor weight on every page load.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G2 — Knowledge graph must paint on a cold load
// ─────────────────────────────────────────────────────────────────────────────
describe('G2 knowledge graph cold load', function () {
  it('defaults the node budget to the owner-specified 50', function () {
    const m = odGraph().match(/MAX_NODES\s*=\s*(\d+)/);
    assert.ok(m, 'MAX_NODES not found in od-graph.js');
    const v = Number(m[1]);
    assert.ok(v >= 50 && v <= 60,
      `MAX_NODES default is ${v}; owner set it to "50 only or 50-60". ` +
      'At 120 the first paint is both slow and (today) blank.');
  });

  it('keeps the slider default in sync with MAX_NODES', function () {
    const src = odGraph();
    const hardcoded = src.match(/id="odg-budget"[^>]*value="(\d+)"/);
    assert.ok(!hardcoded,
      `the budget slider hardcodes value="${hardcoded && hardcoded[1]}" instead of ` +
      'deriving it from MAX_NODES. These drift apart silently — on the live ' +
      'daemon the slider read 120 while the header reported 181 nodes.');
  });

  it('re-renders when its container gains size or becomes visible', function () {
    const src = odGraph();
    const reactive = /ResizeObserver|IntersectionObserver/.test(src);
    assert.ok(reactive,
      'od-graph.js sizes itself from stage.clientWidth/clientHeight but never ' +
      'observes the container. On a cold load into a pane that is not yet laid ' +
      'out, width/height are 0 and the canvas stays blank forever — measured ' +
      'live: every pixel zero while 181 nodes were loaded and the legend was ' +
      'populated. Observe the stage and re-render when it acquires size.');
  });

  it('refuses to draw into a zero-sized stage instead of silently painting nothing', function () {
    const src = stripComments(odGraph());
    const guards = /(clientWidth|clientHeight|\bW\b|\bH\b)\s*(===?\s*0|<=?\s*0|\|\||\?)/.test(src)
      || /if\s*\(\s*!?\s*(W|H|w|h)\s*(\|\||&&|<)/.test(src);
    assert.ok(guards,
      'no zero-size guard around the draw path. A renderer that computes W=0,H=0 ' +
      'and proceeds produces exactly the observed failure: a correctly-sized ' +
      'canvas element containing nothing.');
  });

  it('fits the camera to the graph bounds after layout', function () {
    const src = odGraph();
    assert.match(src, /fitTo|fitBounds|fitView|autoFit|centerOn|fit\s*\(/,
      'no fit-to-bounds step. Even once nodes paint, the viewport is not framed ' +
      'to them, so a cold load can show empty space beside an off-screen graph.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G3 — the brain must not present an untrained prior as a finding
// ─────────────────────────────────────────────────────────────────────────────
describe('G3 brain honesty', function () {
  it('never renders a progress value above its own denominator', function () {
    const src = stripComments(odBrain());
    const clamped = /Math\.min\s*\(\s*100/.test(src) || /Math\.min\s*\(\s*1\s*,/.test(src)
      || /clamp\s*\(/.test(src);
    assert.ok(clamped,
      'the adaptive-ranking bar rendered "5,339 / 200 signals . 100%" on the ' +
      'live daemon — a count 26x past its own target, presented as progress. ' +
      'Clamp the ratio and stop showing a completed phase as in-progress.');
  });

  it('stops showing progress toward a target that is already passed', function () {
    // STRENGTHENED after the first pass. The original check above only proved the
    // PERCENTAGE was clamped with Math.min — which it already was. So the gate
    // went green while the pane still rendered the literal string
    // "5,339 / 200 signals". The percentage was never the absurdity; the FRACTION
    // was. A gate that green-lights the exact defect it was written for is worse
    // than no gate, so this asserts the missing branch instead of the clamp.
    // Second attempt. My first strengthening searched for words like "complete"
    // or "done" — and matched the CSS rule ".phase.done{...}". Vocabulary guesses
    // are not evidence. This targets the exact concatenation that produces the
    // defect, at od-brain.js:308:
    //     text: fmtNum(signals) + ' / ' + fmtNum(mlGate) + ' signals'
    const src = stripComments(odBrain());
    const rawFraction = /fmtNum\(\s*signals\s*\)\s*\+\s*['"]\s*\/\s*['"]/.test(src);
    assert.ok(!rawFraction,
      'od-brain.js still builds the progress label by concatenating the raw ' +
      'signal count with the phase gate: fmtNum(signals) + " / " + fmtNum(mlGate). ' +
      'On the live daemon that renders "5,339 / 200 signals" — a fraction 26x past ' +
      'its own denominator — because in-progress is the only state the card can ' +
      'express. Once a phase is complete, say so and show what comes next.');
  });

  it('distinguishes an unmeasured prior from a measured score', function () {
    const src = stripComments(odBrain());
    assert.match(src, /unmeasured|not_measured|notMeasured|no_signal|noSignal|insufficient|prior/i,
      'Source quality listed 18 rows at exactly 0.50 and Reward signal reported ' +
      '"Average settled reward 0.500 / 100% Neutral". Those are untrained priors, ' +
      'not measurements. The UI must be able to say "not measured yet" instead of ' +
      'dressing a default up as a result.');
  });

  it('never shows a raw internal capability id as a source name', function () {
    const src = stripComments(odBrain());
    const humanises = /(source|label|name|display)/i.test(src)
      && /(slice|substring|split|replace|prettif|humanis|humaniz|friendly|shorten|truncate)/i.test(src);
    assert.ok(humanises,
      'Source quality rendered names like ' +
      '"http:daemon-capability:062b81f8fe47a22ebba2fd6334dd0eec93565b3f9c69be8..." ' +
      'straight to a non-technical user, 18 times. Map these to something a ' +
      'person can act on, or do not show the list.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G4 — plain language (75% of users are non-technical)
// ─────────────────────────────────────────────────────────────────────────────
describe('G4 plain language', function () {
  // Terms measured on the live Brain pane that carry no meaning for a
  // non-technical reader. Matched against shipped strings only.
  const BANNED = [
    'settled numeric label',
    'source-outcome posterior',
    'confidence-weighted',
    'not supported by this read model',
    'Ml-Model',
  ];

  for (const term of BANNED) {
    it(`does not ship the phrase "${term}"`, function () {
      const src = stripComments(odBrain());
      assert.ok(!src.includes(term),
        `"${term}" is rendered to users on the Brain pane. It was measured on ` +
        'the live 4.0.5 dashboard. Replace with language a non-technical owner ' +
        'of their own memory can act on.');
    });
  }

  it('does not leak internal layer numbering into user copy', function () {
    const src = stripComments(odBrain());
    assert.ok(!/Layer\s*[0-9]/.test(src),
      'the Behaviour tab shows "Layer 1 . confidence-weighted" and ' +
      '"Layer 3 . sequence & temporal". Layer numbers are an implementation ' +
      'detail; they tell the reader nothing about what was learned.');
  });

  it('explains an empty state instead of only stating emptiness', function () {
    const src = odBrain();
    // Every empty state should tell the user what would fill it. The live pane
    // already does this well in places ("These appear when the same behaviour is
    // detected in 2+ projects") — this gate stops that quality from regressing
    // as panes are rewritten.
    const explains = (src.match(/These (appear|emerge)|will appear|once you|after /gi) || []).length;
    assert.ok(explains >= 3,
      `only ${explains} empty states explain what would populate them. A blank ` +
      'panel with "no data" teaches a non-technical user nothing and reads as ' +
      'broken software.');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// G5 — floor: properties already correct that this wave must not break
// ─────────────────────────────────────────────────────────────────────────────
describe('G5 regression floor', function () {
  it('keeps the observation-only disclaimer on agent evidence', function () {
    const src = odBrain();
    assert.match(src, /do not change recall, ranking, or model routing/,
      'the observation-only disclaimer was removed. Wave 4 established that SLM ' +
      'stores integration-reported evidence WITHOUT verifying it; the UI must ' +
      'keep saying so or the product overclaims.');
  });

  it('keeps learning.db and memory.db visibly separate', function () {
    const src = odBrain();
    assert.match(src, /learning\.db/,
      'the "learning.db — separate from memory.db" framing was removed. Users ' +
      'need to see that behavioural learning is stored apart from their memories.');
  });

  it('keeps the graph render budget bounded', function () {
    const src = odGraph();
    assert.match(src, /PHYSICS_MAX_NODES/,
      'the physics node cap was removed — force simulation over an unbounded ' +
      'node set will stall the pane on large stores (existing perf budget test).');
  });
});
