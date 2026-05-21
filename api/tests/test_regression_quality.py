"""Offline matcher-quality regression tests.

These tests load **frozen golden snapshots** (raw scraped jobs + LLM
judge verdicts captured once via ``python -m eval.capture_golden``) and
re-run only the deterministic in-process matcher. They make ZERO
network calls and ZERO LLM calls, so the whole 28-pair suite runs in
under 5 seconds at $0 cost.

What they catch:
  - Regressions in ``match_jobs_to_profile`` (filter / score / discipline /
    location / seniority / skill extraction).
  - Filter logic that drops a previously-good job from the top-10.
  - Score-formula tweaks that demote a previously-good job below position 10.

What they intentionally do NOT catch (because re-running them every
test is too slow / expensive):
  - LLM-rerank prompt regressions \u2014 the saved verdicts are the truth.
  - Vector-embedding model swaps \u2014 not in the offline pipeline.
  - Live scraper breakage \u2014 covered by the optional ``regression-live``
    suite (``api/tests/test_regression_quality_live.py``) and by
    ``capture_golden.py`` itself surfacing scrape errors at refresh time.

Refresh the golden when you ship an intentional matcher improvement
and want to lock the new bar in::

    cd api
    $env:AZURE_AI_ENDPOINT = "..." ; $env:AZURE_AI_KEY = "..."
    python -m eval.capture_golden                # all pairs
    git add eval/golden && git commit -m "refresh regression golden"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(os.environ.get("GOLDEN_DIR", str(_API_DIR / "eval" / "golden")))

# Make sibling packages importable regardless of the cwd pytest was
# invoked from. Same trick capture_golden.py uses.
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))

# How many positions we score precision over. Must match the snapshot's
# ``baseline.judge_top_n`` semantics (capture_golden writes top 10).
_TOP_N = 10
_TOP5 = 5

# ── Tolerances ──────────────────────────────────────────────────────────
# Because everything is offline and deterministic, the only real source
# of variance is *intentional* matcher changes. Default tolerances are
# correspondingly tight; widen via env if a temporary regression is
# acceptable.
P_AT_N_DROP = float(os.environ.get("REGRESSION_P_DROP", "0.10"))
P_AT_5_DROP = float(os.environ.get("REGRESSION_P5_DROP", "0.20"))
BAD_INC = int(os.environ.get("REGRESSION_BAD_INC", "1"))
BAD5_INC = int(os.environ.get("REGRESSION_BAD5_INC", "1"))

# How many of the new top-10 positions are allowed to be "unknown"
# (i.e. surfacing a job we never judged). One unknown = re-judge later;
# more = the matcher has drifted enough that the golden is stale and
# must be refreshed.
MAX_UNKNOWN_TOP_N = int(os.environ.get("REGRESSION_MAX_UNKNOWN", "3"))

# Aggregate tolerances (averaged across all snapshots). Tighter still.
AGG_P_DROP = float(os.environ.get("REGRESSION_AGG_P_DROP", "0.05"))
AGG_P5_DROP = float(os.environ.get("REGRESSION_AGG_P5_DROP", "0.07"))
AGG_BAD_INC = float(os.environ.get("REGRESSION_AGG_BAD_INC", "0.5"))


# ── Snapshot discovery ──────────────────────────────────────────────────


def _list_snapshots() -> list[Path]:
    if not GOLDEN_DIR.exists():
        return []
    return sorted(p for p in GOLDEN_DIR.glob("*.json") if p.is_file())


def _snap_id(p: Path) -> str:
    return p.stem  # e.g. "junior-1y-india__comp-amazon"


_SNAPSHOTS = _list_snapshots()
_SNAP_IDS = [_snap_id(p) for p in _SNAPSHOTS]


# ── Helpers ─────────────────────────────────────────────────────────────


def _profile_by_label(label: str) -> dict | None:
    from eval.regression_harness import PROFILES  # noqa: WPS433
    for p in PROFILES:
        if p.get("_label") == label:
            return p
    return None


def _bucket(top_jobs: list[dict], verdicts: dict[str, dict]) -> dict:
    good = maybe = bad = unknown = 0
    for j in top_jobs:
        v = (verdicts.get(j.get("id") or "") or {}).get("v")
        if v == "GOOD":
            good += 1
        elif v == "MAYBE":
            maybe += 1
        elif v == "BAD":
            bad += 1
        else:
            unknown += 1
    return {"good": good, "maybe": maybe, "bad": bad, "unknown": unknown}


def _eval_snapshot(snap: dict) -> dict:
    """Re-run the deterministic matcher on the saved raw_jobs and score
    the resulting top-10 against the saved verdicts."""
    from shared.career_scraper import match_jobs_to_profile  # noqa: WPS433

    profile = _profile_by_label(snap["profile"])
    if profile is None:
        return {"_error": f"no PROFILES entry for label {snap['profile']!r}"}

    raw_jobs = snap.get("raw_jobs") or []
    matched = match_jobs_to_profile(raw_jobs, profile)
    top_n = matched[:_TOP_N]
    top_5 = matched[:_TOP5]

    verdicts = snap.get("verdicts") or {}
    b10 = _bucket(top_n, verdicts)
    b5 = _bucket(top_5, verdicts)

    n10, n5 = len(top_n), len(top_5)
    return {
        "n_raw": len(raw_jobs),
        "n_matched": len(matched),
        "top_ids": [j.get("id") for j in top_n],
        "p_at_10": (b10["good"] / n10) if n10 else 0.0,
        "p_at_5":  (b5["good"] / n5) if n5 else 0.0,
        "good": b10["good"], "maybe": b10["maybe"],
        "bad": b10["bad"], "unknown": b10["unknown"],
        "good5": b5["good"], "bad5": b5["bad"], "unknown5": b5["unknown"],
    }


@pytest.fixture(scope="module")
def _all_results() -> dict[str, dict]:
    """Evaluate every golden snapshot exactly once per test session."""
    out: dict[str, dict] = {}
    for path in _SNAPSHOTS:
        try:
            snap = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            out[_snap_id(path)] = {"_error": f"failed to load: {e}"}
            continue
        out[_snap_id(path)] = {
            "snapshot": snap,
            "current": _eval_snapshot(snap),
        }
    return out


# ── Tests ───────────────────────────────────────────────────────────────


def test_golden_directory_exists() -> None:
    """Cheap smoke test that runs by default \u2014 fails loudly if someone
    accidentally deletes the entire golden snapshot folder."""
    assert GOLDEN_DIR.exists(), (
        f"Golden snapshot dir missing: {GOLDEN_DIR}. "
        f"Run `python -m eval.capture_golden` from api/ to create it."
    )
    assert _SNAPSHOTS, (
        f"No *.json snapshots in {GOLDEN_DIR}. "
        f"Run `python -m eval.capture_golden` from api/."
    )


def test_golden_snapshot_format() -> None:
    """Cheap structural check on every snapshot \u2014 runs by default."""
    if not _SNAPSHOTS:
        pytest.skip("No snapshots to validate (golden dir empty).")
    for path in _SNAPSHOTS:
        snap = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(snap.get("schemaVersion"), int), path.name
        assert snap.get("profile"), path.name
        assert snap.get("company"), path.name
        assert isinstance(snap.get("raw_jobs"), list), path.name
        assert isinstance(snap.get("verdicts"), dict), path.name
        base = snap.get("baseline") or {}
        assert "p_at_10" in base and "p_at_5" in base, path.name
        assert _profile_by_label(snap["profile"]) is not None, (
            f"snapshot {path.name} references unknown profile {snap['profile']!r}; "
            f"add it to regression_harness.PROFILES or re-capture."
        )


@pytest.mark.regression
@pytest.mark.parametrize("snap_id", _SNAP_IDS or ["no-snapshots"])
def test_no_quality_regression_per_snapshot(snap_id: str, _all_results: dict[str, dict]) -> None:
    if snap_id == "no-snapshots" or not _SNAPSHOTS:
        pytest.skip("No golden snapshots committed.")
    bundle = _all_results[snap_id]
    if "_error" in bundle:
        pytest.fail(f"{snap_id}: {bundle['_error']}")
    cur = bundle["current"]
    if "_error" in cur:
        pytest.fail(f"{snap_id}: {cur['_error']}")

    base = bundle["snapshot"].get("baseline") or {}
    base_p10 = float(base.get("p_at_10", 0.0))
    base_p5 = float(base.get("p_at_5", 0.0))
    base_bad = int(base.get("bad", 0))
    base_bad5 = int(base.get("bad5", 0))

    # Surface the comparison even on success so the run log is readable.
    print(
        f"\n[{snap_id}] raw={cur['n_raw']} matched={cur['n_matched']}  "
        f"P@10 base={base_p10:.2f} cur={cur['p_at_10']:.2f}  "
        f"P@5 base={base_p5:.2f} cur={cur['p_at_5']:.2f}  "
        f"BAD base={base_bad} cur={cur['bad']}  "
        f"unknown_in_top10={cur['unknown']}"
    )

    assert cur["unknown"] <= MAX_UNKNOWN_TOP_N, (
        f"{snap_id}: matcher surfaced {cur['unknown']} jobs in top-{_TOP_N} that are "
        f"not in the saved verdict map (allowed {MAX_UNKNOWN_TOP_N}). "
        f"Either the matcher drifted or the golden is stale \u2014 refresh with "
        f"`python -m eval.capture_golden {bundle['snapshot']['profile']}`."
    )

    # Use rounded-to-2dp comparisons to avoid FP boundary surprises (e.g.
    # 0.7 < 0.8 - 0.1 == 0.7000000000000001 fails despite being "equal" to
    # the user). The assertion message already prints values to 2 decimals
    # so the rounding here matches what the human sees.
    assert round(cur["p_at_10"], 2) >= round(base_p10 - P_AT_N_DROP, 2), (
        f"{snap_id}: P@10 regressed: baseline={base_p10:.2f} current={cur['p_at_10']:.2f} "
        f"(allowed drop {P_AT_N_DROP:.2f})"
    )
    assert round(cur["p_at_5"], 2) >= round(base_p5 - P_AT_5_DROP, 2), (
        f"{snap_id}: P@5 regressed: baseline={base_p5:.2f} current={cur['p_at_5']:.2f} "
        f"(allowed drop {P_AT_5_DROP:.2f})"
    )
    assert cur["bad"] <= base_bad + BAD_INC, (
        f"{snap_id}: BAD count grew: baseline={base_bad} current={cur['bad']} "
        f"(allowed increase {BAD_INC})"
    )
    assert cur["bad5"] <= base_bad5 + BAD5_INC, (
        f"{snap_id}: BAD-in-top5 grew: baseline={base_bad5} current={cur['bad5']} "
        f"(allowed increase {BAD5_INC})"
    )


@pytest.mark.regression
def test_no_quality_regression_aggregate(_all_results: dict[str, dict]) -> None:
    if not _SNAPSHOTS:
        pytest.skip("No golden snapshots committed.")
    valid = [b for b in _all_results.values()
             if "_error" not in b and "_error" not in b.get("current", {})]
    if not valid:
        pytest.fail("No valid snapshot evaluations \u2014 matcher pipeline appears broken.")

    base_agg = {
        "p_at_10": sum(float((b["snapshot"]["baseline"] or {}).get("p_at_10", 0.0)) for b in valid) / len(valid),
        "p_at_5":  sum(float((b["snapshot"]["baseline"] or {}).get("p_at_5", 0.0))  for b in valid) / len(valid),
        "bad":     sum(int((b["snapshot"]["baseline"] or {}).get("bad", 0))         for b in valid) / len(valid),
    }
    cur_agg = {
        "p_at_10": sum(b["current"]["p_at_10"] for b in valid) / len(valid),
        "p_at_5":  sum(b["current"]["p_at_5"]  for b in valid) / len(valid),
        "bad":     sum(b["current"]["bad"]     for b in valid) / len(valid),
    }
    print(
        "\n[aggregate]\n"
        f"  baseline: P@10={base_agg['p_at_10']:.3f} P@5={base_agg['p_at_5']:.3f} BAD={base_agg['bad']:.2f}\n"
        f"  current : P@10={cur_agg['p_at_10']:.3f} P@5={cur_agg['p_at_5']:.3f} BAD={cur_agg['bad']:.2f}"
    )
    assert cur_agg["p_at_10"] >= base_agg["p_at_10"] - AGG_P_DROP, (
        f"Aggregate P@10 regressed: baseline={base_agg['p_at_10']:.3f} "
        f"current={cur_agg['p_at_10']:.3f} (allowed drop {AGG_P_DROP:.2f})"
    )
    assert cur_agg["p_at_5"] >= base_agg["p_at_5"] - AGG_P5_DROP, (
        f"Aggregate P@5 regressed: baseline={base_agg['p_at_5']:.3f} "
        f"current={cur_agg['p_at_5']:.3f} (allowed drop {AGG_P5_DROP:.2f})"
    )
    assert cur_agg["bad"] <= base_agg["bad"] + AGG_BAD_INC, (
        f"Aggregate BAD grew: baseline={base_agg['bad']:.2f} "
        f"current={cur_agg['bad']:.2f} (allowed increase {AGG_BAD_INC})"
    )
