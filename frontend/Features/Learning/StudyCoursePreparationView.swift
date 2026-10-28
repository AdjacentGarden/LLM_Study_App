import SwiftUI

struct StudyCoursePreparationView: View {
  let preparation: StudyCoursePreparation
  var compact = false
  @Environment(AppStore.self) private var store

  private var record: CourseImportRecord? { store.selectedCourseImport }
  private var job: CloudCourseJob? { store.selectedCourseJob }
  private var running: Bool {
    if preparation == .generation { return store.cloudState.courseTask != nil }
    return record.map { store.importState.tasks[$0.id] != nil || $0.job.status == .running } ?? false
  }
  private var title: String {
    switch preparation {
    case .materials:
      if compact { return "新增资料正在整理" }
      if record?.job.status == .failed { return "资料解析未完成" }
      if record?.job.status == .cancelled || record?.job.status == .interrupted { return "继续整理这门课程" }
      return running ? "正在解析课程资料" : "课程资料已保存"
    case .setup: return "继续完善这门课程"
    case .localGeneration: return "教材已在本机就绪"
    case .diagnosis: return "准备生成课程"
    case .generation: return job?.status == .failed ? "课程生成未完成" : "课程正在生成"
    }
  }
  private var message: String {
    switch preparation {
    case .materials: return record?.message ?? "资料和创建进度已保存，可继续整理。"
    case .setup: return "教材解析已完成，继续完成课程设置和目录确认。"
    case .localGeneration: return "连接学习服务后，可继续生成教学内容。"
    case .diagnosis: return "教材目录已就绪，继续学习准备或诊断后生成课程。"
    case .generation: return job?.message ?? "正在生成教学内容。"
    }
  }

  var body: some View {
    BCCard {
      VStack(alignment: .leading, spacing: 16) {
        HStack(spacing: 12) {
          StickerIcon(preparation == .materials ? "Upload" : "ClipboardCheck", size: 40)
          VStack(alignment: .leading, spacing: 6) {
            Text(title).font(DS.font(20.8, weight: .bold))
            CourseProgressMessage(message: message).font(DS.font(14)).foregroundStyle(
              record?.error != nil || job?.status == .failed ? DS.danger : DS.secondary)
          }
          Spacer(minLength: 0)
          if running { ProgressView().tint(DS.primary) }
        }
        if preparation == .materials, let progress = record?.ringProgress {
          BCProgress(value: progress)
          Text("解析进度 \(Int((progress * 100).rounded()))%")
            .font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        if !compact, let record {
          ForEach(record.books) { book in
            HStack(spacing: 10) {
              Image(systemName: book.status == .completed ? "checkmark.circle" : "doc.text")
                .foregroundStyle(book.status == .completed ? DS.mint : DS.primary)
              VStack(alignment: .leading, spacing: 4) {
                Text(store.sources.first { $0.id == book.id }?.summary.filename
                  ?? store.sources.first { $0.id == book.id }?.summary.title ?? "课程资料")
                  .font(DS.font(14, weight: .semibold)).lineLimit(2)
                if preparation == .materials {
                  CourseProgressMessage(message: book.message).font(DS.font(13)).foregroundStyle(DS.secondary)
                }
              }
            }
          }
        }
        actions
      }
    }
    .accessibilityIdentifier(compact ? "study.materials.notice" : "study.preparation")
  }

  @ViewBuilder private var actions: some View {
    switch preparation {
    case .materials:
      if let record {
        if !running {
          BCButton(record.job.status == .queued && !record.started ? "开始解析" : "继续整理") {
            Task { await store.startCourseImport(record.id,
              retryPreparation: record.job.status == .cancelled) }
          }.accessibilityIdentifier("study.preparation.resume")
          if record.job.preparationFailed == true && record.job.status != .cancelled {
            BCButton("重试教材准备", style: .secondary) {
              Task { await store.startCourseImport(record.id, retryPreparation: true) }
            }
          }
        }
        BCButton("查看解析进度", style: running ? .primary : .secondary) {
          store.openCoursePreparationDetails()
        }.accessibilityIdentifier("study.preparation.details")
      }
    case .setup:
      BCButton("继续整理") { store.resumeSelectedCourseSetup() }
        .accessibilityIdentifier("study.preparation.resume")
    case .localGeneration:
      BCButton("在 Wi-Fi 下生成课程") {
        if let bookID = store.selectedBookID { store.prepareLocalCourseOnWiFi(bookID: bookID) }
      }.accessibilityIdentifier("study.preparation.generate")
    case .diagnosis, .generation:
      BCButton(preparation == .generation ? "查看生成详情" : "继续准备课程") {
        store.resumeSelectedCourseGeneration()
      }.disabled(store.cloudState.isWorking && store.selectedCourseJob == nil)
        .accessibilityIdentifier("study.preparation.generate")
    }
  }
}
