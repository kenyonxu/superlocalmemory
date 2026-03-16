// SuperLocalMemory V3 — Math Health
// Displays status of Fisher-Rao, sheaf cohomology, and Langevin dynamics layers.

async function loadMathHealth() {
    try {
        var response = await fetch('/api/v3/math/health');
        if (!response.ok) return;
        var data = await response.json();

        var container = document.getElementById('math-health-cards');
        container.textContent = '';

        var layers = data.health || {};
        var colors = { fisher: 'primary', sheaf: 'success', langevin: 'info' };
        var icons = { fisher: 'bi-graph-up', sheaf: 'bi-diagram-3', langevin: 'bi-activity' };

        Object.keys(layers).forEach(function(key) {
            var layer = layers[key];
            var col = document.createElement('div');
            col.className = 'col-md-4';

            var card = document.createElement('div');
            card.className = 'card h-100';

            // Card header
            var header = document.createElement('div');
            header.className = 'card-header bg-' + (colors[key] || 'secondary') + ' text-white';
            var h6 = document.createElement('h6');
            h6.className = 'mb-0';
            var icon = document.createElement('i');
            icon.className = 'bi ' + (icons[key] || 'bi-gear');
            h6.appendChild(icon);
            h6.appendChild(document.createTextNode(' ' + key.charAt(0).toUpperCase() + key.slice(1)));
            header.appendChild(h6);
            card.appendChild(header);

            // Card body
            var body = document.createElement('div');
            body.className = 'card-body';

            var desc = document.createElement('p');
            desc.className = 'text-muted';
            desc.textContent = layer.description || '';
            body.appendChild(desc);

            var ul = document.createElement('ul');
            ul.className = 'list-unstyled mb-0';

            // Status item
            var liStatus = document.createElement('li');
            liStatus.appendChild(document.createTextNode('Status: '));
            var statusBadge = document.createElement('span');
            statusBadge.className = 'badge bg-success';
            statusBadge.textContent = layer.status || 'active';
            liStatus.appendChild(statusBadge);
            ul.appendChild(liStatus);

            // Mode item (if present)
            if (layer.mode) {
                var liMode = document.createElement('li');
                liMode.appendChild(document.createTextNode('Mode: '));
                var modeStrong = document.createElement('strong');
                modeStrong.textContent = layer.mode;
                liMode.appendChild(modeStrong);
                ul.appendChild(liMode);
            }

            // Threshold item (if present)
            if (layer.threshold) {
                var liThresh = document.createElement('li');
                liThresh.appendChild(document.createTextNode('Threshold: '));
                var threshStrong = document.createElement('strong');
                threshStrong.textContent = layer.threshold;
                liThresh.appendChild(threshStrong);
                ul.appendChild(liThresh);
            }

            // Temperature item (if present)
            if (layer.temperature) {
                var liTemp = document.createElement('li');
                liTemp.appendChild(document.createTextNode('Temperature: '));
                var tempStrong = document.createElement('strong');
                tempStrong.textContent = layer.temperature;
                liTemp.appendChild(tempStrong);
                ul.appendChild(liTemp);
            }

            body.appendChild(ul);
            card.appendChild(body);
            col.appendChild(card);
            container.appendChild(col);
        });
    } catch (e) {
        console.log('Math health error:', e);
    }
}

document.getElementById('math-health-tab')?.addEventListener('shown.bs.tab', loadMathHealth);
