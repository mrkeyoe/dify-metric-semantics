"""Tests for normalisation, SQL canonicalisation, similarity and numeric scope."""

from __future__ import annotations

import unittest

from core.text import (
    canonical_sql,
    char_ngrams,
    jaccard,
    normalize_text,
    numeric_markers,
    semantic_distance,
    semantic_similarity,
    sql_equivalent,
    token_set,
    truncate,
)


class NormalizeTextTest(unittest.TestCase):
    def test_drops_spaces_and_punctuation(self) -> None:
        self.assertEqual(normalize_text("上个月 活跃用户数？"), "上个月活跃用户数")

    def test_casefold_and_nfkc(self) -> None:
        self.assertEqual(normalize_text("ＧＭＶ (GMV)"), "gmvgmv")

    def test_empty(self) -> None:
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text(None), "")  # type: ignore[arg-type]

    def test_alias_is_substring_of_sentence(self) -> None:
        self.assertIn(normalize_text("活跃用户"), normalize_text("上个月活跃用户数"))


class CanonicalSqlTest(unittest.TestCase):
    def test_ignores_case_whitespace_and_comments(self) -> None:
        left = "SELECT COUNT(DISTINCT user_id)\n  FROM t WHERE a = 1 -- trailing"
        right = "select count(distinct user_id) from t where a=1"
        self.assertEqual(canonical_sql(left), canonical_sql(right))
        self.assertTrue(sql_equivalent(left, right))

    def test_different_columns_are_not_equivalent(self) -> None:
        left = "SELECT COUNT(DISTINCT user_id) FROM t"
        right = "SELECT COUNT(DISTINCT buyer_id) FROM t"
        self.assertFalse(sql_equivalent(left, right))

    def test_none_when_fragment_missing(self) -> None:
        self.assertIsNone(sql_equivalent(None, "SELECT 1"))
        self.assertIsNone(sql_equivalent("SELECT 1", None))
        self.assertIsNone(canonical_sql(None))

    def test_trailing_semicolon_and_spacing_around_punctuation(self) -> None:
        self.assertEqual(
            canonical_sql("SELECT a , b FROM t ;"), canonical_sql("select a,b from t")
        )


class SimilarityTest(unittest.TestCase):
    def test_char_ngrams(self) -> None:
        self.assertEqual(char_ngrams("ab", 2), frozenset({"ab"}))
        self.assertEqual(char_ngrams(""), frozenset())
        self.assertIn("活跃", char_ngrams("活跃用户"))

    def test_jaccard_bounds(self) -> None:
        self.assertEqual(jaccard([], []), 0.0)
        self.assertEqual(jaccard(["a"], ["a"]), 1.0)
        self.assertEqual(jaccard(["a"], ["b"]), 0.0)

    def test_token_set_includes_latin_words_and_cjk_bigrams(self) -> None:
        tokens = token_set("DAU 活跃用户")
        self.assertIn("dau", tokens)
        self.assertIn("活跃", tokens)

    def test_similarity_identity_and_unrelated(self) -> None:
        text = "过去 7 天内登录过的去重用户数"
        self.assertEqual(semantic_similarity(text, text), 1.0)
        self.assertLess(
            semantic_similarity(text, "统计周期内支付成功订单的实付金额之和"),
            0.3,
        )

    def test_similarity_is_symmetric(self) -> None:
        left = "过去 7 天内登录过的去重用户数"
        right = "过去 7 天内的去重登录用户数"
        self.assertEqual(semantic_similarity(left, right), semantic_similarity(right, left))


class NumericMarkersTest(unittest.TestCase):
    def test_days_and_months(self) -> None:
        self.assertEqual(numeric_markers("过去 7 天内登录"), frozenset({"7d"}))
        self.assertEqual(numeric_markers("近 2 个月的下单用户"), frozenset({"2mo"}))
        self.assertEqual(numeric_markers("近 2 个月 3 天内"), frozenset({"2mo", "3d"}))

    def test_percent(self) -> None:
        self.assertEqual(numeric_markers("点击率 5%"), frozenset({"5pct"}))

    def test_no_markers(self) -> None:
        self.assertEqual(numeric_markers("统计周期内的成交金额"), frozenset())

    def test_decimal_number_keeps_value(self) -> None:
        self.assertEqual(numeric_markers("过去 1.5 天"), frozenset({"1.5d"}))


class SemanticDistanceTest(unittest.TestCase):
    def test_identical_text_has_zero_distance(self) -> None:
        text = "过去 7 天内登录过的去重用户数"
        self.assertEqual(semantic_distance(text, text), 0.0)

    def test_different_window_increases_distance(self) -> None:
        left = "过去 7 天内登录过的用户数"
        right = "过去 30 天内登录过的用户数"
        plain = semantic_distance(left, right, numeric_penalty=0.0)
        penalised = semantic_distance(left, right, numeric_penalty=0.25)
        self.assertAlmostEqual(penalised - plain, 0.25, places=6)

    def test_one_sided_marker_gets_half_penalty(self) -> None:
        left = "过去 7 天内登录过的用户数"
        right = "登录过的用户数"
        plain = semantic_distance(left, right, numeric_penalty=0.0)
        penalised = semantic_distance(left, right, numeric_penalty=0.25)
        self.assertAlmostEqual(penalised - plain, 0.125, places=6)

    def test_distance_is_bounded(self) -> None:
        self.assertLessEqual(semantic_distance("a", "完全不同的一句话"), 1.0)
        self.assertGreaterEqual(semantic_distance("a", "完全不同的一句话"), 0.0)

    def test_acceptance_seed_distance_exceeds_default_threshold(self) -> None:
        from core.seeds import example_definitions

        first, second, _ = example_definitions()
        self.assertGreater(
            semantic_distance(first.definition_text, second.definition_text), 0.35
        )


class TruncateTest(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        self.assertEqual(truncate("abc", 10), "abc")

    def test_long_text_gets_ellipsis(self) -> None:
        result = truncate("abcdefghij", 5)
        self.assertEqual(len(result), 5)
        self.assertTrue(result.endswith("…"))

    def test_zero_limit(self) -> None:
        self.assertEqual(truncate("abc", 0), "abc")


if __name__ == "__main__":
    unittest.main()