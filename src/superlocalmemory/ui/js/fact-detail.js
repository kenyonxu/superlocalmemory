// SuperLocalMemory V3 — Fact Detail View
// v3.4.31: scoped click listener, real fact_id lookup, no text-based re-query.
//
// Scope: only `.fact-result-item` elements (search results view), NEVER
// fires on the main memories table rows (those use openMemoryDetail via
// memories.js). This prevents the two listeners from colliding.

document.addEventListener('click', function(e) {
    var item = e.target.closest('.fact-result-item[data-fact-id]');
    if (!item) return;

    // Don't interfere if the click was on an action button/link inside the row
    if (e.target.closest('button, a, [data-bs-toggle]')) return;

    var existingDetail = item.querySelector('.fact-detail-panel');
    if (existingDetail) {
        existingDetail.remove();
        return;
    }

    var factId = item.getAttribute('data-fact-id');
    if (!factId) return;

    fetch('/api/facts/' + encodeURIComponent(factId))
        .then(function(r) { return r.ok ? r.json() : null; })
        .then(function(data) {
            if (!data || !data.fact_id) return;

            var panel = document.createElement('div');
            panel.className = 'fact-detail-panel card mt-2 mb-2 border-info';
            var body = document.createElement('div');
            body.className = 'card-body small';

            var head = document.createElement('div');
            head.className = 'mb-2';
            var h = document.createElement('strong');
            h.textContent = 'Atomic fact';
            head.appendChild(h);
            head.appendChild(document.createTextNode(
                ' · ' + (data.fact_type || '-') +
                ' · confidence ' + (data.confidence || 0) +
                ' · importance ' + (data.importance || 0)
            ));
            body.appendChild(head);

            if (data.source_memory_content) {
                var src = document.createElement('div');
                src.className = 'text-muted small mt-2';
                var srcLabel = document.createElement('strong');
                srcLabel.textContent = 'From memory: ';
                src.appendChild(srcLabel);
                var srcText = String(data.source_memory_content);
                src.appendChild(document.createTextNode(
                    srcText.length > 200 ? srcText.substring(0, 200) + '...' : srcText
                ));
                body.appendChild(src);
            }

            var ids = document.createElement('div');
            ids.className = 'text-muted mt-2';
            ids.style.fontSize = '0.75rem';
            ids.textContent = 'Fact ID: ' + data.fact_id + ' · Memory ID: ' + (data.memory_id || '-');
            body.appendChild(ids);

            if (data.entities && data.entities.length > 0) {
                var ent = document.createElement('div');
                ent.className = 'mt-2';
                var entLabel = document.createElement('strong');
                entLabel.textContent = 'Entities: ';
                ent.appendChild(entLabel);
                ent.appendChild(document.createTextNode(data.entities.join(', ')));
                body.appendChild(ent);
            }

            // Code this memory refers to (4.0.7). Absent for anyone without a
            // built code graph — the API returns [] rather than an error, so no
            // section appears and nothing looks broken.
            //
            // textContent throughout, never innerHTML: every string here is a
            // qualified name or file path read out of the indexed repository, so
            // it is untrusted input as far as this panel is concerned.
            if (data.code_links && data.code_links.length > 0) {
                var codeWrap = document.createElement('div');
                codeWrap.className = 'mt-2';

                var codeLabel = document.createElement('strong');
                codeLabel.textContent = 'Code referenced:';
                codeWrap.appendChild(codeLabel);

                var list = document.createElement('ul');
                list.className = 'mb-0 ps-3';
                list.style.listStyle = 'none';

                data.code_links.forEach(function (link) {
                    var li = document.createElement('li');
                    li.className = 'mt-1';

                    var nameEl = document.createElement('code');
                    nameEl.textContent = link.qualified_name || link.name || '?';
                    li.appendChild(nameEl);

                    if (link.kind) {
                        var kind = document.createElement('span');
                        kind.className = 'text-muted';
                        kind.style.fontSize = '0.75rem';
                        kind.textContent = ' ' + link.kind;
                        li.appendChild(kind);
                    }

                    // A stale link points at code that has moved or gone. Saying
                    // so is the whole value of tracking staleness — silently
                    // showing it as current would be worse than not showing it.
                    if (link.is_stale) {
                        var stale = document.createElement('span');
                        stale.className = 'badge bg-warning text-dark ms-1';
                        stale.style.fontSize = '0.65rem';
                        stale.textContent = 'code changed';
                        li.appendChild(stale);
                    }

                    if (link.file_path) {
                        var loc = document.createElement('div');
                        loc.className = 'text-muted';
                        loc.style.fontSize = '0.7rem';
                        loc.textContent = link.file_path;
                        li.appendChild(loc);
                    }

                    list.appendChild(li);
                });

                codeWrap.appendChild(list);
                body.appendChild(codeWrap);
            }

            panel.appendChild(body);
            item.appendChild(panel);
        })
        .catch(function(err) {
            console.warn('Fact detail error:', err);
        });
});
