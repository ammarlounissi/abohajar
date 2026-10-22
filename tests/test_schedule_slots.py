"""توزيع المنشورات على ساعات يومية ثابتة (منطق خالص، بلا قاعدة بيانات)."""
import unittest
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.services.schedule_slots import generate_slots, parse_times

ALGIERS = ZoneInfo("Africa/Algiers")      # UTC+1 طوال السنة
T = [time(8), time(14), time(18)]


def utc(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute)


class ParseTimesTests(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_times([18, 8, 14]), T)
        self.assertEqual(parse_times("8,14, 18"), T)
        self.assertEqual(parse_times(["08:30", "8", 8]), [time(8), time(8, 30)])

    def test_invalid(self):
        for bad in ([], "", [24], [-1], ["8:60"], ["x"], [True], [8.5], 8, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_times(bad)


class GenerateSlotsTests(unittest.TestCase):
    def test_six_posts_take_two_days_stored_as_utc(self):
        # الآن 22 أكتوبر 06:00 بتوقيت الجزائر (05:00 UTC): مواعيد اليوم كلها قادمة
        slots = generate_slots(6, T, now_utc=utc(22, 5), tz=ALGIERS)
        self.assertEqual(slots, [utc(22, 7), utc(22, 13), utc(22, 17), utc(23, 7), utc(23, 13), utc(23, 17)])

    def test_past_slots_today_are_skipped(self):
        # الآن 15:00 محلياً (14:00 UTC): بقي 18:00 فقط من اليوم
        slots = generate_slots(3, T, now_utc=utc(22, 14), tz=ALGIERS)
        self.assertEqual(slots, [utc(22, 17), utc(23, 7), utc(23, 13)])

    def test_slot_equal_to_now_is_not_used(self):
        self.assertEqual(generate_slots(1, T, now_utc=utc(22, 7), tz=ALGIERS), [utc(22, 13)])

    def test_start_date_in_future(self):
        slots = generate_slots(4, T, now_utc=utc(22, 5), tz=ALGIERS, start_date=date(2026, 10, 25))
        self.assertEqual(slots, [utc(25, 7), utc(25, 13), utc(25, 17), utc(26, 7)])

    def test_start_date_in_past_still_starts_from_now(self):
        slots = generate_slots(1, T, now_utc=utc(22, 14), tz=ALGIERS, start_date=date(2026, 10, 1))
        self.assertEqual(slots, [utc(22, 17)])

    def test_occupied_slots_are_skipped(self):
        slots = generate_slots(3, T, now_utc=utc(22, 5), tz=ALGIERS, occupied={utc(22, 7), utc(22, 17)})
        self.assertEqual(slots, [utc(22, 13), utc(23, 7), utc(23, 13)])

    def test_midnight_slot_belongs_to_previous_utc_day(self):
        # 00:30 بتوقيت الجزائر = 23:30 UTC من اليوم السابق
        slots = generate_slots(1, [time(0, 30)], now_utc=utc(22, 5), tz=ALGIERS)
        self.assertEqual(slots, [utc(22, 23, 30)])

    def test_zero_count(self):
        self.assertEqual(generate_slots(0, T, now_utc=utc(22, 5), tz=ALGIERS), [])


if __name__ == "__main__":
    unittest.main()
