import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from data.build_fixtures import SCHEMA, populate
from investigation import CURRENT_MONTH, FIXTURES, PRIOR_MONTH, ROOT


class FixtureBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.databases = [Path(directory.name) / name for name in ('first.sqlite3', 'second.sqlite3')]
        for path in cls.databases:
            with closing(sqlite3.connect(path)) as conn:
                conn.executescript(SCHEMA)
                with conn:
                    populate(conn)
        cls.metric = (ROOT / 'sql' / 'metric.sql').read_text()

    def count_rows(self, db, scenario):
        with closing(sqlite3.connect(db)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(self.metric, {'scenario_id': scenario,
                                              'prior_month': PRIOR_MONTH,
                                              'current_month': CURRENT_MONTH})
            return [dict(row) for row in rows]

    def test_public_case_count_rows_match_baseline(self):
        # Each arm lists source returns/stays, then dashboard returns/stays.
        january = (10, 100, 10, 100)
        expected = {
            'a': (january, january, (10, 100, 10, 100), (10, 100, 10, 25)),
            'b': (january, january, (12, 100, 12, 100), (20, 100, 20, 100)),
            'c': (january, january, (6, 100, 6, 100), (6, 100, 6, 100)),
        }
        scopes = [(month, hospital) for month in (PRIOR_MONTH, CURRENT_MONTH)
                  for hospital in ('Harbor Medical Center', 'Mesa Regional Hospital')]
        for db in (self.databases[0], FIXTURES):
            for scenario, arms in expected.items():
                with self.subTest(db=db.name, scenario=scenario):
                    self.assertEqual(self.count_rows(db, scenario), [
                        {'cohort_month': month, 'hospital_name': hospital,
                         'source_readmissions': source_k, 'source_discharges': source_n,
                         'source_rate': source_k / source_n,
                         'dashboard_readmissions': dashboard_k, 'dashboard_discharges': dashboard_n,
                         'dashboard_rate': dashboard_k / dashboard_n}
                        for (month, hospital), (source_k, source_n, dashboard_k, dashboard_n)
                        in zip(scopes, arms)
                    ])

    def test_seeded_builder_reproduces_identical_database(self):
        first, second = self.databases
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_signal_fixtures_cover_small_zero_and_incomplete_arms(self):
        for db in (self.databases[0], FIXTURES):
            with self.subTest(db=db.name):
                small = [row for row in self.count_rows(db, 'small')
                         if row['cohort_month'] == CURRENT_MONTH]
                self.assertEqual(sum(row['source_readmissions'] for row in small), 2)
                self.assertEqual(sum(row['source_discharges'] for row in small), 12)

                mesa = [row for row in self.count_rows(db, 'zero')
                        if row['hospital_name'] == 'Mesa Regional Hospital']
                self.assertEqual([(row['source_readmissions'], row['source_discharges'])
                                  for row in mesa], [(10, 100), (0, 100)])

                with closing(sqlite3.connect(db)) as conn:
                    coverage = conn.execute("""
                        SELECT hospital_id, observed_through
                        FROM source_coverage WHERE scenario_id = ? ORDER BY hospital_id
                    """, ('incomplete',)).fetchall()
                self.assertEqual(coverage, [('harbor', '2026-04-01'), ('mesa', '2026-03-12')])
