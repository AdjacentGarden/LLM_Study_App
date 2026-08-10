import { describe, expect, it } from "vitest";

import { communityBooks } from "../data/mockBook";
import {
  communityCategories,
  filterCommunityBooks,
  resolveCommunityBook
} from "./communityCatalog";

describe("community catalog", () => {
  it("exposes the stable category order used by the community filter", () => {
    expect(communityCategories).toEqual([
      "推荐",
      "生物",
      "数学",
      "物理",
      "化学",
      "历史",
      "地理",
      "语文",
      "英语"
    ]);
  });

  it("filters books by subject without changing their catalog order", () => {
    expect(filterCommunityBooks(communityBooks, "推荐").map((book) => book.id)).toEqual([
      "community_genetics",
      "community_functions",
      "community_higher_mathematics",
      "community_high_school_mathematics_2",
      "community_theoretical_mechanics",
      "community_high_school_physics_3",
      "community_high_school_english_3",
      "community_high_school_chemistry_2"
    ]);
    expect(filterCommunityBooks(communityBooks, "全部").map((book) => book.id)).toEqual(
      communityBooks.map((book) => book.id)
    );
    expect(filterCommunityBooks(communityBooks, "生物").map((book) => book.catalogTitle)).toEqual([
      "遗传与进化",
      "生态系统与稳态"
    ]);
    expect(filterCommunityBooks(communityBooks, "数学").map((book) => book.catalogTitle)).toEqual([
      "函数与导数",
      "高等数学·上册",
      "数学必修第二册"
    ]);
    expect(filterCommunityBooks(communityBooks, "物理").map((book) => book.catalogTitle)).toEqual([
      "力与运动",
      "理论力学 I",
      "物理必修第三册"
    ]);
    expect(filterCommunityBooks(communityBooks, "化学").map((book) => book.catalogTitle)).toEqual([
      "化学必修第二册"
    ]);
    expect(filterCommunityBooks(communityBooks, "英语").map((book) => book.catalogTitle)).toEqual([
      "英语必修第三册"
    ]);
    expect(filterCommunityBooks(communityBooks, "历史")).toEqual([]);
  });

  it("matches normalized queries against catalog metadata", () => {
    expect(filterCommunityBooks(communityBooks, "全部", "  北师大版 ").map((book) => book.id)).toEqual([
      "community_functions"
    ]);
    expect(filterCommunityBooks(communityBooks, "全部", "高一").map((book) => book.id)).toEqual([
      "community_motion",
      "community_high_school_mathematics_2",
      "community_high_school_english_3",
      "community_high_school_chemistry_2"
    ]);
    expect(filterCommunityBooks(communityBooks, "全部", "数学").map((book) => book.id)).toEqual([
      "community_functions",
      "community_higher_mathematics",
      "community_high_school_mathematics_2"
    ]);
    expect(filterCommunityBooks(communityBooks, "全部", "遗传与进化").map((book) => book.id)).toEqual([
      "community_genetics"
    ]);
    expect(filterCommunityBooks(communityBooks, "生物", "人教版").map((book) => book.id)).toEqual([
      "community_genetics",
      "community_ecology"
    ]);
  });

  it("resolves the requested book and falls back explicitly for an invalid id", () => {
    expect(resolveCommunityBook("community_motion").catalogTitle).toBe("力与运动");
    expect(resolveCommunityBook("missing-community-book").id).toBe(communityBooks[0].id);
    expect(resolveCommunityBook(null).id).toBe(communityBooks[0].id);
  });

  it("supports keyword matching within the recommended subset", () => {
    expect(filterCommunityBooks(communityBooks, "推荐", "数学").map((book) => book.id)).toEqual([
      "community_functions",
      "community_higher_mathematics",
      "community_high_school_mathematics_2"
    ]);
    expect(filterCommunityBooks(communityBooks, "推荐", "物理").map((book) => book.id)).toEqual([
      "community_theoretical_mechanics",
      "community_high_school_physics_3"
    ]);
  });
});
