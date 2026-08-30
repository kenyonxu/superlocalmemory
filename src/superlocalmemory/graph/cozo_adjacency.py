# Copyright (c) 2026 Varun Pratap Bhardwaj / Qualixar
# Licensed under AGPL-3.0-or-later - see LICENSE file

"""The graph projection's reader: one profile's edges, for the walk to run over.

WHY THIS EXISTS
---------------
Until this module the projection had no reader. It was written on every store,
drained through an outbox, kept at bidirectional parity with SQLite, and purged
on erasure -- and nothing in retrieval ever queried it. That is the same failure
the traversal removed from ``cozo_backend`` had ("correct data that nothing could
use"), one layer up, and it is the honest answer to "what does the graph engine
buy us": on its own, nothing. It buys something here.

Measured on a copy of the author's 208,151-edge store, warmed, five runs:

    reading one profile's adjacency     p50
    SQLite (open + logical-edge query)  2,477 ms
    graph projection (one query)          395 ms   6.3x

Recall rebuilds that adjacency whenever the edge count changes or the cache TTL
expires, so this is on the recall path, and the gap widens with edge count.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It returns edges. It does not walk them, rank them, or decide what is visible.
The walk lives once, in ``retrieval/spreading``, as a pure function of a
snapshot; visibility is applied by the channel, which prunes both endpoints
against the visible fact corpus. A source that answered questions instead of
supplying data is exactly the defect the ``AdjacencySource`` seam was carved to
prevent -- the previous attempt shipped a second, differently-behaved
implementation of the walk and diverged from SQLite on every query.

WHEN IT DECLINES
----------------
Global and shared scope. The projection stores one ``profile_id`` per edge and
this reader filters on it, so a query that must also see another profile's global
or shared memories would come back short. It returns ``None`` for those, and the
channel reads SQLite, which is the only store that can answer them. Failing over
is not a fallback here; it is the correct answer.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: One profile's edges, both directions left to the caller. Selecting the three
#: columns the walk needs -- and no ``edge_type`` -- lets the engine collapse
#: parallel typed edges between the same pair, which is what the snapshot does
#: with them anyway.
_EDGE_QUERY = (
    "?[source, target, weight] := *edge{from_id: source, to_id: target, "
    "                                   weight: weight, profile_id: $pid}"
)


class CozoAdjacencySource:
    """Supplies one profile's fact graph from the projection. Data only."""

    name = "cozo"

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def edges(
        self,
        profile_id: str,
        *,
        include_global: bool = False,
        include_shared: bool = False,
    ) -> list[tuple[str, str, float]] | None:
        """Edge triples, or ``None`` when this source cannot answer in full.

        ``None`` is not an error and must not be logged as one -- it is this
        source saying the question is outside what it holds. A partial answer
        would silently shrink the graph around a candidate, which is the failure
        mode that makes a projection worse than no projection.
        """
        if include_global or include_shared:
            return None
        client = getattr(self._backend, "_db", None)
        if client is None:
            return None
        try:
            result = client.run(_EDGE_QUERY, {"pid": profile_id})
        except Exception as exc:  # noqa: BLE001 -- SQLite can always answer this
            logger.debug("Graph projection unreadable, using SQLite: %s", exc)
            return None
        try:
            return [
                (str(row[0]), str(row[1]), float(row[2]))
                for row in result.values.tolist()
            ]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Graph projection returned an unusable shape: %s", exc)
            return None


def adjacency_source() -> CozoAdjacencySource | None:
    """The live projection's reader, or None when there is not one.

    Resolved through the orchestrator because the daemon is the projection's sole
    owner: it runs on RocksDB, which takes an exclusive process lock, so a second
    process opening it would not degrade -- it would fail. ``get_graph_backend``
    already returns the backend only while its status is active.
    """
    try:
        from superlocalmemory.core.backend_orchestrator import get_orchestrator

        orchestrator = get_orchestrator()
        if orchestrator is None:
            return None
        backend = orchestrator.get_graph_backend()
        if backend is None:
            return None
        return CozoAdjacencySource(backend)
    except Exception as exc:  # noqa: BLE001
        logger.debug("No graph projection available: %s", exc)
        return None
