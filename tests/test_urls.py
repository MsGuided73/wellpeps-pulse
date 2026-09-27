"""Tests for permalink normalization (the url_norm dedupe key)."""

import pytest

from harvey.urls import normalize_url


@pytest.mark.parametrize(
    "raw, expected",
    [
        # scheme + host lowercased, path case preserved
        ("HTTPS://WWW.Reddit.COM/r/Peptides/Comments/abc",
         "https://www.reddit.com/r/Peptides/Comments/abc"),
        # fragment stripped
        ("https://example.com/post/1#comment-9", "https://example.com/post/1"),
        # trailing slash stripped
        ("https://example.com/post/1/", "https://example.com/post/1"),
        # tracking params stripped
        ("https://example.com/p?utm_source=x&utm_medium=y&utm_content=z",
         "https://example.com/p"),
        ("https://example.com/p?fbclid=1&gclid=2&igshid=3&si=4", "https://example.com/p"),
        # other params kept and sorted
        ("https://example.com/p?b=2&utm_term=q&a=1", "https://example.com/p?a=1&b=2"),
        # surrounding whitespace ignored
        ("  https://example.com/p  ", "https://example.com/p"),
        # bare host keeps no trailing slash
        ("https://Example.com/", "https://example.com"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_equivalent_urls_normalize_identically():
    a = normalize_url("https://www.instagram.com/p/XYZ/?igshid=abc")
    b = normalize_url("https://WWW.instagram.com/p/XYZ#top")
    assert a == b


def test_tracking_param_names_are_case_insensitive():
    assert normalize_url("https://example.com/p?UTM_Source=x") == "https://example.com/p"


def test_blank_url_normalizes_to_empty_string():
    assert normalize_url("") == ""
    assert normalize_url("   ") == ""
