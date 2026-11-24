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
