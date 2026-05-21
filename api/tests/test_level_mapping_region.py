"""Phase 4 — region-aware level mapping unit tests.

These run offline in <1 s. They DO NOT replace the regression suite; they
document the contract of `_resolve_level_min_years` and `_user_region` and
guard the JSON config from accidental regressions.
"""
from shared import career_scraper as cs


class TestUserRegion:
    def test_no_buckets_returns_default(self):
        assert cs._user_region(set()) == "default"

    def test_in_takes_priority_over_us(self):
        # IN is the largest non-US user base; if both, prefer IN.
        assert cs._user_region({"US", "IN"}) == "IN"

    def test_us_only_returns_default(self):
        # US has no override (it IS the baseline) so default is correct.
        assert cs._user_region({"US"}) == "default"

    def test_uk_returns_uk(self):
        assert cs._user_region({"UK"}) == "UK"

    def test_germany_returns_de(self):
        assert cs._user_region({"DE"}) == "DE"

    def test_ireland_returns_ie(self):
        assert cs._user_region({"IE"}) == "IE"

    def test_unknown_country_falls_back_to_default(self):
        # JP not in level_mappings.json — fall back, do not crash.
        assert cs._user_region({"JP"}) == "default"


class TestResolveLevelMinYears:
    BASE = {
        "intern": 0, "junior": 0, "mid": 3,
        "senior": 5, "lead": 5, "staff": 8, "principal": 10,
    }

    def test_default_region_returns_base_unchanged(self):
        out = cs._resolve_level_min_years(self.BASE, "default")
        assert out == self.BASE
        # Must not mutate the input dict.
        assert self.BASE["senior"] == 5

    def test_in_overrides_senior_to_3(self):
        out = cs._resolve_level_min_years(self.BASE, "IN")
        # Phase 4 rationale: India "Senior @ 3y" should not be hard-dropped
        # against a 3-year-experience user.
        assert out["senior"] == 3
        assert out["lead"] == 4
        assert out["staff"] == 6
        # Levels not overridden in the JSON keep the base value.
        assert out["intern"] == 0
        assert out["mid"] == 3

    def test_uk_overrides_senior_to_4(self):
        out = cs._resolve_level_min_years(self.BASE, "UK")
        assert out["senior"] == 4
        assert out["staff"] == 7

    def test_unknown_region_returns_base(self):
        out = cs._resolve_level_min_years(self.BASE, "ZZ")
        assert out == self.BASE


class TestLevelMappingsLoaded:
    """Sanity-check the JSON file actually loaded at import time."""

    def test_mappings_contain_in(self):
        assert "IN" in cs._LEVEL_MAPPINGS, "IN region missing — level_mappings.json failed to load?"

    def test_in_senior_is_3(self):
        assert cs._LEVEL_MAPPINGS["IN"]["senior"] == 3

    def test_no_underscore_keys_leaked(self):
        # _comment fields in the JSON should be stripped by the loader.
        for region, mapping in cs._LEVEL_MAPPINGS.items():
            assert not region.startswith("_")
            for key in mapping:
                assert not key.startswith("_")

    def test_all_values_are_ints(self):
        for region, mapping in cs._LEVEL_MAPPINGS.items():
            for key, val in mapping.items():
                assert isinstance(val, int), f"{region}.{key} = {val!r} (not int)"
