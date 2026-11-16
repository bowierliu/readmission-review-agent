"""Rebuild the synthetic, deterministic readmission demo database."""

import calendar
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory


HOSPITALS = (('harbor', 'Harbor Medical Center'), ('mesa', 'Mesa Regional Hospital'))
COMPLETE = date(2026, 4, 1)
INCOMPLETE = date(2026, 3, 12)

# Per case: stays per hospital/month, January returns, February returns, watermarks.
# Hospital pairs are ordered Harbor, Mesa. The original three cases retain their
# original encounter placement. Test-only cases use a per-scenario seed for return
# placement and timing so every rebuild yields the same records.
COHORTS = {
    'a': (100, (10, 10), (10, 10), (COMPLETE, COMPLETE)),
    'b': (100, (10, 10), (12, 20), (COMPLETE, COMPLETE)),
    'c': (100, (10, 10), (6, 6), (INCOMPLETE, INCOMPLETE)),
    'small': (6, (1, 1), (1, 1), (COMPLETE, COMPLETE)),    # 2 returns / 12 stays overall
    'zero': (100, (10, 10), (15, 0), (COMPLETE, COMPLETE)),  # Mesa February has zero returns
    'incomplete': (100, (10, 10), (12, 6), (COMPLETE, INCOMPLETE)),  # Mesa follow-up is open
}

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE encounters (
    scenario_id TEXT NOT NULL,
    encounter_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    hospital_id TEXT NOT NULL,
    hospital_name TEXT NOT NULL,
    admit_date TEXT NOT NULL,
    discharge_date TEXT NOT NULL,
    principal_diagnosis TEXT NOT NULL,
    setting TEXT NOT NULL,
    planned INTEGER NOT NULL,
    discharged_alive INTEGER NOT NULL,
    PRIMARY KEY (scenario_id, encounter_id)
);

CREATE TABLE reporting_index (
    scenario_id TEXT NOT NULL,
    encounter_id TEXT NOT NULL,
    PRIMARY KEY (scenario_id, encounter_id),
    FOREIGN KEY (scenario_id, encounter_id) REFERENCES encounters (scenario_id, encounter_id)
);

CREATE TABLE source_coverage (
    scenario_id TEXT NOT NULL,
    hospital_id TEXT NOT NULL,
    hospital_name TEXT NOT NULL,
    observed_through TEXT NOT NULL,
    PRIMARY KEY (scenario_id, hospital_id)
);

CREATE TABLE refresh_log (
    scenario_id TEXT NOT NULL,
    hospital_id TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    note TEXT NOT NULL,
    PRIMARY KEY (scenario_id, hospital_id, batch_id)
);
"""


def populate(connection):
    for scenario, (stays, january_returns, february_returns, watermarks) in COHORTS.items():
        rng = None if scenario in ('a', 'b', 'c') else Random(f'readmission-fixtures-2026-{scenario}')
        for hospital, january_count, february_count, observed_through in zip(
            HOSPITALS, january_returns, february_returns, watermarks,
        ):
            hospital_id, hospital_name = hospital
            connection.execute(
                'INSERT INTO source_coverage VALUES (?, ?, ?, ?)',
                (scenario, hospital_id, hospital_name, observed_through.isoformat()),
            )
            late_refresh = scenario == 'a' and hospital_id == 'mesa'
            note = 'Routine synthetic source refresh completed.'
            if late_refresh:
                note = 'Synthetic source refresh completed late. Reporting index reconciliation is pending.'
            connection.execute(
                'INSERT INTO refresh_log VALUES (?, ?, ?, ?, ?)',
                (scenario, hospital_id, f'{observed_through.isoformat()}T18:00:00Z',
                 f'{hospital_id}-{observed_through.isoformat()}', note),
            )
            for month, return_count in ((1, january_count), (2, february_count)):
                populate_cohort(connection, scenario, hospital, month, stays, return_count,
                                observed_through, rng)


def populate_cohort(connection, scenario, hospital, month, stays, return_count, observed_through, rng):
    hospital_id, hospital_name = hospital
    positions = range(1, stays + 1)
    returns = (range(1, return_count + 1) if rng is None
               else set(rng.sample(positions, return_count)))
    encounter_insert = 'INSERT INTO encounters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    days_in_month = calendar.monthrange(2026, month)[1]
    for position in positions:
        patient_id = f'{hospital_id}-2026-{month:02d}-{position:03d}'
        encounter_id = f'{patient_id}-index'
        discharge = date(2026, month, 1 + (position * 5) % days_in_month)
        admit = discharge - timedelta(days=3)
        connection.execute(
            encounter_insert,
            (scenario, encounter_id, patient_id, hospital_id, hospital_name,
             admit.isoformat(), discharge.isoformat(), 'HF', 'inpatient', 0, 1),
        )

        missing_from_reporting = scenario == 'a' and month == 2 and hospital_id == 'mesa' and position > 25
        if not missing_from_reporting:
            connection.execute('INSERT INTO reporting_index VALUES (?, ?)', (scenario, encounter_id))

        if position in returns:
            if rng is None:
                days_until_return = 30 if position == return_count else 3 + position
            else:
                max_delay = min(30, (observed_through - discharge).days)
                days_until_return = rng.randint(1, max_delay)
            return_admit = min(discharge + timedelta(days=days_until_return), observed_through)
            return_discharge = min(return_admit + timedelta(days=2), observed_through)
            connection.execute(
                encounter_insert,
                (scenario, f'{patient_id}-return', patient_id, hospital_id, hospital_name,
                 return_admit.isoformat(), return_discharge.isoformat(), 'PNEUMONIA', 'inpatient', 0, 1),
            )


def main():
    destination = Path(__file__).with_name('fixtures.sqlite3')
    with TemporaryDirectory(dir=destination.parent) as temp_dir:
        database = Path(temp_dir) / destination.name
        with closing(sqlite3.connect(database)) as connection:
            connection.executescript(SCHEMA)
            with connection:
                populate(connection)
        database.replace(destination)
    print(f'Wrote {destination}')


if __name__ == '__main__':
    main()
