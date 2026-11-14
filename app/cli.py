import sys

import uvicorn

from .brown_test import run_brown_checks
from .pipeline import run_once
from .replay import snapshot_replay
from .topic_selection import DEFAULT_TOPIC, resolve_topic
from .storage import clear_drafts


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "help"
    if command == "run-once":
        topic_arg = sys.argv[2] if len(sys.argv) > 2 else None
        drafts = run_once(topic_arg)
        print(f"Created {len(drafts)} drafts")
    elif command == "dashboard":
        uvicorn.run("app.dashboard:app", host="127.0.0.1", port=8000, reload=False)
    elif command == "brown-test":
        for result in run_brown_checks():
            print(f"PASS: {result}")
    elif command == "snapshot-replay":
        topic_id = resolve_topic(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_TOPIC
        if topic_id is None:
            raise SystemExit("Unknown topic. Use cybersecurity, governance_risk, or ai_announcements.")
        meta = snapshot_replay(topic_id)
        print(f"Saved {topic_id} replay snapshot with {len(meta['draft_ids'])} passing drafts")
    elif command == "clear-drafts":
        from .config import ROOT
        draft_dir = ROOT / "drafts"
        files = list(draft_dir.glob("*.json")) + list(draft_dir.glob("*.txt"))
        for path in files:
            path.unlink()
        state_names = (
            "drafts_ai_agents.json", "drafts_cybersecurity.json", "drafts_governance_risk.json",
            "faithfulness_ai_agents.json", "faithfulness_cybersecurity.json", "faithfulness_governance_risk.json",
            "optimized_drafts.json", "final_validation_report.json",
        )
        state_files = [ROOT / "data" / "adk_workflow" / name for name in state_names]
        state_removed = 0
        for path in state_files:
            if path.exists():
                path.unlink()
                state_removed += 1
        print(
            f"Deleted {len(files)} draft files, {clear_drafts()} stored draft records, "
            f"and {state_removed} draft-stage state files."
        )
    else:
        print("Usage: python -m app.cli [run-once [topic]|dashboard|brown-test|snapshot-replay [topic]|clear-drafts]")


if __name__ == "__main__":
    main()
