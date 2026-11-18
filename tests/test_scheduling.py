"""15.4.6 VRAM fit gate: pure admission logic."""

from memnc.scheduling import HEADROOM, REST_SPLIT_THRESHOLD, fit, placement_for, rest_split


def test_fit_ok_when_model_below_free():
    r = fit(min_free_bytes=24 * 2**30, model_bytes=20 * 2**30)
    assert r.fits
    assert r.reason == "ok"


def test_fit_rejects_when_model_exceeds_worst_card():
    r = fit(min_free_bytes=16 * 2**30, model_bytes=20 * 2**30)
    assert not r.fits
    assert r.reason == "unschedulable:fits-no-card"


def test_fit_headroom_absorbs_kv_cache():
    # exactly at the weight boundary without cache must still pass, and the
    # headroom factor must count KV bytes on top of the weights
    weights = 20 * 2**30
    assert fit(min_free_bytes=weights * HEADROOM, model_bytes=weights).fits
    r = fit(
        min_free_bytes=weights * HEADROOM,
        model_bytes=weights,
        kv_cache_bytes=1024,
    )
    assert not r.fits


def test_fit_unknown_size_strict_rejects_lenient_passes():
    r = fit(min_free_bytes=2**30, model_bytes=None, strict=True)
    assert not r.fits
    assert r.reason == "unschedulable:unknown-size"
    r = fit(min_free_bytes=2**30, model_bytes=None, strict=False)
    assert r.fits
    assert r.reason == "unknown-size"


def test_moe_requires_hot_cache_capability():
    r = fit(
        min_free_bytes=2**40,
        model_bytes=2**30,
        placement="moe-hot-cache",
        hot_cache_capable=False,
    )
    assert not r.fits
    assert r.reason == "unschedulable:no-hot-cache"
    r = fit(
        min_free_bytes=2**40,
        model_bytes=2**30,
        placement="moe-hot-cache",
        hot_cache_capable=True,
    )
    assert r.fits


def test_rest_split_flags_low_resident_footprint():
    weights = 20 * 2**30
    assert rest_split(weights * REST_SPLIT_THRESHOLD * 0.5, weights)
    assert not rest_split(weights, weights)
    # no data is not a verdict
    assert not rest_split(0, weights)
    assert not rest_split(weights, None)


def test_placement_for_matches_fnmatch_patterns():
    assert placement_for("moe-a3b:q4", ["moe-*"]) == "moe-hot-cache"
    assert placement_for("qwen2.5-coder:32b", ["moe-*"]) == "full"
    assert placement_for("any", []) == "full"
