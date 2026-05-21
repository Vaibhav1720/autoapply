"""Tests for shared.telemetry — Phase 7 match_events recorder."""

import os
from unittest.mock import patch

import pytest

from shared import telemetry


def test_build_event_minimal():
    evt = telemetry.build_event(
        user_id="u1",
        company_id="comp-amazon",
        matches=[],
    )
    assert evt["userId"] == "u1"
    assert evt["companyId"] == "comp-amazon"
    assert evt["matchedCount"] == 0
    assert evt["topJobIds"] == []
    assert evt["topScores"] == []
    assert evt["ttl"] == 30 * 24 * 60 * 60
    assert evt["id"].startswith("u1__comp-amazon__")


def test_build_event_full():
    matches = [
        {"id": "j1", "score": 87.4},
        {"id": "j2", "score": 71},
        {"id": "j3", "score": 65},
    ]
    evt = telemetry.build_event(
        user_id="u2",
        company_id="comp-google",
        matches=matches,
        scraped_count=120,
        filtered_count=15,
        duration_ms=8421,
        rerank_model="o4mini",
        weights={"skill": 0.18, "title": 0.20, "experience": 0.32},
        region="IN",
        search_id="srch-1",
    )
    assert evt["matchedCount"] == 3
    assert evt["topJobIds"] == ["j1", "j2", "j3"]
    assert evt["topScores"] == [87, 71, 65]
    assert evt["scrapedCount"] == 120
    assert evt["filteredCount"] == 15
    assert evt["durationMs"] == 8421
    assert evt["rerankModel"] == "o4mini"
    assert evt["region"] == "IN"
    assert evt["searchId"] == "srch-1"
    assert evt["weights"]["experience"] == 0.32


def test_build_event_caps_top_at_10():
    matches = [{"id": f"j{i}", "score": i} for i in range(50)]
    evt = telemetry.build_event(user_id="u", company_id="c", matches=matches)
    assert len(evt["topJobIds"]) == 10
    assert len(evt["topScores"]) == 10
    assert evt["matchedCount"] == 50  # full count preserved


def test_build_event_unique_ids():
    a = telemetry.build_event(user_id="u", company_id="c", matches=[])
    b = telemetry.build_event(user_id="u", company_id="c", matches=[])
    assert a["id"] != b["id"]


def test_record_disabled_short_circuits(monkeypatch):
    monkeypatch.setenv("MATCH_EVENTS_DISABLE", "1")
    with patch("shared.cosmos_client.upsert_item") as mock_up:
        telemetry.record(user_id="u", company_id="c", matches=[])
    mock_up.assert_not_called()


def test_record_writes_when_enabled(monkeypatch):
    monkeypatch.delenv("MATCH_EVENTS_DISABLE", raising=False)
    with patch("shared.cosmos_client.upsert_item") as mock_up:
        telemetry.record(
            user_id="u3",
            company_id="comp-meta",
            matches=[{"id": "j1", "score": 88}],
            scraped_count=10,
            filtered_count=5,
            duration_ms=2200,
        )
    mock_up.assert_called_once()
    args, kwargs = mock_up.call_args
    assert args[0] == "match_events"
    body = args[1]
    assert body["userId"] == "u3"
    assert body["topJobIds"] == ["j1"]
    assert body["topScores"] == [88]


def test_record_swallows_cosmos_failure(monkeypatch, caplog):
    monkeypatch.delenv("MATCH_EVENTS_DISABLE", raising=False)
    with patch(
        "shared.cosmos_client.upsert_item",
        side_effect=RuntimeError("cosmos down"),
    ):
        # Must not raise — telemetry is fire-and-forget.
        telemetry.record(user_id="u", company_id="c", matches=[])
    assert any("write failed" in r.message for r in caplog.records)


def test_record_swallows_build_failure(caplog):
    # Pass an arg type that breaks build_event; recorder must not raise.
    telemetry.record(user_id="u", company_id="c", matches="not-a-list")
    assert any("build failed" in r.message for r in caplog.records)


def test_score_coerces_non_numeric():
    matches = [{"id": "j1", "score": "high"}, {"id": "j2", "score": None}]
    evt = telemetry.build_event(user_id="u", company_id="c", matches=matches)
    assert evt["topScores"] == [0, 0]


def test_jobid_fallback_to_jobId_key():
    matches = [{"jobId": "alt-1", "score": 50}]
    evt = telemetry.build_event(user_id="u", company_id="c", matches=matches)
    assert evt["topJobIds"] == ["alt-1"]
