// SuperLocalMemory V3 — Fact Detail View
// Adds click-to-expand on memory list items to show channel scores and trust data.

document.addEventListener('click', function(e) {
    var item = e.target.closest('[data-fact-id]');
    if (!item) return;

    // Toggle: if detail panel already exists, remove it
    var existingDetail = item.querySelector('.fact-detail-panel');
    if (existingDetail) {
        existingDetail.remove();
        return;
    }

    // Extract query text from the item (first 100 chars)
    var queryText = (item.textContent || '').substring(0, 100).trim();
    if (!queryText) return;

    fetch('/api/v3/recall/trace', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: queryText, limit: 1 })
    }).then(function(r) {
        return r.json();
    }).then(function(data) {
        var result = (data.results || [])[0];
        if (!result) return;

        var panel = document.createElement('div');
        panel.className = 'fact-detail-panel card mt-2 mb-2 border-info';

        var cardBody = document.createElement('div');
        cardBody.className = 'card-body small';

        // Score / Trust / Confidence row
        var row1 = document.createElement('div');
        row1.className = 'row';

        var col1 = document.createElement('div');
        col1.className = 'col-md-4';
        var col1Label = document.createElement('strong');
        col1Label.textContent = 'Score: ';
        col1.appendChild(col1Label);
        col1.appendChild(document.createTextNode(result.score || 0));
        row1.appendChild(col1);

        var col2 = document.createElement('div');
        col2.className = 'col-md-4';
        var col2Label = document.createElement('strong');
        col2Label.textContent = 'Trust: ';
        col2.appendChild(col2Label);
        col2.appendChild(document.createTextNode(result.trust_score || 0));
        row1.appendChild(col2);

        var col3 = document.createElement('div');
        col3.className = 'col-md-4';
        var col3Label = document.createElement('strong');
        col3Label.textContent = 'Confidence: ';
        col3.appendChild(col3Label);
        col3.appendChild(document.createTextNode(result.confidence || 0));
        row1.appendChild(col3);

        cardBody.appendChild(row1);

        // Channel scores section
        var channels = result.channel_scores || {};
        var channelKeys = Object.keys(channels);
        if (channelKeys.length > 0) {
            var label = document.createElement('div');
            label.className = 'mt-2';
            var labelStrong = document.createElement('strong');
            labelStrong.textContent = 'Channel Scores:';
            label.appendChild(labelStrong);
            cardBody.appendChild(label);

            var row2 = document.createElement('div');
            row2.className = 'row text-muted';
            channelKeys.forEach(function(chKey) {
                var chCol = document.createElement('div');
                chCol.className = 'col-md-3';
                chCol.textContent = chKey + ': ' + channels[chKey];
                row2.appendChild(chCol);
            });
            cardBody.appendChild(row2);
        }

        panel.appendChild(cardBody);
        item.appendChild(panel);
    }).catch(function(e) {
        console.log('Fact detail error:', e);
    });
});
