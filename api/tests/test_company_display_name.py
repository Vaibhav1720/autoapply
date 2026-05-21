"""Lock the contract that `_company_display_name` never returns a raw `comp-`
identifier. This is the regression guard for the customer-facing bug where
zero-match / scrape-failed responses leaked `comp-amazon` etc. to the UI.
"""
from services.jobs.routes import _company_display_name


class TestCompanyDisplayName:
    def test_known_company_returns_registry_name(self):
        # Registry entry has a proper "Amazon" name.
        assert _company_display_name("comp-amazon") == "Amazon"

    def test_morgan_stanley_kept_intact(self):
        # Multi-word names with title casing must come from the registry,
        # not from .title() on the slug (which would lowercase "Stanley").
        assert _company_display_name("comp-morgan-stanley") == "Morgan Stanley"

    def test_lowes_apostrophe_preserved(self):
        # Apostrophes / special chars survive only via the registry path.
        assert _company_display_name("comp-lowes-india") == "Lowe's India"

    def test_unknown_company_falls_back_to_title_case(self):
        # Slug-derived fallback for an id the registry doesn't know.
        assert _company_display_name("comp-some-newcomer") == "Some Newcomer"

    def test_underscore_slug_handled(self):
        assert _company_display_name("comp-foo_bar") == "Foo Bar"

    def test_no_prefix_still_pretty_prints(self):
        # Defensive: id without `comp-` prefix should still get cleaned up.
        assert _company_display_name("acme-co") == "Acme Co"

    def test_empty_string_returns_empty_string(self):
        assert _company_display_name("") == ""

    def test_none_returns_empty_string(self):
        assert _company_display_name(None) == ""

    def test_never_returns_a_raw_comp_prefix(self):
        # The headline contract: no path through this function returns a
        # string starting with "comp-". This is what the user complained about.
        for cid in ("comp-amazon", "comp-microsoft", "comp-acme-newco-india",
                    "comp-x", "comp-", "comp-foo_bar"):
            out = _company_display_name(cid)
            assert not out.lower().startswith("comp-"), (
                f"{cid!r} -> {out!r} still starts with 'comp-'"
            )
