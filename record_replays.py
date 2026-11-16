"""Record the real SQL evidence and drafts produced by the local demo policy."""

import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app import REPLAYS
from investigation import CASE_IDS, CaseData, investigate, record_with_local_policy


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
    print(f"Wrote {REPLAYS}")


if __name__ == "__main__":
    main()
