"""Explicitly package the original project's exported biology course; never learner state."""

import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--sources", type=Path, required=True)
parser.add_argument("--community", type=Path, required=True)
args = parser.parse_args()
raw = args.sources.read_bytes()
source = next(s for s in json.loads(raw) if s["summary"]["book_id"] == "book_biology_2")
entry = next(s for s in json.loads(args.community.read_bytes()) if s["id"] == "community_genetics")
entry.update(
    owner="原项目已有课程",
    learners=0,
    progress=0,
    flashcardCount=len(source["flashcards"]),
    chapters=[
        c["source_title"]
        for c in source["chapters"]
        if c["level"] == 1 and c["chapter_id"] != "frontmatter"
    ],
    description="原项目《生物 必修2 遗传与进化》已有课程，保留15个课时、90张闪卡和45道练习，以及已发布的125页原文图片。部分内容为原项目AI补充，继续保留待原文核验标记。",
    tags=["已有课程", "章节导学", "闪卡", "原文阅读"],
    recommended=True,
)
target = Path(__file__).resolve().parents[1] / "src/adaptive_learning/data/native_biology.json"
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(
    json.dumps(
        {
            "provenance": {
                "origin": "study_app_demo_ios exported biology course",
                "sources_sha256": hashlib.sha256(raw).hexdigest(),
            },
            "entry": entry,
            "source": source,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    + "\n",
    encoding="utf-8",
)
print(
    f"Packaged {len(source['lessons'])} lessons, {len(source['flashcards'])} cards, {len(source['quizzes'])} questions"
)
