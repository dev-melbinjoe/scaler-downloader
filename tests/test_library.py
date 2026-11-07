"""Tests for scaler_dl.library catalog filtering, tags, and selection."""

import unittest

from scaler_dl.library import in_display_order, is_class, select, tag


class TestLibrary(unittest.TestCase):
    def setUp(self):
        self.catalog = {
            "modules": [
                {"id": 101, "name": "Programming Basics", "order": 1},
                {"id": 102, "name": "Advanced Data Structures", "order": 2},
            ],
            "classes": [
                {
                    "class_id": 1001,
                    "no": 1,
                    "module_id": 101,
                    "module": "Programming Basics",
                    "title": "Introduction to Java",
                    "date": "2026-01-01T15:00:00Z",
                    "kind": "class",
                    "folder": "2026-01-01 Introduction to Java",
                },
                {
                    "class_id": 1002,
                    "no": 2,
                    "module_id": 101,
                    "module": "Programming Basics",
                    "title": "Control Structures",
                    "date": "2026-01-03T15:00:00Z",
                    "kind": "class",
                    "folder": "2026-01-03 Control Structures",
                },
                {
                    "class_id": 1003,
                    "no": 3,
                    "module_id": 102,
                    "module": "Advanced Data Structures",
                    "title": "Binary Trees",
                    "date": "2026-01-05T15:00:00Z",
                    "kind": "class",
                    "folder": "2026-01-05 Binary Trees",
                },
                {
                    "class_id": 1004,
                    "module_id": 102,
                    "module": "Advanced Data Structures",
                    "title": "DSA Contest 1",
                    "date": "2026-01-07T15:00:00Z",
                    "kind": "test",
                },
                {
                    "class_id": 1005,
                    "module_id": 102,
                    "module": "Advanced Data Structures",
                    "title": "Holiday Break",
                    "date": "2026-01-08T15:00:00Z",
                    "kind": "class",
                    "no_session": True,
                },
            ],
        }

    def test_is_class(self):
        self.assertTrue(is_class(self.catalog["classes"][0]))
        self.assertFalse(is_class(self.catalog["classes"][3]))  # kind="test"
        self.assertFalse(is_class(self.catalog["classes"][4]))  # no_session=True

    def test_tag(self):
        self.assertEqual(tag(self.catalog["classes"][3]), "test")
        self.assertEqual(tag(self.catalog["classes"][4]), "holiday")
        self.assertEqual(tag({"kind": "class", "status": "bonus"}), "bonus")

    def test_select_single(self):
        chosen = select(self.catalog, ["2"])
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["no"], 2)

    def test_select_range(self):
        chosen = select(self.catalog, ["1-2"])
        self.assertEqual(len(chosen), 2)
        self.assertEqual([c["no"] for c in chosen], [1, 2])

    def test_select_search(self):
        chosen = select(self.catalog, [], search="Binary Trees")
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["title"], "Binary Trees")

    def test_select_module(self):
        chosen = select(self.catalog, [], module="Advanced Data Structures")
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["no"], 3)

    def test_in_display_order(self):
        ordered = in_display_order(self.catalog)
        ids = [c["class_id"] for c in ordered]
        self.assertEqual(ids, [1001, 1002, 1003, 1004, 1005])


if __name__ == "__main__":
    unittest.main()
