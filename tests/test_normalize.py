"""Normalization rules — §13.

Pure functions, so these run everywhere with no services. They are also the rules most
likely to be tuned later, which is exactly why each one is pinned by an example drawn
from the spec or from how real feeds behave.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.normalize import (
    content_hash,
    normalize_title,
    normalize_url,
    slugify,
    strip_html,
    to_utc,
)


class TestNormalizeUrl:
    def test_the_spec_example(self):
        """§13's own case: a tracking parameter must not create a second article."""
        a = normalize_url("https://article.com/story?id=123&utm_source=rss")
        b = normalize_url("https://article.com/story?id=123")
        assert a == b

    def test_scheme_and_www_and_case_are_irrelevant(self):
        forms = [
            "https://www.Example.com/Path",
            "http://example.com/Path",
            "https://example.com/Path/",
            "https://EXAMPLE.com:443/Path",
        ]
        assert len({normalize_url(f) for f in forms}) == 1

    def test_path_case_is_preserved(self):
        """Hosts are case-insensitive; paths are not. /Story and /story may differ."""
        assert normalize_url("https://a.example/Story") != normalize_url("https://a.example/story")

    def test_remaining_parameters_are_sorted_not_dropped(self):
        one = normalize_url("https://a.example/x?b=2&a=1")
        two = normalize_url("https://a.example/x?a=1&b=2")
        assert one == two
        assert "a=1" in one and "b=2" in one

    def test_fragment_is_dropped(self):
        assert normalize_url("https://a.example/x#section") == normalize_url("https://a.example/x")

    def test_every_known_tracker_is_stripped(self):
        noisy = (
            "https://a.example/x?id=7&utm_source=rss&fbclid=abc&gclid=def"
            "&mc_cid=1&igshid=2&spm=3"
        )
        assert normalize_url(noisy) == normalize_url("https://a.example/x?id=7")

    def test_root_path_survives(self):
        assert normalize_url("https://a.example/") == "a.example/"

    def test_empty_input(self):
        assert normalize_url(None) is None
        assert normalize_url("   ") is None


class TestNormalizeTitle:
    def test_breaking_prefix_removed(self):
        assert normalize_title("BREAKING: Storm hits coast") == normalize_title("Storm hits coast")

    def test_publication_suffix_removed(self):
        assert normalize_title("Storm hits coast - Example Times") == normalize_title(
            "Storm hits coast"
        )

    def test_case_punctuation_and_accents_folded(self):
        assert normalize_title("Café's Owner, Sued!") == normalize_title("cafes owner sued")

    def test_html_is_stripped(self):
        assert normalize_title("<b>Storm</b> hits") == "storm hits"

    def test_distinct_headlines_stay_distinct(self):
        assert normalize_title("Storm hits coast") != normalize_title("Storm misses coast")

    def test_empty_input(self):
        assert normalize_title(None) is None
        assert normalize_title("   ") is None


class TestContentHash:
    def test_stable_across_incidental_differences(self):
        a = content_hash("Storm hits", "<p>Details.</p>", "https://a.example/x?utm_source=rss")
        b = content_hash("Storm hits", "Details.", "https://a.example/x")
        assert a == b

    def test_changes_with_substance(self):
        a = content_hash("Storm hits", "Details.", "https://a.example/x")
        b = content_hash("Storm misses", "Details.", "https://a.example/x")
        assert a != b

    def test_is_hex_sha256(self):
        value = content_hash("t", "d", "https://a.example/x")
        assert len(value) == 64 and int(value, 16) >= 0


class TestStripHtml:
    def test_tags_and_entities(self):
        assert strip_html("<p>Rates &amp; markets</p>") == "Rates & markets"

    def test_collapses_whitespace(self):
        assert strip_html("a\n\n   b") == "a b"

    def test_empty(self):
        assert strip_html(None) is None
        assert strip_html("<p></p>") is None


class TestToUtc:
    def test_naive_is_assumed_utc(self):
        assert to_utc(datetime(2026, 8, 11, 8, 0)).tzinfo == timezone.utc

    def test_aware_is_converted(self):
        value = datetime(2026, 8, 11, 8, 0, tzinfo=timezone(timedelta(hours=5)))
        assert to_utc(value).hour == 3

    def test_none(self):
        assert to_utc(None) is None


class TestSlugify:
    def test_basic(self):
        assert slugify("Power Grid News") == "power-grid-news"

    def test_accents_and_punctuation(self):
        assert slugify("Café & Bar!") == "cafe-bar"

    def test_never_empty(self):
        assert slugify("!!!") == "untitled"

    def test_length_bounded(self):
        assert len(slugify("x" * 500)) <= 80
