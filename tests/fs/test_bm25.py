"""Тесты лексического индекса BM25 и его токенизации."""

from fs.bm25 import BM25Index, tokenize


class TestTokenize:
    def test_lowercases_and_splits_words(self):
        assert tokenize("Hello World") == ["hello", "world"]

    def test_splits_snake_case(self):
        assert tokenize("get_user_id") == ["get", "user", "id"]

    def test_splits_camel_case(self):
        assert tokenize("HTTPServer") == ["http", "server"]
        assert tokenize("userID") == ["user", "id"]

    def test_keeps_cyrillic(self):
        assert tokenize("Привет, мир!") == ["привет", "мир"]

    def test_ignores_punctuation(self):
        assert tokenize("a.b+c=d") == ["a", "b", "c", "d"]


class TestBM25Index:
    def test_empty_index_returns_nothing(self):
        index = BM25Index()
        index.build([])
        assert index.search("query", top_k=5) == []

    def test_finds_matching_document(self):
        index = BM25Index()
        index.build(["def hello(): pass", "class Widget: pass"])

        ranked = index.search("hello", top_k=5)

        assert ranked
        assert ranked[0][0] == 0  # документ с «hello»

    def test_rarer_term_scores_higher(self):
        index = BM25Index()
        # «widget» есть во всех, «gadget» — только в одном.
        index.build(["widget a", "widget b", "widget gadget"])

        ranked = index.search("widget gadget", top_k=3)

        assert ranked[0][0] == 2

    def test_respects_top_k(self):
        index = BM25Index()
        index.build(["alpha"] * 5)

        assert len(index.search("alpha", top_k=2)) == 2

    def test_unknown_term_returns_nothing(self):
        index = BM25Index()
        index.build(["alpha beta"])

        assert index.search("gamma", top_k=5) == []
