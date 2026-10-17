import unittest

from scripts.seed_posts import TAG, build_seed_posts

IMG = "https://hadjretbladi.com/B001/1.jpg"


class SeedBuilderTests(unittest.TestCase):
    def test_default_has_no_video_and_all_tagged(self):
        posts = build_seed_posts(IMG, None, False, "https://x/ZZ999/1.jpg")
        self.assertEqual(len(posts), 6)
        self.assertTrue(all(p["description"].startswith(f"{TAG}{p['n']}]") for p in posts))
        self.assertNotIn("reel", {p["media_type"] for p in posts})

    def test_video_and_failures(self):
        posts = build_seed_posts(IMG, "https://x/v.mp4", True, "https://x/ZZ999/1.jpg")
        self.assertEqual(len(posts), 12)
        self.assertEqual(sum(p["media_type"] == "reel" for p in posts), 3)
        self.assertTrue(any(p["media_urls"] == ["https://hadjretbladi.com/B001/1.webp"] for p in posts))
        self.assertTrue(any(len(p["description"]) > 2200 and p["platforms"] == "instagram" for p in posts))

    def test_text_posts_have_no_media(self):
        for p in build_seed_posts(IMG, None, False, "x"):
            if p["media_type"] == "text":
                self.assertEqual(p["media_urls"], [])


if __name__ == "__main__":
    unittest.main()
