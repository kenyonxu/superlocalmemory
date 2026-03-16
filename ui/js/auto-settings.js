// SuperLocalMemory V3 — Auto-Capture/Recall Settings
// Wires the auto-capture and auto-recall toggle switches to the V3 API.

async function loadAutoSettings() {
    try {
        var captureResp = await fetch('/api/v3/auto-capture/config');
        var recallResp = await fetch('/api/v3/auto-recall/config');
        var capture = captureResp.ok ? await captureResp.json() : {};
        var recall = recallResp.ok ? await recallResp.json() : {};

        var cc = capture.config || {};
        var rc = recall.config || {};

        var el;
        el = document.getElementById('auto-capture-toggle');
        if (el) el.checked = cc.enabled !== false;
        el = document.getElementById('auto-capture-decisions');
        if (el) el.checked = cc.capture_decisions !== false;
        el = document.getElementById('auto-capture-bugs');
        if (el) el.checked = cc.capture_bugs !== false;
        el = document.getElementById('auto-recall-toggle');
        if (el) el.checked = rc.enabled !== false;
        el = document.getElementById('auto-recall-session');
        if (el) el.checked = rc.on_session_start !== false;
    } catch (e) {
        console.log('Auto settings load error:', e);
    }
}

function saveAutoCaptureConfig() {
    var payload = {
        enabled: document.getElementById('auto-capture-toggle')?.checked,
        capture_decisions: document.getElementById('auto-capture-decisions')?.checked,
        capture_bugs: document.getElementById('auto-capture-bugs')?.checked
    };
    fetch('/api/v3/auto-capture/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(function(e) { console.log('Save auto-capture error:', e); });
}

function saveAutoRecallConfig() {
    var payload = {
        enabled: document.getElementById('auto-recall-toggle')?.checked,
        on_session_start: document.getElementById('auto-recall-session')?.checked
    };
    fetch('/api/v3/auto-recall/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(function(e) { console.log('Save auto-recall error:', e); });
}

// Bind change listeners for auto-capture toggles
document.querySelectorAll('#auto-capture-toggle, #auto-capture-decisions, #auto-capture-bugs').forEach(function(el) {
    if (el) {
        el.addEventListener('change', saveAutoCaptureConfig);
    }
});

// Bind change listeners for auto-recall toggles
document.querySelectorAll('#auto-recall-toggle, #auto-recall-session').forEach(function(el) {
    if (el) {
        el.addEventListener('change', saveAutoRecallConfig);
    }
});

// Load settings when the settings tab is shown
document.getElementById('settings-tab')?.addEventListener('shown.bs.tab', loadAutoSettings);
