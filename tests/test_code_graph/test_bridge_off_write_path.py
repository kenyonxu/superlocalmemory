"""The code bridge must never run on the remember/recall write path.

OWNER CONSTRAINT (verbatim, 4.0.7):
  "whether you wire these graphs and everything properly, I do not want my
   remember and recall timing impact. If these things are in milliseconds,
   I'm okay, but recall and remember timings should not impact."

WHY A STRUCTURAL GATE AND NOT A STOPWATCH
-----------------------------------------
A wall-clock assertion on remember latency flakes: the harness in tests/perf
measures p50 CV ~0.7% only on a quiet machine, and CI/laptops are not quiet.
A timing test that fails at 3am on a loaded runner gets muted, and once muted it
protects nothing.

These assertions are structural instead: they check that the bridge is not
*connected* to the write path at all. That is deterministic, cannot flake, and
fails loudly the moment someone reconnects it — including someone who
"optimises" by making linking synchronous again. Timing is still measured, by
tests/perf/test_recall_remember_baseline.py; this file guarantees the property
that makes the timing safe.

THE SPECIFIC HAZARD
-------------------
BridgeEventListeners was authored to subscribe on_memory_stored to
"memory.stored". EventBus._notify_listeners calls listeners synchronously on the
emitting thread, so that subscription puts entity resolution + fact enrichment +
Hebbian linking inside every single remember.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "superlocalmemory"
_BRIDGE = _SRC / "code_graph" / "bridge"


class _RecordingBus:
    """Minimal event bus that records what gets subscribed."""

    def __init__(self) -> None:
        self.subscriptions: list[str] = []

    def subscribe(self, event_type: str, callback) -> None:  # noqa: ANN001
        self.subscriptions.append(event_type)

    def unsubscribe(self, event_type: str, callback) -> None:  # noqa: ANN001
        pass


def _listeners(**overrides):
    """BridgeEventListeners with stub collaborators — no DB, no code graph."""
    from superlocalmemory.code_graph.bridge.event_listeners import BridgeEventListeners

    class _Stub:
        def __getattr__(self, _name):  # any method call is a no-op
            return lambda *a, **k: None

    kwargs = dict(
        entity_resolver=_Stub(),
        fact_enricher=_Stub(),
        hebbian_linker=_Stub(),
        temporal_checker=_Stub(),
        code_graph_db=_Stub(),
    )
    kwargs.update(overrides)
    return BridgeEventListeners(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# W1 — the bridge must not subscribe to any memory-write event
# ─────────────────────────────────────────────────────────────────────────────
class TestBridgeNotOnWritePath:
    #: Events emitted by memory writes. Subscribing the bridge to any of these
    #: puts its work on the remember path, because dispatch is synchronous.
    _WRITE_EVENTS = ("memory.stored", "fact.stored", "memory.created")

    def test_start_subscribes_no_write_event(self) -> None:
        bus = _RecordingBus()
        _listeners().start(bus)

        offenders = [e for e in bus.subscriptions if e in self._WRITE_EVENTS]
        assert not offenders, (
            f"BridgeEventListeners.start() subscribed to {offenders}. "
            "EventBus._notify_listeners calls listeners SYNCHRONOUSLY on the "
            "emitting thread, so this puts entity resolution, fact enrichment "
            "and Hebbian linking inside every remember. The owner's constraint "
            "for 4.0.7 is that remember/recall timing must not move. Run this "
            "work from code_graph.bridge.maintenance (background) instead."
        )

    def test_start_still_registers_the_code_graph_listeners(self) -> None:
        """Guard against 'fixing' W1 by disabling the bridge entirely."""
        bus = _RecordingBus()
        _listeners().start(bus)
        assert "code_graph.node_deleted" in bus.subscriptions
        assert "code_graph.node_changed" in bus.subscriptions, (
            "the code-graph listeners are the half of this class that IS safe — "
            "they fire on code-graph builds, never on memory writes. Dropping "
            "them would silently disable staleness marking."
        )

    def test_store_pipeline_does_not_import_the_bridge(self) -> None:
        """The other way onto the write path: a direct import.

        Parsed, not grepped — a comment mentioning the bridge must not fail this,
        and a real import must not pass it.
        """
        pipeline = _SRC / "core" / "store_pipeline.py"
        tree = ast.parse(pipeline.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)

        offenders = sorted(m for m in imported if "code_graph.bridge" in m)
        assert not offenders, (
            f"core/store_pipeline.py imports {offenders}. store_pipeline runs on "
            "every remember; the bridge must not be reachable from it."
        )


# ─────────────────────────────────────────────────────────────────────────────
# W2 — Hebbian edges are the only bridge output recall can see, so cap them
# ─────────────────────────────────────────────────────────────────────────────
class TestBridgeWritesNothingRecallCanSee:
    """``association_edges`` is read by retrieval/spreading_activation.py via a
    UNION with ``graph_edges``. Any row written there is an extra neighbour recall
    traverses, and changes which memories come back — not just how fast.

    4.0.7 therefore ships NO writer for it. HebbianLinker stays unwired until a
    release that can measure edge volume against the recall baseline first. These
    tests hold that line: they fail if any bridge module starts writing that
    table, whoever adds it and for whatever reason.
    """

    def test_no_bridge_module_writes_association_edges(self) -> None:
        offenders: list[str] = []
        for path in sorted(_BRIDGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))

            # Docstrings and bare string expressions are PROSE, not SQL. These
            # modules discuss association_edges at length to explain why nothing
            # writes it, and that explanation must not fail its own gate. A
            # docstring is a string Constant that is the entire value of an Expr
            # statement; a SQL string is a Constant used as an argument.
            prose = {
                id(node.value)
                for node in ast.walk(tree)
                if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            }

            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                if id(node) in prose:
                    continue
                if "association_edges" not in node.value:
                    continue
                offenders.append(f"{path.name}:{node.lineno}")
        assert not offenders, (
            "these bridge modules embed association_edges in a string literal:\n  "
            + "\n  ".join(offenders)
            + "\n\nThat table feeds recall's spreading activation. 4.0.7 ships no "
            "writer for it by design — see the deferral note in "
            "bridge/maintenance.py. Adding one changes which memories recall "
            "returns, so it needs its own release with a measured edge budget."
        )

    def test_link_pass_caps_exist_and_are_finite(self) -> None:
        from superlocalmemory.code_graph.bridge import maintenance as m

        for name in ("MAX_FACTS_PER_PASS", "MAX_LINKS_PER_FACT"):
            value = getattr(m, name, None)
            assert isinstance(value, int) and value > 0, (
                f"{name} must be a positive int; got {value!r}. Without it one "
                "file-path mention can link every node in that file — measured "
                "at 17 links from a single fact before the cap."
            )

    def test_hebbian_linker_is_not_invoked_by_the_pass(self) -> None:
        """The deferral must be real, not just documented."""
        from superlocalmemory.code_graph.bridge import maintenance as m

        src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.update(a.name for a in node.names)
        assert "HebbianLinker" not in imported, (
            "bridge/maintenance.py imports HebbianLinker. Hebbian edges are "
            "deferred out of 4.0.7 because they alter recall results; wiring "
            "them here reintroduces exactly that."
        )


# ─────────────────────────────────────────────────────────────────────────────
# W3 — the link/enrich pass must stay inside code_graph.db
# ─────────────────────────────────────────────────────────────────────────────
class TestLinkPassIsRecallNeutral:
    def test_recall_never_reads_code_memory_links(self) -> None:
        """The architectural fact the whole design rests on.

        If some future recall channel starts joining code_memory_links, then
        EntityResolver output stops being invisible to recall and the timing
        argument in this file no longer holds.
        """
        offenders: list[str] = []
        for sub in ("retrieval", "core"):
            root = _SRC / sub
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                if "bridge" in path.parts:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "code_memory_links" in text:
                    offenders.append(str(path.relative_to(_SRC)))

        assert not offenders, (
            "these recall/core modules reference code_memory_links:\n  "
            + "\n  ".join(sorted(offenders))
            + "\n\nThe bridge's link pass is safe precisely because recall never "
            "opens code_graph.db. Reading bridge links from a recall path makes "
            "recall cost depend on bridge output — measure it before allowing it."
        )

    def test_no_per_fact_logging_above_debug(self) -> None:
        """A 3,527-fact store must not emit 3,527 log lines.

        The owner asked for no log spam. Per-fact detail belongs at debug; only
        per-pass summaries may be info.
        """
        src = (_BRIDGE / "maintenance.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        # Collect logger.info/warning/error calls that sit inside a for-loop.
        loud_in_loop: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.For, ast.AsyncFor)):
                continue
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                f = inner.func
                if (isinstance(f, ast.Attribute)
                        and f.attr in ("info", "warning", "error")
                        and isinstance(f.value, ast.Name)
                        and f.value.id == "logger"):
                    loud_in_loop.append(f"line {inner.lineno}: logger.{f.attr}")

        assert not loud_in_loop, (
            "logger.info/warning/error inside a per-fact loop in "
            "bridge/maintenance.py:\n  " + "\n  ".join(loud_in_loop)
            + "\n\nThat scales log volume with store size. Use logger.debug for "
            "per-fact detail and emit one summary line per pass."
        )
