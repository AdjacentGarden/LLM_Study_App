import SwiftUI

extension AppStore {
  func openCommunitySharing(bookID: String? = nil) {
    sharingBookID = bookID
    openSheet(.shareBook)
  }
}

struct CommunitySharingSheet: View {
  @Environment(AppStore.self) private var store

  var body: some View {
    CommunitySharingForm(api: store.cloud, accountID: store.cloudState.account?.user_id,
      epoch: store.cloudState.epoch, preferredBookID: store.sharingBookID)
      .id(store.cloudState.epoch)
  }
}

private struct CommunitySharingForm: View {
  @Environment(AppStore.self) private var store
  @State private var model: CommunitySharingModel
  @State private var actionTask: Task<Void, Never>?
  @State private var choosingBook = false
  @State private var confirmingWithdraw = false
  @FocusState private var editingDescription: Bool
  private let epoch: UUID

  init(api: CloudAPI?, accountID: String?, epoch: UUID, preferredBookID: String?) {
    self.epoch = epoch
    _model = State(initialValue: CommunitySharingModel(api: api, accountID: accountID,
      epoch: epoch, preferredBookID: preferredBookID))
  }

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 18) {
        HStack(alignment: .top, spacing: 12) {
          StickerIcon("Upload", size: 44, glyphSize: 21)
          VStack(alignment: .leading, spacing: 5) {
            Text("让好教材被更多人看见").font(DS.font(18, weight: .bold))
            Text("分享原 PDF，其他人可以导入自己的课程。")
              .font(DS.font(14)).foregroundStyle(DS.secondary)
          }
        }

        if model.loading {
          ProgressView("正在读取你的教材…").padding(.vertical, 24)
        } else if model.candidates.isEmpty {
          emptyState
        } else {
          bookPicker
          if model.selectedCandidate != nil {
            if model.publicationStatus != .unshared {
              publication
            } else {
              publishForm
            }
          }
        }

        if let error = model.error {
          Text(error).font(DS.font(15)).foregroundStyle(DS.danger)
            .accessibilityIdentifier("community.share.error")
        }
        if let message = model.message {
          Text(message).font(DS.font(15)).foregroundStyle(DS.primaryPressed)
            .accessibilityIdentifier("community.share.status")
        }
        if model.submissionUncertain {
          Text("提交结果尚未确认，请先核对状态。")
            .font(DS.font(14)).foregroundStyle(DS.secondary)
          BCButton(model.checkingStatus ? "正在核对…" : "核对分享状态", style: .secondary) {
            perform { await model.refreshStatus() }
          }.disabled(model.submitting || model.checkingStatus)
            .accessibilityIdentifier("community.share.checkStatus")
        }
      }.padding(.top, 12).padding(.bottom, 24)
        .frame(maxWidth: .infinity, alignment: .leading)
    }.scrollDismissesKeyboard(.interactively)
      .accessibilityIdentifier("community.share.sheet")
      .task { await model.load() }
      .onChange(of: model.selectedBookID) { _, _ in
        choosingBook = false
        confirmingWithdraw = false
      }
      .onChange(of: model.publicationRevision) { _, _ in
        Task {
          guard epoch == store.cloudState.epoch else { return }
          try? await store.refreshCommunity()
        }
      }
      .onChange(of: store.cloudState.epoch) { _, _ in
        actionTask?.cancel()
        model.invalidateIdentity()
      }
      .onDisappear { actionTask?.cancel() }
      .toolbar {
        ToolbarItemGroup(placement: .keyboard) {
          Spacer()
          Button("完成") { editingDescription = false }
        }
      }
  }

  private var emptyState: some View {
    VStack(alignment: .leading, spacing: 14) {
      Text(store.cloudState.authenticated ? "还没有可分享的教材" : "登录后分享你的教材")
        .font(DS.font(18, weight: .bold))
      Text(store.cloudState.authenticated
        ? "先上传自己的 PDF 并完成解析，再来分享给社区。"
        : "连接学习服务并登录后，可以选择自己上传的 PDF。")
        .font(DS.font(15)).foregroundStyle(DS.secondary)
      BCButton(store.cloudState.authenticated ? "去上传教材" : "前往设置", style: .secondary) {
        store.closeSheet()
        if store.cloudState.authenticated {
          store.courseFeatureStartDraft()
          store.go(.upload)
        } else {
          store.go(.settings)
        }
      }
      if store.cloudState.authenticated {
        BCButton("刷新教材", style: .text) { perform { await model.load() } }
      }
    }.padding(16).frame(maxWidth: .infinity, alignment: .leading)
      .background(DS.background, in: RoundedRectangle(cornerRadius: 14))
  }

  private var bookPicker: some View {
    VStack(alignment: .leading, spacing: 10) {
      Text("选择教材").font(DS.font(16, weight: .bold))
      if let book = model.selectedCandidate {
        Button {
          editingDescription = false
          withAnimation(DS.localAnimation) { choosingBook.toggle() }
        } label: {
          HStack(spacing: 12) {
            CourseCoverImage(path: book.cover_url)
              .frame(width: 46, height: 62).clipped()
              .clipShape(RoundedRectangle(cornerRadius: 7))
            VStack(alignment: .leading, spacing: 4) {
              Text(book.title).font(DS.font(16, weight: .bold)).lineLimit(2)
              Text("\(book.page_count) 页 · \(book.chapter_count) 个章节")
                .font(DS.font(14)).foregroundStyle(DS.secondary)
            }.frame(maxWidth: .infinity, alignment: .leading)
            BCGlyph(choosingBook ? "ChevronUp" : "ChevronDown", color: DS.primary)
              .frame(width: 18, height: 18)
          }.padding(12).background(DS.background, in: RoundedRectangle(cornerRadius: 14))
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
        }.buttonStyle(.plain).disabled(model.submitting || model.submissionUncertain)
          .accessibilityIdentifier("community.share.chooseBook")
      }
      if choosingBook || model.selectedCandidate == nil {
        VStack(spacing: 0) {
          ForEach(model.candidates, id: \.book_id) { book in
            Button {
              model.selectedBookID = book.book_id
              withAnimation(DS.localAnimation) { choosingBook = false }
            } label: {
              HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                  Text(book.title).font(DS.font(16, weight: .bold)).lineLimit(2)
                  Text(book.filename).font(DS.font(13)).foregroundStyle(DS.secondary).lineLimit(1)
                }.frame(maxWidth: .infinity, alignment: .leading)
                if book.book_id == model.selectedBookID {
                  BCGlyph("Check", color: DS.primary).frame(width: 18, height: 18)
                }
              }.padding(12).frame(maxWidth: .infinity, alignment: .leading)
                .background(book.book_id == model.selectedBookID ? DS.primarySoft : DS.surface)
            }.buttonStyle(.plain).disabled(model.submitting || model.submissionUncertain)
              .accessibilityIdentifier("community.share.book.\(book.book_id)")
          }
        }.clipShape(RoundedRectangle(cornerRadius: 14))
          .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
      }
    }
  }

  private var publishForm: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack {
        Text("分享简介").font(DS.font(16, weight: .bold))
        Text("选填").font(DS.font(13)).foregroundStyle(DS.secondary)
        Spacer()
        Text("\(model.description.count)/500").font(DS.font(13)).foregroundStyle(DS.secondary)
      }
      ZStack(alignment: .topLeading) {
        if model.description.isEmpty {
          Text("介绍一下这本教材适合谁、有什么特点…")
            .font(DS.font(16)).foregroundStyle(DS.secondary).padding(.horizontal, 12).padding(.top, 14)
        }
        TextEditor(text: $model.description).font(DS.font(16))
          .scrollContentBackground(.hidden).padding(6).frame(minHeight: 100, maxHeight: 120)
          .focused($editingDescription).disabled(model.submitting || model.submissionUncertain)
          .accessibilityLabel("分享简介").accessibilityIdentifier("community.share.description")
      }.background(DS.background, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DS.border))
        .onChange(of: model.description) { _, value in
          if value.count > 500 { model.description = String(value.prefix(500)) }
        }

      Button {
        model.rightsConfirmed.toggle()
      } label: {
        HStack(alignment: .top, spacing: 10) {
          Image(systemName: model.rightsConfirmed ? "checkmark.square.fill" : "square")
            .font(.system(size: 22)).foregroundStyle(model.rightsConfirmed ? DS.primary : DS.secondary)
          Text("我确认有权公开分享这份 PDF")
            .font(DS.font(15)).foregroundStyle(DS.text).multilineTextAlignment(.leading)
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8)
      }.buttonStyle(.plain).disabled(model.submitting || model.submissionUncertain)
        .accessibilityValue(model.rightsConfirmed ? "已勾选" : "未勾选")
        .accessibilityIdentifier("community.share.rights")

      BCButton(model.submitting ? "正在提交…" : "发布到发现", icon: "Upload") {
        editingDescription = false
        perform { await model.publish() }
      }.disabled(!model.canPublish).accessibilityIdentifier("community.share.publish")
      Text("分享的是教材原文件，个人笔记、答题与学习进度会保留在自己的账号中。")
        .font(DS.font(13)).foregroundStyle(DS.secondary)
    }
  }

  private var publication: some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack(spacing: 8) {
        Image(systemName: "checkmark.circle.fill").foregroundStyle(DS.primary)
        Text(model.publicationStatus == .sharedByOther ? "这本教材已在发现中" : "已分享到发现")
          .font(DS.font(18, weight: .bold))
      }.accessibilityIdentifier("community.share.published")
      Text("其他人可以查看并导入这份教材。")
        .font(DS.font(15)).foregroundStyle(DS.secondary)
      HStack(spacing: 10) {
        BCButton("查看发现", icon: "Compass") {
          store.closeSheet()
          store.go(.community)
          Task { try? await store.refreshCommunity() }
        }
        BCButton("完成", style: .secondary) { store.closeSheet() }
      }
      if model.selectedCandidate?.can_withdraw == true {
        if confirmingWithdraw {
          VStack(alignment: .leading, spacing: 12) {
            Text("撤回后，发现页将不再展示这本教材。已导入者仍保留自己的资料。")
              .font(DS.font(14)).foregroundStyle(DS.secondary)
            HStack(spacing: 10) {
              BCButton(model.submitting ? "正在撤回…" : "确认撤回", style: .danger) {
                perform { await model.withdraw(); confirmingWithdraw = false }
              }.disabled(model.submitting || model.submissionUncertain)
                .accessibilityIdentifier("community.share.confirmWithdraw")
              BCButton("取消", style: .secondary) { confirmingWithdraw = false }
                .disabled(model.submitting)
            }
          }.padding(14).background(Color(hex: 0xFFF1F2), in: RoundedRectangle(cornerRadius: 14))
        } else {
          BCButton("撤回分享", style: .text) { confirmingWithdraw = true }
            .disabled(model.submitting || model.submissionUncertain)
            .accessibilityIdentifier("community.share.withdraw")
        }
      }
    }.padding(16).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 14))
  }

  private func perform(_ operation: @escaping @MainActor () async -> Void) {
    actionTask?.cancel()
    actionTask = Task { await operation() }
  }
}
