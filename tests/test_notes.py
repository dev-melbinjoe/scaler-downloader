"""Tests for scaler_dl.notes Markdown and HTML generators."""

import unittest

from scaler_dl.notes import collect_links, make_chapters, number_quizzes, render_html, render_md


class TestNotes(unittest.TestCase):
    def test_number_quizzes(self):
        info = {
            "bookmarks": [
                {"at": 100.0, "title": "Introduction"},
                {"at": 320.0, "title": "Quiz 1 Solution"},
            ],
            "quizzes": [
                {
                    "at": 300.0,
                    "question": "What is 2 + 2?",
                    "choices": ["3", "4", "5"],
                }
            ],
        }
        quizzes = number_quizzes(info)
        self.assertEqual(len(quizzes), 1)
        self.assertEqual(quizzes[0]["no"], 1)
        self.assertEqual(quizzes[0]["solution_at"], 320.0)

    def test_make_chapters(self):
        info = {
            "bookmarks": [
                {"at": 60.0, "title": "Welcome and Agenda", "type": "topic"},
                {"at": 600.0, "title": "Algorithm Discussion", "type": "topic"},
            ]
        }
        chapters = make_chapters(info, duration=1200.0, sents=[], frames=[])
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["start"], 0.0)
        self.assertEqual(chapters[1]["start"], 600.0)

    def test_collect_links(self):
        info = {
            "lecture_notes": [{"filename": "notes.pdf", "file": "attachments/notes.pdf", "url": "https://scaler.com/notes.pdf"}],
            "notices": [{"title": "Course Repo", "cta_link": "https://github.com/scaler/example", "at": 10.0}],
            "instructor_messages": [{"text": "Reference: https://docs.oracle.com/en/java/", "at": 50.0}],
        }
        links = collect_links(info)
        urls = [link["url"] for link in links]
        self.assertIn("attachments/notes.pdf", urls)
        self.assertIn("https://github.com/scaler/example", urls)
        self.assertIn("https://docs.oracle.com/en/java/", urls)

    def test_render_md_and_html(self):
        doc = {
            "info": {
                "title": "Sample Class",
                "module": "Test Module",
                "date": "2026-01-01",
                "no": 1,
                "bookmarks": [],
            },
            "duration": 3600.0,
            "has_video": False,
            "segments": [],
            "transcribed": False,
            "model": "small",
            "chapters": [{"no": 1, "start": 0.0, "end": 3600.0, "title": "Main Chapter", "frames": [], "highlights": [], "quizzes": [], "doubts": [], "paragraphs": []}],
            "quizzes": [],
            "revision": "### Quick Recap\nImportant points.",
            "cards": [{"card_title": "Concept", "card_content": "Definition", "times": [10.0]}],
            "terms": [{"term": "concept", "count": 5, "times": [10.0, 20.0]}],
            "questions": [],
            "doubts": [],
            "links": [],
            "frames": [],
        }
        md = render_md(doc)
        self.assertIn("# Sample Class", md)
        self.assertIn("### Quick Recap", md)

        html_out = render_html(doc)
        self.assertIn("<title>Sample Class</title>", html_out)
        self.assertIn("Quick Recap", html_out)


if __name__ == "__main__":
    unittest.main()
