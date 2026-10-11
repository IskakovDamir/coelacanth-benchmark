"""Tests for cost computation from token usage at the official prices.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import unittest

from coelacanth.pricing import cli_cost, price, usage_cost


class TestPricing(unittest.TestCase):
    def test_list_and_batch_prices(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
        self.assertAlmostEqual(usage_cost("claude-haiku-4-5-20251001", usage), 1.0 + 0.5)
        self.assertAlmostEqual(usage_cost("claude-haiku-4-5-20251001", usage, batch=True), 0.75)
        self.assertAlmostEqual(usage_cost("claude-opus-5-5", usage), 4.0 + 2.0)

    def test_cache_writes_by_duration_and_reads(self):
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 1_000_000,
                 "cache_creation_input_tokens": 2_000_000,
                 "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}}
        self.assertAlmostEqual(usage_cost("claude-sonnet-5", usage), 0.20 + 2.50 + 4.00)
        no_split = {"cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(usage_cost("claude-sonnet-5", no_split), 2.50)

    def test_aliases_and_unknown_models(self):
        self.assertEqual(price("claude-haiku-4-5"), price("claude-haiku-4-5-20251001"))
        self.assertEqual(price("claude-opus-5").input, 5.00)
        self.assertNotEqual(price("claude-opus-5"), price("claude-opus-5-5"))
        with self.assertRaises(KeyError):
            price("gpt-4o")
        with self.assertRaises(KeyError):
            price("claude-sonnet-4")

    def test_cli_output_single_and_several_models(self):
        single = {"modelUsage": {"claude-sonnet-5": {"inputTokens": 1_000_000, "cacheCreationInputTokens": 1_000_000}},
                  "usage": {"input_tokens": 1_000_000, "output_tokens": 0,
                            "cache_creation": {"ephemeral_1h_input_tokens": 400_000}}}
        self.assertAlmostEqual(cli_cost(single), 2.0 + 0.6 * 2.50 + 0.4 * 4.00)
        several = {"modelUsage": {"claude-sonnet-5": {"inputTokens": 1_000_000, "outputTokens": 0},
                                  "claude-haiku-4-5-20251001": {"inputTokens": 0, "outputTokens": 1_000_000}}}
        self.assertAlmostEqual(cli_cost(several), 2.0 + 5.0)
        with self.assertRaises(KeyError):  # run_jobs then books the CLI's own figure
            cli_cost({"modelUsage": {}})


class TestWebSearch(unittest.TestCase):
    def test_web_searches_are_priced_on_top_of_tokens(self):
        from coelacanth.pricing import cli_cost, web_searches
        out = {"modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 1000, "outputTokens": 100,
                                                            "webSearchRequests": 3}},
               "usage": {"input_tokens": 1000, "output_tokens": 100}}
        self.assertEqual(web_searches(out), 3)
        self.assertAlmostEqual(cli_cost(out), (1000 * 1.0 + 100 * 5.0) / 1e6 + 0.03)
        self.assertEqual(web_searches({"usage": {"server_tool_use": {"web_search_requests": 2}}}), 2)

    def test_tool_sub_calls_in_model_usage_are_counted(self):
        from coelacanth.pricing import cli_cost
        # the CLI output of probe "safe" on 2026-10-06: top-level usage covers the main loop only
        out = {"usage": {"input_tokens": 5387, "output_tokens": 203},
               "modelUsage": {"claude-haiku-4-5-20251001": {"inputTokens": 15762, "outputTokens": 699,
                                                            "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                                                            "webSearchRequests": 1, "costUSD": 0.029257}}}
        self.assertAlmostEqual(cli_cost(out), 0.029257, places=6)


if __name__ == "__main__":
    unittest.main()
