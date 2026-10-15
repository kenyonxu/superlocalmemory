// Daily dashboard tasks. Uses the same checked-recall verdict as Recall Lab.
(function () {
  'use strict';
  function node(tag, text, id) {
    var n = document.createElement(tag);
    if (text) n.textContent = text;
    if (id) n.id = id;
    return n;
  }
  function mount(pane) {
    if (!pane || pane.querySelector('#daily-tasks')) return;
    var card = node('section', '', 'daily-tasks'); card.className = 'card card-pad dash-ask';
    var head = node('div'); head.className = 'dash-ask-head';
    head.appendChild(node('h3', 'Ask your memory'));
    head.appendChild(node('span', 'Answers come from your saved notes, with the source shown.'));
    card.appendChild(head);
    var form = node('form', '', 'daily-question-form');
    var label = node('label', 'What would you like to know?'); label.htmlFor = 'daily-question';
    var question = node('input', '', 'daily-question'); question.type = 'search'; question.maxLength = 500;
    question.className = 'form-control';
    var ask = node('button', 'Ask'); ask.type = 'submit'; ask.className = 'btn primary';
    label.className = 'visually-hidden';
    question.placeholder = 'What would you like to know?';
    question.setAttribute('autocomplete', 'off');
    form.className = 'dash-ask-form';
    var row = node('div'); row.className = 'dash-ask-row'; row.append(question, ask);
    form.append(label, row);
    var notice = node('p', 'The answer check uses your settings. Online checking sends the question and top memories to your chosen provider.');
    notice.className = 'dash-help';
    var verdict = node('div', '', 'daily-verdict'); verdict.setAttribute('role', 'status');
    var answer = node('div', '', 'daily-answer');
    var sources = node('div', '', 'daily-sources');
    card.append(form, notice, verdict, answer, sources);
    var activity = node('details'); activity.className = 'dash-quiet'; activity.appendChild(node('summary', 'What have I done?'));
    var summaries = node('div'); summaries.innerHTML = window.ODSummaries.controls('daily');
    activity.appendChild(summaries);
    activity.addEventListener('toggle', function () { if (activity.open) window.ODSummaries.onShow('daily'); });
    card.appendChild(activity);
    var work = node('div', '', 'daily-work'); work.setAttribute('role', 'status');
    var refresh = node('button', 'Show background work', 'daily-work-refresh'); refresh.type = 'button'; refresh.className = 'btn sm ghost dash-work-btn';
    refresh.addEventListener('click', function () { loadWork(work); });
    card.append(refresh, work);
    var slot = pane.querySelector('#od-dash-ask-slot');
    if (slot) slot.appendChild(card); else pane.prepend(card);
    form.addEventListener('submit', function (e) {
      e.preventDefault(); var q = question.value.trim();
      if (q) search(q, ask, verdict, answer, sources);
    });
  }
  function showAnswer(body, out) {
    var check = body.answer_check || {};
    out.textContent = '';
    if (check.status !== 'judged' || check.abstained || body.abstained) return;
    out.appendChild(node('h4', 'Answer from saved notes'));
    // The trace API provides checked evidence, but no synthesized answer.
    // Show its words verbatim and expose that boundary to the reader.
    out.appendChild(node('p', body.synthesis || (body.results || []).slice(0, 3).map(function (r) { return r.content || ''; }).join('\n')));
    out.appendChild(node('p', 'These are excerpts from your saved notes, checked for relevance to this question.'));
  }
  function addSources(body, out) {
    (body.results || []).forEach(function (r) {
      var id = r.fact_id || r.id;
      if (!id) return;
      var link = window.slmSourceRow(id, 'Open source memory ' + id + (r.source ? ' · ' + r.source : ''));
      out.appendChild(link);
    });
  }
  function search(query, ask, verdict, answer, sources) {
    ask.disabled = true; verdict.textContent = 'Searching and checking…'; answer.textContent = ''; sources.textContent = '';
    fetch('/api/v3/recall/trace', { method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:query, limit:10}),
      slmInvalidatesCache:false, slmRequiresWriteAuth:false })
      .then(function (r) {
        if (r.status === 503) return r.json().catch(function () { return {}; }).then(function (b) {
          throw new Error(b.message || 'SuperLocalMemory is busy. Ask again in a few seconds.');
        });
        if (!r.ok) throw new Error('Search is unavailable. Check your access or try again.');
        return r.json();
      })
      .then(function (body) {
        if (body.error) throw new Error('Search could not finish. Try again.');
        window.SLMAnswerCheckTryIt.renderVerdict(verdict, body);
        showAnswer(body, answer);
        window.SLMAnswerCheckTryIt.renderFound(sources, body); addSources(body, sources);
      }).catch(function (e) { verdict.textContent = e.message; })
      .finally(function () { ask.disabled = false; });
  }
  function loadWork(out) {
    out.textContent = 'Reading recorded background work…';
    fetch('/api/dashboard/background', {credentials:'same-origin'})
      .then(function (r) { if (!r.ok) throw new Error('Background work could not be read. Retry.'); return r.json(); })
      .then(function (body) {
        out.textContent = body.scope || 'Recorded background changes';
        if (body.running) out.appendChild(node('p', 'Background tidy-up is running now.'));
        if (!(body.activities || []).length) out.appendChild(node('p', 'No changes recorded yet.'));
        (body.activities || []).forEach(function (a) {
          out.appendChild(node('p', a.timestamp + ' · ' + a.action_type + ': ' + (a.reason || '') +
            ' · ' + (a.generated_by === 'ollama' ? 'Written by the local model. ' : a.generated_by === 'cloud' ? 'Written by the configured provider. ' : '') +
            'Changed memories: ' + [a.existing_fact_id, a.new_fact_id].concat(a.source_fact_ids || []).filter(Boolean).join(', ')));
        });
      }).catch(function (e) { out.textContent = e.message; });
  }
  window.SLMDaily = {mount:mount};
  document.addEventListener('DOMContentLoaded', function () { mount(document.getElementById('dashboard-pane')); });
}());
