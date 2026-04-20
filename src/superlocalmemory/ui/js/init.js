// SuperLocalMemory V2 - Event Listener Wiring
// Loads LAST — after all module scripts. Connects UI events to module functions.
//
// v3.4.21 Domain rewrite: several tabs / panes were folded together
// (Patterns/Learning/Behavioral -> Brain; Clusters/Entities -> Graph;
// Recall Lab/Timeline -> Memories; Events/Agents/Trust/Lifecycle/
// Compliance/Math/Ingestion -> Health). Every line below is now
// null-guarded so missing elements never halt JS execution — a
// silent TypeError here used to cascade into "all spinners forever"
// because ng-shell.js failed to initialise. This whole file is
// slated for deletion in the final dead-code purge; until then,
// null-guards keep it harmless.

(function wire() {
    function onShown(id, fn) {
        var el = document.getElementById(id);
        if (el && typeof fn === 'function') {
            el.addEventListener('shown.bs.tab', fn);
        }
    }

    function on(id, ev, fn) {
        var el = document.getElementById(id);
        if (el) el.addEventListener(ev, fn);
    }

    onShown('memories-tab', typeof loadMemories === 'function' ? loadMemories : null);
    onShown('clusters-tab', typeof loadClusters === 'function' ? loadClusters : null);
    onShown('patterns-tab', typeof loadPatterns === 'function' ? loadPatterns : null);
    onShown('timeline-tab', typeof loadTimeline === 'function' ? loadTimeline : null);
    onShown('settings-tab', typeof loadSettings === 'function' ? loadSettings : null);
    onShown('events-tab', typeof loadEventStats === 'function' ? loadEventStats : null);
    onShown('agents-tab', typeof loadAgents === 'function' ? loadAgents : null);
    onShown('learning-tab', typeof loadLearning === 'function' ? loadLearning : null);
    onShown('lifecycle-tab', typeof loadLifecycle === 'function' ? loadLifecycle : null);
    onShown('behavioral-tab', typeof loadBehavioral === 'function' ? loadBehavioral : null);
    onShown('compliance-tab', typeof loadCompliance === 'function' ? loadCompliance : null);

    on('search-query', 'keypress', function (e) {
        if (e.key === 'Enter' && typeof searchMemories === 'function') searchMemories();
    });

    on('profile-select', 'change', function () {
        if (typeof switchProfile === 'function') switchProfile(this.value);
    });

    on('add-profile-btn', 'click', function () {
        if (typeof createProfile === 'function') createProfile();
    });

    on('new-profile-name', 'keypress', function (e) {
        if (e.key === 'Enter' && typeof createProfile === 'function') createProfile();
    });
})();
