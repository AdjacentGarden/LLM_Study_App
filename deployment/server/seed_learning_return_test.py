"""Opt-in historical fixture for one fresh, isolated E2E session, never a real profile.

This fabricates elapsed time for software verification, NOT learning-effect evidence.
It uses an existing book's chapter evidence and never changes its source data.
"""
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from adaptive_learning.assessment.models import InterviewPhase
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.models import PublicChapterLearningBundle
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.review import schedule_review

root=Path('/data1/zhenghang/adaptive-book-ocr/data/state')
repo=SQLiteAssessmentRepository(root/'assessments.sqlite3')
session=repo.get_session(sys.argv[1])
now=datetime.now(UTC)
assert session and session.profile.user_id.startswith('return-e2e-')
assert (now-session.created_at).total_seconds()<600 and session.revision==0
assert not session.profile.flashcard_reviews
structure=SQLiteOCRJobRepository(root/'ocr_jobs.sqlite3').get_structure(session.profile.book_id)
assert structure
chapter=next(ch for ch in structure.chapters if ch.knowledge_point_evidence)
session.phase=InterviewPhase.COMPLETE
assert session.pending_turn
session.pending_turn=session.pending_turn.model_copy(update={"phase":InterviewPhase.COMPLETE,"message":"独立回访测试资料","progress":1.0})
bundle=ChapterCourseCompiler().compile(chapter=chapter,profile=session.profile,decision=PersonalizationPolicy().decide(session.profile,chapter.chapter_id),instance_key=session.session_id)
repo.save_course(session.session_id,bundle)
card=bundle.flashcards[0]
previous=None
for i,days in enumerate([365,357]):
    at=now-timedelta(days=days)
    previous=schedule_review(previous,'good',now=at)
    session.profile.flashcard_reviews[card.card_id]=previous
    repo.save_session_with_event(session,event_id=f'return-fixture-{session.session_id}-{i}',course_id=bundle.course_id,item_id=card.card_id,event_type='flashcard_review',payload_json=json.dumps({'review_state':previous.model_dump(mode='json')}),now=at.timestamp())
assert previous.due_at<now
print(PublicChapterLearningBundle.from_private(bundle).model_dump_json())
