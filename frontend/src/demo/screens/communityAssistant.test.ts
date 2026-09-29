import { describe, expect, it } from "vitest";

import { communityBooks } from "../data/mockBook";
import { answerCommunityCourseQuery } from "./communityAssistant";

describe("community course assistant", () => {
  it("answers a named course from catalog metadata", () => {
    const answer = answerCommunityCourseQuery("函数与导数课程怎么样？");

    expect(answer.courseIds).toEqual(["community_functions"]);
    expect(answer.text).toContain("函数与导数");
    expect(answer.text).toContain("北师大版");
  });

  it("combines subject and grade filters when recommending courses", () => {
    const answer = answerCommunityCourseQuery("推荐高一数学课程");

    expect(answer.courseIds).toEqual(["community_high_school_mathematics_2"]);
    expect(answer.text).toContain("数学必修第二册");
  });

  it("links every discovery course when the user asks for the full catalog", () => {
    const answer = answerCommunityCourseQuery("查看全部课程");

    expect(answer.courseIds).toEqual(communityBooks.map((book) => book.id));
    expect(answer.text).toContain(`共有 ${communityBooks.length} 门课程`);
  });

  it("stays within the discovery catalog when no course matches", () => {
    const answer = answerCommunityCourseQuery("给我讲量子引力");

    expect(answer.text).toContain("我只根据发现页的课程目录回答");
    expect(answer.courseIds.every((id) => communityBooks.some((book) => book.id === id))).toBe(true);
  });
});
