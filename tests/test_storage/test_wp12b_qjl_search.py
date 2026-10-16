"""QJL estimates inner products; search must convert them to cosine units."""

from unittest.mock import MagicMock

import numpy as np
import pytest

from superlocalmemory.core.config import QuantizationConfig
from superlocalmemory.storage.quantized_store import QuantizedEmbeddingStore


def test_search_normalizes_the_entire_corrected_inner_product():
    db, polar, qjl = MagicMock(), MagicMock(), MagicMock()
    db.execute.return_value = [{
        "fact_id": "f", "radius": 5., "angle_indices": b"x",
        "bit_width": 4, "qjl_bits": b"payload",
    }]
    polar.decode.return_value = np.array([2., 0.])
    polar.approximate_similarity.return_value = 2 / np.sqrt(5)
    qjl.estimate_correction.return_value = 6.
    store = QuantizedEmbeddingStore(db, polar, qjl, QuantizationConfig())
    # dot([2,1],[2,0])=4; QJL adds 6; divide by ||q||*||original||.
    score = store.search(np.array([2., 1.]), "default")[0][1]
    assert score == pytest.approx(10 / (np.sqrt(5) * 5))
