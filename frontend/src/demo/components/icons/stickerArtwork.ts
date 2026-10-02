export type StickerTone = "pink" | "blue" | "mint" | "yellow" | "violet" | "peach";

type ShapeTag = "path" | "rect" | "circle" | "ellipse" | "line";
export type StickerShape = Readonly<{
  tag: ShapeTag;
  attributes: Readonly<Record<string, string | number>>;
}>;

export type StickerArtwork = Readonly<{
  label: string;
  tone: StickerTone;
  shapes: readonly StickerShape[];
}>;

export const stickerPalette = {
  ink: "#252436",
  paper: "#fffdf7",
  blue: "#91c9ff",
  mint: "#9ce2bf",
  pink: "#ffa9c3",
  yellow: "#ffe078",
  violet: "#c2adff",
  peach: "#ffbf92"
} as const;

export const stickerSurfaces: Record<StickerTone, string> = {
  pink: "#ffc9d8",
  blue: "#bcdfff",
  mint: "#b7efd8",
  yellow: "#ffe883",
  violet: "#ddd0ff",
  peach: "#ffd3a0"
};

const { ink, paper, blue, mint, pink, yellow, violet, peach } = stickerPalette;
const path = (d: string, fill = "none", attributes: Record<string, string | number> = {}): StickerShape => ({
  tag: "path", attributes: { d, fill, ...attributes }
});
const rect = (x: number, y: number, width: number, height: number, fill: string, rx = 2, attributes: Record<string, string | number> = {}): StickerShape => ({
  tag: "rect", attributes: { x, y, width, height, rx, fill, ...attributes }
});
const circle = (cx: number, cy: number, r: number, fill: string, attributes: Record<string, string | number> = {}): StickerShape => ({
  tag: "circle", attributes: { cx, cy, r, fill, ...attributes }
});
const line = (x1: number, y1: number, x2: number, y2: number, attributes: Record<string, string | number> = {}): StickerShape => ({
  tag: "line", attributes: { x1, y1, x2, y2, ...attributes }
});
const check = (d = "m7 12 3 3 7-7") => path(d, "none", { className: "sticker-check-path", strokeWidth: 2.2 });

const openBook: readonly StickerShape[] = [
  path("M3 5.5c3.1-1.1 6.1-.6 9 1.5 2.9-2.1 5.9-2.6 9-1.5V19c-3.3-1-6.3-.4-9 1.5C9.3 18.6 6.3 18 3 19Z", blue),
  path("M4.8 3.5c2.8-.3 5.2.5 7.2 2.2 2-1.7 4.4-2.5 7.2-2.2V16c-2.8-.2-5.2.6-7.2 2.3-2-1.7-4.4-2.5-7.2-2.3Z", paper),
  line(12, 5.7, 12, 18.3)
];
const calendar: readonly StickerShape[] = [
  rect(3, 4.5, 18, 16, paper, 3),
  path("M6 4.5h12a3 3 0 0 1 3 3V10H3V7.5a3 3 0 0 1 3-3Z", blue),
  line(7.5, 2.5, 7.5, 6.5), line(16.5, 2.5, 16.5, 6.5)
];
const file: readonly StickerShape[] = [
  path("M5 2.5h9l5 5V20a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 20Z", paper),
  path("M14 2.5v5h5", blue),
  line(8, 11, 15.5, 11), line(8, 14.5, 14.5, 14.5)
];
const clock: readonly StickerShape[] = [
  circle(12, 12, 9, paper),
  path("M12 3a9 9 0 0 1 9 9h-9Z", yellow),
  path("M12 6.5V12l3.5 2"), circle(12, 12, 1, ink, { stroke: "none" })
];

/** Small, filled SVG illustrations. Names match the original icons at migration sites. */
export const stickerArtwork = {
  ClipboardCheck: {
    label: "作业", tone: "pink", shapes: [
      rect(4, 4.5, 16, 17, yellow, 3), rect(6.5, 7, 11, 11.5, paper, 1.2),
      rect(8, 2, 8, 5, mint, 1.5), check("m8.5 12.5 2.5 2.5 4.5-5")
    ]
  },
  Layers3: {
    label: "闪卡", tone: "blue", shapes: [
      rect(4, 4, 16, 16, mint, 2.5, { transform: "rotate(-9 12 12)" }),
      rect(3.5, 4.5, 16, 16, pink, 2.5, { transform: "rotate(6 11.5 12.5)" }),
      rect(4.5, 5, 15, 15, paper, 2.5),
      path("m12 8 1.1 2.8 2.9 1.1-2.9 1.1-1.1 2.8-1.1-2.8L8 11.9l2.9-1.1Z", yellow)
    ]
  },
  BookX: {
    label: "错题", tone: "peach", shapes: [
      rect(4, 3, 15, 18, pink, 2.5), path("M7 3v18"),
      path("M4 18h15v3H6.5A2.5 2.5 0 0 1 4 18Z", paper),
      line(10, 7, 15.5, 12.5), line(15.5, 7, 10, 12.5)
    ]
  },
  NotebookPen: {
    label: "文字笔记", tone: "mint", shapes: [
      rect(3.5, 3, 14, 18, paper, 2.5), rect(3.5, 3, 4, 18, pink, 1.5),
      line(9.5, 7, 14, 7), line(9.5, 10.5, 13, 10.5),
      path("m12 18 1.2-4L19 7.2l2.8 2.5-5.8 6.8Z", blue),
      path("m12 18 4-1.5-2.8-2.5Z", yellow)
    ]
  },
  CalendarDays: {
    label: "学习计划", tone: "violet", shapes: [
      ...calendar, rect(6.5, 13, 4, 4, mint, 1),
      circle(15, 14.5, 1.1, ink, { stroke: "none" }),
      circle(17.8, 17.5, 1.1, ink, { stroke: "none" })
    ]
  },
  CircleAlert: {
    label: "提醒", tone: "pink", shapes: [
      circle(12, 12, 9, yellow), path("M6.5 7a7 7 0 0 1 4-2", "none", { stroke: paper, strokeWidth: 1.5 }),
      line(12, 7, 12, 12.5, { strokeWidth: 2.5 }), circle(12, 16.5, 1.2, ink, { stroke: "none" })
    ]
  },
  Upload: {
    label: "上传资料", tone: "blue", shapes: [
      path("M3 15v4.5A1.5 1.5 0 0 0 4.5 21h15a1.5 1.5 0 0 0 1.5-1.5V15h-5v2H8v-2Z", blue),
      path("m12 2.5-6 6h3.5V15h5V8.5H18Z", mint),
      line(5.5, 18.5, 7, 18.5, { stroke: paper })
    ]
  },
  BookOpenText: {
    label: "开始学习", tone: "mint", shapes: [
      ...openBook, line(7, 7.5, 9.5, 8), line(7, 10.5, 9.5, 11),
      line(14.5, 8, 17, 7.5), line(14.5, 11, 17, 10.5)
    ]
  },
  LibraryBig: {
    label: "课程书库", tone: "violet", shapes: [
      rect(2.5, 5, 5, 15.5, pink, 1), rect(7.5, 3, 5, 17.5, mint, 1),
      path("m13.5 5 4.5-1 3.5 15.5-4.5 1Z", blue),
      line(4, 8, 6, 8), line(9, 6, 11, 6), line(16, 7.5, 18, 7)
    ]
  },
  Check: {
    label: "已完成", tone: "mint", shapes: [
      path("m3 12.5 4-3.5 3.5 4L18 4l3.5 3-11 13Z", mint, { className: "sticker-check-path" })
    ]
  },
  BookOpenCheck: {
    label: "课程学习", tone: "blue", shapes: [
      ...openBook, circle(18, 17.5, 4.5, mint), check("m15.5 17.5 1.7 1.7 3.3-3.5")
    ]
  },
  Sparkles: {
    label: "AI 灵感", tone: "yellow", shapes: [
      path("m10 3 2.3 6.7L19 12l-6.7 2.3L10 21l-2.3-6.7L1 12l6.7-2.3Z", yellow),
      path("m19 2 .9 2.6 2.6.9-2.6.9L19 9l-.9-2.6-2.6-.9 2.6-.9Z", pink, { strokeWidth: 1.3 }),
      circle(20, 19.5, 1.5, blue)
    ]
  },
  BookOpen: { label: "课程", tone: "blue", shapes: openBook },
  Clock3: { label: "学习时间", tone: "peach", shapes: clock },
  FilePlus2: {
    label: "添加文件", tone: "mint", shapes: [
      ...file, circle(17.5, 17.5, 4.5, mint), line(17.5, 15.5, 17.5, 19.5), line(15.5, 17.5, 19.5, 17.5)
    ]
  },
  PenLine: {
    label: "手写批注", tone: "yellow", shapes: [
      path("m4 16 1-4L16 2.5a1.5 1.5 0 0 1 2.1.2l2 2.3a1.5 1.5 0 0 1-.2 2.1L9 17Z", yellow),
      path("m4 16 5 1-5.5 3Z", paper), path("m14 4.2 4 4.6"),
      path("m3.5 20 .5-4 2.7 3Z", ink), line(11, 20.5, 21, 20.5, { stroke: blue, strokeWidth: 2.5 })
    ]
  },
  Mic2: {
    label: "语音笔记", tone: "blue", shapes: [
      rect(8, 2, 8, 13, mint, 4), path("M5.5 11.5v1a6.5 6.5 0 0 0 13 0v-1"),
      line(12, 19, 12, 22), line(8, 22, 16, 22),
      line(10, 5.5, 13, 5.5, { stroke: paper }), line(10, 8.5, 13, 8.5, { stroke: paper })
    ]
  },
  FileText: {
    label: "文字记录", tone: "mint", shapes: [...file, line(8, 18, 12, 18, { stroke: pink, strokeWidth: 2.5 })]
  },
  MessageSquareText: {
    label: "文字批注", tone: "pink", shapes: [
      path("M5 3.5h14a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H9l-5 3v-3H3V5.5a2 2 0 0 1 2-2Z", paper),
      line(7, 8, 17, 8), line(7, 12, 14.5, 12), line(7, 15.5, 11.5, 15.5, { stroke: pink, strokeWidth: 2.5 })
    ]
  },
  CheckCircle2: {
    label: "完成确认", tone: "mint", shapes: [circle(12, 12, 9, mint), check(), path("M6.5 7a7 7 0 0 1 4-2", "none", { stroke: paper, strokeWidth: 1.5 })]
  },
  ListChecks: {
    label: "选择题", tone: "mint", shapes: [
      rect(3, 3, 18, 18, paper, 3), check("m6 8 1.5 1.5L10 6.8"), check("m6 15 1.5 1.5 2.5-2.7"),
      line(13, 8, 17.5, 8), line(13, 15, 17.5, 15)
    ]
  },
  Lightbulb: {
    label: "学习提示", tone: "yellow", shapes: [
      path("M8 17v-1.5a7 7 0 1 1 8 0V17Z", yellow),
      rect(8, 17, 8, 3.5, blue, 1), path("M10 21h4"), path("m9 10 3 2 3-2M12 12v5"),
      path("M7.5 7A5 5 0 0 1 11 5", "none", { stroke: paper })
    ]
  },
  CalendarClock: {
    label: "复习安排", tone: "violet", shapes: [
      ...calendar, circle(16.5, 16.5, 5, yellow), path("M16.5 13.5v3l2 1.3")
    ]
  },
  RotateCcw: {
    label: "回顾错因", tone: "pink", shapes: [
      path("M5.5 8a7.5 7.5 0 1 1-1 8", "none", { strokeWidth: 3.5, stroke: mint }),
      path("M5.5 8a7.5 7.5 0 1 1-1 8"), path("M2.5 3.5v7h7Z", yellow)
    ]
  },
  Target: {
    label: "复习重点", tone: "yellow", shapes: [
      circle(10.5, 13.5, 8, pink), circle(10.5, 13.5, 5, paper), circle(10.5, 13.5, 2, mint),
      path("m10.5 13.5 7-7"), path("m16.5 3 1 3.5 3.5 1 .5-5Z", blue)
    ]
  },
  Search: {
    label: "查找", tone: "blue", shapes: [
      path("m14.5 15 5.5 5.5 2-2-5.5-5.5Z", yellow), circle(10, 10, 7, blue),
      path("M6 10a4 4 0 0 1 4-4", "none", { stroke: paper, strokeWidth: 2 })
    ]
  },
  CalendarCheck2: {
    label: "今日计划", tone: "mint", shapes: [...calendar, check("m7 14 3 3 6-5")]
  },
  Bot: {
    label: "AI 助手", tone: "violet", shapes: [
      line(12, 4, 12, 7), circle(12, 3, 1.5, yellow),
      rect(2, 10, 3, 6, violet, 1.2), rect(19, 10, 3, 6, violet, 1.2), rect(4, 7, 16, 13, blue, 4),
      circle(8.5, 12, 1.2, ink, { stroke: "none" }), circle(15.5, 12, 1.2, ink, { stroke: "none" }),
      circle(6.8, 15.3, 1.1, pink, { stroke: "none" }), circle(17.2, 15.3, 1.1, pink, { stroke: "none" }),
      path("M9 15.5q3 2.5 6 0", "none", { strokeWidth: 1.5 })
    ]
  },
  User: {
    label: "我的消息", tone: "peach", shapes: [
      path("M3.5 21v-1.5A6.5 6.5 0 0 1 10 13h4a6.5 6.5 0 0 1 6.5 6.5V21Z", violet),
      circle(12, 8.5, 5.5, peach), path("M6.5 8.5a5.5 5.5 0 1 1 11-1c-3 .5-5.4-1.5-5.8-3-1 2.1-2.8 3.5-5.2 4Z", paper),
      circle(10, 9, .6, ink, { stroke: "none" }), circle(14, 9, .6, ink, { stroke: "none" }), path("M10.5 11.5q1.5 1 3 0", "none", { strokeWidth: 1.3 })
    ]
  },
  AlertTriangle: {
    label: "冲突警告", tone: "peach", shapes: [
      path("M10.3 3.7a2 2 0 0 1 3.4 0L22 18a2 2 0 0 1-1.7 3H3.7A2 2 0 0 1 2 18Z", yellow),
      line(12, 8, 12, 13.5, { strokeWidth: 2.5 }), circle(12, 17.2, 1.2, ink, { stroke: "none" })
    ]
  },
  BrainCircuit: {
    label: "核心解释", tone: "pink", shapes: [
      path("M12 5a3.5 3.5 0 0 0-6.5-1.5A4 4 0 0 0 3 10a4 4 0 0 0 1 7.5A4 4 0 0 0 12 19Z", pink),
      path("M12 5a3.5 3.5 0 0 1 6.5-1.5A4 4 0 0 1 21 10a4 4 0 0 1-1 7.5A4 4 0 0 1 12 19Z", mint),
      path("M7 6q3 0 3 3M4.5 10q3-1 4 2M7 18q-1-3 2-4M17 6q-3 0-3 3M19.5 10q-3-1-4 2M17 18q1-3-2-4")
    ]
  },
  TrendingUp: {
    label: "能力提升", tone: "mint", shapes: [
      rect(3, 15, 4, 6, blue, 1), rect(9, 12, 4, 9, violet, 1), rect(15, 9, 4, 12, pink, 1),
      path("m3.5 11 5-5 4.5 3L20.5 2", "none", { strokeWidth: 2.2 }),
      path("M15.5 2h5v5", "none", { strokeWidth: 2.2 })
    ]
  },
  Heart: {
    label: "日常兴趣", tone: "pink", shapes: [
      path("M12 20.5 3.7 12.2a5.2 5.2 0 0 1 7.4-7.3l.9.9.9-.9a5.2 5.2 0 0 1 7.4 7.3Z", pink),
      path("M5.5 8a2.5 2.5 0 0 1 2.8-2.5", "none", { stroke: paper, strokeWidth: 1.8 }),
      path("m19 14 .9 2.6 2.6.9-2.6.9L19 21l-.9-2.6-2.6-.9 2.6-.9Z", yellow, { strokeWidth: 1.3 })
    ]
  }
} as const satisfies Record<string, StickerArtwork>;

export type StickerIconName = keyof typeof stickerArtwork;
