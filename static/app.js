"use strict";

const byId = (id) => document.getElementById(id);
const runButton = byId("run-investigation");
const freshButton = byId("run-fresh");
const modeSelect = byId("run-mode");
const runIcon = byId("run-icon");
const playIcon = runIcon.firstElementChild;
const caseSelector = byId("case-selector");
const runs = new Map();
const compareButton = byId("compare-cases");
const reviewButton = byId("mark-reviewed");
const modeLabels = { checklist: "Checklist (fresh)", replay: "Saved trace", live: "Live agent" };
const iconPaths = {
  file: '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10Zm0 0v7h7M8 14h8m-8 3h5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  alert: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4m0 4h.01"/>',
  arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
};
let cases = [];
let currentCase;
let mode = "checklist";
let comparisonVersion = 0;
let shownEvidenceRun;

function element(tag, className, value) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (value !== undefined) node.textContent = value;
  return node;
}

function icon(name) {
  const wrapper = element("span", "button-icon");
  wrapper.setAttribute("aria-hidden", "true");
  // Only static SVG paths are inserted as markup. All API text uses textContent.
  wrapper.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none">' + iconPaths[name] + '</svg>';
  return wrapper;
}

function activeKey() {
  return currentCase.id + ":" + mode;
}

function dateLabel(value, options) {
  return new Intl.DateTimeFormat("en-US", { ...options, timeZone: "UTC" }).format(new Date(value));
}

function observedRate(rate) {
  return rate == null ? "Unavailable" : (rate * 100).toFixed(1) + "%";
}

function showCase(run) {
  const result = run?.phase === "complete" ? run.result : null;
  const card = result?.card ?? currentCase;
  byId("case-title").textContent = card.label + " · " + card.metric;
  const prior = dateLabel(card.prior_month, { month: "short", year: "numeric" });
  const latest = dateLabel(card.current_month, { month: "short", year: "numeric" });
  const situation = byId("case-situation");
  situation.hidden = Boolean(result);
  situation.textContent = card.preliminary
    ? "The latest cohort's 30-day follow-up is still incomplete."
    : "Review the change in observed readmissions from " + prior + " to " + latest + ".";
  for (const [key, detail, month] of [["prior", card.prior, prior], ["current", card.current, latest]]) {
    byId(key + "-month").textContent = month;
    byId(key + "-rate").textContent = observedRate(detail.rate);
    const returns = detail.readmissions.toLocaleString("en-US");
    const discharges = detail.discharges.toLocaleString("en-US");
    byId(key + "-counts").textContent = returns + " readmissions / " + discharges + " eligible discharges";
  }

  const preliminary = card.preliminary;
  const ratesAvailable = card.current.rate != null && card.prior.rate != null;
  const showDelta = ratesAvailable && !preliminary;
  byId("provisional").hidden = !preliminary;
  const deltaValue = byId("delta-rate");
  deltaValue.parentElement.toggleAttribute("data-preliminary", !showDelta);
  deltaValue.textContent = showDelta ? signed((card.current.rate - card.prior.rate) * 100, 1) + " pp"
    : preliminary ? "Change withheld" : "Change unavailable";
  byId("delta-caption").textContent = preliminary ? "30-day follow-up is still open"
    : ratesAvailable ? "Percentage-point change" : "Both periods need a reported rate";

  const provenance = byId("metric-provenance");
  provenance.replaceChildren(result ? runProvenance(result)
    : element("span", "provenance-chip", "Dashboard preview · " + card.source));
  if (card.as_of) {
    provenance.append(element("span", "", "Data through "
      + dateLabel(card.as_of, { month: "short", day: "numeric", year: "numeric" })));
  }
}

function draftList(title, items) {
  const section = element("section", "draft-section");
  const list = element("ul");
  for (const item of items) list.append(element("li", "", item));
  section.append(element("h4", "eyebrow draft-label", title), list);
  return section;
}

function paintDraft(run) {
  const complete = run?.phase === "complete";
  byId("draft-panel").hidden = !complete;
  if (!complete) return;

  const draft = run.result.draft;
  const content = element("article");
  const header = element("header", "draft-header");
  header.append(element("p", "eyebrow", "Draft finding"), element("h3", "", draft.title), element("p", "", draft.finding));
  content.append(header);
  if (draft.evidence.length) content.append(draftList("Supporting evidence", draft.evidence));
  if (draft.open_questions.length) content.append(draftList("Open questions", draft.open_questions));
  const action = element("div", "draft-action");
  action.append(element("p", "eyebrow", "Proposed next action"), element("p", "", draft.next_action));
  if (draft.owner) action.append(element("p", "", "Suggested owner · " + draft.owner));
  content.append(action);
  byId("draft-content").replaceChildren(content);
}

function evidenceTable(step) {
  const scroll = element("div", "table-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "region");
  scroll.setAttribute("aria-label", step.title + " table");
  const table = element("table");
  table.toggleAttribute("data-wide", step.columns.length > 4);
  const head = table.createTHead().insertRow();
  for (const column of step.columns) {
    const cell = element("th", "", column);
    cell.scope = "col";
    head.append(cell);
  }
  const body = table.createTBody();
  for (const values of step.rows) {
    const row = body.insertRow();
    if (step.statistics?.strongest_scope === values[0]) row.dataset.strongerSignal = "";
    for (const [i, value] of values.entries()) {
      const cell = row.insertCell();
      const gap = step.drilldowns?.find((detail) => (
        i === values.length - 1 && detail.month === values[0] && detail.hospital === values[1]
      ));
      if (gap) {
        const button = element("button", "gap-button", String(value));
        button.type = "button";
        button.setAttribute("aria-label", "Show " + value + " unmatched stays for " + gap.hospital + ", " + gap.month);
        button.setAttribute("aria-expanded", "false");
        button.setAttribute("aria-controls", gapId(step, gap));
        button.addEventListener("click", () => toggleGap(step, gap, button));
        cell.append(button);
      } else if (typeof value === "string" && /^(batch|encounter id|id|code)$/i.test(step.columns[i])) {
        cell.append(element("code", "", value));
      } else {
        cell.textContent = value ?? "—";
        if (i === 0 && row.hasAttribute("data-stronger-signal")) {
          cell.append(element("small", "signal-marker", "Stronger observed signal"));
        }
      }
      cell.dataset.valueKind = typeof value;
    }
  }
  scroll.append(table);
  return scroll;
}

function runProvenance(run, source = run.card.source) {
  const time = element("time", "", run.ran_at);
  time.dateTime = run.ran_at;
  const chip = element("span", "provenance-chip");
  chip.append(modeLabels[run.mode], " · ", time, " · ", source);
  return chip;
}

function evidenceProvenance(step) {
  const line = element("p", "provenance");
  line.append(runProvenance(step, step.source),
    " · ", step.row_count + (step.row_count === 1 ? " row" : " rows"));
  return line;
}

function sqlDisclosure(step) {
  const details = element("details", "sql-details");
  const pre = element("pre", "", step.sql);
  pre.tabIndex = 0;
  pre.setAttribute("aria-label", "Read-only SQL for " + step.title);
  details.append(element("summary", "", "Show SQL"),
    element("p", "", "SQL returned " + step.sql_row_count + (step.sql_row_count === 1 ? " row." : " rows.")), pre);
  return details;
}

function gapId(step, gap) {
  return "gap-" + step.id + "-" + step.drilldowns.indexOf(gap);
}

function toggleGap(step, gap, button) {
  const section = byId(gapId(step, gap));
  section.hidden = !section.hidden;
  button.setAttribute("aria-expanded", String(!section.hidden));
  if (!section.hidden) section.querySelector("h4").focus();
}

function gapSection(step, gap) {
  const section = element("section", "gap-detail");
  section.id = gapId(step, gap);
  section.hidden = true;
  const title = element("h4", "", gap.title);
  title.id = section.id + "-title";
  title.tabIndex = -1;
  section.setAttribute("aria-labelledby", title.id);
  const hospital = gap.hospital.split(" ")[0];
  const without = gap.non_readmitted;
  const withReturn = gap.readmitted;
  const allKept = withReturn.eligible > 0 && withReturn.eligible === withReturn.kept;
  let caption = "Of the " + without.eligible + " non-readmitted " + hospital + " stays, "
    + without.missing + " are dropped while "
    + (allKept ? "all " + withReturn.kept : withReturn.kept + " of " + withReturn.eligible)
    + " readmitted stays are kept";
  caption += allKept && without.missing ? ", which inflates the reported rate." : ".";
  const next = element("a", "back-to-action", "Back to next action");
  next.href = "#next-action";
  next.addEventListener("click", (event) => {
    event.preventDefault();
    reviewButton.focus();
  });
  next.hidden = true;
  section.append(title, element("p", "", caption), evidenceProvenance(gap), next,
    evidenceTable(gap), sqlDisclosure(gap));
  return section;
}

function paintEvidence(run) {
  const steps = run?.steps ?? [];
  const panel = byId("evidence-content");
  byId("evidence-panel").hidden = steps.length === 0;
  if (run !== shownEvidenceRun) {
    shownEvidenceRun = run;
    panel.replaceChildren();
  }
  for (const step of steps.slice(panel.childElementCount)) {
    const record = element("section", "evidence-record");
    record.id = "evidence-" + step.id;
    record.tabIndex = -1;
    record.append(element("h3", "", step.title), element("p", "", step.summary));
    if (step.columns.length > 0) {
      record.append(evidenceProvenance(step), evidenceTable(step), sqlDisclosure(step));
    }
    for (const gap of step.drilldowns ?? []) record.append(gapSection(step, gap));
    panel.append(record);
  }
  for (const link of panel.querySelectorAll(".back-to-action")) {
    link.hidden = run?.phase !== "complete";
  }
}

function progressStep(symbol, name, kind, evidenceId) {
  const row = element("li", "progress-step");
  row.dataset.kind = kind;
  const mark = element("span", "step-icon");
  mark.append(icon(symbol));
  const title = element("span", "step-title", name);
  row.append(mark);
  if (evidenceId) {
    const link = element("a", "step-link");
    link.href = "#evidence-" + evidenceId;
    link.append(title, icon("arrow"));
    link.addEventListener("click", (event) => {
      event.preventDefault();
      const record = byId("evidence-" + evidenceId);
      record.focus({ preventScroll: true });
      record.scrollIntoView({ block: "start" });
    });
    row.append(link);
  } else {
    row.append(title);
  }
  return row;
}

function paintProgress(run) {
  const steps = run?.steps ?? [];
  const rows = steps.map((step) => progressStep("check", step.title, "checked", step.id));
  let description = "Checks appear here as results arrive.";
  if (run?.phase === "running") {
    rows.push(progressStep("clock", "Awaiting next result", "active"));
  } else if (run?.phase === "complete") {
    if (run.result.notice) rows.push(progressStep("alert", run.result.notice, "failed"));
    rows.push(progressStep("file", "Review draft ready", "checked"));
    description = modeLabels[run.result.mode] + " · " + steps.length + (steps.length === 1 ? " check" : " checks");
  } else if (run?.phase === "error") {
    rows.push(progressStep("alert", "Investigation stopped", "failed"));
    description = "Returned tool results remain available.";
  }
  byId("progress-panel").hidden = !run;
  byId("progress-description").textContent = description;
  byId("progress-steps").replaceChildren(...rows);
}

function signed(value, digits) {
  return (value > 0 ? "+" : "") + value.toFixed(digits);
}

function signalStats(scope) {
  const [low, high] = scope.interval_pp;
  const separation = "Change " + signed(scope.change_pp, 1) + " pp, 95% interval "
    + signed(low, 1) + " to " + signed(high, 1) + " pp";
  return separation + (scope.low_events ? "" : ", Fisher exact p = " + scope.fisher_p.toFixed(2));
}

function paintSignal(run) {
  const summary = byId("signal-summary");
  const signal = run?.phase === "complete" ? run.result.signal_check : null;
  summary.hidden = !signal;
  if (!signal) {
    summary.replaceChildren();
    return;
  }
  const overall = signal.scopes[0];
  summary.replaceChildren(element("strong", "", signal.label));
  if (overall?.interval_pp) {
    summary.append(element("p", "", signalStats(overall) + ", unadjusted observed rates, synthetic data."));
  }
}

function paintVerdict(run) {
  const verdict = byId("verdict");
  const complete = run?.phase === "complete";
  verdict.hidden = !complete;
  if (!complete) return;
  verdict.dataset.disposition = run.result.status;
  byId("verdict-title").textContent = run.result.verdict.title;
  byId("verdict-support").textContent = run.result.verdict.support;
}

function paintNextAction(run) {
  const complete = run?.phase === "complete";
  byId("next-action").hidden = !complete;
  if (!complete) return;
  byId("next-action-text").textContent = run.result.verdict.next_action;
  const reviewed = run.result.reviewed;
  reviewButton.textContent = reviewed ? "Reviewed locally"
    : run.savingReview ? "Saving review…" : "Mark reviewed";
  reviewButton.setAttribute("aria-disabled", String(reviewed || Boolean(run.savingReview)));
  reviewButton.toggleAttribute("data-reviewed", reviewed);
  const message = byId("review-error");
  message.hidden = !run.reviewError;
  message.textContent = run.reviewError ?? "";

  const gaps = run.steps.find((step) => step.id === "reconcile_sources")?.drilldowns ?? [];
  const missing = gaps.reduce((total, gap) => total + gap.row_count, 0);
  const link = byId("jump-to-gap");
  link.hidden = missing === 0;
  link.textContent = "Inspect " + missing + (missing === 1 ? " unmatched stay" : " unmatched stays");
}

function announce(message) {
  byId("run-status").textContent = message;
}

function paint() {
  const run = runs.get(activeKey());
  const phase = run?.phase ?? "ready";
  const running = phase === "running";
  const complete = phase === "complete";
  const error = phase === "error";
  const comparing = !byId("compare-view").hidden;
  for (const button of caseSelector.children) {
    button.disabled = running;
    button.setAttribute("aria-pressed", String(!comparing && button.dataset.caseId === currentCase.id));
  }
  compareButton.disabled = running;
  compareButton.setAttribute("aria-pressed", String(comparing));
  runButton.setAttribute("aria-disabled", String(running));
  modeSelect.disabled = running;
  freshButton.hidden = !complete || mode === "checklist";
  runIcon.replaceChildren(running ? element("span", "spinner") : playIcon);
  byId("run-label").textContent = running ? "Running…" : error ? "Retry investigation"
    : complete ? (mode === "checklist" ? "Run fresh" : "Run again") : "Run investigation";
  const message = byId("run-error");
  message.hidden = !error;
  message.textContent = error ? run.error : "";
  showCase(run);
  paintVerdict(run);
  paintSignal(run);
  paintNextAction(run);
  paintEvidence(run);
  paintDraft(run);
  paintProgress(run);
}

async function post(path, data) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    signal: AbortSignal.timeout(180_000),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.message || payload.error || "The server returned HTTP " + response.status + ".");
  }
  return response;
}

async function readInvestigation(response, run, onStep) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  function accept(line) {
    if (!line.trim()) return;
    const event = JSON.parse(line);
    if (event.type === "error") throw new Error(event.message || "The investigation could not finish.");
    if (event.type === "step") {
      run.steps.push(event.step);
      onStep?.(event.step);
    }
    if (event.type === "complete") run.result = event.result;
  }

  try {
    while (true) {
      const { done, value } = await reader.read();
      buffer += decoder.decode(value, { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop();
      for (const line of lines) accept(line);
      if (done) break;
    }
    accept(buffer);
  } finally {
    reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  if (!run.result) throw new Error("The stream closed before a draft was returned.");
}

async function fetchRun(caseId, mode, run, onStep) {
  const response = await post("/api/investigate", { case_id: caseId, mode });
  await readInvestigation(response, run, onStep);
  run.phase = "complete";
  return run;
}

async function investigate() {
  const key = activeKey();
  if (runs.get(key)?.phase === "running") return;
  const run = { phase: "running", steps: [] };
  runs.set(key, run);
  paint();
  announce("Investigation running.");
  try {
    await fetchRun(currentCase.id, mode, run, (step) => {
      paintEvidence(run);
      paintProgress(run);
      announce(step.title + " returned.");
    });
  } catch (error) {
    run.phase = "error";
    run.error = error.name === "TimeoutError" ? "The investigation timed out." : error.message;
  }
  paint();
  if (run.phase === "complete") {
    announce("Verdict. " + run.result.verdict.title + (run.result.notice ? " " + run.result.notice : ""));
    if (document.activeElement === runButton) byId("verdict-title").focus();
  }
}

async function markReviewed() {
  const key = activeKey();
  const run = runs.get(key);
  if (run?.phase !== "complete" || run.savingReview || run.result.reviewed) return;
  run.savingReview = true;
  run.reviewError = "";
  paintNextAction(run);
  try {
    const response = await post("/api/review", { run_id: run.result.run_id });
    const review = await response.json();
    run.result.reviewed = review.reviewed;
  } catch (error) {
    run.reviewError = "Could not save the review. " + error.message;
  } finally {
    run.savingReview = false;
  }
  if (activeKey() === key && runs.get(key) === run && byId("compare-view").hidden) {
    paintNextAction(run);
    if (run.result.reviewed) announce("Reviewed locally.");
  }
}

function showCaseView(caseId, run) {
  currentCase = cases.find((item) => item.id === caseId);
  byId("compare-view").hidden = true;
  byId("case-view").hidden = false;
  if (run) {
    mode = modeSelect.value = "checklist";
    runs.set(activeKey(), run);
  }
  paint();
  announce(currentCase.label + " selected.");
  if (run) byId("verdict-title").focus();
}

function comparisonCard(run) {
  const { card, status, verdict } = run.result;
  const content = element("article", "panel comparison-card");
  const rate = element("p", "compare-rate", observedRate(card.prior.rate) + " → " + observedRate(card.current.rate));
  const header = element("h2", "", card.label);
  header.id = "compare-case-" + card.id;
  content.setAttribute("aria-labelledby", header.id);
  const decision = element("div", "verdict");
  decision.dataset.disposition = status;
  decision.append(element("h3", "", verdict.title), element("p", "", verdict.reason));
  const button = element("button", "button button-secondary", "Open " + card.label + " investigation");
  button.type = "button";
  button.addEventListener("click", () => showCaseView(card.id, run));
  content.append(header, rate, runProvenance(run.result), decision, button);
  return content;
}

async function loadComparison() {
  const version = ++comparisonVersion;
  const view = byId("compare-view");
  const output = byId("compare-output");
  byId("case-view").hidden = true;
  view.hidden = false;
  output.replaceChildren(element("p", "panel-description", "Running both checklists…"));
  paint();
  byId("compare-title").focus();
  announce("Running fresh checklists for Cases A and B.");
  try {
    const results = await Promise.all(["a", "b"].map((id) =>
      fetchRun(id, "checklist", { phase: "running", steps: [] })));
    if (view.hidden || version !== comparisonVersion) return;
    const grid = element("div", "comparison-grid");
    grid.append(...results.map(comparisonCard));
    const [a, b] = results.map((run) => run.result);
    const latestA = a.card.current.rate;
    const latestB = b.card.current.rate;
    const sameHeadline = latestA != null && latestB != null && observedRate(latestA) === observedRate(latestB);
    const takeaway = sameHeadline && a.status !== b.status
      ? "Same " + observedRate(latestA).replace(/\.0%$/, "%") + ". Different answer."
      : "Review the reported rates and verdicts together.";
    output.replaceChildren(grid, element("p", "comparison-takeaway", takeaway));
    announce("Comparison ready. " + takeaway);
  } catch (error) {
    if (view.hidden || version !== comparisonVersion) return;
    const message = element("p", "inline-error", "Could not load the comparison. " + error.message);
    message.setAttribute("role", "alert");
    const retry = element("button", "button button-secondary", "Retry comparison");
    retry.type = "button";
    retry.addEventListener("click", loadComparison);
    output.replaceChildren(message, retry);
  }
}

async function loadCases() {
  byId("load-message").textContent = "Loading cases…";
  byId("retry-cases").hidden = true;
  try {
    const response = await fetch("/api/cases", { signal: AbortSignal.timeout(15_000) });
    if (!response.ok) throw new Error("Cases could not be loaded.");
    const payload = await response.json();
    cases = payload.cases;
    if (!cases?.length) throw new Error("No cases were returned.");
    currentCase = cases[0];
    caseSelector.replaceChildren(...cases.map((item) => {
      const button = element("button", "", item.label);
      button.type = "button";
      button.dataset.caseId = item.id;
      button.addEventListener("click", () => {
        showCaseView(item.id);
      });
      return button;
    }));
    modeSelect.querySelector('[value="live"]').disabled = !payload.live_available;
    paint();
    byId("case-load").hidden = true;
    byId("case-content").hidden = false;
  } catch (error) {
    byId("load-message").textContent = "Cases could not be loaded. Check that the local server is running and try again.";
    byId("retry-cases").hidden = false;
  }
}

modeSelect.addEventListener("change", () => {
  mode = modeSelect.value;
  paint();
  announce(modeLabels[mode] + " selected.");
});
runButton.addEventListener("click", investigate);
freshButton.addEventListener("click", () => {
  mode = modeSelect.value = "checklist";
  runButton.focus();
  investigate();
});
reviewButton.addEventListener("click", markReviewed);
byId("jump-to-gap").addEventListener("click", (event) => {
  event.preventDefault();
  document.querySelector("#evidence-reconcile_sources .gap-button")?.focus();
});
compareButton.addEventListener("click", () => {
  if (byId("compare-view").hidden) loadComparison();
  else showCaseView(currentCase.id);
});
byId("retry-cases").addEventListener("click", loadCases);
loadCases();
