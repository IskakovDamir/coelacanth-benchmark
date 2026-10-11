"""Tests for the Message Batches client. No network: a fake stands in for HTTP.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import json
import unittest

from coelacanth.anthropic_batch import API_URL, BatchClient, BatchError, LowCreditBalance, message_text


class FakeRequest:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        status, payload = self.answers.pop(0)
        return status, {}, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


def client(fake):
    return BatchClient("KEY", request=fake, sleep=lambda s: None)


class TestBatchClient(unittest.TestCase):
    def test_create_posts_requests_with_key_and_version_headers(self):
        fake = FakeRequest((200, {"id": "msgbatch_1", "processing_status": "in_progress"}))
        batch = client(fake).create([{"custom_id": "W1", "params": {"model": "m", "max_tokens": 5, "messages": []}}])
        method, url, headers, body = fake.calls[0]
        self.assertEqual((method, url, batch["id"]), ("POST", API_URL, "msgbatch_1"))
        self.assertEqual((headers["x-api-key"], headers["anthropic-version"]), ("KEY", "2023-06-01"))
        self.assertEqual(json.loads(body)["requests"][0]["custom_id"], "W1")

    def test_wait_polls_until_ended_and_results_parse_jsonl(self):
        ended = {"id": "b", "processing_status": "ended", "results_url": "https://example/results"}
        lines = [{"custom_id": "W1", "result": {"type": "succeeded", "message": {
                     "content": [{"type": "text", "text": "[{\"n\": 1}]"}], "usage": {"input_tokens": 3}}}},
                 {"custom_id": "W2", "result": {"type": "errored", "error": {"type": "invalid_request"}}}]
        fake = FakeRequest((200, {"id": "b", "processing_status": "in_progress"}), (529, b"overloaded"), (200, ended),
                           (200, "\n".join(json.dumps(x) for x in lines).encode()))
        c = client(fake)
        batch = c.wait("b", poll_seconds=0)
        results = list(c.results(batch))
        self.assertEqual([r["custom_id"] for r in results], ["W1", "W2"])
        self.assertEqual(message_text(results[0]), '[{"n": 1}]')
        self.assertIsNone(message_text(results[1]))
        self.assertEqual(fake.calls[-1][1], "https://example/results")

    def test_network_errors_are_retried_for_get_but_not_for_post(self):
        class Flaky(FakeRequest):
            def __call__(self, method, url, headers, body, timeout):
                if not self.calls:
                    self.calls.append((method, url, dict(headers), body))
                    raise OSError("nodename nor servname provided")
                return super().__call__(method, url, headers, body, timeout)
        got = client(Flaky((200, {"id": "b", "processing_status": "in_progress"}))).get("b")
        self.assertEqual(got["id"], "b")
        with self.assertRaises(BatchError):
            client(Flaky((200, {"id": "b"}))).create([{"custom_id": "W1", "params": {}}])

    def test_low_credit_balance_raises_its_own_error(self):
        body = b'{"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low"}}'
        with self.assertRaises(LowCreditBalance):
            client(FakeRequest((400, body))).get("b")

    def test_client_errors_raise_and_a_key_is_required(self):
        with self.assertRaises(BatchError):
            client(FakeRequest((400, b"bad"))).get("b")
        with self.assertRaises(BatchError):
            BatchClient("", request=FakeRequest())


if __name__ == "__main__":
    unittest.main()
