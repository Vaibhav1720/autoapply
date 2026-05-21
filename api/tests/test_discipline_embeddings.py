"""Tests for shared.discipline_embeddings — Phase 6 opt-in classifier."""

from unittest.mock import patch

import pytest

from shared import discipline_embeddings as de


@pytest.fixture(autouse=True)
def _reset_cache():
    de.reset_cache_for_tests()
    yield
    de.reset_cache_for_tests()


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DISCIPLINE_EMBED_ENABLE", raising=False)
    with patch("shared.embeddings.generate_embedding") as ge:
        result = de.disciplines_for_text("Senior backend engineer")
    assert result == set()
    ge.assert_not_called()


def test_enabled_returns_empty_when_anchors_fail(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    with patch("shared.embeddings.generate_embeddings_batch", return_value=[]):
        result = de.disciplines_for_text("Senior backend engineer")
    assert result == set()


def test_empty_text_returns_empty(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    assert de.disciplines_for_text("") == set()
    assert de.disciplines_for_text("   ") == set()


def test_text_embed_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    # Non-empty anchor cache, but text embed returns []
    fake_anchors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    with patch("shared.embeddings.generate_embeddings_batch", return_value=fake_anchors):
        with patch("shared.embeddings.generate_embedding", return_value=[]):
            result = de.disciplines_for_text("anything")
    assert result == set()


def test_high_similarity_classifies(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    monkeypatch.setenv("DISCIPLINE_EMBED_THRESHOLD", "0.5")
    # Stub anchors: load_texts returns 2 disciplines we control.
    with patch.object(de, "_load_anchor_texts",
                      return_value={"frontend": "frontend", "backend": "backend"}):
        # batch returns one vector per discipline (same order as keys).
        anchor_vecs = [[1.0, 0.0], [0.0, 1.0]]
        with patch("shared.embeddings.generate_embeddings_batch", return_value=anchor_vecs):
            # text vec = [1, 0] → cosine 1.0 with frontend, 0 with backend
            with patch("shared.embeddings.generate_embedding", return_value=[1.0, 0.0]):
                result = de.disciplines_for_text("any text")
    assert "frontend" in result
    assert "backend" not in result


def test_low_similarity_returns_empty(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    monkeypatch.setenv("DISCIPLINE_EMBED_THRESHOLD", "0.99")
    with patch.object(de, "_load_anchor_texts",
                      return_value={"frontend": "x", "backend": "y"}):
        with patch("shared.embeddings.generate_embeddings_batch",
                   return_value=[[1.0, 0.0], [0.0, 1.0]]):
            # cosine 0.707 with both — below 0.99
            with patch("shared.embeddings.generate_embedding",
                       return_value=[0.707, 0.707]):
                result = de.disciplines_for_text("ambiguous role")
    assert result == set()


def test_anchors_cached_across_calls(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    monkeypatch.setenv("DISCIPLINE_EMBED_THRESHOLD", "0.5")
    with patch.object(de, "_load_anchor_texts",
                      return_value={"a": "a", "b": "b"}):
        with patch("shared.embeddings.generate_embeddings_batch",
                   return_value=[[1.0, 0.0], [0.0, 1.0]]) as mock_batch:
            with patch("shared.embeddings.generate_embedding",
                       return_value=[1.0, 0.0]):
                de.disciplines_for_text("first")
                de.disciplines_for_text("second")
                de.disciplines_for_text("third")
    # Anchors should be embedded ONCE, not per call
    assert mock_batch.call_count == 1


def test_anchor_load_failure_short_circuits(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_ENABLE", "1")
    with patch.object(de, "_load_anchor_texts", return_value={}):
        with patch("shared.embeddings.generate_embeddings_batch") as mock_batch:
            with patch("shared.embeddings.generate_embedding") as mock_one:
                de.disciplines_for_text("first")
                de.disciplines_for_text("second")
    mock_batch.assert_not_called()
    mock_one.assert_not_called()


def test_anchor_file_exists_and_loads():
    """Sanity: the JSON file ships valid anchors."""
    anchors = de._load_anchor_texts()
    assert len(anchors) >= 20
    # Critical disciplines must be present
    for required in ["frontend", "backend", "fullstack", "ml", "data",
                      "devops", "product", "design", "finance", "sales"]:
        assert required in anchors, f"missing anchor: {required}"


def test_threshold_parses_invalid(monkeypatch):
    monkeypatch.setenv("DISCIPLINE_EMBED_THRESHOLD", "garbage")
    assert de._threshold() == 0.55


def test_cosine_zero_vectors():
    assert de._cosine([0, 0], [1, 1]) == 0.0
    assert de._cosine([], [1, 2]) == 0.0
    assert de._cosine([1, 2], [3, 4, 5]) == 0.0
