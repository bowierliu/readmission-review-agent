"""Record the real SQL evidence and drafts produced by the local demo policy."""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app import REPLAYS, STATIC, add_provenance, load_replay
from investigation import CASE_IDS, CaseData, investigate, record_with_local_policy


def browser_asset(traces):
    recordings = {key: [add_provenance(event, "replay", traces["captured_at"]) for event in events]
                  for key, events in traces["cases"].items()}
    return "window.readmissionRecordings = " + json.dumps(recordings, separators=(",", ":")) + ";\n"


def main():
    traces = {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "provenance": "Captured synthetic SQL tool runs using the local evidence-based policy.",
        "cases": {
            case_id: list(investigate(CaseData(case_id), record_with_local_policy))
            for case_id in CASE_IDS
        },
    }
    with TemporaryDirectory(dir=REPLAYS.parent) as directory:
        path = Path(directory) / REPLAYS.name
        path.write_text(json.dumps(traces, indent=2) + "\n")
        path.replace(REPLAYS)
    (STATIC / "assets" / "recordings.js").write_text(browser_asset(load_replay()))
    print(f"Wrote {REPLAYS} and browser recordings")


if __name__ == "__main__":
    main()
