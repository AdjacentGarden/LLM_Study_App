import type { ApiChapter, CourseSourceSummary, ScanResult, StudyPlan } from "../types/api";

export const demoPhysicsBookId = "catalog_physics_required_3";

// Transcribed from PDF page 3 of the user's 140-page demonstration textbook.
// Printed page 1 is PDF page 5; this fixture includes the directory only.
const pdfPageOffset = 4;
const directory = [
  { number: 9, title: "静电场及其应用", start: 1, end: 24, sections: [
    ["电荷", 2], ["库仑定律", 6], ["电场 电场强度", 11], ["静电的防止与利用", 18]
  ] },
  { number: 10, title: "静电场中的能量", start: 25, end: 51, sections: [
    ["电势能和电势", 26], ["电势差", 31], ["电势差与电场强度的关系", 35],
    ["电容器的电容", 38], ["带电粒子在电场中的运动", 44]
  ] },
  { number: 11, title: "电路及其应用", start: 52, end: 77, sections: [
    ["电源和电流", 53], ["导体的电阻", 57], ["实验：导体电阻率的测量", 62],
    ["串联电路和并联电路", 68], ["实验：练习使用多用电表", 72]
  ] },
  { number: 12, title: "电能 能量守恒定律", start: 78, end: 102, sections: [
    ["电路中的能量转化", 79], ["闭合电路的欧姆定律", 83],
    ["实验：电池电动势和内阻的测量", 89], ["能源与可持续发展", 93]
  ] },
  { number: 13, title: "电磁感应与电磁波初步", start: 103, end: 129, sections: [
    ["磁场 磁感线", 104], ["磁感应强度 磁通量", 109], ["电磁感应现象及应用", 114],
    ["电磁波的发现及应用", 119], ["能量量子化", 124]
  ] }
] as const;

function entry(id: string, title: string, start: number, end: number, parentId: string | null = null): ApiChapter {
  return {
    chapter_id: id,
    level: parentId ? 2 : 1,
    source_title: title,
    ai_title: title,
    page_start: start + pdfPageOffset,
    page_end: end + pdfPageOffset,
    printed_page_start: start,
    printed_page_end: end,
    confidence: 100,
    status: "已确认",
    source: "示范文件 · PDF 原书目录",
    parent_id: parentId
  };
}

export const demoPhysicsChapters: ApiChapter[] = [
  ...directory.flatMap((chapter) => {
    const id = `physics-c${chapter.number}`;
    return [
      entry(id, `第 ${chapter.number} 章 ${chapter.title}`, chapter.start, chapter.end),
      ...chapter.sections.map(([title, start], index) => entry(
        `${id}-s${index + 1}`,
        `第 ${index + 1} 节 ${title}`,
        start,
        (chapter.sections[index + 1]?.[1] ?? chapter.end + 1) - 1,
        id
      ))
    ];
  }),
  entry("physics-research", "课题研究", 130, 134),
  entry("physics-index", "索引", 135, 135)
];

export const demoPhysicsSummary: CourseSourceSummary = {
  book_id: demoPhysicsBookId,
  title: "物理 必修 第三册",
  filename: "普通高中教科书 物理 必修 第三册.pdf",
  cover_url: "/assets/book-covers/physics-required-3.webp",
  content_mode: "directory",
  status: "ready",
  page_count: 140,
  chapter_count: directory.length,
  chunk_count: 0,
  asset_count: 0,
  average_confidence: 100,
  next_title: "浏览原书目录",
  rag_index_status: "unavailable",
  parse_job_status: "done",
  parse_job_progress: 100,
  parse_job_message: "演示课程 · 仅包含封面、标题与原书目录",
  updated_at: 1790870400
};

export const demoPhysicsScan: ScanResult = {
  book_id: demoPhysicsBookId,
  filename: demoPhysicsSummary.filename!,
  file_type: "pdf",
  page_count: demoPhysicsSummary.page_count,
  has_text_layer: false,
  needs_ocr: true,
  source_unit: "page",
  source_locations: demoPhysicsChapters.map((chapter) => ({
    index: chapter.page_start,
    pdf_page: chapter.page_start,
    printed_page: chapter.printed_page_start,
    confidence: chapter.confidence,
    source: chapter.source,
    evidence: chapter.source_title
  })),
  quality_warnings: []
};

export const demoPhysicsStudyPlan: StudyPlan = {
  user_id: "local_user",
  book_id: demoPhysicsBookId,
  days: 0,
  daily_minutes: 0,
  tasks: []
};
