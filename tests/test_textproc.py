"""Tests for scaler_dl.textproc text processing and NLP extraction."""

import unittest

from scaler_dl.textproc import (
    highlights,
    instructor_questions,
    key_terms,
    mentions,
    paragraphs,
    sentences,
    tokens,
)


class TestTextProc(unittest.TestCase):
    def test_tokens(self):
        text = "Today we will discuss Binary Search and Time Complexity."
        toks = tokens(text)
        self.assertIn("binary", toks)
        self.assertIn("search", toks)
        self.assertIn("complexity", toks)
        self.assertNotIn("we", toks)
        self.assertNotIn("will", toks)

    def test_sentences(self):
        segments = [
            {"start": 0.0, "end": 4.0, "text": "Hello students. Today we learn algorithms."},
            {"start": 4.0, "end": 8.0, "text": "This is important. Let us begin."},
        ]
        sents = sentences(segments)
        self.assertEqual(len(sents), 4)
        self.assertEqual(sents[0]["text"], "Hello students.")
        self.assertGreaterEqual(sents[0]["start"], 0.0)
        self.assertLessEqual(sents[0]["end"], 4.0)

    def test_paragraphs(self):
        sents = [
            {"start": 0.0, "end": 2.0, "text": "Sentence one."},
            {"start": 2.5, "end": 5.0, "text": "Sentence two."},
            {"start": 12.0, "end": 15.0, "text": "Sentence three after pause."},
        ]
        paras = paragraphs(sents, pause=3.0)
        self.assertEqual(len(paras), 2)
        self.assertIn("Sentence one. Sentence two.", paras[0]["text"])
        self.assertEqual(paras[1]["text"], "Sentence three after pause.")

    def test_key_terms(self):
        sents = [
            {"start": 10.0, "end": 15.0, "text": "Binary search divides the search space in half."},
            {"start": 80.0, "end": 85.0, "text": "Notice that binary search requires a sorted array."},
            {"start": 160.0, "end": 165.0, "text": "Binary search time complexity is logarithmic."},
        ]
        terms = key_terms(sents, limit=5)
        terms_found = [t["term"] for t in terms]
        self.assertTrue(any("binary" in t for t in terms_found))

    def test_mentions(self):
        sents = [
            {"start": 10.0, "end": 15.0, "text": "We initialize a hash map here."},
            {"start": 120.0, "end": 125.0, "text": "Insert the key into hash map."},
        ]
        times = mentions(sents, "Hash Map")
        self.assertEqual(len(times), 2)
        self.assertEqual(times[0], 10.0)
        self.assertEqual(times[1], 120.0)

    def test_instructor_questions(self):
        sents = [
            {"start": 10.0, "end": 15.0, "text": "Can you see my screen?"},  # chatter -> should be ignored
            {"start": 20.0, "end": 25.0, "text": "This is clear, right?"},  # tag question -> should be ignored
            {
                "start": 30.0,
                "end": 35.0,
                "text": "What happens if the target element is not present in the array?",
            },  # substantive question
        ]
        qs = instructor_questions(sents)
        self.assertEqual(len(qs), 1)
        self.assertIn("What happens if the target element", qs[0]["text"])

    def test_highlights(self):
        chapter1 = [
            {
                "start": 0.0,
                "end": 10.0,
                "text": "The fundamental concept of recursion is breaking problems down into base cases.",
            },
            {
                "start": 10.0,
                "end": 20.0,
                "text": "Remember that every recursive function must define a proper terminating condition.",
            },
            {
                "start": 20.0,
                "end": 30.0,
                "text": "Yeah okay fine let us see right.",  # chatter
            },
        ]
        res = highlights([chapter1])
        self.assertEqual(len(res), 1)
        self.assertGreaterEqual(len(res[0]), 1)


if __name__ == "__main__":
    unittest.main()
