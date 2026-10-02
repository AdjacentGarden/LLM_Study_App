import type { ApiChapter, CourseSourceSummary, ScanResult, StudyPlan } from "../types/api";
import { demoPhysicsChapters, demoPhysicsScan, demoPhysicsStudyPlan, demoPhysicsSummary } from "./demoPhysicsCourse";

type DirectoryChapter = {
  title: string;
  start: number;
  end: number;
  sections: readonly (readonly [title: string, page: number])[];
};
export type DemoDirectoryCourse = {
  summary: CourseSourceSummary;
  chapters: ApiChapter[];
  scan: ScanResult;
  plan: StudyPlan;
  tocPages: number[];
};

function directoryCourse(input: {
  bookId: string; prefix: string; title: string; filename: string; cover: string;
  pages: number; offset: number; tocPages: number[]; hasText: boolean;
  unitLabel?: "章" | "单元";
  chapters: readonly DirectoryChapter[];
  references: readonly (readonly [title: string, start: number, end: number])[];
}): DemoDirectoryCourse {
  function entry(id: string, title: string, start: number, end: number, parentId: string | null = null): ApiChapter {
    return { chapter_id: id, level: parentId ? 2 : 1, source_title: title, ai_title: title,
      page_start: start + input.offset, page_end: end + input.offset,
      printed_page_start: start, printed_page_end: end,
      confidence: 100, status: "已确认", source: "demo-directory", parent_id: parentId };
  }
  const chapters = input.chapters.flatMap((chapter, index) => {
    const id = `${input.prefix}-c${index + 1}`;
    return [entry(id, chapter.title, chapter.start, chapter.end),
      ...chapter.sections.map(([title, start], sectionIndex) => entry(`${id}-s${sectionIndex + 1}`, title, start,
        Math.max(start, (chapter.sections[sectionIndex + 1]?.[1] ?? chapter.end + 1) - 1), id))];
  });
  chapters.push(...input.references.map(([title, start, end], index) => entry(`${input.prefix}-ref${index + 1}`, title, start, end)));
  const summary: CourseSourceSummary = {
    book_id: input.bookId, title: input.title, filename: input.filename,
    cover_url: `/assets/book-covers/${input.cover}.webp`, content_mode: "directory",
    directory_unit_label: input.unitLabel ?? "章", status: "ready", page_count: input.pages,
    chapter_count: input.chapters.length, chunk_count: 0, asset_count: 0, average_confidence: 100,
    next_title: "浏览原书目录", rag_index_status: "unavailable", parse_job_status: "done", parse_job_progress: 100,
    parse_job_message: "演示课程 · 仅包含封面、标题与原书目录", updated_at: 1790870400
  };
  const scan: ScanResult = {
    book_id: input.bookId, filename: input.filename, file_type: "pdf", page_count: input.pages,
    has_text_layer: input.hasText, needs_ocr: !input.hasText, source_unit: "page", quality_warnings: [],
    source_locations: chapters.map((chapter) => ({ index: chapter.page_start, pdf_page: chapter.page_start,
      printed_page: chapter.printed_page_start, confidence: 100, source: "示范文件 · PDF 原书目录", evidence: chapter.source_title }))
  };
  return { summary, chapters, scan, tocPages: input.tocPages,
    plan: { user_id: "local_user", book_id: input.bookId, days: 0, daily_minutes: 0, tasks: [] } };
}

// PDF pages 3-4; printed page 1 is PDF page 5.
export const demoChemistryCourse = directoryCourse({
  bookId: "catalog_chemistry_required_2", prefix: "chemistry", title: "化学 必修 第二册",
  filename: "普通高中教科书 化学 必修 第二册.pdf", cover: "chemistry-required-2", pages: 138,
  offset: 4, tocPages: [3, 4], hasText: false,
  chapters: [
    { title: "第五章 化工生产中的重要非金属元素", start: 1, end: 30, sections: [
      ["第一节 硫及其化合物", 2], ["第二节 氮及其化合物", 11], ["第三节 无机非金属材料", 19],
      ["整理与提升", 26], ["实验活动 4 用化学沉淀法去除粗盐中的杂质离子", 29], ["实验活动 5 不同价态含硫物质的转化", 30]
    ] },
    { title: "第六章 化学反应与能量", start: 31, end: 58, sections: [
      ["第一节 化学反应与能量变化", 32], ["第二节 化学反应的速率与限度", 42], ["整理与提升", 52],
      ["实验活动 6 化学能转化成电能", 56], ["实验活动 7 化学反应速率的影响因素", 57]
    ] },
    { title: "第七章 有机化合物", start: 59, end: 96, sections: [
      ["第一节 认识有机化合物", 60], ["第二节 乙烯与有机高分子材料", 67], ["第三节 乙醇与乙酸", 77],
      ["第四节 基本营养物质", 83], ["整理与提升", 91],
      ["实验活动 8 搭建球棍模型认识有机化合物分子结构的特点", 95], ["实验活动 9 乙醇、乙酸的主要性质", 96]
    ] },
    { title: "第八章 化学与可持续发展", start: 97, end: 125, sections: [
      ["第一节 自然资源的开发利用", 98], ["第二节 化学品的合理使用", 107], ["第三节 环境保护与绿色化学", 117], ["整理与提升", 123]
    ] }
  ],
  references: [["附录Ⅰ 名词索引", 126, 126], ["附录Ⅱ 部分酸、碱和盐的溶解性表（室温）", 127, 127],
    ["附录Ⅲ 一些常见元素的中英文名称对照表", 128, 128], ["附录Ⅳ 相对原子质量表", 129, 129]]
});

// PDF pages 5-6 and each unit's original section headings; printed page 1 is PDF page 8.
const englishUnits = ["Festivals and Celebrations", "Morals and Virtues", "Diverse Cultures", "Space Exploration", "The Value of Money"];
export const demoEnglishCourse = directoryCourse({
  bookId: "catalog_english_required_3", prefix: "english", title: "英语 必修 第三册",
  filename: "普通高中教科书 英语 必修 第三册.pdf", cover: "english-required-3", pages: 130,
  offset: 7, tocPages: [5, 6], hasText: true, unitLabel: "单元",
  chapters: englishUnits.map((title, index) => {
    const start = 1 + index * 12;
    return { title: `Unit ${index + 1} ${title}`, start, end: start + 11, sections: [
      ["Listening and Speaking", start + 1], ["Reading and Thinking", start + 3],
      ["Discovering Useful Structures", start + 5], [index === 4 ? "Viewing and Talking" : "Listening and Talking", start + 6],
      ["Reading for Writing", start + 7], ["Assessing Your Progress", start + 9], ["Project", start + 10], ["Video Time", start + 11]
    ] };
  }),
  references: [...englishUnits.map((title, index) => [`Workbook Unit ${index + 1} ${title}`, 61 + index * 6, 66 + index * 6] as const),
    ["Notes", 91, 97], ["Grammar", 98, 101], ["Words and Expressions in Each Unit", 102, 108],
    ["Vocabulary", 109, 116], ["Irregular Verbs", 117, 118]]
});

// PDF pages 11-15 and embedded PDF bookmarks; printed page 1 is PDF page 16.
export const demoCalculusCourse = directoryCourse({
  bookId: "catalog_advanced_mathematics_1", prefix: "calculus", title: "高等数学 上册（第七版）",
  filename: "高等数学·上册 第七版.pdf", cover: "advanced-mathematics-1", pages: 442,
  offset: 15, tocPages: [11, 12, 13, 14, 15], hasText: false,
  chapters: [
    { title: "第一章 函数与极限", start: 1, end: 72, sections: [
      ["第一节 映射与函数", 1], ["第二节 数列的极限", 18], ["第三节 函数的极限", 27], ["第四节 无穷小与无穷大", 34],
      ["第五节 极限运算法则", 38], ["第六节 极限存在准则 两个重要极限", 45], ["第七节 无穷小的比较", 52],
      ["第八节 函数的连续性与间断点", 56], ["第九节 连续函数的运算与初等函数的连续性", 62], ["第十节 闭区间上连续函数的性质", 66]
    ] },
    { title: "第二章 导数与微分", start: 73, end: 124, sections: [
      ["第一节 导数概念", 73], ["第二节 函数的求导法则", 84], ["第三节 高阶导数", 96],
      ["第四节 隐函数及由参数方程所确定的函数的导数 相关变化率", 101], ["第五节 函数的微分", 110]
    ] },
    { title: "第三章 微分中值定理与导数的应用", start: 125, end: 183, sections: [
      ["第一节 微分中值定理", 125], ["第二节 洛必达法则", 132], ["第三节 泰勒公式", 137],
      ["第四节 函数的单调性与曲线的凹凸性", 144], ["第五节 函数的极值与最大值最小值", 152],
      ["第六节 函数图形的描绘", 163], ["第七节 曲率", 168], ["第八节 方程的近似解", 177]
    ] },
    { title: "第四章 不定积分", start: 184, end: 223, sections: [
      ["第一节 不定积分的概念与性质", 184], ["第二节 换元积分法", 193], ["第三节 分部积分法", 208],
      ["第四节 有理函数的积分", 213], ["第五节 积分表的使用", 219]
    ] },
    { title: "第五章 定积分", start: 224, end: 273, sections: [
      ["第一节 定积分的概念与性质", 224], ["第二节 微积分基本公式", 237], ["第三节 定积分的换元法和分部积分法", 246],
      ["第四节 反常积分", 256], ["第五节 反常积分的审敛法 Γ 函数", 262]
    ] },
    { title: "第六章 定积分的应用", start: 274, end: 296, sections: [
      ["第一节 定积分的元素法", 274], ["第二节 定积分在几何学上的应用", 276], ["第三节 定积分在物理学上的应用", 289]
    ] },
    { title: "第七章 微分方程", start: 297, end: 362, sections: [
      ["第一节 微分方程的基本概念", 297], ["第二节 可分离变量的微分方程", 302], ["第三节 齐次方程", 308],
      ["第四节 一阶线性微分方程", 314], ["第五节 可降阶的高阶微分方程", 321], ["第六节 高阶线性微分方程", 329],
      ["第七节 常系数齐次线性微分方程", 338], ["第八节 常系数非齐次线性微分方程", 347],
      ["第九节 欧拉方程", 355], ["第十节 常系数线性微分方程组解法举例", 357]
    ] }
  ],
  references: [["附录Ⅰ 二阶和三阶行列式简介", 363, 367], ["附录Ⅱ 基本初等函数的图形", 368, 370],
    ["附录Ⅲ 几种常用的曲线", 371, 373], ["附录Ⅳ 积分表", 374, 384], ["习题答案与提示", 385, 385]]
});

export const demoDirectoryCourses: readonly DemoDirectoryCourse[] = [
  { summary: demoPhysicsSummary, chapters: demoPhysicsChapters, scan: demoPhysicsScan, plan: demoPhysicsStudyPlan, tocPages: [3] },
  demoChemistryCourse, demoEnglishCourse, demoCalculusCourse
];
const byBookId = new Map(demoDirectoryCourses.map((course) => [course.summary.book_id, course]));
export function demoDirectoryCourseFor(bookId: string) { return byBookId.get(bookId); }
