# Readmission Review Agent

An independent portfolio project for investigating 30-day heart failure readmission signals. All data is synthetic, including the two fictional hospitals, encounters, counts, and refresh notes. Rates are observed and unadjusted.

## Launch locally

From this project folder, use Python 3.10 or later and a browser. The app uses Python’s standard library and SQLite. The generated database and saved traces are included.

```bash
python3 app.py
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The page opens on **Case A** with **Checklist (fresh)** selected. Use `python3 app.py --port 8766` if port 8765 is in use.

## Demo script

| Time | On screen | Talk track |
| --- | --- | --- |
| 0–30 s | Open **Case A**. Select **Run investigation**, then **Inspect 75 unmatched stays** and activate the **75** cell. Open **Show SQL** if asked how it was computed. | “The checklist just ran against SQLite. The dashboard says 16%, but Mesa's index drops 75 of 90 stays without a readmission and keeps all 10 stays with one. Hold the rate and send it to the data team.” |
| 30–48 s | Select **Compare A and B**. Point to the identical headlines and the two verdicts. | “Same 16%. Different answer. With reconciled counts in B, the next step is quality review.” |
| 48–65 s | Select **Open Case B investigation** and point to the signal-check row. | “Observed readmissions rose from 20 to 32 out of 200 stays. The overall change is +6.0 percentage points, with a 95% interval of −0.6 to +12.7 and Fisher p of 0.10. Mesa has the larger observed change. Quality reviews the finding with that uncertainty in view.” |
| 65–80 s | Select **Case C** and run the checklist. | “Wait. The March 12 data completes follow-up only for discharges through February 10. February needs data through March 30.” |
| 80–90 s | Point to the mode control and the checks trace. | “The model only selects allowlisted checks. SQL calculates the counts and Python owns the gates, statistics, and draft. A pilot would measure missed holds, routing agreement, and time to a reviewable draft against the fixed checklist.” |

## Measure definition

The denominator is the number of inpatient index discharges with a principal heart failure diagnosis where the patient left the hospital alive. Assign each discharge to its discharge calendar month and discharging hospital. The numerator counts those discharges with at least one unplanned inpatient return for any diagnosis at either fictional hospital, on days 1 through 30 after discharge. Count each index discharge once, even if it has several qualifying returns. The rate is the numerator divided by the denominator. [Metric SQL](sql/metric.sql)

The demo uses calendar dates, a single synthetic patient identifier across both hospitals, a simplified heart failure code, and a recorded planned-return flag. The simulated return feed covers both hospitals. Patient risk adjustment, transfer chaining, and outside-network returns fall outside this measure. The dashboard uses its own index-discharge extract and shares the return feed with the source calculation, so reconciliation tests the completeness of the reporting extract and the resulting counts.

A cohort is reviewable when both hospital feeds are observed through month-end plus 30 days. For February 2026 that date is March 30. The dashboard preview can display provisional observations before the gate passes.

## Who does what

- **SQL** applies one measure definition to source and dashboard cohorts and their unmatched stays. The server binds case and month parameters and opens the database read-only.
- **Python** enforces follow-up and reconciliation gates, computes the signal check, and drafts the handoff from the checks that ran. On reconciled data, the signal check reports Wilson intervals for each rate, the Newcombe method 10 interval for their difference, and the probability-ordered two-sided Fisher exact p-value. [Statistical references and independent calculation](docs/STATS_REFERENCE.md)
- **The live model** sees aggregate synthetic evidence and selects an available read-only check or stops. Signal check becomes available after reconciliation passes.
- **The analyst** inspects the evidence, verifies the next action and records **Mark reviewed** in the local review store at `data/review_state.sqlite3`. The analyst owns any handoff to a team.

## Investigation modes

**Checklist (fresh)** runs the fixed sequence against the current SQLite fixture. **Run fresh** starts a new run. Each evidence table shows its query source and timestamp. **Compare A and B** runs two fresh checklists and opens either full result.

**Saved trace** streams a prior investigation using its original counts and recording timestamp. The included recordings used an evidence-based local policy. Both local modes work without an API key or network.

**Live agent** has only been tested with simulated responses. To enable it, set `OPENAI_API_KEY` in the server process environment and restart the app. The default model is `gpt-4.1-mini`. Set `OPENAI_MODEL` before launch to configure a model that supports Chat Completions function calls. The provider receives aggregate synthetic evidence.

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
printf '\n'
python3 app.py
```

## Pilot against a fixed checklist

Give the agent and a fixed ordered checklist the same metric, tools, and follow-up gate. Use a blinded set of analyst-adjudicated cases that includes true increases, missing batches, immature cohorts, and cases with several plausible explanations. Have reviewers judge whether each draft routes to the correct owner, cites the supporting counts, and preserves open questions. Track missed holds, unnecessary checks, time to a reviewable draft, and analyst corrections. Review disagreements to see whether adaptive tool ordering saves work and whether any errors justify keeping a check mandatory.

## Rebuild and test

```bash
python3 data/build_fixtures.py
python3 record_replays.py
python3 -m unittest discover -s tests -v
```

The scenario answer key lives with the [fixture expectations](tests/fixture_expectations.md). It is separate from the live model inputs.

## Screenshots

[Default case](docs/screens/change-3-desktop-a-default.png) · [Case A drill-down](docs/screens/change-3-desktop-a-gap.png) · [Desktop comparison](docs/screens/change-3-desktop-compare.png) · [Narrow comparison](docs/screens/change-3-narrow-compare.png)

## Visual contrast

| Foreground | On white `#ffffff` | On canvas `#fafaf9` |
| --- | ---: | ---: |
| Ink `#1c1917` | 17.49:1 | 16.74:1 |
| Warm gray `#57534e` | 7.63:1 | 7.30:1 |
| Accent blue `#234daf` | 7.63:1 | 7.31:1 |

The pale status backgrounds yield 6.99:1 for neutral, 6.92:1 for blue, 6.87:1 for amber, 7.50:1 for green, and 7.13:1 for error text. The `#e7e5e4` hairlines are decorative. Outlined controls use `#8c8480` at 3.67:1 on white.
