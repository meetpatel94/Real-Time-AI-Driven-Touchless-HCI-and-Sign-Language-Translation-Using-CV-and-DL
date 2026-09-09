"""Service-level tests for the contextual sentence completion engine.

The suite mirrors the acceptance cases for the studio autocomplete: partial
sentences, mid-word input, punctuation, long and multi-sentence input,
caret editing, deletion, repeated words and empty input.  It runs without
Flask, MongoDB or a camera.
"""

import unittest

from services.sentence_completion_service import (
    MAX_SUGGESTIONS,
    SentenceCompletionService,
    sentence_completion_service,
)
from services.phrase_corpus import FRAGMENT_ENDINGS


def texts(result):
    return [item["text"] for item in result["suggestions"]]


class SentenceCompletionTests(unittest.TestCase):
    """Spec examples: the four documented inputs plus the "I need" frame."""

    def test_partial_sentence_i_am_go(self):
        result = sentence_completion_service.complete("I am go")
        self.assertIn("I am going home", texts(result))
        self.assertIn("I am going to school", texts(result))
        self.assertIn("I am going outside", texts(result))

    def test_i_want_to(self):
        result = sentence_completion_service.complete("I want to")
        suggestions = texts(result)
        self.assertIn("I want to go home", suggestions)
        self.assertIn("I want to talk to you", suggestions)
        self.assertTrue(any(item.startswith("I want to eat") for item in suggestions))

    def test_can_you(self):
        suggestions = texts(sentence_completion_service.complete("Can you"))
        self.assertEqual(suggestions[:3], ["Can you help me?", "Can you come here?", "Can you call me?"])

    def test_where_is(self):
        suggestions = texts(sentence_completion_service.complete("Where is"))
        self.assertEqual(
            suggestions[:3],
            ["Where is the bathroom?", "Where is my phone?", "Where is the hospital?"],
        )

    def test_i_need(self):
        suggestions = texts(sentence_completion_service.complete("I need"))
        self.assertEqual(suggestions[:3], ["I need help", "I need some water", "I need to go home"])


class SuggestionQualityTests(unittest.TestCase):
    """Ranking, de-duplication and "fewer but better" behaviour."""

    def test_at_most_three_suggestions(self):
        for sample in ("I am", "I want to", "Can you", "Where is", "I", "please", "I have a"):
            result = sentence_completion_service.complete(sample)
            self.assertLessEqual(len(result["suggestions"]), MAX_SUGGESTIONS)

    def test_suggestions_are_sorted_by_confidence(self):
        result = sentence_completion_service.complete("I want to")
        scores = [item["confidence"] for item in result["suggestions"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_duplicate_suggestions(self):
        for sample in ("I am go", "I want to", "Can you", "Where is", "I need", "thank you"):
            result = sentence_completion_service.complete(sample)
            normalized = [" ".join(item["text"].lower().split()) for item in result["suggestions"]]
            self.assertEqual(len(normalized), len(set(normalized)))

    def test_suggestions_never_end_on_a_fragment(self):
        for sample in ("Where is", "I want to", "I am", "Can you", "I went to the", "do you"):
            for item in sentence_completion_service.complete(sample)["suggestions"]:
                words = [word.strip(".,;:!?'") for word in item["text"].split()]
                words = [word for word in words if word.isalpha()]
                self.assertTrue(words, item["text"])
                self.assertNotIn(words[-1].lower(), FRAGMENT_ENDINGS, item["text"])

    def test_suggestions_complete_the_typed_text(self):
        """Every suggestion must extend the clause, never restart it."""
        for sample in ("I am go", "I want to", "Can you", "Where is"):
            result = sentence_completion_service.complete(sample)
            for item in result["suggestions"]:
                self.assertTrue(
                    item["text"].lower().startswith(sample.strip().lower()[:4]),
                    "%s does not continue %s" % (item["text"], sample),
                )

    def test_unknown_input_stays_quiet(self):
        for sample in ("zzz", "xqwrt", "qqq qqq qqq"):
            self.assertEqual(texts(sentence_completion_service.complete(sample)), [])

    def test_single_letter_word_is_not_invented(self):
        # "I" has strong evidence; a single unrelated letter has none.
        self.assertTrue(texts(sentence_completion_service.complete("I")))
        self.assertEqual(texts(sentence_completion_service.complete("q")), [])


class EmptyAndShortInputTests(unittest.TestCase):
    def test_empty_input_returns_nothing(self):
        for sample in ("", "   ", "\n"):
            result = sentence_completion_service.complete(sample)
            self.assertEqual(result["suggestions"], [])

    def test_only_punctuation_returns_nothing(self):
        self.assertEqual(texts(sentence_completion_service.complete("...")), [])

    def test_short_input_uses_the_high_confidence_floor(self):
        result = sentence_completion_service.complete("I")
        self.assertLessEqual(len(result["suggestions"]), MAX_SUGGESTIONS)
        for item in result["suggestions"]:
            self.assertGreaterEqual(item["confidence"], 0.70)

    def test_single_word_prefix_completes_from_the_corpus(self):
        self.assertIn("Thank you.", texts(sentence_completion_service.complete("th")))


class PunctuationAndContextTests(unittest.TestCase):
    def test_sentence_end_starts_a_new_clause(self):
        result = sentence_completion_service.complete("I am fine. Where is")
        self.assertEqual(result["clause"], "Where is")
        self.assertEqual(result["clause_start"], 11)
        self.assertIn("Where is the bathroom?", texts(result))

    def test_comma_keeps_the_last_clause_only(self):
        result = sentence_completion_service.complete("Hello, I am go")
        self.assertEqual(result["clause"], "I am go")
        self.assertIn("I am going home", texts(result))

    def test_punctuation_is_preserved_in_suggestions(self):
        for item in sentence_completion_service.complete("Where is")["suggestions"]:
            self.assertTrue(item["text"].endswith("?"))

    def test_attached_comma_is_not_lost(self):
        suggestions = texts(sentence_completion_service.complete("Hello"))
        self.assertIn("Hello, how are you?", suggestions)

    def test_completed_sentence_offers_nothing(self):
        self.assertEqual(texts(sentence_completion_service.complete("Can you help me?")), [])
        self.assertEqual(texts(sentence_completion_service.complete("I am going home now")), [])

    def test_long_sentence_completes_from_the_trailing_words(self):
        sentence = "Good morning, my name is Alex and I am go"
        result = sentence_completion_service.complete(sentence)
        self.assertEqual(result["clause"], "my name is Alex and I am go")
        self.assertIn("my name is Alex and I am going home", texts(result))

    def test_long_sentence_keeps_the_leading_text_untouched(self):
        result = sentence_completion_service.complete("After the meeting I want to")
        self.assertEqual(result["clause"], "After the meeting I want to")
        for item in result["suggestions"]:
            self.assertTrue(item["text"].startswith("After the meeting "), item["text"])

    def test_caret_editing_in_the_middle(self):
        text = "I am going home now"
        result = sentence_completion_service.complete(text, caret=4)
        self.assertEqual(result["clause"], "I am")
        self.assertEqual(result["clause_start"], 0)
        self.assertIn("I am going home", texts(result))

    def test_backspace_deletion_is_deterministic(self):
        typed = "I am going hom"
        shrinking = [typed[: len(typed) - step] for step in range(0, 4)]
        results = [texts(sentence_completion_service.complete(value)) for value in shrinking]
        self.assertEqual(results[0], results[1])
        self.assertIn("I am going home", results[0])

    def test_repeated_words_do_not_produce_garbage(self):
        result = sentence_completion_service.complete("I I I")
        for item in result["suggestions"]:
            self.assertLessEqual(item["text"].lower().count("i i"), 1)

    def test_apostrophes_are_supported(self):
        self.assertIn("I don't know", texts(sentence_completion_service.complete("I don't")))
        self.assertIn("I'm sorry", texts(sentence_completion_service.complete("I'm")))


class PerformanceAndCacheTests(unittest.TestCase):
    def test_repeated_prediction_is_cached(self):
        service = SentenceCompletionService()
        service.clear_cache()
        first = service.complete("cache probe: I want to")
        second = service.complete("cache probe: I want to")
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(texts(first), texts(second))

    def test_prediction_is_fast(self):
        service = SentenceCompletionService()
        service.clear_cache()
        samples = ["I am go", "I want to", "Can you", "Where is", "I need", "thank you for"]
        for index in range(40):
            service.complete(samples[index % len(samples)] + (" " if index % 2 else ""))
        result = service.complete("I am go")
        self.assertLess(result["elapsed_ms"], 60.0)


class PersonalizationTests(unittest.TestCase):
    def setUp(self):
        self.service = SentenceCompletionService()
        self.service.clear_cache()
        self.service.clear_memory("test-user-autocomplete")
        self.user = "test-user-autocomplete"

    def tearDown(self):
        self.service.clear_memory(self.user)
        self.service.clear_cache()

    def test_acceptance_is_remembered(self):
        self.assertTrue(
            self.service.register_acceptance("I need", "I need help", caret=6, user_id=self.user)
        )
        snapshot = self.service.memory_snapshot(self.user)
        self.assertTrue(any(item["context"] == "i need" for item in snapshot))
        self.assertTrue(any("help" in item["tail"] for item in snapshot))

    def test_unrelated_acceptance_is_ignored(self):
        self.assertFalse(
            self.service.register_acceptance("Where is", "I need help", user_id=self.user)
        )

    def test_personalization_boosts_without_overriding_the_model(self):
        before = {item["text"]: item["confidence"] for item in self.service.complete("I need", user_id=self.user)["suggestions"]}
        for _ in range(4):
            self.service.register_acceptance("I need", "I need a break", caret=6, user_id=self.user)
        self.service.clear_cache()
        after = {item["text"]: item["confidence"] for item in self.service.complete("I need", user_id=self.user)["suggestions"]}

        self.assertIn("I need a break", after)
        self.assertGreater(after["I need a break"], before.get("I need a break", 0.0))
        # The language model still decides the top suggestion.
        self.assertEqual(max(after, key=after.get), "I need help")

    def test_memory_is_scoped_per_user(self):
        self.service.register_acceptance("I need", "I need a break", caret=6, user_id=self.user)
        self.service.clear_cache()
        other = [item["text"] for item in self.service.complete("I need", user_id="other-user")["suggestions"]]
        self.assertNotIn("I need a break", other)


if __name__ == "__main__":
    unittest.main()
