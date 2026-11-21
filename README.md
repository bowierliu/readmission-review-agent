# Readmission Review Agent

An independent portfolio project for investigating 30-day heart failure readmission signals. All data is synthetic, including the two fictional hospitals, encounters, counts, and refresh notes. Rates are observed and unadjusted.

## Try the demo

[Open the interactive replay](https://bowierliu.github.io/readmission-review-agent/). Select **Play saved trace**, then **Inspect 75 unmatched stays**. Compare Cases A and B to see how two dashboards reach 16% with different implications for the analyst. You can also download the repository, extract it, and open `docs/index.html` directly. Both replay paths use bundled recordings and require no account, API key, installation, or backend.

| Mode | Where | What you see |
| --- | --- | --- |
| Saved trace | Public page, downloaded HTML or local app | The original SQL evidence, timestamp and review draft from a deterministic local policy. Browser-only review marks last for the current tab session. |
| Checklist (fresh) | Local app, offline | Python runs the fixed sequence against the bundled SQLite fixture. The analyst can inspect the SQL, statistics and unmatched synthetic stays. |
| Live agent | Local app with an enabled budget and provider key | A real model chooses successive checks from aggregate evidence. Python validates each choice and owns the follow-up gate, statistics and draft. |

## Local live agent

1. Download and extract the [project ZIP](https://github.com/bowierliu/readmission-review-agent/archive/refs/heads/main.zip), or clone this repository. Install Python 3.10 or later. All runtime dependencies are in the Python standard library.
2. For live investigations, configure the shared spending ledger once and store your OpenAI key in macOS Keychain. Use service **Healthcare Portfolio** and account **openai**. Other operating systems can supply `PORTFOLIO_OPENAI_KEY` to the server from a private environment. Enable the ledger when ready. [Key setup, spending limits and disable switch](docs/LIVE_BUDGET.md)
3. On macOS, double-click **Launch Readmission.command**. On other platforms, run `python3 launcher.py` (or `py launcher.py` on Windows). The launcher opens your browser, reuses a running instance, and starts a background loopback server that stops after 45 minutes of inactivity. You can close the launcher window. If the default port is occupied, run `python3 launcher.py --port 8766`.
4. Select **Case A** or **Case B**, choose **Live agent**, and select **Run investigation**. Case C stops at the follow-up gate before any model call. Only aggregate synthetic evidence goes to OpenAI. The browser never asks for a key. **Mark reviewed** records the analyst's acknowledgment locally. Any handoff to a real team belongs to the analyst.

To run a local checklist, skip the key and ledger setup, launch the app, and leave **Checklist (fresh)** selected. For foreground debugging run `python3 app.py` and open [the local workspace](http://127.0.0.1:8765).

The public demo serves static files. A recruiter who wants to exercise the actual model can use their own key in their local credential store. A hosted owner-funded model would also need an authenticated application server and one durable, shared ledger. The current service accepts only loopback requests and uses one trusted local identity across the portfolio. The model flow is tested with simulated provider responses.

## Local checklist walkthrough

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

## Pilot against a fixed checklist

Give the agent and a fixed ordered checklist the same metric, tools, and follow-up gate. Use a blinded set of analyst-adjudicated cases that includes true increases, missing batches, immature cohorts, and cases with several plausible explanations. Have reviewers judge whether each draft routes to the correct owner, cites the supporting counts, and preserves open questions. Track missed holds, unnecessary checks, time to a reviewable draft, and analyst corrections. Review disagreements to see whether adaptive tool ordering saves work and whether any errors justify keeping a check mandatory.

## Rebuild and test

```bash
python3 data/build_fixtures.py
python3 record_replays.py
python3 -m unittest discover -s tests -v
```

The browser recordings derive from the archived synthetic traces used by the local replay. Maintainers can run `scripts/publish-pages.sh` after publishing an audited main commit. It copies the four allowlisted HTML, CSS, JavaScript and recording files plus `.nojekyll` to the root of `gh-pages`. Select that branch and root directory in GitHub Pages settings.

Each live SQL diagnostic counts toward the tool limit. SQLite checks the live deadline during long reads and immediately returns an error on a locked fixture. A withheld February cohort stops before any model request.

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
