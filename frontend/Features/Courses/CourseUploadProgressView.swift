import SwiftUI

struct CourseFileUploadProgressView: View {
  let batchID: UUID
  @Environment(AppStore.self) private var store
  private var batch: CourseUploadBatch? { store.uploadState.batches.first { $0.id == batchID } }

  var body: some View {
    GeometryReader { geometry in
      VStack(spacing: 0) {
        BCHeader("上传课程资料").padding(.top, 8)
        ScrollView {
          VStack(spacing: 0) {
            Image(systemName: "icloud.and.arrow.up")
              .font(.system(size: 36, weight: .medium)).foregroundStyle(DS.primary)
              .frame(width: 80, height: 80)
              .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 24))
              .accessibilityHidden(true)
              .padding(.top, geometry.size.height < 540 ? 20 : min(72, geometry.size.height * 0.10))
            Text(batch?.isComplete == true ? "资料已上传" : batch?.hasFailures == true && batch?.isRunning == false
              ? "部分资料尚未上传" : "文件正在上传")
              .font(DS.font(.sectionTitle, weight: .semibold))
              .padding(.top, 20).padding(.bottom, 40)
              .accessibilityIdentifier("upload.progress.title")
            VStack(spacing: 20) {
              ForEach(batch?.files ?? []) { file in fileCard(file) }
            }
            .padding(.bottom, 24)
          }
          .frame(maxWidth: 520).padding(.horizontal, store.isPad ? 32 : 28)
          .frame(maxWidth: .infinity)
        }.scrollIndicators(.hidden)
      }
      .safeAreaInset(edge: .bottom, spacing: 0) {
        VStack(spacing: 18) {
          if batch?.isRunning == true {
            BCButton("后台上传") {
              store.backgroundCourseUpload(batchID)
              store.showToast("已转为后台上传", kind: .info)
            }.accessibilityIdentifier("upload.background")
            whiteButton("取消上传") { store.cancelCourseUpload(batchID) }
              .accessibilityIdentifier("upload.cancel")
          } else {
            if batch?.isComplete == true {
              BCButton("继续整理资料") { store.continueCourseUpload(batchID) }
                .accessibilityIdentifier("upload.continue")
            } else {
              BCButton("重试未完成的上传") { store.retryCourseUpload(batchID) }
                .accessibilityIdentifier("upload.retry")
            }
            whiteButton("返回学习页") { store.backgroundCourseUpload(batchID) }
              .accessibilityIdentifier("upload.close")
          }
        }
        .frame(width: min(240, max(180, geometry.size.width * 0.60)))
        .padding(.top, 12).padding(.bottom, store.isPad ? 24 : 20)
        .frame(maxWidth: .infinity).background(DS.background)
      }
    }
    .background(DS.background.ignoresSafeArea())
    .foregroundStyle(DS.text)
    .courseUploadCompletionAlert(visibleBatchID: batchID)
  }

  private func fileCard(_ file: CourseUploadFile) -> some View {
    VStack(alignment: .leading, spacing: 12) {
      HStack(alignment: .top, spacing: 8) {
        Text(file.filename).font(DS.font(.cardTitle)).lineLimit(2)
          .frame(maxWidth: .infinity, alignment: .leading)
        if file.status == .completed {
          Label("完成", systemImage: "checkmark.circle.fill")
            .font(DS.font(.supporting)).foregroundStyle(DS.primary)
            .fixedSize().accessibilityIdentifier("upload.file.\(file.id).complete")
        } else {
          Text(status(file)).font(DS.font(.supporting)).foregroundStyle(
            file.status == .failed || file.status == .cancelled ? DS.danger : DS.secondary)
            .fixedSize()
        }
      }
      GeometryReader { geometry in
        ZStack(alignment: .leading) {
          Capsule().fill(DS.background)
          Capsule().fill(DS.primary).frame(width: geometry.size.width * file.fraction)
        }
      }.frame(height: 7)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(file.filename + "上传进度")
        .accessibilityValue("\(Int(file.fraction * 100))%")
        .accessibilityIdentifier("upload.file.\(file.id).progress")
      Text(bytes(file.sentBytes) + " / " + bytes(file.totalBytes))
        .font(DS.font(.supporting)).monospacedDigit().foregroundStyle(DS.secondary)
        .accessibilityIdentifier("upload.file.\(file.id).bytes")
      if let error = file.error {
        Text(error).font(DS.font(.supporting)).foregroundStyle(DS.danger)
          .fixedSize(horizontal: false, vertical: true)
      }
    }
    .padding(.horizontal, 16).padding(.vertical, 16)
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(DS.surface, in: RoundedRectangle(cornerRadius: 24))
    .compositingGroup()
    .shadow(color: Color.black.opacity(0.10), radius: 4.5, x: 3, y: 4)
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier("upload.file.\(file.id)")
  }
  private func whiteButton(_ title: String, action: @escaping () -> Void) -> some View {
    Button(action: action) {
      Text(title).font(DS.font(.button))
        .buttonFrame(.regular, fillsWidth: true)
        .frame(maxWidth: .infinity)
        .background(DS.surface, in: Capsule())
        .contentShape(Capsule())
    }.buttonStyle(BCPressStyle())
  }
  private func bytes(_ value: Int64) -> String {
    let formatter = ByteCountFormatter()
    formatter.countStyle = .file
    formatter.allowsNonnumericFormatting = false
    return formatter.string(fromByteCount: value)
  }
  private func status(_ file: CourseUploadFile) -> String {
    switch file.status {
    case .preparing: "准备中"
    case .queued: "等待上传"
    case .uploading: file.fraction >= 1 ? "确认中" : "\(Int(file.fraction * 100))%"
    case .completed: "完成"
    case .failed: "上传失败"
    case .cancelled: "已取消"
    }
  }
}

private struct CourseUploadCompletionAlert: ViewModifier {
  var enabled: Bool
  var visibleBatchID: UUID?
  @Environment(AppStore.self) private var store
  @Environment(\.scenePhase) private var scenePhase
  private var completion: CourseUploadBatch? {
    guard enabled, scenePhase == .active else { return nil }
    return store.uploadState.batches.first { batch in
      batch.isComplete && !batch.completionAcknowledged
        && (visibleBatchID.map { batch.id == $0 } ?? (store.uploadState.presentedID == nil))
    }
  }
  func body(content: Content) -> some View {
    let batch = completion
    content.alert(batch?.onDeviceOnly == true ? "资料已保存" : "上传成功",
      isPresented: Binding(get: { batch != nil }, set: { value in
        if !value, let batch { store.acknowledgeCourseUpload(batch.id) }
      })) {
      Button("稍后处理", role: .cancel) {
        if let batch { store.acknowledgeCourseUpload(batch.id) }
      }
      Button("继续整理") { if let batch { store.continueCourseUpload(batch.id) } }
    } message: {
      if let batch {
        Text("“\(batch.courseName)”的 \(batch.files.count) 份资料\(batch.onDeviceOnly ? "已保存在本机" : "已上传成功")，可以继续整理教材目录并设置课程。")
      }
    }
  }
}

extension View {
  func courseUploadCompletionAlert(enabled: Bool = true, visibleBatchID: UUID? = nil) -> some View {
    modifier(CourseUploadCompletionAlert(enabled: enabled, visibleBatchID: visibleBatchID))
  }
}
