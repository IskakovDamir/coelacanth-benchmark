"""Clean-room guard for the re-implemented checkers' step prompts (eval/protocol.md decision 11; docs/design/harness.md
section 10): no span of 8 words of any step prompt appears in the cached prompt code of the Ai2 Idea Novelty Checker
or Idea-Catalyst repositories. The test reads the cached repository text; the prompt writer never does. It is skipped
while prompts/CLEAN-ROOM.md or the step prompts do not exist yet, and where the gitignored cache is absent.

    PYTHONPATH=src python -m unittest discover -s tests -v
"""

import re
import unicodedata
import unittest

from coelacanth.env import REPO_ROOT

PROMPTS = REPO_ROOT / "eval" / "results" / "measurement-a" / "prompts"
CACHED = (REPO_ROOT / "cache" / "fulltext" / "ai2-inc-gh-utils-prompts.txt",
          REPO_ROOT / "cache" / "fulltext" / "ideacatalyst-code-prompts.txt")
SPAN = 8


def words(text: str) -> list[str]:
    """Lowercased words after Unicode normalization; punctuation, markup and spacing are ignored."""
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).lower())


def spans(text: str, n: int = SPAN) -> set[tuple[str, ...]]:
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


def step_prompts() -> list:
    """Every prompt file of the re-implementations: prompts/<reimpl>/*.txt, the agent league's briefs left out."""
    return sorted(p for p in PROMPTS.glob("*/*.txt") if p.parent.name != "agents")


class TestCleanRoom(unittest.TestCase):
    def test_no_8_word_span_shared_with_cached_repo_prompts(self):
        prompts = step_prompts()
        if not (PROMPTS / "CLEAN-ROOM.md").exists() or not prompts:
            self.skipTest("no clean-room step prompt exists yet")
        missing = [p.name for p in CACHED if not p.exists()]
        if missing:
            self.skipTest(f"the gitignored cache lacks {missing}")
        cached = set().union(*(spans(p.read_text(encoding="utf-8", errors="replace")) for p in CACHED))
        shared = {p.relative_to(PROMPTS).as_posix(): sorted(" ".join(s) for s in spans(p.read_text(encoding="utf-8"))
                                                            & cached)[:3]
                  for p in prompts}
        self.assertEqual({k: v for k, v in shared.items() if v}, {})

    def test_span_check_finds_a_copied_sentence_whatever_its_case_and_punctuation(self):
        source = "You are an expert reviewer. Decide whether the idea is NOVEL, given the retrieved papers below."
        copied = "Step 2: you are an expert reviewer -- decide whether the idea is novel; answer in JSON."
        self.assertTrue(spans(copied) & spans(source))
        self.assertFalse(spans("Read the claim and decide whether the idea is novel enough.") & spans(source))


if __name__ == "__main__":
    unittest.main()
