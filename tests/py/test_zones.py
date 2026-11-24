from app import zones as Z

GOOGLE = [{"type": "LIGHT", "min": 30, "max": 108}, {"type": "MODERATE", "min": 109, "max": 131},
          {"type": "VIGOROUS", "min": 132, "max": 161}, {"type": "PEAK", "min": 162, "max": 220}]


def test_parse_google_zones_sorts_and_converts():
    dp = {"dailyHeartRateZones": {"heartRateZones": [
        {"heartRateZoneType": "PEAK", "minBeatsPerMinute": "162", "maxBeatsPerMinute": "220"},
        {"heartRateZoneType": "LIGHT", "minBeatsPerMinute": "30", "maxBeatsPerMinute": "108"}]}}
    assert Z.parse_google_zones(dp) == [{"type": "LIGHT", "min": 30, "max": 108},
                                        {"type": "PEAK", "min": 162, "max": 220}]


def test_fallback_zones_from_max_hr():
    z = Z.fallback_zones(200)
    assert [x["type"] for x in z] == Z.ZONE_TYPES
    assert z[0] == {"type": "LIGHT", "min": 100, "max": 119}
    assert z[1]["min"] == 120 and z[2]["min"] == 140 and z[3] == {"type": "PEAK", "min": 170, "max": 200}


def test_zone_index_boundaries():
    assert Z.zone_index(29, GOOGLE) == -1
    assert Z.zone_index(30, GOOGLE) == 0
    assert Z.zone_index(108, GOOGLE) == 0
    assert Z.zone_index(109, GOOGLE) == 1
    assert Z.zone_index(161, GOOGLE) == 2
    assert Z.zone_index(162, GOOGLE) == 3
    assert Z.zone_index(250, GOOGLE) == 3  # above the top zone still counts as peak


def test_choose_zones_same_day_then_earlier_then_later():
    daily = {"2026-11-10": "A", "2026-11-20": "B"}
    assert Z.choose_zones(daily, "2026-11-20") == ("B", "2026-11-20")
    assert Z.choose_zones(daily, "2026-11-23") == ("B", "2026-11-20")
    assert Z.choose_zones(daily, "2026-11-15") == ("A", "2026-11-10")
    assert Z.choose_zones(daily, "2026-11-01") == ("A", "2026-11-10")
    assert Z.choose_zones({}, "2026-11-01") == (None, None)


def test_time_in_zones_holds_value_until_next_sample():
    s = [(0, 100), (10, 120), (20, 140), (30, 170)]
    tiz = Z.time_in_zones(s, GOOGLE, 0, 40)
    assert tiz == {"below": 0, "LIGHT": 10, "MODERATE": 10, "VIGOROUS": 10, "PEAK": 10}


def test_time_in_zones_skips_gaps_and_clamps_to_window():
    s = [(-5, 150), (0, 100), (5, 100), (100, 120), (105, 120), (200, 170)]
    tiz = Z.time_in_zones(s, GOOGLE, 0, 110, gap_s=30)
    # 0-5 light, 5-100 is a 95 s gap (not counted), 100-105 moderate, 105-110 moderate (clamped at end)
    assert tiz["LIGHT"] == 5 and tiz["MODERATE"] == 10 and tiz["PEAK"] == 0 and tiz["VIGOROUS"] == 0


def test_time_in_zones_below_lowest_zone():
    tiz = Z.time_in_zones([(0, 20), (10, 20)], GOOGLE, 0, 20)
    assert tiz["below"] == 20


def test_time_in_zones_empty():
    assert Z.time_in_zones([], GOOGLE, 0, 100) == {"below": 0, "LIGHT": 0, "MODERATE": 0, "VIGOROUS": 0, "PEAK": 0}


def test_sample_stats_only_inside_window():
    s = [(-10, 200), (0, 100), (10, 140), (20, 120), (60, 190)]
    assert Z.sample_stats(s, 0, 30) == {"count": 3, "avg": 120, "max": 140, "min": 100}
    assert Z.sample_stats([], 0, 30)["count"] == 0


def test_recovery_hr_drops_at_1_and_2_minutes():
    end = 1000
    s = [(990, 150), (998, 152), (1059, 130), (1061, 128), (1120, 110)]
    r = Z.recovery_hr(s, end)
    assert r["end_hr"] == 152
    assert r["hr_1"] == 130 and r["drop_1"] == 22   # 1059 is 1 s from target, 1061 also 1 s: first wins
    assert r["hr_2"] == 110 and r["drop_2"] == 42


def test_recovery_hr_not_available_without_samples():
    end = 1000
    assert Z.recovery_hr([(995, 150)], end) == {"end_hr": 150, "hr_1": None, "drop_1": None, "hr_2": None, "drop_2": None}
    # samples after the end, but nothing near the end itself
    r = Z.recovery_hr([(900, 150), (1060, 120), (1120, 100)], end)
    assert r["end_hr"] is None and r["drop_1"] is None and r["drop_2"] is None
    # 1-minute point exists, 2-minute point is outside the ±10 s tolerance
    r = Z.recovery_hr([(1000, 150), (1060, 120), (1135, 100)], end)
    assert r["drop_1"] == 30 and r["drop_2"] is None
    assert Z.recovery_hr([], end)["drop_1"] is None


def test_zone_breakdown_minutes_and_percent():
    rows = Z.zone_breakdown({"below": 0, "LIGHT": 300, "MODERATE": 600, "VIGOROUS": 300, "PEAK": 0}, GOOGLE)
    assert [r["type"] for r in rows] == ["LIGHT", "MODERATE", "VIGOROUS", "PEAK"]
    assert [r["pct"] for r in rows] == [25.0, 50.0, 25.0, 0]
    rows = Z.zone_breakdown({"below": 60, "LIGHT": 60, "MODERATE": 0, "VIGOROUS": 0, "PEAK": 0}, GOOGLE)
    assert rows[0]["type"] == "below" and rows[0]["pct"] == 50.0
