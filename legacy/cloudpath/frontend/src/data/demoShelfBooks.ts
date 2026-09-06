import type { HomeBookCatalogItem } from "../screens/homeBookModel";

/**
 * Display-only shelf entries sourced from the local demonstration files.
 *
 * These records deliberately contain no parsed pages, chapters, or study data.
 * The current biology course is matched by id so it receives its original cover;
 * the remaining records stay as catalog previews until the user imports a book.
 */
export const demoShelfBooks: readonly HomeBookCatalogItem[] = [
  {
    bookId: "catalog_high_school_math_required_2",
    title: "高中数学 必修 第二册",
    filename: "高中数学必修第二册.pdf",
    coverUrl: "/assets/book-covers/high-school-math-required-2.webp"
  },
  {
    bookId: "book_biology_2",
    title: "生物 必修 2 遗传与进化",
    filename: "人教版高中生物必修2遗传与进化.pdf",
    coverUrl: "/assets/book-covers/biology-required-2.webp"
  },
  {
    bookId: "catalog_physics_required_3",
    title: "物理 必修 第三册",
    filename: "普通高中教科书 物理 必修 第三册.pdf",
    coverUrl: "/assets/book-covers/physics-required-3.webp"
  },
  {
    bookId: "catalog_chemistry_required_2",
    title: "化学 必修 第二册",
    filename: "普通高中教科书化学 必修 第二册.pdf",
    coverUrl: "/assets/book-covers/chemistry-required-2.webp"
  },
  {
    bookId: "catalog_english_required_3",
    title: "英语 必修 第三册",
    filename: "普通高中教科书·英语·必修第三册.pdf",
    coverUrl: "/assets/book-covers/english-required-3.webp"
  },
  {
    bookId: "catalog_advanced_mathematics_1",
    title: "高等数学 上册（第七版）",
    filename: "高等数学·上册 第七版.pdf",
    coverUrl: "/assets/book-covers/advanced-mathematics-1.webp"
  },
  {
    bookId: "catalog_theoretical_mechanics_1",
    title: "理论力学Ⅰ（第8版）",
    filename: "理论力学Ⅰ(第8版).pdf",
    coverUrl: "/assets/book-covers/theoretical-mechanics-1.webp"
  },
  {
    bookId: "catalog_micro_psychology_set",
    title: "微表情·微动作·微反应心理学（套装三册）",
    filename: "微表情心理学 微动作心理学 微反应心理学（套装三册）.azw3",
    coverUrl: "/assets/book-covers/micro-psychology-set.webp"
  }
] as const;
