# Synthetic fixture expectations

All encounters, hospital names, and refresh events are fabricated. The January and February 2026 cohorts each contain 100 eligible heart failure discharges per hospital in the encounter source. The latest February discharge is February 28, so the current cohort needs coverage through March 30, 2026.

| Case | January source and dashboard | February dashboard | February source | Expected finding |
| --- | --- | --- | --- | --- |
| A | 20/200 (10%) | 20/125 (16%) | 20/200 (10%) | Mesa is missing 75 eligible February discharges from the dashboard index. A late Mesa refresh is documented. |
| B | 20/200 (10%) | 32/200 (16%) | 32/200 (16%) | The source confirms the increase. Mesa accounts for 10 of the 12 additional returns. |
| C | 20/200 (10%) | 12/200 observed | 12/200 observed | Both hospitals have source coverage through March 12. The 30-day February rate is still unknown. |

| Case | February Harbor source | February Mesa source | Hospital source coverage |
| --- | --- | --- | --- |
| A | 10/100 | 10/100 | April 1 at both hospitals |
| B | 12/100 | 20/100 | April 1 at both hospitals |
| C | 6/100 observed | 6/100 observed | March 12 at both hospitals |

In Case A, the dashboard keeps all 10 Mesa index discharges that have a qualifying return, and shows 10/25 for Mesa. In Cases B and C, every eligible source discharge appears in the dashboard index. January has 10/100 at each hospital in every case, with complete 30-day follow-up.

## Test-only signal scenarios

These scenarios use fixed per-scenario seeds in `data/build_fixtures.py`. They are available to direct fixture tests. The demo case switcher lists A, B, and C.

| Fixture | Expected boundary |
| --- | --- |
| `small` | Two returns in 12 stays overall in January and February, split evenly across the hospitals. Both watermarks are April 1. |
| `zero` | Mesa has 10 returns per 100 stays in January and zero per 100 in February. Both watermarks are April 1. |
| `incomplete` | Harbor has follow-up through April 1. Mesa has follow-up through March 12, before the February requirement of March 30. |
