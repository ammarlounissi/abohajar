"""مزامنة صور Google Drive إلى مكتبة المصنع. rclone مُستبدل بدوال وهمية، والباقي حقيقي:
قاعدة SQLite في الذاكرة ومجلد مؤقت للمكتبة."""
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.config import settings
from app.core.database import Base
from app.services import drive_sync, errors


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buffer, format="PNG")
    return buffer.getvalue()


class DriveSyncCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name, value in (
            ("FACTORY_MEDIA_ROOT", Path(self.tmp.name) / "factory"),
            ("BASE_URL", "https://api.example.test"),
        ):
            patcher = mock.patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(models.Factory(id=1, name="روستيكا", phone_number="213550000000"))
            db.add(models.Factory(id=2, name="آخر", phone_number="213550000001"))
            db.add(models.FactoryContact(factory_id=1, channel="email", identifier="rustika@gmail.com"))
            db.add(models.FactoryContact(factory_id=2, channel="email", identifier="other@gmail.com"))
            db.commit()

        self.folders = [
            {"id": "F1", "name": "Rustika", "owner": "rustika@gmail.com"},
            {"id": "F2", "name": "Stranger", "owner": "evil@gmail.com"},
        ]
        self.images = {"F1": [
            {"id": "a", "path": "a.png", "name": "a.png", "size": 100, "modtime": "1"},
            {"id": "b", "path": "sub/b.PNG", "name": "b.PNG", "size": 100, "modtime": "2"},
        ], "F2": [{"id": "x", "path": "x.png", "name": "x.png", "size": 100, "modtime": "1"}]}
        self.downloads = []
        self.payloads = {}

        def fake_download(folder_id, path, destination, kind="image"):
            self.downloads.append((folder_id, path))
            destination.write_bytes(self.payloads.get(path, png_bytes()))

        for target, value in (
            ("list_shared_folders", lambda: self.folders),
            ("list_folder_media", lambda folder_id: list(self.images[folder_id])),
            ("download_media", fake_download),
        ):
            patcher = mock.patch.object(drive_sync, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def sync(self, factory_id=1, **kw):
        with self.Session() as db:
            return drive_sync.sync_factory(db, factory_id, **kw)


class SyncFactoryTests(DriveSyncCase):
    def test_copies_new_images_into_library_and_records_url(self):
        report = self.sync()
        self.assertEqual((report.folders, report.copied, report.skipped, report.failed), (1, 2, 0, 0))
        with self.Session() as db:
            items = db.query(models.Media).filter(models.Media.factory_id == 1).all()
            self.assertEqual(len(items), 2)
            for item in items:
                self.assertEqual(item.media_type, "image")
                self.assertTrue(item.url.startswith("https://api.example.test/api/media/factories/1/"))
                self.assertIn("Rustika/", item.description)
            rows = db.query(models.DriveSyncedFile).all()
            self.assertEqual({r.status for r in rows}, {"copied"})
            self.assertEqual({r.media_id for r in rows}, {i.id for i in items})
        saved = list((Path(self.tmp.name) / "factory" / "1" / "library").iterdir())
        self.assertEqual(len(saved), 2)

    def test_only_folders_owned_by_factory_email(self):
        self.sync()
        self.assertEqual({folder_id for folder_id, _ in self.downloads}, {"F1"})      # مجلد الغريب لم يُلمس
        with self.Session() as db:
            self.assertEqual(db.query(models.Media).filter(models.Media.factory_id == 2).count(), 0)

    def test_second_run_copies_nothing_even_after_media_deleted(self):
        self.sync()
        with self.Session() as db:
            db.query(models.Media).delete()
            db.commit()
        report = self.sync()
        self.assertEqual((report.copied, len(self.downloads)), (0, 2))
        with self.Session() as db:
            self.assertEqual(db.query(models.Media).count(), 0)

    def test_new_file_in_folder_is_picked_up_later(self):
        self.sync()
        self.images["F1"].append({"id": "c", "path": "c.png", "name": "c.png", "size": 100, "modtime": "3"})
        self.assertEqual(self.sync().copied, 1)

    def test_invalid_image_is_skipped_and_not_retried(self):
        self.payloads["a.png"] = b"not an image"
        report = self.sync()
        self.assertEqual((report.copied, report.skipped), (1, 1))
        self.assertEqual(self.sync().skipped, 0)
        with self.Session() as db:
            row = db.query(models.DriveSyncedFile).filter_by(drive_file_id="a").one()
            self.assertEqual(row.status, "skipped")
            self.assertIn("ليس صورة", row.error)

    def test_oversized_file_is_skipped_without_download(self):
        self.images["F1"][0]["size"] = drive_sync.MAX_IMAGE_BYTES + 1
        report = self.sync()
        self.assertEqual(report.skipped, 1)

    def test_download_failure_is_retried_next_run(self):
        original = drive_sync.download_media

        def flaky(folder_id, path, destination, kind="image"):
            if path == "a.png":
                raise drive_sync.DriveSyncError("network")
            original(folder_id, path, destination, kind)

        with mock.patch.object(drive_sync, "download_media", flaky):
            report = self.sync()
        self.assertEqual((report.copied, report.failed), (1, 1))
        self.assertIn("a.png", report.errors[0])
        self.assertEqual(self.sync().copied, 1)          # الفاشل لم يُسجَّل فأُعيد

    def test_limit_leaves_remaining(self):
        report = self.sync(limit=1)
        self.assertEqual((report.copied, report.remaining), (1, 1))
        self.assertEqual(self.sync(limit=1).copied, 1)

    def test_factory_without_email_is_rejected(self):
        with self.Session() as db:
            db.query(models.FactoryContact).filter_by(factory_id=1).delete()
            db.commit()
        with self.assertRaises(errors.InvalidInputError):
            self.sync()

    def test_stale_copying_rows_are_recovered(self):
        from datetime import timedelta
        from app.core.time import utc_now
        with self.Session() as db:
            db.add(models.DriveSyncedFile(
                factory_id=1, drive_file_id="a", name="a.png", status="copying",
                updated_at=utc_now() - timedelta(hours=2),
            ))
            db.commit()
        self.assertEqual(self.sync().copied, 2)


class VideoTests(DriveSyncCase):
    def add_video(self, size=1000, name="v.mp4"):
        self.images["F1"] = [{"id": "v", "path": name, "name": name, "size": size, "modtime": "1", "kind": "video"}]
        self.payloads[name] = b"fake-video-bytes"

    def probe(self, returncode=0, stdout="3.5\n"):
        done = mock.Mock(returncode=returncode, stdout=stdout)
        return mock.patch.multiple(
            drive_sync, find_tool=mock.Mock(return_value="ffprobe"), subprocess=mock.Mock(run=mock.Mock(return_value=done)),
        )

    def test_video_is_copied_as_video_media(self):
        self.add_video()
        with self.probe():
            report = self.sync()
        self.assertEqual((report.copied, report.skipped), (1, 0))
        with self.Session() as db:
            item = db.query(models.Media).one()
            self.assertEqual(item.media_type, "video")
            self.assertTrue(item.url.endswith(".mp4"))

    def test_broken_video_is_skipped(self):
        self.add_video()
        with self.probe(returncode=1, stdout=""):
            report = self.sync()
        self.assertEqual((report.copied, report.skipped), (0, 1))
        with self.Session() as db:
            self.assertEqual(db.query(models.Media).count(), 0)

    def test_oversized_video_is_skipped(self):
        self.add_video(size=drive_sync.MAX_VIDEO_BYTES + 1)
        self.assertEqual(self.sync().skipped, 1)

    def test_without_ffprobe_video_is_still_copied(self):
        self.add_video()
        with mock.patch.object(drive_sync, "find_tool", return_value=None):
            self.assertEqual(self.sync().copied, 1)


class MediaKindTests(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(drive_sync.media_kind("a.JPG", "image/jpeg"), "image")
        self.assertEqual(drive_sync.media_kind("a.mov", "video/quicktime"), "video")
        self.assertEqual(drive_sync.media_kind("a.mp4", "image/png"), None)      # امتداد ونوع متعارضان
        self.assertIsNone(drive_sync.media_kind("a.pdf", "application/pdf"))
        self.assertIsNone(drive_sync.media_kind("a.gif", "image/gif"))


class SyncAllTests(DriveSyncCase):
    def test_syncs_every_factory_with_email(self):
        self.folders.append({"id": "F3", "name": "Other", "owner": "other@gmail.com"})
        self.images["F3"] = [{"id": "o", "path": "o.png", "name": "o.png", "size": 10, "modtime": "1"}]
        reports = drive_sync.sync_all(self.Session)
        self.assertEqual({fid: r.copied for fid, r in reports.items()}, {1: 2, 2: 1})

    def test_no_emails_no_rclone_call(self):
        with self.Session() as db:
            db.query(models.FactoryContact).delete()
            db.commit()
        with mock.patch.object(drive_sync, "list_shared_folders", side_effect=AssertionError):
            self.assertEqual(drive_sync.sync_all(self.Session), {})


if __name__ == "__main__":
    unittest.main()
