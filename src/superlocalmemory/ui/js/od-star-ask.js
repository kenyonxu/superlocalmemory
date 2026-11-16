/* od-star-ask.js - a quiet GitHub star ask for people who find SuperLocalMemory useful.
 *
 * Two surfaces, one preference:
 *   - the "Star on GitHub" link in the top bar (always there, never hidden), and
 *   - a small dismissible card on the Dashboard pane, shown until the person
 *     dismisses it or clicks the star button.
 * Dismissing, or clicking either star link, is remembered in localStorage so the card
 * does not come back. Storage can be missing or throw; the card then closes for this
 * visit only. Nothing here is sent anywhere: the links open GitHub in a new tab.
 *
 * Exposes window.odStarAsk { mount(host), KEY, REPO_URL, TEXT }.
 * Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar - AGPL-3.0
 */
(function () {
  'use strict';

  var KEY = 'slm.star.dismissed';
  var REPO_URL = 'https://github.com/qualixar/superlocalmemory';
  var TEXT = 'Is SuperLocalMemory useful to you? A GitHub star helps other developers find it.';

  var _closedThisVisit = false;   // Dismiss must work even when storage is blocked

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = String(text);
    return e;
  }

  function dismissed() {
    if (_closedThisVisit) return true;
    try { return window.localStorage.getItem(KEY) === '1'; } catch (e) { return false; }
  }

  function remember() {
    _closedThisVisit = true;
    try { window.localStorage.setItem(KEY, '1'); } catch (e) { /* closed for this visit only */ }
  }

  function closeCard(card) {
    remember();
    if (card && card.parentNode) card.parentNode.removeChild(card);
  }

  function starLink(cls, label) {
    var a = el('a', cls);
    a.href = REPO_URL;
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.appendChild(el('span', 'od-star-glyph', '★'));
    a.appendChild(document.createTextNode(' ' + label));
    return a;
  }

  function buildCard() {
    var card = el('div', 'card card-pad od-star-ask');
    card.setAttribute('role', 'region');
    card.setAttribute('aria-label', 'Star SuperLocalMemory on GitHub');
    card.appendChild(el('p', 'od-star-text', TEXT));
    var actions = el('div', 'od-star-actions');
    var star = starLink('btn sm primary', 'Star on GitHub');
    star.addEventListener('click', function () { closeCard(card); });
    var later = el('button', 'btn sm ghost', 'Dismiss');
    later.type = 'button';
    later.addEventListener('click', function () { closeCard(card); });
    actions.appendChild(star);
    actions.appendChild(later);
    card.appendChild(actions);
    return card;
  }

  function mount(host) {
    if (!host || dismissed()) return;
    host.textContent = '';
    host.appendChild(buildCard());
  }

  function wireHeaderLink() {
    var link = document.getElementById('star-cta');
    if (!link) return;
    link.addEventListener('click', function () {
      remember();
      var card = document.querySelector('.od-star-ask');
      if (card) closeCard(card);
    });
  }

  function init() {
    wireHeaderLink();
    mount(document.getElementById('od-star-ask'));
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.odStarAsk = { mount: mount, KEY: KEY, REPO_URL: REPO_URL, TEXT: TEXT };
})();
