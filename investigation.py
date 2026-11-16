"""Read-only SQL evidence and the bounded investigation flow."""

import sqlite3
from calendar import monthrange
from contextlib import closing, contextmanager
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path

from signal_stats import fisher_exact_two_sided, newcombe_diff_interval, wilson_interval

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "data" / "fixtures.sqlite3"
CASE_IDS = ("a", "b", "c")
FIXTURE_CASE_IDS = (*CASE_IDS, "small", "zero", "incomplete")
PRIOR_MONTH = "2026-01"
CURRENT_MONTH = "2026-02"
MEASURE = ROOT / "sql" / "metric.sql"
MONTHS = {"prior_month": PRIOR_MONTH, "current_month": CURRENT_MONTH}


COVERAGE_SQL = """
SELECT hospitals.hospital_name, coverage.observed_through
FROM (
    SELECT DISTINCT hospital_id, hospital_name
    FROM encounters
    WHERE scenario_id = :scenario_id
) AS hospitals
LEFT JOIN source_coverage AS coverage
  ON coverage.scenario_id = :scenario_id
 AND coverage.hospital_id = hospitals.hospital_id
ORDER BY hospitals.hospital_name
"""
REFRESH_SQL = """
SELECT coverage.hospital_name, log.occurred_at, log.batch_id, log.note
FROM refresh_log AS log
JOIN source_coverage AS coverage
  ON coverage.scenario_id = log.scenario_id AND coverage.hospital_id = log.hospital_id
WHERE log.scenario_id = :scenario_id
ORDER BY log.occurred_at, coverage.hospital_name, log.batch_id
"""

TOOL_DESCRIPTIONS = {
    "hospital_counts": "Compare the dashboard numerator, denominator, and rate by hospital and month.",
    "reconcile_sources": "Check dashboard discharge and readmission counts against source encounter counts by hospital and month.",
    "refresh_notes": "Inspect reporting refresh notes for the latest cohort's reporting cycle.",
    "signal_check": "Compare unadjusted rates and uncertainty after source reconciliation passes.",
}


def step(step_id, title, summary, columns, rows):
    return {"id": step_id, "title": title, "summary": summary, "columns": columns, "rows": rows}


def percent(rate):
    return "—" if rate is None else f"{rate:.1%}"


def has_gaps(reconciliation):
    return any(row[-2] or row[-1] for row in reconciliation["rows"])


def signal_for_scope(name, prior, current):
    arms = (prior, current)
    counts = [arm[key] for arm in arms for key in ("readmissions", "discharges")]
    low_events = any(arm["readmissions"] < 5 or arm["discharges"] < 30 for arm in arms)
    rates_available = all(arm["discharges"] for arm in arms)
    change_pp = (current["rate"] - prior["rate"]) * 100 if rates_available else None
    interval = [limit * 100 for limit in newcombe_diff_interval(*counts)] if rates_available else None
    fisher_p = fisher_exact_two_sided(*counts) if rates_available else None
    if low_events:
        label = "Too few events to interpret"
    elif interval[0] <= 0 <= interval[1]:
        label = "Consistent with chance variation (unadjusted)"
    else:
        label = "Difference is outside chance variation (unadjusted)"

    def with_wilson(arm):
        k, n = arm["readmissions"], arm["discharges"]
        return {**arm, "wilson": wilson_interval(k, n) if n else None}

    return {"scope": name, "prior": with_wilson(prior), "current": with_wilson(current),
            "change_pp": change_pp, "interval_pp": interval, "fisher_p": fisher_p,
            "low_events": low_events, "signal": label}


def signal_row(scope):
    def rate_with_interval(arm):
        limits = arm["wilson"]
        if limits is None:
            return "—"
        return f"{percent(arm['rate'])} ({limits[0]:.2%} to {limits[1]:.2%})"

    prior, current = scope["prior"], scope["current"]
    interval = scope["interval_pp"]
    return [scope["scope"],
            f"{prior['readmissions']}/{prior['discharges']}", rate_with_interval(prior),
            f"{current['readmissions']}/{current['discharges']}", rate_with_interval(current),
            f"{scope['change_pp']:+.1f}" if interval else "—",
            f"{interval[0]:+.2f} to {interval[1]:+.2f}" if interval else "—",
            f"{scope['fisher_p']:.3f}" if not scope["low_events"] else "—",
            scope["signal"]]


class CaseData:
    def __init__(self, case_id, path=FIXTURES):
        if case_id not in FIXTURE_CASE_IDS:
            raise ValueError("Unknown case.")
        self.case_id = case_id
        self.path = path
        self._snapshot = None

    def connect(self):
        conn = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def snapshot(self):
        """Keep coverage, counts and stay-level evidence on one SQLite snapshot."""
        with closing(self.connect()) as conn:
            conn.execute("BEGIN")
            self._snapshot = conn
            try:
                yield
            finally:
                self._snapshot = None

    def query(self, sql, **params):
        bindings = {"scenario_id": self.case_id, **params}
        if self._snapshot is not None:
            return [dict(row) for row in self._snapshot.execute(sql, bindings)]
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(sql, bindings)]

    @cached_property
    def metric_sql(self):
        return MEASURE.read_text()

    @cached_property
    def counts(self):
        rows = self.query(self.metric_sql, **MONTHS)
        if not rows:
            raise ValueError("No eligible index discharges were found.")
        return rows

    @cached_property
    def follow_up(self):
        rows = self.query(COVERAGE_SQL)
        year, month = map(int, CURRENT_MONTH.split("-"))
        month_end = date(year, month, monthrange(year, month)[1])
        required = (month_end + timedelta(days=30)).isoformat()
        watermarks = [row["observed_through"] for row in rows]
        observed = min(watermarks) if watermarks and all(watermarks) else None
        complete = observed is not None and observed >= required
        return rows, observed, required, complete

    def totals(self, month, source, hospital=None):
        rows = [row for row in self.counts if row["cohort_month"] == month
                and (hospital is None or row["hospital_name"] == hospital)]
        returns = sum(row[f"{source}_readmissions"] for row in rows)
        discharges = sum(row[f"{source}_discharges"] for row in rows)
        return {"readmissions": returns, "discharges": discharges,
                "rate": returns / discharges if discharges else None}

    def card(self):
        _, observed, _, complete = self.follow_up
        return {
            "id": self.case_id,
            "label": f"Case {self.case_id.upper()}",
            "metric": "30-day heart failure readmissions",
            "prior_month": PRIOR_MONTH,
            "current_month": CURRENT_MONTH,
            "source": "sql/metric.sql",
            "prior": self.totals(PRIOR_MONTH, "dashboard"),
            "current": self.totals(CURRENT_MONTH, "dashboard"),
            "as_of": observed,
            "preliminary": not complete,
        }

    def coverage(self):
        rows, observed, required, complete = self.follow_up
        summary = (f"Both hospitals have data through {observed}. The latest cohort "
                   f"requires follow-up through {required}.") if complete else (
                       f"Latest cohort requires data through {required}. "
                       f"System-wide coverage is confirmed through {observed}."
                       if observed else f"Confirm coverage through {required} at both hospitals.")
        return step("coverage", "30-day follow-up", summary,
                    ["Hospital", "Observed through", "Required through", "Status"],
                    [[row["hospital_name"], row["observed_through"], required,
                      "Complete" if (row["observed_through"] or "") >= required else "Incomplete"]
                     for row in rows])

    def headline(self):
        prior = self.totals(PRIOR_MONTH, "dashboard")
        current = self.totals(CURRENT_MONTH, "dashboard")
        summary = f"The observed dashboard rate moved from {percent(prior['rate'])} to {percent(current['rate'])}."
        return step("headline", "Dashboard comparison", summary,
                    ["Discharge month", "Dashboard 30-day readmissions", "Dashboard eligible discharges",
                     "Observed dashboard rate"],
                    [[period, values["readmissions"], values["discharges"], percent(values["rate"])]
                     for period, values in ((PRIOR_MONTH, prior), (CURRENT_MONTH, current))])

    def hospital_counts(self):
        rows = [[row["cohort_month"], row["hospital_name"], row["dashboard_readmissions"],
                 row["dashboard_discharges"], percent(row["dashboard_rate"])]
                for row in self.counts]
        return step("hospital_counts", "Hospital-level counts",
                    "Compare the numerator and denominator at each discharging hospital.",
                    ["Discharge month", "Hospital", "Dashboard 30-day readmissions",
                     "Dashboard eligible discharges", "Observed dashboard rate"], rows)

    def reconcile_sources(self):
        rows = []
        for row in self.counts:
            rows.append([row["cohort_month"], row["hospital_name"], row["dashboard_readmissions"],
                         row["dashboard_discharges"], row["source_readmissions"],
                         row["source_discharges"], row["source_readmissions"] - row["dashboard_readmissions"],
                         row["source_discharges"] - row["dashboard_discharges"]])
        summary = ("Source and dashboard counts differ. The gaps below are source minus dashboard."
                   if any(row[-2] or row[-1] for row in rows)
                   else "Dashboard and source totals agree for both hospitals in both cohorts.")
        return step("reconcile_sources", "Source reconciliation", summary,
                    ["Discharge month", "Hospital", "Dashboard 30-day readmissions", "Dashboard eligible discharges",
                     "Source 30-day readmissions", "Source eligible discharges",
                     "Source minus dashboard readmissions", "Unmatched eligible discharges"], rows)

    def signal_check(self):
        if not self.follow_up[3] or has_gaps(self.reconcile_sources()):
            raise ValueError("Signal check requires complete follow-up and reconciled counts.")
        hospitals = sorted({row["hospital_name"] for row in self.counts})
        scopes = [signal_for_scope(hospital or "Overall", self.totals(PRIOR_MONTH, "source", hospital),
                                   self.totals(CURRENT_MONTH, "source", hospital))
                  for hospital in [None, *hospitals]]
        by_change = sorted((scope for scope in scopes[1:] if not scope["low_events"]),
                           key=lambda scope: abs(scope["change_pp"]), reverse=True)
        strongest = (by_change[0]["scope"] if len(by_change) > 1
                     and abs(by_change[0]["change_pp"]) > abs(by_change[1]["change_pp"]) else None)
        statistics = {"label": scopes[0]["signal"], "scopes": scopes, "strongest_scope": strongest}
        check = step("signal_check", "Signal check",
                     "Observed, unadjusted synthetic rates. The 95% change interval uses Newcombe method 10. "
                     "Fisher's test conditions on the table margins. The interpretation follows the interval.",
                     ["Scope", f"{PRIOR_MONTH} returns / stays", f"{PRIOR_MONTH} rate (Wilson 95%)",
                      f"{CURRENT_MONTH} returns / stays", f"{CURRENT_MONTH} rate (Wilson 95%)",
                      "Change (pp)", "Change 95% interval (pp)", "Fisher exact p", "Interpretation"],
                     [signal_row(scope) for scope in scopes])
        return {**check, "statistics": statistics}

    def missing_stays(self, reconciliation):
        """Drill into positive gaps with the same outcomes used in measure counts."""
        outcomes, _, _ = self.metric_sql.partition("-- Aggregate index outcomes.")
        sql = outcomes + """SELECT encounter_id, hospital_name, cohort_month, readmitted
FROM index_outcomes
WHERE NOT in_dashboard
  AND cohort_month = :gap_month AND hospital_name = :hospital
ORDER BY encounter_id;
"""
        gaps = []
        for month, hospital, dashboard_k, _, source_k, source_n, _, gap in reconciliation["rows"]:
            if gap <= 0:
                continue
            missing = self.query(sql, **MONTHS, gap_month=month, hospital=hospital)
            rows = [list(row.values()) for row in missing]
            missing_non_readmitted = sum(not row["readmitted"] for row in missing)
            gaps.append({
                "hospital": hospital, "month": month,
                "title": f"Unmatched stays · {hospital} · {month}",
                "columns": ["Encounter ID", "Hospital", "Discharge month", "Readmitted within 30 days"],
                "rows": rows, "row_count": len(rows), "sql_row_count": len(missing),
                "sql": sql, "source": "sql/metric.sql",
                "non_readmitted": {"eligible": source_n - source_k, "missing": missing_non_readmitted},
                "readmitted": {"eligible": source_k, "kept": dashboard_k},
            })
        return gaps

    def evidence(self, check):
        check_id = check["id"]
        if check_id == "coverage":
            sql, source = COVERAGE_SQL, "investigation.py"
        elif check_id == "refresh_notes":
            sql, source = REFRESH_SQL, "investigation.py"
        else:
            sql, source = self.metric_sql, "sql/metric.sql"
        count = len(check["rows"])
        details = {**check, "sql": sql, "source": source, "row_count": count,
                   "sql_row_count": len(self.counts) if source == "sql/metric.sql" else count}
        if check_id == "reconcile_sources":
            details["drilldowns"] = self.missing_stays(check)
        return details

    def refresh_notes(self):
        rows = self.query(REFRESH_SQL)
        return step("refresh_notes", "Refresh log",
                    ("Reporting-cycle notes provide context for reconciling the extract."
                     if rows else "The synthetic reporting cycle has no refresh notes."),
                    ["Hospital", "Logged at", "Batch", "Note"], [list(row.values()) for row in rows])


class ToolSelectionError(ValueError):
    pass


def build_result(status, title, finding, evidence, questions, next_action, owner):
    return {
        "status": status,
        "draft": {
            "title": title,
            "finding": finding,
            "evidence": evidence,
            "open_questions": questions,
            "next_action": next_action,
            "owner": owner,
        },
        "reviewed": False,
    }


def handoff(data, steps):
    """Write the draft from checks that actually ran, with conservative status rules."""
    completed = {check["id"]: check for check in steps}
    prior = data.totals(PRIOR_MONTH, "dashboard")
    current = data.totals(CURRENT_MONTH, "dashboard")
    reconciliation = completed.get("reconcile_sources")
    evidence = [
        f"Dashboard: {prior['readmissions']}/{prior['discharges']} ({percent(prior['rate'])}) "
        f"to {current['readmissions']}/{current['discharges']} ({percent(current['rate'])})."
    ]

    if reconciliation:
        gaps = [row for row in reconciliation["rows"] if row[-2] or row[-1]]
        missing_discharges = [row for row in gaps if row[-1] > 0]
        if gaps:
            if len(gaps) == len(missing_discharges) == 1 and gaps[0][0] == CURRENT_MONTH:
                _, hospital, _, dashboard_n, _, source_n, _, gap = gaps[0]
                source = data.totals(CURRENT_MONTH, "source")
                evidence.append(f"{hospital}: {dashboard_n} dashboard vs. {source_n} source "
                                f"discharges. Source totals are {source['readmissions']}/{source['discharges']} "
                                f"({percent(source['rate'])}).")
                for name, _, batch, note in completed.get("refresh_notes", {}).get("rows", []):
                    if name == hospital:
                        evidence.append(f"Refresh log, batch {batch}: {note}")
                finding = f"The dashboard is missing {gap} eligible discharges from {hospital}."
                if source["readmissions"] == current["readmissions"]:
                    finding += " The difference is in the denominator."
                return build_result(
                    "data_quality", "Hold the dashboard rate for reconciliation", finding, evidence,
                    ["Was the expected discharge batch included in the publishing refresh?"],
                    f"Verify the missing {hospital} batch and rerun reconciliation before certifying the rate.",
                    "Data team",
                )
            return build_result(
                "data_quality", "Hold the dashboard rate for reconciliation",
                "The dashboard and source counts differ.", evidence + [reconciliation["summary"]],
                ["Which reporting rows explain the differences?"],
                "Verify the source extracts and rerun reconciliation before certifying the rate.", "Data team",
            )
        evidence.append(reconciliation["summary"])
        if "hospital_counts" in completed and current["readmissions"] > prior["readmissions"]:
            rows = completed["hospital_counts"]["rows"]
            prior_by_hospital = {row[1]: row[2] for row in rows if row[0] == PRIOR_MONTH}
            changes = [(row[2] - prior_by_hospital.get(row[1], 0), row[1], row[2])
                       for row in rows if row[0] == CURRENT_MONTH]
            addition, hospital, latest = max(changes)
            total = current["readmissions"] - prior["readmissions"]
            evidence.append(f"{hospital}: {prior_by_hospital.get(hospital, 0)} to {latest} readmissions.")
            return build_result(
                "observed_increase", "Review the increase with the quality team",
                f"{hospital} had the largest increase, with {addition} additional readmissions "
                f"({addition} of {total} across the two hospitals).",
                evidence, ["What were the reasons for return?", "Did case mix or discharge follow-up change?"],
                f"Start a structured review of recent {hospital} readmissions and compare with the prior cohort.",
                "Quality team",
            )

    return build_result(
        "needs_review", "Complete analyst review",
        "The checks completed so far do not establish a verified change to hand off.", evidence,
        ["Do the rates reconcile, and is any change concentrated at one hospital?"],
        "Review the tool trace and complete any missing hospital or reconciliation checks.", "Analyst",
    )


def short_date(value):
    day = date.fromisoformat(value)
    return f"{day:%b} {day.day}"


def verdict_for(status, card, signal, drilldowns, required):
    """Write one verdict and action for both the full and comparison views."""
    if status == "data_quality":
        latest_rate = card["current"]["rate"]
        title = (f"Hold. Do not publish {percent(latest_rate)}." if latest_rate is not None
                 else "Hold. Reconcile the dashboard rate.")
        reason = "The dashboard and source counts differ."
        support = f"{reason} Send to data team for reconciliation."
        if len(drilldowns) == 1:
            gap = drilldowns[0]
            hospital = gap["hospital"].split()[0]
            without_return = gap["non_readmitted"]
            with_return = gap["readmitted"]
            reason = f"{gap['row_count']} {hospital} stays are missing from the reporting index."
            support = f"{reason} Send to data team for reconciliation."
            if (gap["month"] == CURRENT_MONTH and without_return["missing"] > 0
                    and with_return["kept"] == with_return["eligible"] > 0):
                support = (f"{hospital}'s reporting index is missing {without_return['missing']} of "
                           f"{without_return['eligible']} non-readmitted stays while keeping all "
                           f"{with_return['kept']} readmitted stays, which inflates the rate. "
                           "Send to data team for reconciliation.")
        action = "Send to data team."
    elif status == "observed_increase":
        title = "Counts reconcile. Send to quality review."
        action = "Send to quality team."
        reason = support = "Both sources agree."
        if signal["scopes"] and signal["scopes"][0]["interval_pp"]:
            overall = signal["scopes"][0]
            low, high = overall["interval_pp"]
            change = f"{overall['change_pp']:+.1f} pp"
            zero = "includes" if low <= 0 <= high else "excludes"
            reason = f"Counts reconcile. The {change} change has a 95% interval of {low:+.1f} to {high:+.1f} pp."
            support += f" The change is {change} with an interval that {zero} zero overall"
            stronger = signal["strongest_scope"]
            support += f"; {stronger.split()[0]} shows the stronger signal." if stronger else "."
        support += " Observed, unadjusted, synthetic data."
    elif status == "premature":
        month = date.fromisoformat(f"{card['current_month']}-01").strftime("%B")
        title = f"Wait. {month} is not complete."
        reason = f"{month} needs coverage through {short_date(required)}."
        observed = card["as_of"]
        if observed:
            complete_through = (date.fromisoformat(observed) - timedelta(days=30)).isoformat()
            support = (f"Data through {short_date(observed)} completes the 30-day window only for discharges "
                       f"through {short_date(complete_through)}; "
                       f"{month} needs coverage through {short_date(required)}.")
        else:
            support = reason
        support += " Re-run when follow-up is complete."
        action = "Wait for complete follow-up."
    else:
        title = "Review. Complete the analyst checks."
        reason = support = "The completed checks leave the change unverified."
        action = "Complete analyst review."
    return {"title": title, "support": support, "next_action": action, "reason": reason}


def investigate(data, choose=None):
    with data.snapshot():
        yield from investigation_events(data, choose)


def investigation_events(data, choose):
    def as_event(check):
        return {"type": "step", "step": data.evidence(check)}

    drilldowns = []

    def as_completion(result, signal):
        card = data.card()
        verdict = verdict_for(result["status"], card, signal, drilldowns, data.follow_up[2])
        return {"type": "complete", "result": {**result, "card": card, "signal_check": signal,
                                                "verdict": verdict}}

    def skipped(label):
        return {"label": label, "scopes": [], "strongest_scope": None}

    coverage = data.coverage()
    yield as_event(coverage)
    _, observed, required, complete = data.follow_up
    if not complete:
        result = build_result(
            "premature", "Wait for 30-day follow-up",
            f"The {CURRENT_MONTH} cohort is still accumulating readmissions. Defer month-over-month comparison.",
            [coverage["summary"]],
            ["When will inpatient encounter data be refreshed through the entire window?"],
            f"Refresh and verify coverage through {required} at both hospitals, then rerun the comparison.",
            "Reporting analyst",
        )
        yield as_completion(result, skipped("Not computed: follow-up incomplete"))
        return

    headline = data.headline()
    yield as_event(headline)
    checks = [coverage, headline]
    remaining = {key: description for key, description in TOOL_DESCRIPTIONS.items()
                 if key != "signal_check"}
    signal = skipped("Skipped: source reconciliation did not run.")
    failed_reconciliation = False
    notice = None
    while remaining:
        try:
            selection = choose(remaining.copy(), tuple(checks)) if choose else next(iter(remaining))
            if selection is not None and selection not in remaining:
                raise ToolSelectionError("The model returned an unavailable check.")
        except ToolSelectionError as error:
            if not failed_reconciliation:
                raise
            # A later model error cannot erase a hold already established by SQL.
            notice = str(error)
            break
        if selection is None:
            break
        del remaining[selection]
        check = getattr(data, selection)()
        checks.append(check)
        if selection == "reconcile_sources":
            failed_reconciliation = has_gaps(check)
            if failed_reconciliation:
                signal = skipped("Skipped: source reconciliation failed.")
            else:
                signal = skipped("Not computed: signal check was not selected.")
                remaining["signal_check"] = TOOL_DESCRIPTIONS["signal_check"]
        elif selection == "signal_check":
            signal = check["statistics"]
        event = as_event(check)
        if selection == "reconcile_sources":
            drilldowns = event["step"]["drilldowns"]
        yield event
    result = handoff(data, checks)
    if notice:
        result["notice"] = notice
    yield as_completion(result, signal)


def record_with_local_policy(choices, evidence):
    """Evidence-based policy for recording offline traces without API credentials."""
    if "hospital_counts" in choices:
        return "hospital_counts"
    if "reconcile_sources" in choices:
        return "reconcile_sources"
    if "refresh_notes" in choices:
        reconciliation = next(check for check in evidence if check["id"] == "reconcile_sources")
        if has_gaps(reconciliation):
            return "refresh_notes"
    return None
