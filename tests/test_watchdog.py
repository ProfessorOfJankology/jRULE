"""Standalone watchdog tests do not require the application dependencies."""
import importlib.util
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'jrule-watchdog.py'
spec = importlib.util.spec_from_file_location('jrule_watchdog', SCRIPT)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class WatchdogTests(unittest.TestCase):
    def test_fresh_and_stale_heartbeat(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            now = datetime.now(timezone.utc)
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE object_properties(object_name TEXT,property_name TEXT,last_polled TEXT)')
                conn.execute('CREATE TABLE settings(key TEXT,value_json TEXT)')
                conn.execute('INSERT INTO settings VALUES (?,?)', ('rule_interval_seconds', json.dumps(10)))
                conn.execute('INSERT INTO object_properties VALUES (?,?,?)',
                             ('DateTime', 'datetime', (now - timedelta(seconds=15)).isoformat()))
            age, threshold = watchdog.inspect(path, now)
            self.assertAlmostEqual(age, 15)
            self.assertEqual(threshold, 180)
            with sqlite3.connect(path) as conn:
                conn.execute('UPDATE object_properties SET last_polled=?',
                             ((now - timedelta(seconds=200)).isoformat(),))
                conn.execute('UPDATE settings SET value_json=?', (json.dumps(90),))
            age, threshold = watchdog.inspect(path, now)
            self.assertAlmostEqual(age, 200)
            self.assertEqual(threshold, 270)

    def test_missing_heartbeat_is_explicit(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.db'
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE object_properties(object_name TEXT,property_name TEXT,last_polled TEXT)')
                conn.execute('CREATE TABLE settings(key TEXT,value_json TEXT)')
            self.assertEqual(watchdog.inspect(path), (None, 180))


if __name__ == '__main__':
    unittest.main()
