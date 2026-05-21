"""Phase 5 — companies.json overlay unit tests.

Strategy: test the loader function in isolation rather than relying on
module reload tricks (which are fragile under pytest's import cache).
The integration test (companies.json gets read at module import time and
populates COMPANIES with real data) is covered by the regression suite —
all 28 golden snapshots resolve real company IDs through COMPANIES.

Runs offline in <1s.
"""
import json
import os
import shutil

from shared import career_scraper as cs


class TestOverlayWithCurrentJson:
    """Verify the live module loaded the JSON overlay (the real, shipped state)."""

    def test_companies_dict_populated(self):
        # Embedded dict has 150 entries; JSON should match exactly (no extras
        # on first generation). If someone adds via JSON later, this can rise.
        assert len(cs.COMPANIES) >= 150

    def test_amazon_present_with_full_metadata(self):
        amz = cs.COMPANIES["comp-amazon"]
        assert amz["name"] == "Amazon"
        assert amz["careersUrl"].startswith("http")
        assert amz["industry"]
        assert amz["description"]

    def test_phase5_routing_fields_propagated(self):
        """The JSON has `ats` fields. They should be present in COMPANIES
        even though the runtime doesn't currently consume them — this is a
        forward-compat check that the JSON file shipped with the deploy."""
        airbnb = cs.COMPANIES.get("comp-airbnb", {})
        assert airbnb.get("ats") == "greenhouse", (
            f"comp-airbnb.ats should be 'greenhouse' from companies.json; "
            f"got {airbnb.get('ats')!r}. Did the JSON ship with the package?"
        )

    def test_microsoft_has_linkedin_id(self):
        ms = cs.COMPANIES["comp-microsoft"]
        assert ms.get("linkedinId") == "1035"

    def test_lever_companies_have_board_slug(self):
        cred = cs.COMPANIES["comp-cred"]
        assert cred.get("ats") == "lever"
        assert cred.get("atsBoard") == "cred"


class TestOverlayHelperDirect:
    """Test `_overlay_companies_from_json` directly — no module reload."""

    BASE = {
        "comp-test1": {"id": "comp-test1", "name": "Test1", "careersUrl": "http://x"},
        "comp-test2": {"id": "comp-test2", "name": "Test2", "careersUrl": "http://y"},
    }

    def test_disable_flag_returns_base_unchanged(self, monkeypatch):
        monkeypatch.setenv("COMPANIES_REGISTRY_DISABLE", "1")
        out = cs._overlay_companies_from_json(self.BASE)
        assert out == self.BASE
        assert "ats" not in out["comp-test1"]

    def test_missing_data_dir_returns_base(self, tmp_path):
        """Temporarily move the data dir away, confirm fallback."""
        data_dir = os.path.join(os.path.dirname(cs.__file__), "data")
        json_path = os.path.join(data_dir, "companies.json")
        moved = str(tmp_path / "companies.json")
        shutil.move(json_path, moved)
        try:
            out = cs._overlay_companies_from_json(self.BASE)
            assert out == self.BASE
        finally:
            shutil.move(moved, json_path)

    def test_per_field_merge_overrides_one_key(self, tmp_path):
        """Stand up a temp companies.json that ONLY changes one field.
        Validate the embedded fields survive."""
        data_dir = os.path.join(os.path.dirname(cs.__file__), "data")
        json_path = os.path.join(data_dir, "companies.json")
        original = open(json_path, "r", encoding="utf-8").read()
        try:
            override = {
                "_comment": "test override",
                "companies": {
                    "comp-test1": {"description": "OVERRIDDEN", "ats": "test"},
                    "comp-newbie": {
                        "id": "comp-newbie", "name": "Newbie",
                        "careersUrl": "http://n", "ats": "linkedin_by_name",
                    },
                },
            }
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(override, f)
            out = cs._overlay_companies_from_json(self.BASE)
            # comp-test1: per-field merge — name kept from base, description
            # set from JSON, ats added from JSON.
            assert out["comp-test1"]["name"] == "Test1"
            assert out["comp-test1"]["careersUrl"] == "http://x"
            assert out["comp-test1"]["description"] == "OVERRIDDEN"
            assert out["comp-test1"]["ats"] == "test"
            # comp-test2: untouched (not in override) — present from base.
            assert out["comp-test2"]["name"] == "Test2"
            # comp-newbie: brand-new entry, only in JSON.
            assert "comp-newbie" in out
            assert out["comp-newbie"]["ats"] == "linkedin_by_name"
            # Base must NOT be mutated.
            assert "description" not in self.BASE["comp-test1"]
        finally:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_malformed_json_returns_base(self, tmp_path):
        data_dir = os.path.join(os.path.dirname(cs.__file__), "data")
        json_path = os.path.join(data_dir, "companies.json")
        original = open(json_path, "r", encoding="utf-8").read()
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write("not json {{{")
            out = cs._overlay_companies_from_json(self.BASE)
            assert out == self.BASE
        finally:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(original)

    def test_missing_top_level_companies_key_returns_base(self):
        data_dir = os.path.join(os.path.dirname(cs.__file__), "data")
        json_path = os.path.join(data_dir, "companies.json")
        original = open(json_path, "r", encoding="utf-8").read()
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump({"unrelated": "value"}, f)
            out = cs._overlay_companies_from_json(self.BASE)
            assert out == self.BASE
        finally:
            with open(json_path, "w", encoding="utf-8") as f:
                f.write(original)
