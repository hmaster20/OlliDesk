import json

import pytest

from core.search_cache import SearchCache


@pytest.fixture
def cache(tmp_path):
    return SearchCache(tmp_path)


class TestSearchCache:
    def test_set_get_roundtrip(self, cache):
        cache.set("q", "result")
        assert cache.get("q") == "result"

    def test_get_missing(self, cache):
        assert cache.get("nope") is None

    def test_expired_entry_evicted(self, cache):
        cache.set("q", "result")
        assert cache.get("q", max_age_hours=0) is None
        assert cache.get("q") is None


class TestSearchCacheList:
    def test_list_roundtrip(self, cache):
        cache.set_list("rerank|abc", ["c1", "c2"])
        assert cache.get_list("rerank|abc") == ["c1", "c2"]

    def test_list_missing(self, cache):
        assert cache.get_list("rerank|none") is None

    def test_list_rejects_non_json(self, cache):
        cache.set("rerank|bad", "not json")
        assert cache.get_list("rerank|bad") is None

    def test_list_rejects_non_list_json(self, cache):
        cache.set("rerank|obj", json.dumps({"a": 1}))
        assert cache.get_list("rerank|obj") is None
