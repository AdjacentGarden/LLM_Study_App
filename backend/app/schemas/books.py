from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QualityWarning(BaseModel):
    page: int | None = None
    code: str
    message: str


class ScanResult(BaseModel):
    book_id: str
    filename: str
    file_type: str
    page_count: int
    has_text_layer: bool
    needs_ocr: bool
    # ``page_count`` remains the backwards-compatible logical unit count.
    # The unit explicitly distinguishes PDF pages from slides, sheets, image
    # inputs, and DOCX's non-paginated document structure.
    source_unit: str = "page"
    source_locations: list[dict[str, object]] = Field(default_factory=list)
    quality_warnings: list[QualityWarning] = Field(default_factory=list)


class TextBlock(BaseModel):
    block_id: str
    page: int
    type: str
    text: str
    bbox: list[float] | None = None
    font_size: float | None = None
    confidence: float | None = None
    low_confidence: bool = False
    heading_level: int | None = None
    source_parser: str | None = None
    source_block_id: str | None = None
    content_format: str | None = None
    content_hash: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class LayoutRegion(BaseModel):
    region_id: str
    page: int
    type: str
    bbox: list[float]
    confidence: float
    source: str


class PageResult(BaseModel):
    page: int
    pdf_page_index: int
    text: str
    needs_ocr: bool = False
    blocks: list[TextBlock] = Field(default_factory=list)
    ocr_provider: str | None = None
    ocr_confidence: float | None = None
    quality_warnings: list[QualityWarning] = Field(default_factory=list)
    preprocessed_image_url: str | None = None
    layout_regions: list[LayoutRegion] = Field(default_factory=list)
    parser: str | None = None
    quality_score: float | None = None
    source_width: float | None = None
    source_height: float | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class JobStatusResponse(BaseModel):
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    message: str | None = None
    error: str | None = None


class Chapter(BaseModel):
    chapter_id: str
    level: int
    source_title: str
    ai_title: str
    page_start: int
    page_end: int
    confidence: int
    status: str
    source: str
    parent_id: str | None = None


class ChapterUpdate(BaseModel):
    source_title: str | None = None
    ai_title: str | None = None
    level: int | None = None
    parent_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    status: str | None = None


class TocPageCandidate(BaseModel):
    page: int
    score: int
    line_count: int
    reasons: list[str] = Field(default_factory=list)
    sample_lines: list[str] = Field(default_factory=list)


class PageMapEntry(BaseModel):
    pdf_page: int
    printed_page: int | None = None
    confidence: int
    source: str
    evidence: str | None = None


class ChapterEvidence(BaseModel):
    chapter_id: str
    source_title: str
    level: int
    printed_page_start: int | None = None
    pdf_page_start: int
    pdf_page_end: int
    toc_line_confidence: int
    page_map_confidence: int
    title_match_page: int | None = None
    title_match_score: int
    confidence: int
    status: str
    reasons: list[str] = Field(default_factory=list)


class TocAnalysis(BaseModel):
    book_id: str
    status: str
    toc_pages: list[TocPageCandidate] = Field(default_factory=list)
    page_map: list[PageMapEntry] = Field(default_factory=list)
    chapter_evidence: list[ChapterEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ChapterConfirmRequest(BaseModel):
    chapters: list[Chapter] | None = None


class CourseSummary(BaseModel):
    book_id: str
    title: str
    filename: str | None = None
    status: str
    page_count: int = 0
    chapter_count: int = 0
    chunk_count: int = 0
    asset_count: int = 0
    average_confidence: int = 0
    next_title: str | None = None
    rag_index_status: str | None = None
    rag_index_provider: str | None = None
    rag_index_generation: int | None = None
    rag_fallback_reason: str | None = None
    parse_job_id: str | None = None
    parse_job_status: str | None = None
    parse_job_stage: str | None = None
    parse_job_progress: int | None = None
    parse_job_message: str | None = None
    parse_job_error: str | None = None
    updated_at: float


class Asset(BaseModel):
    asset_id: str
    book_id: str
    chapter_id: str | None = None
    source_type: str
    page: int | None = None
    type: str
    caption: str
    bbox: list[float] | None = None
    image_url: str
    thumbnail_url: str
    source_page_image_url: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    generation_provider: str | None = None
    generation_prompt: str | None = None
    review_status: str | None = None
    source_parser: str | None = None
    content_hash: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class AssetPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    asset_id: str
    book_id: str
    chapter_id: str | None = None
    source_type: str
    page: int | None = None
    type: str
    caption: str
    bbox: list[float] | None = None
    image_url: str
    thumbnail_url: str
    source_page_image_url: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    generation_provider: str | None = None
    review_status: str | None = None
    source_parser: str | None = None
    content_hash: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    book_id: str
    chapter_id: str
    page_start: int
    page_end: int
    content_type: str
    text: str
    asset_ids: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    # Chunk V2 provenance and quality fields are optional so artifacts written
    # before the V2 rollout remain valid without a data migration.
    parser: str | None = None
    parser_version: str | None = None
    chunk_version: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    source_block_ids: list[str] = Field(default_factory=list)
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    token_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    bbox: list[float] | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class LessonCitation(BaseModel):
    chunk_id: str
    page_start: int
    page_end: int
    quote: str | None = None


class LessonBlock(BaseModel):
    block_id: str
    block_type: str
    title: str
    content: str
    citations: list[LessonCitation] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    ai_generated: bool = False


class Lesson(BaseModel):
    book_id: str
    lesson_id: str
    chapter_id: str
    title: str
    source_title: str
    page_start: int
    page_end: int
    lesson_kind: str = "lesson"
    status: str = "ready"
    confidence: int = 0
    objectives: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    summary: str = ""
    blocks: list[LessonBlock] = Field(default_factory=list)
    source_chunk_ids: list[str] = Field(default_factory=list)
    asset_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LessonBuildRequest(BaseModel):
    chapter_ids: list[str] | None = None
    force: bool = False


class LessonBuildChapterResult(BaseModel):
    chapter_id: str
    chapter_title: str
    status: str
    reason: str | None = None
    lesson_kind: str | None = None
    lesson_id: str | None = None
    message: str | None = None


class LessonBuildJobResponse(BaseModel):
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    lessons: list[Lesson] = Field(default_factory=list)
    chapter_results: list[LessonBuildChapterResult] = Field(default_factory=list)
    error: str | None = None


class Flashcard(BaseModel):
    card_id: str
    book_id: str
    lesson_id: str
    chapter_id: str
    front: str
    back: str
    concept: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    page_start: int
    page_end: int
    due: str = "today"
    mastery: int = 0
    reason: str = ""


class QuizQuestion(BaseModel):
    question_id: str
    book_id: str
    lesson_id: str
    chapter_id: str
    prompt: str
    choices: list[str] = Field(default_factory=list)
    answer: str
    explanation: str
    concept: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    page_start: int
    page_end: int


class ChapterSourceWindow(BaseModel):
    window_id: str
    page_start: int
    page_end: int
    source_chunk_ids: list[str] = Field(default_factory=list)
    text: str
    plain_text: str | None = None


class ChapterSourcePackage(BaseModel):
    book_id: str
    chapter_id: str
    chapter_title: str
    page_start: int
    page_end: int
    chapter_confidence: float = 0
    chunk_count: int
    text_length: int
    source_chunk_ids: list[str] = Field(default_factory=list)
    windows: list[ChapterSourceWindow] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ImageGenerationRequest(BaseModel):
    book_id: str
    lesson_id: str | None = None
    chapter_id: str | None = None
    concepts: list[str] = Field(default_factory=list)
    style: str = "clean_educational_diagram"
    purpose: str
    source_chunk_ids: list[str] = Field(min_length=1)


class ImageGenerationJobResponse(BaseModel):
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    asset: AssetPublic | None = None
    error: str | None = None


class Citation(BaseModel):
    chapter_id: str
    chapter_title: str
    page: int
    chunk_id: str
    quote: str
    score: float = 0
    retrieval_method: str = "rrf"
    source_type: str = "text"
    location_type: str = "page"
    location_label: str | None = None
    source_metadata: dict = Field(default_factory=dict)


class RagQuery(BaseModel):
    book_id: str
    chapter_id: str | None = None
    question: str = Field(min_length=1)
    history: list[dict] = Field(default_factory=list)


class RagResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    related_assets: list[AssetPublic] = Field(default_factory=list)
    confidence: str


class AssignmentSubmitRequest(BaseModel):
    user_id: str = "anonymous"
    book_id: str
    lesson_id: str | None = None
    chapter_id: str | None = None
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)


class AssignmentSubmitResponse(BaseModel):
    assignment_id: str
    submission_id: str
    status: str


class DiagnosisResponse(BaseModel):
    assignment_id: str
    submission_id: str
    result: str
    stuck_point: str
    knowledge_points: list[str] = Field(default_factory=list)
    review_citations: list[Citation] = Field(default_factory=list)
    related_assets: list[AssetPublic] = Field(default_factory=list)
    hint: str
    needs_followup: bool = False
    followup_question: str | None = None
    mistake_recorded: bool = False


class MistakeRecord(BaseModel):
    mistake_id: str
    user_id: str = "anonymous"
    book_id: str
    assignment_id: str
    question: str
    answer: str
    stuck_point: str
    knowledge_points: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)


class StudyTask(BaseModel):
    task_id: str
    user_id: str = "anonymous"
    book_id: str | None = None
    day: int
    title: str
    task_type: str
    minutes: int
    lesson_id: str | None = None
    review_target: str | None = None
    status: str = "pending"
    score: int | None = None
    weak_points: list[str] = Field(default_factory=list)
    adjustment_reason: str | None = None


class StudyPlan(BaseModel):
    user_id: str = "anonymous"
    book_id: str
    days: int
    daily_minutes: int
    tasks: list[StudyTask]


class StudyPlanRequest(BaseModel):
    user_id: str = "anonymous"
    days: int = Field(default=14, ge=1, le=90)
    daily_minutes: int = Field(default=30, ge=5, le=240)


class StudyTaskUpdate(BaseModel):
    status: str | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    weak_points: list[str] = Field(default_factory=list)


class LearningState(BaseModel):
    user_id: str
    completed_tasks: int
    pending_tasks: int
    average_score: float | None = None
    weak_points: list[str] = Field(default_factory=list)
    mistake_count: int = 0
