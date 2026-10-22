import XCTest

@testable import BookCourse

/// The same four contracts as source screens/communityAssistant.test.ts.
final class CommunityAssistantTests: XCTestCase {
  private func books() throws -> [CommunityBook] {
    try ContentBundle.load([CommunityBook].self, name: "community")
  }
  func testNamedCourseUsesCatalogMetadata() throws {
    let answer = answerCommunityCourseQuery("函数与导数课程怎么样？", books: try books())
    XCTAssertEqual(answer.courseIds, ["community_functions"])
    XCTAssertTrue(answer.text.contains("函数与导数"))
    XCTAssertTrue(answer.text.contains("北师大版"))
  }
  func testRecommendationsCombineSubjectAndGrade() throws {
    let answer = answerCommunityCourseQuery("推荐高一数学课程", books: try books())
    XCTAssertEqual(answer.courseIds, ["community_high_school_mathematics_2"])
    XCTAssertTrue(answer.text.contains("数学必修第二册"))
  }
  func testFullCatalogLinksEveryDiscoveryCourseInOriginalOrder() throws {
    let catalog = try books()
    let answer = answerCommunityCourseQuery("查看全部课程", books: catalog)
    XCTAssertEqual(answer.courseIds, catalog.map(\.id))
    XCTAssertTrue(answer.text.contains("共有 \(catalog.count) 门课程"))
  }
  func testUnmatchedQuestionStaysWithinDiscoveryCatalog() throws {
    let catalog = try books()
    let answer = answerCommunityCourseQuery("给我讲量子引力", books: catalog)
    XCTAssertTrue(answer.text.contains("我只根据发现页的课程目录回答"))
    XCTAssertTrue(answer.courseIds.allSatisfy { id in catalog.contains { $0.id == id } })
  }
}
