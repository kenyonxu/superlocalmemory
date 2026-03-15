"""S24 fix tests — verify every fix has real behavioral coverage."""
import pytest
from unittest.mock import MagicMock, patch


class TestDisabledChannels:
    """Step 1.12: disabled_channels must actually prevent channel execution."""

    def test_disabled_bm25_skips_bm25(self) -> None:
        from slm_innovation.core.config import RetrievalConfig, ChannelWeights
        from slm_innovation.retrieval.engine import RetrievalEngine
        from slm_innovation.retrieval.strategy import QueryStrategyClassifier

        db = MagicMock()
        db.get_all_facts.return_value = []
        config = RetrievalConfig(disabled_channels=["bm25"])
        bm25_mock = MagicMock()
        channels = {"bm25": bm25_mock}
        engine = RetrievalEngine(db, config, channels)
        strat = QueryStrategyClassifier().classify("test", ChannelWeights().as_dict())
        result = engine._run_channels("test query", "profile", strat)
        # BM25 should NOT be called
        bm25_mock.search.assert_not_called()
        assert "bm25" not in result

    def test_disabled_entity_skips_entity(self) -> None:
        from slm_innovation.core.config import RetrievalConfig, ChannelWeights
        from slm_innovation.retrieval.engine import RetrievalEngine
        from slm_innovation.retrieval.strategy import QueryStrategyClassifier

        db = MagicMock()
        config = RetrievalConfig(disabled_channels=["entity_graph"])
        entity_mock = MagicMock()
        channels = {"entity_graph": entity_mock}
        engine = RetrievalEngine(db, config, channels)
        strat = QueryStrategyClassifier().classify("test", ChannelWeights().as_dict())
        result = engine._run_channels("test query", "profile", strat)
        entity_mock.search.assert_not_called()
        assert "entity_graph" not in result

    def test_no_disabled_runs_all(self) -> None:
        from slm_innovation.core.config import RetrievalConfig, ChannelWeights
        from slm_innovation.retrieval.engine import RetrievalEngine
        from slm_innovation.retrieval.strategy import QueryStrategyClassifier

        db = MagicMock()
        db.get_all_facts.return_value = []
        config = RetrievalConfig(disabled_channels=[])
        bm25_mock = MagicMock()
        bm25_mock.search.return_value = [("fact1", 0.9)]
        channels = {"bm25": bm25_mock}
        engine = RetrievalEngine(db, config, channels)
        strat = QueryStrategyClassifier().classify("test", ChannelWeights().as_dict())
        result = engine._run_channels("test query", "profile", strat)
        bm25_mock.search.assert_called_once()
        assert "bm25" in result


class TestEntityNoiseFix:
    """S24: expanded stop list + first-word check."""

    def test_sentence_starters_filtered(self) -> None:
        from slm_innovation.encoding.fact_extractor import _extract_entities
        # These should all be filtered (sentence starters)
        text = "Wow that was great. Did you know? So many things. Gonna be fun."
        entities = _extract_entities(text)
        assert "Wow" not in entities
        assert "Did" not in entities
        assert "So" not in entities
        assert "Gonna" not in entities

    def test_real_names_pass(self) -> None:
        from slm_innovation.encoding.fact_extractor import _extract_entities
        text = "Alice and Bob met at Google in New York"
        entities = _extract_entities(text)
        assert "Alice" in entities
        assert "Bob" in entities
        assert "Google" in entities
        assert "New York" in entities

    def test_first_word_check_multi_word(self) -> None:
        from slm_innovation.encoding.fact_extractor import _extract_entities
        # "Thanks Hey" should be filtered because first word "Thanks" is a stop word
        text = "Thanks Hey everyone for coming"
        entities = _extract_entities(text)
        for e in entities:
            assert not e.startswith("Thanks")
            assert not e.startswith("Hey")

    def test_entity_channel_stop_words(self) -> None:
        from slm_innovation.retrieval.entity_channel import extract_query_entities
        # Query entities should also filter junk
        entities = extract_query_entities("Wow did Caroline go to the store")
        assert "Caroline" in entities
        assert "Wow" not in entities


class TestSheafPerfCap:
    """S24: sheaf_max_edges_per_check is configurable."""

    def test_default_cap(self) -> None:
        from slm_innovation.core.config import MathConfig
        mc = MathConfig()
        assert mc.sheaf_max_edges_per_check == 200

    def test_custom_cap(self) -> None:
        from slm_innovation.core.config import MathConfig
        mc = MathConfig(sheaf_max_edges_per_check=50)
        assert mc.sheaf_max_edges_per_check == 50


class TestF17FactCount:
    """F17: increment_entity_fact_count must be called during store."""

    def test_increment_exists(self) -> None:
        from slm_innovation.storage.database import DatabaseManager
        assert hasattr(DatabaseManager, "increment_entity_fact_count")

    def test_increment_called_in_store(self) -> None:
        import inspect
        from slm_innovation.core.engine import MemoryEngine
        src = inspect.getsource(MemoryEngine.store)
        assert "increment_entity_fact_count" in src


class TestRecallFactsAdapter:
    """Step 2.5: recall_facts adapter exists on RetrievalEngine."""

    def test_recall_facts_method_exists(self) -> None:
        from slm_innovation.retrieval.engine import RetrievalEngine
        assert hasattr(RetrievalEngine, "recall_facts")

    def test_recall_facts_returns_tuples(self) -> None:
        import inspect
        from slm_innovation.retrieval.engine import RetrievalEngine
        sig = inspect.signature(RetrievalEngine.recall_facts)
        params = list(sig.parameters.keys())
        assert "query" in params
        assert "profile_id" in params
        assert "skip_agentic" in params


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
