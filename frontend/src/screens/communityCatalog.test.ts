import { describe, expect, it } from "vitest";
import type { CommunityBookSummary } from "../types/api";
import { communityCategories, filterCommunityBooks } from "./communityCatalog";

const books: CommunityBookSummary[] = [
  {
    id: "calculus",
    title: "Calculus Made Easy",
    catalog_title: "Calculus Made Easy",
    author: "Silvanus P. Thompson",
    cover: "/cover-a",
    subject: "数学",
    level: "大学",
    language: "English",
    edition: "1914",
    page_count: 292,
    file_size_bytes: 1,
    source_page_url: "https://example.test/calculus",
    license_name: "Public domain",
    license_url: "https://example.test/license",
    rights_notice: "notice",
    description: "微积分教材",
    chapters: [],
    tags: ["微积分", "真实 PDF"],
    server_cached: true
  },
  {
    id: "physics",
    title: "Utility of Quaternions in Physics",
    catalog_title: "Quaternions in Physics",
    author: "Alexander McAulay",
    cover: "/cover-b",
    subject: "物理",
    level: "大学",
    language: "English",
    edition: "1893",
    page_count: 134,
    file_size_bytes: 1,
    source_page_url: "https://example.test/physics",
    license_name: "Public domain",
    license_url: "https://example.test/license",
    rights_notice: "notice",
    description: "数学物理教材",
    chapters: [],
    tags: ["四元数", "真实 PDF"],
    server_cached: true
  }
];

describe("community catalog", () => {
  it("keeps the reference UI category order", () => {
    expect(communityCategories).toEqual(["推荐", "生物", "数学", "物理", "化学", "历史", "地理", "语文", "英语"]);
  });

  it("filters the real backend-shaped catalog by category and searchable metadata", () => {
    expect(filterCommunityBooks(books, "推荐")).toHaveLength(2);
    expect(filterCommunityBooks(books, "数学").map((book) => book.id)).toEqual(["calculus"]);
    expect(filterCommunityBooks(books, "全部", "四元数").map((book) => book.id)).toEqual(["physics"]);
    expect(filterCommunityBooks(books, "物理", "1914")).toEqual([]);
  });
});
