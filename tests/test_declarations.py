"""Tests for round-2 paging of the declaration queries, against a fake OpenAlex client. No network.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import unittest

from coelacanth.mining import declarations


class FakeClient:
    daily_budget_usd = 0.10

    def __init__(self, pages: dict[str, list[dict]], spent: float = 0.0) -> None:
        self.pages = pages
        self.spent = spent

    def spent_today(self) -> float:
        return self.spent

    def iter_works(self, *, filter, select, per_page):
        name = next(n for n, f in declarations.QUERIES.items() if f == filter)
        yield from self.pages.get(name, [])


def work(wid: str, title: str) -> dict:
    return {"id": f"https://openalex.org/{wid}", "title": title, "type": "article", "authorships": []}


class TestPage(unittest.TestCase):
    def test_new_works_get_round_two_and_known_works_gain_the_query(self):
        rows = [{"openalex": "W1", "title": "Known", "queries": ["comment_equivalent"]}]
        stats = [{"query": "comment_equivalent", "openalex_count": 3},
                 {"query": "title_by_another_name", "openalex_count": 1}]
        client = FakeClient({"comment_equivalent": [work("W1", "Known"), work("W2", "Is equivalent to X")],
                             "title_by_another_name": [work("W1", "Known")]})
        new_stats, merged = declarations.page(client, rows, stats,
                                              names=("comment_equivalent", "title_by_another_name"))
        by_id = {r["openalex"]: r for r in merged}
        self.assertEqual(by_id["W1"]["queries"], ["comment_equivalent", "title_by_another_name"])
        self.assertNotIn("round", by_id["W1"])
        self.assertEqual(by_id["W2"]["round"], 2)
        self.assertTrue(by_id["W2"]["likely_declaration"])
        self.assertEqual([s.get("fetched_all_pages") for s in new_stats], [2, 1])
        self.assertNotIn("fetched_all_pages", stats[0])  # the input is not modified

    def test_refuses_when_the_planned_searches_pass_the_daily_budget(self):
        stats = [{"query": "comment_equivalent", "openalex_count": 20000}]
        with self.assertRaises(SystemExit):
            declarations.page(FakeClient({}), [], stats, names=("comment_equivalent",))

    def test_paged_queries_exist(self):
        self.assertTrue(set(declarations.PAGED) <= set(declarations.QUERIES))


if __name__ == "__main__":
    unittest.main()
