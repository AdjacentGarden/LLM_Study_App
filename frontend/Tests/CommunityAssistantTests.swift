import XCTest

@testable import BookCourse

/// Test-only server metadata, never a production bundled discovery fallback.
final class CommunityAssistantTests: XCTestCase {
  private func books() -> [CommunityBook] {
    [
      CommunityBook(
        id: "community_functions", title: "函数与导数", catalogTitle: "函数与导数", owner: "测试来源",
        cover: "", subject: "数学", grade: "高三", version: "北师大版", volume: nil,
        learners: nil, progress: nil, flashcardCount: 0, recommended: true,
        tags: [], description: "测试服务返回的课程信息", chapters: []),
      CommunityBook(
        id: "community_high_school_mathematics_2", title: "数学必修第二册", catalogTitle: "数学必修第二册",
        owner: "测试来源", cover: "", subject: "数学", grade: "高一", version: "人教版", volume: nil,
        learners: nil, progress: nil, flashcardCount: 0, recommended: false,
        tags: [], description: "测试服务返回的教材信息", chapters: []),
    ]
  }
  func testNamedCourseUsesCatalogMetadata() {
    let answer = answerCommunityCourseQuery("函数与导数课程怎么样？", books: books())
    XCTAssertEqual(answer.courseIds, ["community_functions"])
    XCTAssertTrue(answer.text.contains("函数与导数"))
    XCTAssertTrue(answer.text.contains("北师大版"))
    XCTAssertFalse(answer.text.contains("人学习"))
  }
  func testRecommendationsCombineSubjectAndGrade() {
    let answer = answerCommunityCourseQuery("推荐高一数学课程", books: books())
    XCTAssertEqual(answer.courseIds, ["community_high_school_mathematics_2"])
    XCTAssertTrue(answer.text.contains("数学必修第二册"))
  }
  func testFullCatalogLinksEveryDiscoveryCourseInOriginalOrder() {
    let catalog = books()
    let answer = answerCommunityCourseQuery("查看全部课程", books: catalog)
    XCTAssertEqual(answer.courseIds, catalog.map(\.id))
    XCTAssertTrue(answer.text.contains("共有 \(catalog.count) 门课程"))
  }
  func testUnmatchedQuestionStaysWithinDiscoveryCatalog() {
    let catalog = books()
    let answer = answerCommunityCourseQuery("给我讲量子引力", books: catalog)
    XCTAssertTrue(answer.text.contains("我只根据发现页的课程目录回答"))
    XCTAssertFalse(answer.text.contains("热门"))
    XCTAssertTrue(answer.courseIds.allSatisfy { id in catalog.contains { $0.id == id } })
  }
  func testEmptyServerCatalogNeverInventsOrRestoresBundledCourses() {
    let answer = answerCommunityCourseQuery("推荐生物课", books: [])
    XCTAssertTrue(answer.courseIds.isEmpty)
    XCTAssertFalse(answer.text.contains("遗传与进化"))
  }
  func testUnknownOptionalMetadataDoesNotProduceStatisticsOrEmptyVersionClaims() {
    var entry = books()[0]
    entry.grade = ""
    entry.version = ""
    entry.volume = nil
    let answer = answerCommunityCourseQuery(entry.title, books: [entry])
    XCTAssertFalse(answer.text.contains("人学习"))
    XCTAssertFalse(answer.text.contains("热门"))
    XCTAssertFalse(answer.text.contains("面向，"))
    XCTAssertFalse(answer.text.contains("版本为"))
  }
}
