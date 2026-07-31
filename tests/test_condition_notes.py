from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from python_vna.condition_notes import (
    condition_for_number,
    condition_number_from_path,
    parse_condition_entries,
    update_condition_readme_text,
    write_condition_readme,
    write_condition_readme_text,
)


class ConditionNotesTests(unittest.TestCase):
    def test_parse_supports_english_and_chinese_colons(self):
        text = "001:4号减振器主基板\n002：3号减振器主基板\n其他说明\n"

        entries = parse_condition_entries(text)

        self.assertEqual(entries["001"].text, "4号减振器主基板")
        self.assertEqual(entries["002"].text, "3号减振器主基板")
        self.assertEqual(condition_for_number(text, "002"), "3号减振器主基板")

    def test_parse_supports_grouped_numbers_like_real_readme(self):
        text = (
            "原减振垫圈\n"
            "静态，1号传感器地上，2号传感器工件台大理石上：\n"
            "004:Z\n"
            "005:X\n"
            "006,007:Y\n"
        )

        entries = parse_condition_entries(text)

        self.assertEqual(entries["006"].text, "Y")
        self.assertEqual(entries["007"].text, "Y")
        self.assertEqual(entries["007"].label, "006,007")
        self.assertEqual(condition_for_number(text, "007"), "Y")
        self.assertEqual(condition_for_number(text, "0007"), "Y")

    def test_update_replaces_existing_and_keeps_other_lines(self):
        text = "总说明\n001:旧工况\n003：第三条\n"

        updated = update_condition_readme_text(text, "001", "新工况")

        self.assertIn("总说明", updated)
        self.assertIn("001：新工况", updated)
        self.assertIn("003：第三条", updated)
        self.assertNotIn("001:旧工况", updated)

    def test_update_grouped_number_line_keeps_group_label(self):
        text = "004:Z\n005:X\n006,007:Y\n"

        updated = update_condition_readme_text(text, "007", "新Y")

        self.assertIn("006,007：新Y", updated)
        self.assertNotIn("006,007:Y", updated)

    def test_update_inserts_new_number_in_numeric_order(self):
        text = "001:第一条\n003：第三条\n备注行\n"

        updated = update_condition_readme_text(text, "002", "第二条")

        self.assertEqual(
            updated.splitlines(),
            ["001:第一条", "002：第二条", "003：第三条", "备注行"],
        )

    def test_condition_number_from_path_uses_trailing_digits(self):
        self.assertEqual(condition_number_from_path(Path("D:/data/003.vna")), "003")
        self.assertEqual(condition_number_from_path(Path("D:/data/test_012.vna")), "012")
        self.assertIsNone(condition_number_from_path(Path("D:/data/session.vna")))

    def test_write_condition_readme_uses_utf8_sig(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_condition_readme(tmpdir, "001", "中文工况")

            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertIn("001：中文工况", path.read_text(encoding="utf-8-sig"))

    def test_write_condition_readme_text_saves_full_editable_readme(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_condition_readme_text(tmpdir, "001：第一条\n003：第三条")

            self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertEqual(path.read_text(encoding="utf-8-sig"), "001：第一条\n003：第三条\n")


if __name__ == "__main__":
    unittest.main()
