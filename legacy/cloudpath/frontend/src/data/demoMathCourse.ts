import type {
  ApiAsset,
  ApiChapter,
  ApiChunk,
  CourseSummary,
  Flashcard,
  Lesson,
  QuizQuestion,
  ScanResult,
  StudyPlan
} from "../types/api";

export const demoMathBookId = "catalog_high_school_math_required_2";

function directoryEntry({
  id,
  level,
  title,
  pageStart,
  pageEnd,
  parentId = null
}: {
  id: string;
  level: number;
  title: string;
  pageStart: number;
  pageEnd: number;
  parentId?: string | null;
}): ApiChapter {
  return {
    chapter_id: id,
    level,
    source_title: title,
    ai_title: title,
    page_start: pageStart,
    page_end: pageEnd,
    printed_page_start: pageStart,
    printed_page_end: pageEnd,
    confidence: 99,
    status: "已确认",
    source: "用户上传目录截图",
    parent_id: parentId
  };
}

export const demoMathChapters: ApiChapter[] = [
  directoryEntry({ id: "math-c6", level: 1, title: "第六章 平面向量及其应用", pageStart: 1, pageEnd: 66 }),
  directoryEntry({ id: "math-c6-s1", level: 2, title: "6.1 平面向量的概念", pageStart: 2, pageEnd: 6, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-reading-1", level: 2, title: "阅读与思考 向量及向量符号的由来", pageStart: 6, pageEnd: 6, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-s2", level: 2, title: "6.2 平面向量的运算", pageStart: 7, pageEnd: 24, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-s3", level: 2, title: "6.3 平面向量基本定理及坐标表示", pageStart: 25, pageEnd: 37, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-s4", level: 2, title: "6.4 平面向量的应用", pageStart: 38, pageEnd: 54, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-reading-2", level: 2, title: "阅读与思考 海伦和秦九韶", pageStart: 55, pageEnd: 56, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-summary", level: 2, title: "小结", pageStart: 57, pageEnd: 58, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-review", level: 2, title: "复习参考题 6", pageStart: 59, pageEnd: 62, parentId: "math-c6" }),
  directoryEntry({ id: "math-c6-explore", level: 2, title: "数学探究 用向量法研究三角形的性质", pageStart: 63, pageEnd: 66, parentId: "math-c6" }),

  directoryEntry({ id: "math-c7", level: 1, title: "第七章 复数", pageStart: 67, pageEnd: 95 }),
  directoryEntry({ id: "math-c7-s1", level: 2, title: "7.1 复数的概念", pageStart: 68, pageEnd: 74, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-s2", level: 2, title: "7.2 复数的四则运算", pageStart: 75, pageEnd: 82, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-reading", level: 2, title: "阅读与思考 代数基本定理", pageStart: 81, pageEnd: 82, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-s3", level: 2, title: "7.3* 复数的三角表示", pageStart: 83, pageEnd: 92, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-explore", level: 2, title: "探究与发现 1 的 n 次方根", pageStart: 91, pageEnd: 92, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-summary", level: 2, title: "小结", pageStart: 93, pageEnd: 93, parentId: "math-c7" }),
  directoryEntry({ id: "math-c7-review", level: 2, title: "复习参考题 7", pageStart: 94, pageEnd: 95, parentId: "math-c7" }),

  directoryEntry({ id: "math-c8", level: 1, title: "第八章 立体几何初步", pageStart: 96, pageEnd: 171 }),
  directoryEntry({ id: "math-c8-s1", level: 2, title: "8.1 基本立体图形", pageStart: 97, pageEnd: 106, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-s2", level: 2, title: "8.2 立体图形的直观图", pageStart: 107, pageEnd: 113, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-reading-1", level: 2, title: "阅读与思考 画法几何与蒙日", pageStart: 112, pageEnd: 113, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-s3", level: 2, title: "8.3 简单几何体的表面积与体积", pageStart: 114, pageEnd: 123, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-explore", level: 2, title: "探究与发现 祖暅原理与柱体、锥体的体积", pageStart: 121, pageEnd: 123, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-s4", level: 2, title: "8.4 空间点、直线、平面之间的位置关系", pageStart: 124, pageEnd: 132, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-s5", level: 2, title: "8.5 空间直线、平面的平行", pageStart: 133, pageEnd: 145, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-s6", level: 2, title: "8.6 空间直线、平面的垂直", pageStart: 146, pageEnd: 164, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-reading-2", level: 2, title: "阅读与思考 欧几里得《原本》与公理化方法", pageStart: 165, pageEnd: 165, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-writing", level: 2, title: "文献阅读与数学写作 几何学的发展", pageStart: 166, pageEnd: 166, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-summary", level: 2, title: "小结", pageStart: 167, pageEnd: 168, parentId: "math-c8" }),
  directoryEntry({ id: "math-c8-review", level: 2, title: "复习参考题 8", pageStart: 169, pageEnd: 171, parentId: "math-c8" }),

  directoryEntry({ id: "math-c9", level: 1, title: "第九章 统计", pageStart: 172, pageEnd: 224 }),
  directoryEntry({ id: "math-c9-s1", level: 2, title: "9.1 随机抽样", pageStart: 173, pageEnd: 191, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-reading-1", level: 2, title: "阅读与思考 如何得到敏感性问题的诚实反应", pageStart: 185, pageEnd: 188, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-tech", level: 2, title: "信息技术应用 统计软件的应用", pageStart: 189, pageEnd: 191, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-s2", level: 2, title: "9.2 用样本估计总体", pageStart: 192, pageEnd: 217, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-reading-2", level: 2, title: "阅读与思考 统计学在军事中的应用——二战时德国坦克总量的估计问题", pageStart: 208, pageEnd: 216, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-reading-3", level: 2, title: "阅读与思考 大数据", pageStart: 217, pageEnd: 217, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-s3", level: 2, title: "9.3 统计案例 公司员工的肥胖情况调查分析", pageStart: 218, pageEnd: 219, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-summary", level: 2, title: "小结", pageStart: 220, pageEnd: 221, parentId: "math-c9" }),
  directoryEntry({ id: "math-c9-review", level: 2, title: "复习参考题 9", pageStart: 222, pageEnd: 224, parentId: "math-c9" }),

  directoryEntry({ id: "math-c10", level: 1, title: "第十章 概率", pageStart: 225, pageEnd: 264 }),
  directoryEntry({ id: "math-c10-s1", level: 2, title: "10.1 随机事件与概率", pageStart: 226, pageEnd: 245, parentId: "math-c10" }),
  directoryEntry({ id: "math-c10-s2", level: 2, title: "10.2 事件的相互独立性", pageStart: 246, pageEnd: 250, parentId: "math-c10" }),
  directoryEntry({ id: "math-c10-s3", level: 2, title: "10.3 频率与概率", pageStart: 251, pageEnd: 260, parentId: "math-c10" }),
  directoryEntry({ id: "math-c10-reading", level: 2, title: "阅读与思考 孟德尔遗传规律", pageStart: 259, pageEnd: 260, parentId: "math-c10" }),
  directoryEntry({ id: "math-c10-summary", level: 2, title: "小结", pageStart: 261, pageEnd: 262, parentId: "math-c10" }),
  directoryEntry({ id: "math-c10-review", level: 2, title: "复习参考题 10", pageStart: 263, pageEnd: 264, parentId: "math-c10" })
];

export const demoMathSummary: CourseSummary = {
  book_id: demoMathBookId,
  title: "数学 必修 第二册",
  filename: "普通高中教科书 数学 必修 第二册 A版.pdf",
  status: "ready",
  page_count: 276,
  chapter_count: 5,
  chunk_count: 0,
  asset_count: 0,
  average_confidence: 99,
  next_title: "第六章 · 平面向量及其应用",
  rag_index_status: "ready",
  rag_index_provider: "toc-screenshot-fixture",
  rag_index_generation: 1,
  parse_job_id: null,
  parse_job_status: "done",
  parse_job_stage: "completed",
  parse_job_progress: 100,
  parse_job_message: "上传目录截图已整理为课程目录",
  parse_job_error: null,
  updated_at: 1786320000
};

export const demoMathScan: ScanResult = {
  book_id: demoMathBookId,
  filename: demoMathSummary.filename ?? demoMathSummary.title,
  file_type: "pdf",
  page_count: demoMathSummary.page_count,
  has_text_layer: true,
  needs_ocr: false,
  source_unit: "page",
  source_locations: demoMathChapters.map((chapter) => ({
    index: chapter.page_start,
    pdf_page: chapter.page_start,
    printed_page: chapter.printed_page_start,
    confidence: chapter.confidence,
    source: "用户上传目录截图",
    evidence: chapter.source_title
  })),
  quality_warnings: []
};

export const demoMathStudyPlan: StudyPlan = {
  user_id: "local_user",
  book_id: demoMathBookId,
  days: 14,
  daily_minutes: 30,
  tasks: []
};

export const demoMathChunks: ApiChunk[] = [];
export const demoMathAssets: ApiAsset[] = [];
export const demoMathLessons: Lesson[] = [];
export const demoMathFlashcards: Flashcard[] = [];
export const demoMathQuizzes: QuizQuestion[] = [];
