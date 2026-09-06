"""Read-only deployed API and migration checks against the pre-migration snapshot."""
import argparse
import json
import sqlite3
import time
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8100")
    args = parser.parse_args()
    with sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as live, \
         sqlite3.connect(f"file:{args.backup}?mode=ro", uri=True) as backup:
        originals = backup.execute("SELECT course_id,bundle_json FROM course_bundles").fetchall()
        total = 0
        for course_id, payload in originals:
            before = json.loads(payload)
            after = json.loads(live.execute("SELECT bundle_json FROM course_bundles WHERE course_id=?",
                                           (course_id,)).fetchone()[0])
            assert before["version"] == after["version"]
            assert len(before["flashcards"]) == len(after["flashcards"])
            for old, new in zip(before["flashcards"], after["flashcards"], strict=True):
                for field in ("card_id", "point_id", "citations", "source", "reason_for_user"):
                    assert old[field] == new[field], (course_id, field)
                total += 1
        old_events = backup.execute("SELECT * FROM learning_events").fetchall()
        for event in old_events:
            assert live.execute("SELECT * FROM learning_events WHERE event_id=?", (event[0],)).fetchone() == event
        pairs = live.execute("SELECT DISTINCT session_id,chapter_id FROM course_bundles").fetchall()
        times = []
        for session_id, chapter_id in pairs:
            start = time.monotonic()
            with urllib.request.urlopen(
                f"{args.api_url}/api/interviews/{session_id}/courses/{chapter_id}", timeout=180
            ) as response:
                assert response.status == 200
                public = json.load(response)
            times.append(time.monotonic() - start)
            stored = json.loads(live.execute("SELECT bundle_json FROM course_bundles "
                "WHERE session_id=? AND chapter_id=? ORDER BY version DESC LIMIT 1",
                (session_id, chapter_id)).fetchone()[0])
            assert public["flashcards"] == stored["flashcards"]
        print(json.dumps({"historical_courses_preserved": len(originals),
            "historical_card_ids_and_sources_preserved": total, "learning_events_preserved": len(old_events),
            "live_course_endpoints_passed": len(pairs), "max_cached_response_seconds": round(max(times), 3)},
            ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
