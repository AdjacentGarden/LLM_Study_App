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
      if compact {
        switch record?.job.status {
        case .failed: return "新增资料解析未完成"
        case .cancelled, .interrupted: return "新增资料解析已暂停"
        case .queued: return "新增资料等待解析"
        default: return "新增资料正在整理"
        }
      }
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
    VStack(alignment: .leading, spacing: 0) {
      HStack(alignment: .top, spacing: 12) {
        StickerIcon(preparation == .materials ? "Upload" : "ClipboardCheck", size: 44)
        VStack(alignment: .leading, spacing: 6) {
          Text(title).font(DS.font(.sectionTitle))
          CourseProgressMessage(message: message)
            .font(DS.font(.supporting)).foregroundStyle(
              record?.error != nil || job?.status == .failed ? DS.danger : DS.secondary)
        }
        Spacer(minLength: 0)
      }
      .padding(.horizontal, 16).padding(.top, 20).padding(.bottom, 32)

      VStack(spacing: 12) {
        ForEach(store.courseCreationSteps) { step in
          HStack(spacing: 16) {
            ZStack {
              Circle().fill(DS.background).frame(width: 32, height: 32)
              if step.completed {
                Image(systemName: "checkmark").font(.system(size: 14, weight: .semibold))
                  .foregroundStyle(DS.mint)
              } else if step.active {
                ProgressView().controlSize(.small).tint(DS.primary)
              } else {
                Text("\(step.id + 1)").font(DS.font(.supporting)).foregroundStyle(DS.secondary)
              }
            }
            Text(step.title).font(DS.font(.body)).frame(maxWidth: .infinity, alignment: .leading)
            Text(step.detail).font(DS.font(.supporting))
              .foregroundStyle(step.completed ? DS.text : step.active ? DS.primary : DS.secondary)
              .multilineTextAlignment(.trailing)
          }
          .padding(.horizontal, 14).padding(.vertical, 14).frame(minHeight: 50)
          .background(DS.surface, in: RoundedRectangle(cornerRadius: 15))
          .shadow(color: Color.black.opacity(0.10), radius: 8, x: 0, y: 4)
          .accessibilityElement(children: .combine)
          .accessibilityIdentifier("study.preparation.step.\(step.id)")
        }
      }
      .accessibilityIdentifier("study.preparation.steps")

      if store.coursePreparationRequiresAction(preparation) {
        BCButton("继续") { continuePreparation() }
          .frame(maxWidth: 240).clipShape(Capsule())
          .frame(maxWidth: .infinity).padding(.top, compact ? 32 : 64)
          .accessibilityIdentifier("study.preparation.resume")
      }
    }
    .padding(.horizontal, 4).padding(.bottom, 24)
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier(compact ? "study.materials.notice" : "study.preparation")
  }

  private func continuePreparation() {
    switch preparation {
    case .materials:
      if let record {
        Task { await store.startCourseImport(record.id,
          retryPreparation: record.job.status == .cancelled || record.job.preparationFailed == true) }
      }
    case .setup: store.resumeSelectedCourseSetup()
    case .localGeneration:
      if let bookID = store.selectedBookID { store.prepareLocalCourseOnWiFi(bookID: bookID) }
    case .diagnosis: store.resumeSelectedCourseGeneration()
    case .generation:
      if job?.status == .failed && job?.retryable == true {
        Task { await store.restartCloudCourseGeneration() }
      } else { store.resumeSelectedCourseGeneration() }
    }
  }
}
