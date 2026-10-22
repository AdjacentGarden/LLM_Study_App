import Foundation
import PencilKit
import UIKit

enum DemoNoteSeed {
  struct SourceNote: Decodable {
    struct Anchor: Decodable {
      var bookId: String
      var chapterId: String
      var pageStart: Int
    }
    struct Stroke: Decodable {
      struct Point: Decodable {
        var x: Double
        var y: Double
        var pressure: Double
        var t: Double
      }
      var color: String
      var width: Double
      var opacity: Double
      var points: [Point]
    }
    var id: String
    var title: String
    var anchor: Anchor
    var pages: [String: [Stroke]]
    var recognizedText: String
  }
  static func make() throws -> (StudyNote, Data) {
    let seed = try ContentBundle.load([SourceNote].self, name: "demo-notes")[0]
    let strokes = (seed.pages[String(seed.anchor.pageStart)] ?? []).map { stroke in
      let points = stroke.points.map {
        PKStrokePoint(
          location: CGPoint(x: $0.x * 1000, y: $0.y * 1000), timeOffset: $0.t / 1000,
          size: CGSize(width: stroke.width * 1000, height: stroke.width * 1000),
          opacity: stroke.opacity, force: $0.pressure, azimuth: 0, altitude: .pi / 2)
      }
      let path = PKStrokePath(
        controlPoints: points, creationDate: Date(timeIntervalSince1970: 1_785_638_400))
      return PKStroke(
        ink: PKInk(
          .pen, color: UIColor(red: 124 / 255, green: 58 / 255, blue: 237 / 255, alpha: 1)),
        path: path)
    }
    let drawing = PKDrawing(strokes: strokes).dataRepresentation()
    let date = Date().addingTimeInterval(-86400)
    let note = StudyNote(
      id: seed.id, kind: .ink, title: seed.title, body: seed.recognizedText,
      anchor: NoteAnchor(
        bookID: seed.anchor.bookId, chapterID: seed.anchor.chapterId, page: seed.anchor.pageStart),
      createdAt: date, updatedAt: date, drawingPath: "attachments/demo-uncertain.pkdrawing")
    return (note, drawing)
  }
}
