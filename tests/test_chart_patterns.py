"""Unit tests for chart pattern heuristics (synthetic price series)."""

from modules.portfolio.services.chart_patterns import (
    _Series,
    analyze_series,
    _detect_inverse_head_shoulders,
    _detect_double_bottom,
)


def _interpolate(anchors: list[tuple[int, float]], n: int) -> list[float]:
    """Piecewise-linear curve through (index, value) anchors."""
    closes = [0.0] * n
    for (i0, v0), (i1, v1) in zip(anchors, anchors[1:]):
        for i in range(i0, i1 + 1):
            t = (i - i0) / (i1 - i0) if i1 > i0 else 0.0
            closes[i] = v0 + (v1 - v0) * t
    last_idx = anchors[-1][0]
    for i in range(last_idx + 1, n):
        closes[i] = anchors[-1][1]
    return closes


def _synthetic_inverse_hs() -> _Series:
    """Inverse H&S with a recent right shoulder (within the recency window)."""
    n = 180
    labels = [f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)]
    # Clean minima only at the two shoulders (88, 87) and the head (76),
    # with neckline peaks (99) between, then a breakout to 112.
    anchors = [
        (0, 100.0),
        (50, 88.0),   # left shoulder
        (72, 99.0),   # neckline peak
        (95, 76.0),   # head (deepest)
        (112, 99.0),  # neckline peak
        (130, 87.0),  # right shoulder (recent)
        (179, 112.0),  # breakout
    ]
    closes = _interpolate(anchors, n)
    highs = list(closes)
    lows = [c - 1.0 for c in closes]
    return _Series(labels=labels, closes=closes, highs=highs, lows=lows)


def test_inverse_head_shoulders_detected():
    series = _synthetic_inverse_hs()
    hit = _detect_inverse_head_shoulders(series)
    assert hit is not None
    assert hit["pattern"] == "inverse_head_shoulders"
    assert hit["bias"] == "bullish"
    assert hit["target_price"] > series.closes[-1]


def test_pattern_returns_anchor_points():
    series = _synthetic_inverse_hs()
    hit = _detect_inverse_head_shoulders(series)
    assert hit is not None
    points = hit["points"]
    labels = {p["label"] for p in points}
    assert labels == {"Left shoulder", "Head", "Right shoulder"}
    for pt in points:
        assert pt["date"] in series.labels
        assert isinstance(pt["price"], float)
    # head should be the lowest of the three anchors
    head = next(p for p in points if p["label"] == "Head")
    shoulders = [p for p in points if p["label"] != "Head"]
    assert all(head["price"] < s["price"] for s in shoulders)


def test_analyze_series_returns_sorted():
    series = _synthetic_inverse_hs()
    patterns = analyze_series(series)
    assert patterns
    assert patterns[0]["confidence"] >= patterns[-1]["confidence"]


def test_double_bottom_on_flat_series_returns_none():
    closes = [50.0] * 120
    series = _Series(
        labels=[f"d{i}" for i in range(120)],
        closes=closes,
        highs=[c + 1 for c in closes],
        lows=[c - 1 for c in closes],
    )
    assert _detect_double_bottom(series) is None
