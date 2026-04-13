// SPDX-License-Identifier: AGPL-3.0-or-later
// Copyright (c) 2026 SuperLocalMemory (superlocalmemory.com)
// Behavioral Learning tab — assertions, outcomes, patterns, tool events (v3.4.7)
// NOTE: All dynamic values use textContent or escapeHtml() from core.js before DOM insertion.

var _behavioralData = null;

async function loadBehavioral() {
    try {
        var response = await fetch('/api/behavioral/status');
        var data = await response.json();
        _behavioralData = data;

        if (!data.available) {
            showEmpty('behavioral-patterns-content', 'lightbulb', 'Behavioral learning not available.');
            return;
        }

        renderBehavioralStats(data);
        renderBehavioralPatterns(data);
        renderBehavioralTransfers(data);
        renderBehavioralOutcomes(data);

        // v3.4.7: Load behavioral assertions (learned patterns with confidence)
        try {
            var assertResp = await fetch('/api/behavioral/assertions');
            var assertData = await assertResp.json();
            renderBehavioralAssertions(assertData);
            // Update stats with assertion count
            animateCounter('bh-patterns-count', (data.stats || {}).patterns_count + (assertData.count || 0));
        } catch (e) { console.debug('assertions load:', e); }

        // v3.4.7: Load tool events summary
        try {
            var evResp = await fetch('/api/behavioral/tool-events?limit=20');
            var evData = await evResp.json();
            renderToolEventsSummary(evData);
        } catch (e) { console.debug('tool events load:', e); }

        var badge = document.getElementById('behavioral-profile-badge');
        if (badge) badge.textContent = data.active_profile || 'default';
    } catch (error) {
        console.error('Error loading behavioral:', error);
    }
}

function renderBehavioralStats(data) {
    var stats = data.stats || {};
    animateCounter('bh-success-count', stats.success_count || 0);
    animateCounter('bh-failure-count', stats.failure_count || 0);
    animateCounter('bh-partial-count', stats.partial_count || 0);
    animateCounter('bh-patterns-count', stats.patterns_count || 0);
}

function renderBehavioralPatterns(data) {
    var container = document.getElementById('behavioral-patterns-content');
    if (!container) return;
    var patterns = data.patterns || [];
    container.textContent = '';

    if (patterns.length === 0) {
        var empty = document.createElement('div');
        empty.className = 'text-center text-muted py-3';
        empty.textContent = 'No patterns learned yet. Report outcomes to start learning.';
        container.appendChild(empty);
        return;
    }

    for (var i = 0; i < patterns.length; i++) {
        var p = patterns[i];
        var successRate = Math.round((p.success_rate || 0) * 100);
        var confPct = Math.round((p.confidence || 0) * 100);
        var barColor = successRate >= 70 ? 'bg-success' : (successRate >= 40 ? 'bg-warning' : 'bg-danger');

        var row = document.createElement('div');
        row.className = 'd-flex align-items-center mb-2';

        // Pattern key label
        var label = document.createElement('div');
        label.style.minWidth = '140px';
        var labelCode = document.createElement('code');
        labelCode.className = 'small';
        labelCode.textContent = p.pattern_key || '';
        label.appendChild(labelCode);

        // Success rate progress bar
        var barWrap = document.createElement('div');
        barWrap.className = 'flex-grow-1 mx-2';
        var progress = document.createElement('div');
        progress.className = 'progress';
        progress.style.height = '20px';
        progress.style.borderRadius = '10px';
        var barEl = document.createElement('div');
        barEl.className = 'progress-bar ' + barColor;
        barEl.style.width = successRate + '%';
        barEl.style.borderRadius = '10px';
        barEl.style.fontSize = '0.7rem';
        barEl.textContent = successRate + '% success';
        progress.appendChild(barEl);
        barWrap.appendChild(progress);

        // Evidence count
        var evidence = document.createElement('small');
        evidence.className = 'text-muted';
        evidence.style.minWidth = '50px';
        evidence.style.textAlign = 'right';
        evidence.textContent = (p.evidence_count || 0) + ' ev.';

        // Confidence badge
        var confBadge = document.createElement('span');
        confBadge.className = 'badge ms-2 ' + (confPct >= 70 ? 'bg-success' : (confPct >= 40 ? 'bg-warning' : 'bg-secondary'));
        confBadge.style.minWidth = '50px';
        confBadge.textContent = confPct + '%';

        row.appendChild(label);
        row.appendChild(barWrap);
        row.appendChild(evidence);
        row.appendChild(confBadge);
        container.appendChild(row);
    }
}

function renderBehavioralTransfers(data) {
    var container = document.getElementById('behavioral-transfers-content');
    if (!container) return;
    var transfers = data.transfers || [];
    container.textContent = '';

    if (transfers.length === 0) {
        var empty = document.createElement('div');
        empty.className = 'text-center text-muted py-3';
        empty.textContent = 'No cross-project transfers yet. Patterns transfer automatically when confidence is high.';
        container.appendChild(empty);
        return;
    }

    var table = document.createElement('table');
    table.className = 'table table-sm table-hover mb-0';
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    ['Pattern', 'From Project', 'To Project', 'Confidence', 'Date'].forEach(function(h) {
        var th = document.createElement('th');
        th.textContent = h;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    for (var i = 0; i < transfers.length; i++) {
        var t = transfers[i];
        var row = document.createElement('tr');

        var patternCell = document.createElement('td');
        var patternCode = document.createElement('code');
        patternCode.className = 'small';
        patternCode.textContent = t.pattern_key || '';
        patternCell.appendChild(patternCode);
        row.appendChild(patternCell);

        var fromCell = document.createElement('td');
        var fromBadge = document.createElement('span');
        fromBadge.className = 'badge bg-secondary';
        fromBadge.textContent = t.from_project || '';
        fromCell.appendChild(fromBadge);
        row.appendChild(fromCell);

        var toCell = document.createElement('td');
        var toBadge = document.createElement('span');
        toBadge.className = 'badge bg-primary';
        toBadge.textContent = t.to_project || '';
        toCell.appendChild(toBadge);
        row.appendChild(toCell);

        var confCell = document.createElement('td');
        confCell.textContent = Math.round((t.confidence || 0) * 100) + '%';
        row.appendChild(confCell);

        var dateCell = document.createElement('td');
        dateCell.className = 'small text-muted';
        dateCell.textContent = formatDate(t.transferred_at || '');
        row.appendChild(dateCell);

        tbody.appendChild(row);
    }
    table.appendChild(tbody);
    container.appendChild(table);
}

function renderBehavioralOutcomes(data) {
    var container = document.getElementById('behavioral-outcomes-content');
    if (!container) return;
    var outcomes = data.recent_outcomes || [];
    container.textContent = '';

    if (outcomes.length === 0) {
        var empty = document.createElement('div');
        empty.className = 'text-center text-muted py-3';
        empty.textContent = 'No outcomes recorded yet. Use the form above or the report_outcome MCP tool.';
        container.appendChild(empty);
        return;
    }

    var table = document.createElement('table');
    table.className = 'table table-sm table-hover mb-0';
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    ['Memory IDs', 'Outcome', 'Action Type', 'Date'].forEach(function(h) {
        var th = document.createElement('th');
        th.textContent = h;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var outcomeBadgeColors = {
        success: 'bg-success',
        failure: 'bg-danger',
        partial: 'bg-warning'
    };

    var tbody = document.createElement('tbody');
    for (var i = 0; i < outcomes.length; i++) {
        var o = outcomes[i];
        var row = document.createElement('tr');

        var idsCell = document.createElement('td');
        var memIds = o.memory_ids || [];
        idsCell.textContent = memIds.join(', ');
        row.appendChild(idsCell);

        var outcomeCell = document.createElement('td');
        var outBadge = document.createElement('span');
        outBadge.className = 'badge ' + (outcomeBadgeColors[o.outcome] || 'bg-secondary');
        outBadge.textContent = o.outcome || '';
        outcomeCell.appendChild(outBadge);
        row.appendChild(outcomeCell);

        var actionCell = document.createElement('td');
        actionCell.textContent = o.action_type || '';
        row.appendChild(actionCell);

        var dateCell = document.createElement('td');
        dateCell.className = 'small text-muted';
        dateCell.textContent = formatDate(o.created_at || '');
        row.appendChild(dateCell);

        tbody.appendChild(row);
    }
    table.appendChild(tbody);
    container.appendChild(table);
}

// v3.4.7: Render behavioral assertions (learned patterns with confidence evolution)
function renderBehavioralAssertions(data) {
    var container = document.getElementById('behavioral-patterns-content');
    if (!container) return;
    var assertions = data.assertions || [];
    if (assertions.length === 0) return; // Don't clear existing patterns

    // Add assertions section header
    var header = document.createElement('div');
    header.className = 'd-flex align-items-center mt-3 mb-2';
    header.innerHTML = '<span class="badge bg-info me-2">NEW</span><strong>Behavioral Assertions (v3.4.7)</strong>';
    container.appendChild(header);

    for (var i = 0; i < assertions.length; i++) {
        var a = assertions[i];
        var confPct = Math.round((a.confidence || 0) * 100);
        var barColor = confPct >= 70 ? 'bg-success' : (confPct >= 40 ? 'bg-warning' : 'bg-secondary');

        var card = document.createElement('div');
        card.className = 'card mb-2';
        card.style.cursor = 'pointer';
        card.style.border = '1px solid rgba(255,255,255,0.1)';
        card.style.background = 'rgba(255,255,255,0.03)';

        var body = document.createElement('div');
        body.className = 'card-body py-2 px-3';

        // Top row: trigger → action
        var topRow = document.createElement('div');
        topRow.className = 'd-flex justify-content-between align-items-start';

        var triggerSpan = document.createElement('div');
        triggerSpan.className = 'small';
        var trigBadge = document.createElement('span');
        trigBadge.className = 'badge bg-secondary me-1';
        trigBadge.textContent = a.trigger_condition || '';
        var arrow = document.createElement('span');
        arrow.className = 'text-muted mx-1';
        arrow.textContent = '→';
        var actionSpan = document.createElement('span');
        actionSpan.textContent = a.action || '';
        triggerSpan.appendChild(trigBadge);
        triggerSpan.appendChild(arrow);
        triggerSpan.appendChild(actionSpan);

        var confBadge = document.createElement('span');
        confBadge.className = 'badge ' + barColor;
        confBadge.textContent = confPct + '%';
        confBadge.title = 'Confidence: ' + confPct + '% (reinforced ' + (a.reinforcement_count || 0) + 'x, contradicted ' + (a.contradiction_count || 0) + 'x)';

        topRow.appendChild(triggerSpan);
        topRow.appendChild(confBadge);
        body.appendChild(topRow);

        // Bottom row: category + evidence + confidence bar
        var bottomRow = document.createElement('div');
        bottomRow.className = 'd-flex align-items-center mt-1';
        var catBadge = document.createElement('span');
        catBadge.className = 'badge bg-outline-secondary me-2 small';
        catBadge.style.border = '1px solid rgba(255,255,255,0.2)';
        catBadge.textContent = a.category || '';
        var evidenceSpan = document.createElement('small');
        evidenceSpan.className = 'text-muted me-2';
        evidenceSpan.textContent = (a.evidence_count || 0) + ' evidence';
        var progress = document.createElement('div');
        progress.className = 'progress flex-grow-1';
        progress.style.height = '4px';
        var bar = document.createElement('div');
        bar.className = 'progress-bar ' + barColor;
        bar.style.width = confPct + '%';
        progress.appendChild(bar);
        bottomRow.appendChild(catBadge);
        bottomRow.appendChild(evidenceSpan);
        bottomRow.appendChild(progress);
        body.appendChild(bottomRow);

        card.appendChild(body);

        // Click to expand details
        (function(assertion, cardEl) {
            cardEl.addEventListener('click', function() {
                var existing = cardEl.querySelector('.assertion-detail');
                if (existing) { existing.remove(); return; }
                var detail = document.createElement('div');
                detail.className = 'assertion-detail px-3 pb-2 small text-muted';
                detail.innerHTML = '<div>ID: <code>' + escapeHtml(assertion.id) + '</code></div>' +
                    '<div>Source: ' + escapeHtml(assertion.source || 'auto') + '</div>' +
                    '<div>Project: ' + escapeHtml(assertion.project_path || 'global') + '</div>' +
                    '<div>Created: ' + escapeHtml(formatDate(assertion.created_at || '')) + '</div>' +
                    '<div>Reinforced: ' + (assertion.reinforcement_count || 0) + 'x | Contradicted: ' + (assertion.contradiction_count || 0) + 'x</div>';
                cardEl.appendChild(detail);
            });
        })(a, card);

        container.appendChild(card);
    }
}

// v3.4.7: Render tool events summary in behavioral tab
function renderToolEventsSummary(data) {
    var container = document.getElementById('behavioral-outcomes-content');
    if (!container) return;
    var events = data.events || [];
    if (events.length === 0) return;

    // Add tool events section
    var header = document.createElement('div');
    header.className = 'd-flex align-items-center mt-3 mb-2';
    header.innerHTML = '<span class="badge bg-info me-2">NEW</span><strong>Recent Tool Events</strong> <small class="text-muted ms-2">(' + data.count + ' shown)</small>';
    container.appendChild(header);

    var table = document.createElement('table');
    table.className = 'table table-sm table-hover mb-0';
    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    ['Tool', 'Event', 'Time'].forEach(function(h) {
        var th = document.createElement('th');
        th.textContent = h;
        headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);

    var tbody = document.createElement('tbody');
    for (var i = 0; i < Math.min(events.length, 10); i++) {
        var ev = events[i];
        var row = document.createElement('tr');
        row.style.cursor = 'pointer';

        var toolCell = document.createElement('td');
        var toolBadge = document.createElement('code');
        toolBadge.className = 'small';
        toolBadge.textContent = ev.tool_name || '';
        toolCell.appendChild(toolBadge);
        row.appendChild(toolCell);

        var typeCell = document.createElement('td');
        var typeBadge = document.createElement('span');
        var typeColors = { invoke: 'bg-primary', complete: 'bg-success', error: 'bg-danger', correction: 'bg-warning' };
        typeBadge.className = 'badge ' + (typeColors[ev.event_type] || 'bg-secondary');
        typeBadge.textContent = ev.event_type || '';
        typeCell.appendChild(typeBadge);
        row.appendChild(typeCell);

        var dateCell = document.createElement('td');
        dateCell.className = 'small text-muted';
        dateCell.textContent = formatDate(ev.created_at || '');
        row.appendChild(dateCell);

        tbody.appendChild(row);
    }
    table.appendChild(tbody);
    container.appendChild(table);
}

async function reportOutcome() {
    var memIdsInput = document.getElementById('bh-memory-ids');
    var outcomeSelect = document.getElementById('bh-outcome');
    var actionSelect = document.getElementById('bh-action-type');
    var contextInput = document.getElementById('bh-context');

    var rawIds = (memIdsInput.value || '').trim();
    if (!rawIds) {
        showToast('Enter at least one memory ID.');
        return;
    }

    var memoryIds = rawIds.split(',').map(function(id) { return id.trim(); }).filter(function(id) { return id.length > 0; });

    try {
        var response = await fetch('/api/behavioral/report-outcome', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                memory_ids: memoryIds,
                outcome: outcomeSelect.value,
                action_type: actionSelect.value,
                context: contextInput.value.trim() || undefined
            })
        });
        var data = await response.json();
        if (response.ok) {
            showToast('Outcome reported successfully.');
            memIdsInput.value = '';
            contextInput.value = '';
            loadBehavioral(); // Refresh
        } else {
            showToast(data.detail || 'Failed to report outcome.');
        }
    } catch (error) {
        console.error('Error reporting outcome:', error);
        showToast('Error reporting outcome.');
    }
}
