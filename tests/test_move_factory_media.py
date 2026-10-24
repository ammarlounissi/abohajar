import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import move_factory_media as m

NAME = "a" * 32 + ".jpg"
NAME2 = "b" * 32 + ".mp4"


class MoveFactoryMediaTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.media = Path(tmp.name)
        self.new = self.media / "factory"
        (self.media / "products" / "B" / "B001").mkdir(parents=True)
        (self.media / "products" / "B" / "B001" / "1.jpg").write_bytes(b"p")
        (self.media / "3").mkdir()
        (self.media / "3" / NAME).write_bytes(b"one")
        (self.media / "3" / NAME2).write_bytes(b"two")
        (self.media / "3" / "notes.txt").write_bytes(b"keep")
        (self.media / "7").mkdir()
        (self.media / "7" / NAME).write_bytes(b"seven")
        (self.media / "audio").mkdir()
        (self.media / "audio" / "a.mp3").write_bytes(b"a")

    def test_plan_only_touches_numeric_folders_with_library_files(self):
        moves, notes = m.plan_moves(self.media, self.new)
        self.assertEqual(
            sorted((s.relative_to(self.media).as_posix(), d.relative_to(self.media).as_posix()) for s, d in moves),
            [("3/" + NAME, "factory/3/" + NAME), ("3/" + NAME2, "factory/3/" + NAME2), ("7/" + NAME, "factory/7/" + NAME)],
        )
        self.assertTrue(any("notes.txt" in n for n in notes))

    def test_apply_moves_files_keeps_others_and_removes_empty_dirs(self):
        moves, _ = m.plan_moves(self.media, self.new)
        moved, problems = m.apply_moves(moves)
        self.assertEqual((moved, problems), (3, []))
        self.assertEqual((self.new / "3" / NAME).read_bytes(), b"one")
        self.assertEqual((self.new / "7" / NAME).read_bytes(), b"seven")
        self.assertFalse((self.media / "7").exists())              # فرغ فحُذف
        self.assertTrue((self.media / "3" / "notes.txt").exists())  # فيه ملف آخر فبقي
        self.assertTrue((self.media / "products" / "B" / "B001" / "1.jpg").exists())
        self.assertTrue((self.media / "audio" / "a.mp3").exists())

    def test_never_overwrites_existing_destination(self):
        (self.new / "7").mkdir(parents=True)
        (self.new / "7" / NAME).write_bytes(b"already there")
        moves, _ = m.plan_moves(self.media, self.new)
        moved, problems = m.apply_moves(moves)
        self.assertEqual(moved, 2)
        self.assertEqual(len(problems), 1)
        self.assertEqual((self.new / "7" / NAME).read_bytes(), b"already there")
        self.assertEqual((self.media / "7" / NAME).read_bytes(), b"seven")

    def test_running_twice_is_a_noop(self):
        m.apply_moves(m.plan_moves(self.media, self.new)[0])
        moves, _ = m.plan_moves(self.media, self.new)
        self.assertEqual(moves, [])


if __name__ == "__main__":
    unittest.main()
