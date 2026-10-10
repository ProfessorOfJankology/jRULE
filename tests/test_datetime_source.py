"""Built-in DateTime source, timezone, and unified-cycle regression tests."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import db, state, engine
from app.datetime_source import properties_at


class DateTimePropertiesTests(unittest.TestCase):
    def test_weekend_and_calendar_fields(self):
        instant=datetime(2026,10,9,23,25,tzinfo=timezone.utc)
        value=properties_at(instant)
        self.assertEqual(value["date"],"2026-10-10")
        self.assertEqual(value["time"],"10:25:00")
        self.assertEqual(value["weekday_name"],"Saturday")
        self.assertEqual(value["day_of_week"],6)
        self.assertTrue(value["is_weekend"])
        self.assertFalse(value["is_weekday"])
        self.assertEqual(value["week_number"],41)
        self.assertEqual(value["quarter"],4)
        self.assertEqual(value["utc_offset"],"+1100")

    def test_dst_standard_time_and_weekday(self):
        instant=datetime(2026,6,1,0,0,tzinfo=timezone.utc)
        value=properties_at(instant)
        self.assertEqual(value["hour"],10)
        self.assertEqual(value["utc_offset"],"+1000")
        self.assertEqual(value["day_of_week"],1)
        self.assertTrue(value["is_weekday"])
        self.assertFalse(value["is_weekend"])

    def test_aware_timestamp_required(self):
        with self.assertRaises(ValueError):
            properties_at(datetime(2026,10,10,10,25))


class DateTimeCycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.original=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/"test.db"
        await db.init_db()

    async def asyncTearDown(self):
        db.DB_PATH=self.original
        self.tmp.cleanup()

    async def test_datetime_advances_last_at_cycle_boundary(self):
        await state.update_properties("DateTime",{"minute":1})
        with patch.object(engine.http_services,"poll_all_sources",new=AsyncMock(return_value=[])):
            with patch.object(engine.datetime_source,"properties_at",return_value={"minute":2}):
                await engine.evaluate_once()
            first=await state.pool()
            self.assertEqual(first["DateTime"]["properties"]["minute"]["last"],1)
            self.assertEqual(first["DateTime"]["properties"]["minute"]["current"],2)
            with patch.object(engine.datetime_source,"properties_at",return_value={"minute":2}):
                await engine.evaluate_once()
        second=await state.pool()
        self.assertEqual(second["DateTime"]["properties"]["minute"]["last"],2)
        self.assertEqual(second["DateTime"]["properties"]["minute"]["current"],2)


if __name__=="__main__":
    unittest.main()
