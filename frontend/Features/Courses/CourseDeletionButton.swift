import SwiftUI

/// Shared by the iPhone course list and course details. Confirmation captures
/// the course ID and account, so changing selection cannot change its target.
struct CourseDeletionButton: View {
  let courseID: String
  let courseName: String
  var iconOnly = false
  @Environment(AppStore.self) private var store
  @State private var confirming = false
  @State private var scope: String?
  @State private var deleting = false
  @State private var error: String?
  var body: some View {
    Button(role: .destructive) {
      scope = store.noteScope
      confirming = scope != nil
    } label: {
      if iconOnly { Image(systemName: "trash").iconButtonFrame() }
      else { Label("从本机删除课程", systemImage: "trash").font(DS.font(.body)).buttonFrame() }
    }
    .buttonStyle(.plain).foregroundStyle(DS.danger)
    .disabled(deleting || store.courseDeletionState.deleting.contains(courseID))
    .accessibilityLabel("从本机删除课程 \(courseName)")
    .accessibilityIdentifier("course.delete.\(courseID)")
    .confirmationDialog("从本机删除《\(courseName)》？", isPresented: $confirming, titleVisibility: .visible) {
      Button("从本机删除课程", role: .destructive) {
        guard let scope else { return }
        deleting = true
        Task { @MainActor in
          defer { deleting = false }
          do {
            try await store.performLocalCourseDeletion(courseID: courseID, scope: scope)
            if store.noteScope == scope { store.showToast("课程已从本机删除", kind: .success) }
          } catch {
            if store.noteScope == scope { self.error = error.localizedDescription }
          }
        }
      }
      Button("取消", role: .cancel) {}
    } message: {
      Text("将删除本机的课程、课程笔记、学习进度和生成内容。其他课程仍在使用的教材和文件会保留。普通文字对话及无法确认归属的旧学习数据会保留。此操作无法撤销。")
    }
    .alert("删除课程未完成", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) {
      Button("知道了", role: .cancel) { error = nil }
    } message: { Text(error ?? "请重试。") }
  }
}
