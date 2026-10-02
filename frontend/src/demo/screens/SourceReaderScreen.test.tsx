import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { emptyCourseState } from "../features/courses/model";
import {
  SourceReaderScreen,
  nextSourcePage,
  previousSourcePage
} from "./SourceReaderScreen";

const contextMock = vi.hoisted(() => vi.fn());

vi.mock("../context/AppContext", () => ({
  useAppContext: contextMock
}));

vi.mock("../context/BookCourseRepositoryContext", () => ({
  useBookCourseRepository: () => ({ queryRag: vi.fn() })
}));

vi.mock("../features/credits/creditStore", () => ({
  creditCosts: { chat: 10 },
  useCredits: () => ({ balance: 100, reserve: vi.fn(), complete: vi.fn(), refund: vi.fn() })
}));

vi.mock("../components/ui", async () => {
  const React = await import("react");
  return {
    Button: ({ children, ...props }: { children: React.ReactNode }) => React.createElement("button", props, children),
    Card: ({ children, ...props }: { children: React.ReactNode }) => React.createElement("section", props, children),
    Pill: ({ children }: { children: React.ReactNode }) => React.createElement("span", null, children)
  };
});

vi.mock("../motion", async () => {
  const React = await import("react");
  return {
    SkeletonReveal: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
    globalMotionFallbackMs: 900,
    useImageMotion: () => ({
      imageRef: { current: null },
      onError: vi.fn(),
      onLoad: vi.fn(),
      settleAnimation: vi.fn(),
      state: "ready"
    }),
    useLocalMotionItem: (motionKey: string) => ({
      attributes: {},
      motionKey,
      state: "idle"
    }),
    useMotionPresence: ({ requested }: { requested: unknown }) => ({
      rendered: requested ?? null,
      state: "idle",
      presenceId: 0,
      onAnimationCancel: vi.fn(),
      onAnimationEnd: vi.fn()
    }),
    useReducedMotion: () => false
  };
});

function renderSourceReader({
  title,
  page,
  sourceText,
  chapters,
  locationLabel,
  bookId = "book_biology_2"
}: {
  title: string;
  page: number;
  sourceText: string;
  chapters: Array<{ source_title: string; page_start: number; page_end: number }>;
  locationLabel: string;
  bookId?: string;
}) {
  contextMock.mockReturnValue({
    courses: { state: { ...emptyCourseState(), activeCourseId: "course-test" } },
    back: vi.fn(),
    go: vi.fn(),
    parsedChapters: chapters.map((chapter, index) => ({
      ...chapter,
      chapter_id: `chapter-${index}`,
      ai_title: chapter.source_title,
      level: 1,
      confidence: 1,
      status: "ready",
      source: "fixture"
    })),
    parsedScanResult: {
      book_id: bookId,
      filename: "人教版高中生物必修2.pdf",
      file_type: "pdf",
      page_count: 125,
      has_text_layer: true,
      needs_ocr: false,
      source_unit: "page",
      source_locations: [{ index: page, label: locationLabel }],
      quality_warnings: []
    },
    sourcePageTarget: {
      bookId,
      title,
      pageStart: page,
      pageEnd: page,
      sourceText,
      previewImageUrl:bookId === "book_biology_2" ? `/api/books/${bookId}/pages/${page}/image` : undefined
    },
    sourceReaderCurrentPage: page,
    setSourceReaderCurrentPage: vi.fn(),
    startNote: vi.fn(),
    showToast: vi.fn(),
    uploadedFile: {
      bookId,
      name: "人教版高中生物必修2.pdf"
    }
  });

  return renderToStaticMarkup(<SourceReaderScreen />);
}

afterEach(() => {
  contextMock.mockReset();
  vi.restoreAllMocks();
});

describe("SourceReaderScreen citation headings", () => {
  it("shows the chapter heading and a single page number above the page tools", () => {
    const markup = renderSourceReader({
      title: "教材前言与目录",
      page: 3,
      sourceText: "本书目录与使用说明。",
      chapters: [{
        source_title: "教材封面、前言与目录",
        page_start: 1,
        page_end: 9
      }],
      locationLabel: "PDF 第 3 页 · 目录"
    });

    expect(markup).toContain("教材封面、前言与目录");
    expect(markup).not.toContain("第 1 章");
    expect(markup).toContain("第 3 页");
    expect(markup).not.toContain("PDF 第 3 页");
  });

  it("prefers the chapter title over a citation title", () => {
    const markup = renderSourceReader({
      title: "本节导读",
      page: 40,
      sourceText: "赫尔希和蔡斯的实验表明 DNA 是遗传物质。",
      chapters: [{
        source_title: "第 3 章 基因的本质",
        page_start: 40,
        page_end: 40
      }],
      locationLabel: "PDF 第 40 页 · 第 3 章 基因的本质"
    });

    expect(markup).toContain("第 3 章 基因的本质");
    expect(markup).not.toContain("PDF 第 40 页");
    expect(markup).not.toContain("保存草稿");
    expect(markup).not.toContain("完成并整理");
  });
});

describe("SourceReaderScreen AI region entry", () => {
  function readerToolbar() {
    const markup = renderSourceReader({
      title: "第 1 节 减数分裂和受精作用",
      page: 16,
      sourceText: "减数分裂是进行有性生殖的生物产生成熟生殖细胞时进行的染色体数目减半的细胞分裂。",
      chapters: [{ source_title: "第 1 节 减数分裂和受精作用", page_start: 16, page_end: 17 }],
      locationLabel: "PDF 第 16 页 · 第 1 节"
    });
    const start = markup.indexOf("source-reader-topbar");
    return markup.slice(start, markup.indexOf("source-reader-workspace", start));
  }

  it("leads the note toolbar with the region-ask star before every other tool", () => {
    const toolbar = readerToolbar();
    expect(toolbar).toContain('class="source-reader-ai-entry"');
    expect(toolbar).toContain('aria-label="圈选问 AI"');
    expect(toolbar).toContain('aria-pressed="false"');
    expect(toolbar.indexOf("source-reader-ai-entry"))
      .toBeLessThan(toolbar.indexOf("source-reader-note-shortcuts"));
    expect(toolbar.indexOf("source-reader-ai-entry"))
      .toBeLessThan(toolbar.indexOf("ink-annotation-tools"));
  });

  it("keeps the page in reading mode until the star is pressed", () => {
    const markup = renderSourceReader({
      title: "第 1 节 减数分裂和受精作用",
      page: 16,
      sourceText: "减数分裂是进行有性生殖的生物产生成熟生殖细胞时进行的染色体数目减半的细胞分裂。",
      chapters: [{ source_title: "第 1 节 减数分裂和受精作用", page_start: 16, page_end: 17 }],
      locationLabel: "PDF 第 16 页 · 第 1 节"
    });
    expect(markup).toContain('data-note-mode="read"');
    expect(markup).toContain('data-region-mode="false"');
    expect(markup).not.toContain("source-region-hint");
    expect(markup).not.toContain('data-region-ai="open"');
  });
});

describe("SourceReaderScreen authenticated source page navigation", () => {
  it.each([1, 11, 125])("renders the supplied backend full-page image for PDF page %i", (page) => {
    const markup = renderSourceReader({
      title: `PDF 第 ${page} 页`,
      page,
      sourceText: "图片失败时使用的受控教材文字。",
      chapters: [{ source_title: "教材章节", page_start: 1, page_end: 125 }],
      locationLabel: `PDF 第 ${page} 页`
    });

    expect(markup).toContain(`/api/books/book_biology_2/pages/${page}/image`);
    expect(markup).toContain("本地教材原页");
  });

  it("moves backward and forward while respecting the first and last page", () => {
    expect(previousSourcePage(1)).toBe(1);
    expect(previousSourcePage(11)).toBe(10);
    expect(nextSourcePage(11, 125)).toBe(12);
    expect(nextSourcePage(125, 125)).toBe(125);
  });

  it("uses controlled text without inventing an image URL for an unpublished book", () => {
    const markup = renderSourceReader({
      title: "未发布教材引用",
      page: 11,
      sourceText: "本地教材原文回退。",
      chapters: [{ source_title: "未发布教材", page_start: 1, page_end: 20 }],
      locationLabel: "PDF 第 11 页",
      bookId: "book_unknown"
    });

    expect(markup).toContain("本地教材原文回退。");
    expect(markup).not.toContain("/assets/textbook/pages/page_011.jpeg");
  });
});
