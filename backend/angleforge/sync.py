"""Measured, conservative audio alignment. No project state is mutated."""
from __future__ import annotations

from itertools import combinations
from math import gcd
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from scipy.signal import correlate, correlation_lags, resample_poly

RATE = 2000
MAX_SECONDS = 1200


class SyncError(ValueError):
    def __init__(self, message, diagnostics=None):
        super().__init__(message)
        self.diagnostics = diagnostics or []


def _check(ctx):
    if ctx is not None:
        ctx.check_cancelled()


def _read(path: Path):
    rate, values = wavfile.read(path, mmap=True)
    if len(values) / rate > MAX_SECONDS:
        raise SyncError("Audio exceeds the 20 minute sync budget; use manual anchors or shorter clips.")
    original_dtype = values.dtype
    values = values.astype(np.float64)
    if original_dtype == np.uint8:
        values -= 128
    if values.ndim == 2:
        values = values.mean(axis=1)
    common = gcd(int(rate), RATE)
    values = resample_poly(values, RATE // common, int(rate) // common)
    values -= values.mean() if len(values) else 0
    scale = np.sqrt(np.mean(values ** 2)) if len(values) else 0
    if len(values) < RATE or scale < 1e-8:
        raise SyncError("Audio is silent or too short for reliable sync; set manual offsets or anchors.")
    return values / scale


def _pair(a, b, min_seconds=2.0):
    """NCC for each lag using actual overlap energy and local mean subtraction."""
    lags = correlation_lags(len(a), len(b))
    a0 = np.maximum(lags, 0)
    b0 = np.maximum(-lags, 0)
    count = np.minimum(len(a) - a0, len(b) - b0)
    required = min(int(min_seconds * RATE), int(min(len(a), len(b)) * 0.7))
    sums_a = np.r_[0, np.cumsum(a)]
    sums_b = np.r_[0, np.cumsum(b)]
    square_a = np.r_[0, np.cumsum(a * a)]
    square_b = np.r_[0, np.cumsum(b * b)]
    sa = sums_a[a0 + count] - sums_a[a0]
    sb = sums_b[b0 + count] - sums_b[b0]
    ea = square_a[a0 + count] - square_a[a0] - sa * sa / count
    eb = square_b[b0 + count] - square_b[b0] - sb * sb / count
    covariance = correlate(a, b, method="fft") - sa * sb / count
    scores = covariance / np.maximum(np.sqrt(np.maximum(ea * eb, 0)), 1e-12)
    scores[count < required] = -1
    i = int(np.argmax(scores))
    best = float(np.clip(scores[i], -1, 1))
    distant = np.abs(lags - lags[i]) > int(0.15 * RATE)
    runner = float(np.max(scores[distant])) if np.any(distant) else -1
    accepted = best >= 0.50 and best - runner >= 0.10
    return {"offset_us": int(round(int(lags[i]) * 1_000_000 / RATE)),
            "confidence": round(max(best, 0), 5), "second_peak": round(max(runner, 0), 5),
            "overlap_us": int(count[i] * 1_000_000 // RATE),
            "accepted": accepted,
            "reason": "measured" if accepted else ("ambiguous audio peaks" if best >= .5 else "weak correlation")}


def _drift(a, b, offset_us):
    """Measure separate head/tail residuals; drift is warned, never silently stretched."""
    lag = round(offset_us * RATE / 1_000_000)
    a0, b0 = max(lag, 0), max(-lag, 0)
    n = min(len(a) - a0, len(b) - b0)
    if n < 30 * RATE:
        return None
    residuals = []
    for start in (0, n - 8 * RATE):
        result = _pair(a[a0 + start:a0 + start + 8 * RATE], b[b0 + start:b0 + start + 8 * RATE])
        if result["accepted"]:
            residuals.append(result["offset_us"])
    if len(residuals) == 2 and abs(residuals[1] - residuals[0]) > 25_000:
        return {"residual_change_us": residuals[1] - residuals[0], "measured_span_us": int((n - 8 * RATE) / RATE * 1e6)}
    return None



def _independent_drift(a, b):
    # For a weak global match, independently locate two short windows. This can
    # reveal why one constant offset cannot explain a long recording.
    if min(len(a), len(b)) < 30 * RATE:
        return None
    window = 8 * RATE
    first = _pair(a, b[:window])
    last = _pair(a, b[-window:])
    if first["accepted"] and last["accepted"]:
        last_offset = last["offset_us"] - int((len(b) - window) * 1_000_000 // RATE)
        change = last_offset - first["offset_us"]
        if abs(change) > 25_000:
            return {"residual_change_us": change}
    return None

def propose_sync(assets: dict, audio_paths: dict[str, Path], reference_id=None, ctx=None) -> dict:
    ids = [key for key, asset in assets.items() if asset.get("status") == "ready" and asset.get("kind") == "video"]
    if not ids:
        raise SyncError("Import at least one ready video before synchronizing.")
    reference_id = reference_id or ids[0]
    if reference_id not in ids:
        raise SyncError("The sync reference must be a ready video asset.")
    if len(ids) > 8:
        raise SyncError("Automatic synchronization is limited to eight videos per proposal.")
    if len(ids) == 1:
        return {"reference_asset_id": reference_id, "offsets": {reference_id: 0}, "confidence": {reference_id: 1.0}, "anchors": [], "warnings": [], "edges": [], "diagnostics": []}
    signals, diagnostics = {}, []
    for key in ids:
        _check(ctx)
        if key not in audio_paths:
            diagnostics.append({"asset_id": key, "reason": "no comparable audio"})
            continue
        try:
            signals[key] = _read(Path(audio_paths[key]))
        except (SyncError, ValueError, OSError) as exc:
            diagnostics.append({"asset_id": key, "reason": str(exc)})
    edges, warnings = [], []
    pairs = list(combinations(signals, 2))
    for number, (left, right) in enumerate(pairs):
        _check(ctx)
        if ctx is not None:
            ctx.progress(number / max(len(pairs), 1), "Measuring audio overlap")
        match = _pair(signals[left], signals[right])
        edge = {"a": left, "b": right, **match}
        diagnostics.append(edge)
        if not match["accepted"]:
            drift = _independent_drift(signals[left], signals[right])
            if drift:
                edge["drift"] = drift
                warnings.append(f"Possible clock drift between {assets[left]['name']} and {assets[right]['name']}: independently matched windows differ by {drift['residual_change_us']/1000:.1f} ms. Time stretching is not implemented.")
        if match["accepted"]:
            edges.append(edge)
            drift = _drift(signals[left], signals[right], match["offset_us"])
            if drift:
                warnings.append(f"Possible clock drift between {assets[left]['name']} and {assets[right]['name']}: head/tail residual changed {drift['residual_change_us']/1000:.1f} ms. Time stretching is not implemented; inspect manual anchors.")
    offsets, confidence = {reference_id: 0}, {reference_id: 1.0}
    while True:
        changed = False
        for edge in sorted(edges, key=lambda e: e["confidence"], reverse=True):
            a, b, difference = edge["a"], edge["b"], edge["offset_us"]
            if a in offsets and b not in offsets:
                offsets[b] = offsets[a] + difference
                confidence[b] = min(confidence[a], edge["confidence"])
                changed = True
            elif b in offsets and a not in offsets:
                offsets[a] = offsets[b] - difference
                confidence[a] = min(confidence[b], edge["confidence"])
                changed = True
            elif a in offsets and b in offsets and abs(offsets[b] - offsets[a] - difference) > 50_000:
                raise SyncError("Conflicting overlap measurements; automatic sync refused. Set manual anchors.", diagnostics)
        if not changed:
            break
    missing = [assets[key]["name"] for key in ids if key not in offsets]
    if missing:
        raise SyncError("Uncertain or disconnected audio for " + ", ".join(missing) + "; automatic sync refused. Set manual offsets or matching anchors." + (" " + " ".join(warnings) if warnings else ""), diagnostics)
    return {"reference_asset_id": reference_id, "offsets": offsets, "confidence": confidence,
            "anchors": [], "warnings": warnings, "edges": edges, "diagnostics": diagnostics}
