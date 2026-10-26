import tempfile
import unittest
from datetime import date
from pathlib import Path
from ledger import Ledger, month_bounds, parse_date, week_bounds


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Ledger(Path(self.temp.name) / 'test.sqlite3')

    def tearDown(self):
        self.temp.cleanup()

    def test_independent_success_and_distinct_dates(self):
        self.db.save(1, 2, '2026-10-26', '식사', 12000, True)
        self.db.save(1, 2, '2026-10-26', '커피', 3000, True)
        self.db.save(1, 2, '2026-10-27', '기록', 0, False)
        self.assertEqual(self.db.summary(1, 2, '2026-10-01', '2026-10-31'), (15000, 1))

    def test_edit_delete_and_scope(self):
        eid = self.db.save(1, 2, '2026-10-26', '기록', 0, True)
        self.assertIsNone(self.db.get(1, 3, eid))
        self.assertIsNone(self.db.get(2, 2, eid))
        with self.assertRaises(ValueError):
            self.db.delete(1, 3, eid, 1)
        self.db.save(1, 2, '2026-10-27', '변경', 500, False, eid, 1)
        with self.assertRaises(ValueError):
            self.db.save(1, 2, '2026-10-26', '오래된 수정', 0, True, eid, 1)
        self.assertEqual(self.db.summary(1, 2, '2026-10-01', '2026-10-31'), (500, 0))
        self.db.delete(1, 2, eid, 2)
        self.assertEqual(self.db.summary(1, 2, '2026-10-01', '2026-10-31'), (0, 0))

    def test_week_month_edges(self):
        self.assertEqual(week_bounds(date(2027, 1, 3)), (date(2026, 12, 28), date(2027, 1, 3)))
        self.assertEqual(month_bounds(date(2028, 2, 15)), (date(2028, 2, 1), date(2028, 2, 29)))
        for day, cost in [('2026-10-25', 1), ('2026-10-26', 2), ('2026-11-01', 4), ('2026-11-02', 8)]:
            self.db.save(1, 2, day, '기록', cost, True)
        self.assertEqual(self.db.summary(1, 2, *week_bounds(date(2026, 10, 26))), (6, 2))
        self.assertEqual(self.db.summary(1, 2, *month_bounds(date(2026, 10, 26))), (3, 2))

    def test_delete_last_success_and_date_move(self):
        a = self.db.save(1, 2, '2026-10-26', 'A', 1, True)
        b = self.db.save(1, 2, '2026-10-26', 'B', 2, True)
        self.db.delete(1, 2, a, 1)
        self.assertEqual(self.db.summary(1, 2, '2026-10-01', '2026-10-31'), (2, 1))
        self.db.save(1, 2, '2026-11-01', 'B', 2, True, b, 1)
        self.assertEqual(self.db.summary(1, 2, '2026-10-01', '2026-10-31'), (0, 0))

    def test_validation_persistence_pagination(self):
        for value in ['2026-2-1', '2026-02-30', 'hello']:
            with self.assertRaises(ValueError):
                parse_date(value)
        for amount in [-1, 1.5, 10**12+1]:
            with self.assertRaises(ValueError):
                self.db.save(1, 2, '2026-10-26', '기록', amount, False)
        for _ in range(12):
            self.db.save(1, 2, '2026-10-26', '기록', 0, False)
        again = Ledger(self.db.path)
        rows, count = again.rows(1, 2, '2026-10-01', '2026-10-31', 2)
        self.assertEqual((len(rows), count), (2, 12))


if __name__ == '__main__':
    unittest.main()
