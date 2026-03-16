// SuperLocalMemory V3 — Trust Dashboard
// Loads and displays Bayesian trust scores per agent and per fact.

async function loadTrustDashboard() {
    try {
        var response = await fetch('/api/v3/trust/dashboard');
        if (!response.ok) return;
        var data = await response.json();

        var agents = data.agents || [];
        document.getElementById('trust-agent-count').textContent = agents.length;

        var avg = agents.length > 0
            ? (agents.reduce(function(s, a) { return s + (a.trust_score || 0); }, 0) / agents.length).toFixed(3)
            : '\u2014';
        document.getElementById('trust-avg-score').textContent = avg;
        document.getElementById('trust-burst-count').textContent = (data.alerts || []).length;

        var tbody = document.getElementById('trust-agents-body');
        tbody.textContent = '';
        agents.forEach(function(a) {
            var tr = document.createElement('tr');
            var score = (a.trust_score || 0);
            var badge = score >= 0.7 ? 'success' : score >= 0.3 ? 'warning' : 'danger';
            var label = score >= 0.7 ? 'Trusted' : score >= 0.3 ? 'Neutral' : 'Low Trust';

            // Target ID cell
            var tdTarget = document.createElement('td');
            tdTarget.textContent = a.target_id || '';
            tr.appendChild(tdTarget);

            // Type cell
            var tdType = document.createElement('td');
            var spanType = document.createElement('span');
            spanType.className = 'badge bg-secondary';
            spanType.textContent = a.target_type || '';
            tdType.appendChild(spanType);
            tr.appendChild(tdType);

            // Trust score progress bar cell
            var tdScore = document.createElement('td');
            var progress = document.createElement('div');
            progress.className = 'progress';
            progress.style.height = '20px';
            var bar = document.createElement('div');
            bar.className = 'progress-bar bg-' + badge;
            bar.style.width = Math.round(score * 100) + '%';
            bar.textContent = score.toFixed(3);
            progress.appendChild(bar);
            tdScore.appendChild(progress);
            tr.appendChild(tdScore);

            // Evidence count cell
            var tdEvidence = document.createElement('td');
            tdEvidence.textContent = a.evidence_count || 0;
            tr.appendChild(tdEvidence);

            // Status badge cell
            var tdStatus = document.createElement('td');
            var spanStatus = document.createElement('span');
            spanStatus.className = 'badge bg-' + badge;
            spanStatus.textContent = label;
            tdStatus.appendChild(spanStatus);
            tr.appendChild(tdStatus);

            tbody.appendChild(tr);
        });
    } catch (e) {
        console.log('Trust dashboard error:', e);
    }
}

document.getElementById('trust-tab')?.addEventListener('shown.bs.tab', loadTrustDashboard);
