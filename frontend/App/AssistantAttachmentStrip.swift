import QuickLook
import SwiftUI

struct AssistantAttachmentStrip: View {
  @Environment(AppStore.self) private var store
  let attachments: [AssistantAttachment]
  var removable = false
  var remove: (String) -> Void = { _ in }
  @State private var previewURL: URL?

  var body: some View {
    ScrollView(.horizontal, showsIndicators: false) {
      HStack(spacing: 8) {
        ForEach(attachments) { item in
          ZStack(alignment: .topTrailing) {
            Button {
              previewURL = store.attachmentURL(item.path)
            } label: {
              HStack(spacing: 8) {
                AssistantAttachmentThumbnail(attachment: item)
                VStack(alignment: .leading, spacing: 4) {
                  Text(item.filename).appFont(.caption, weight: .medium).lineLimit(1)
                  Text(ByteCountFormatter.string(fromByteCount: Int64(item.byteCount), countStyle: .file))
                    .appFont(.caption).foregroundStyle(DS.secondary)
                  if let count = item.characterCount {
                    Text((item.filename.lowercased().hasSuffix(".pdf")
                      ? item.pageCount.map { "\($0) 页 · " } ?? "" : "") + count.formatted() + " 字")
                      .appFont(.caption).foregroundStyle(DS.secondary).lineLimit(1)
                  }
                }.frame(width: 110, alignment: .leading)
              }
              .foregroundStyle(DS.text).padding(8).padding(.trailing, removable ? 28 : 0)
              .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 16))
            }.buttonStyle(.plain).accessibilityLabel("查看附件：" + item.filename)
            if removable {
              Button { remove(item.id) } label: {
                Image(systemName: "xmark.circle.fill").font(.system(size: 18))
                  .symbolRenderingMode(.palette).foregroundStyle(DS.secondary, Color(.systemBackground))
                  .iconButtonFrame()
              }.buttonStyle(.plain).accessibilityLabel("移除附件：" + item.filename)
            }
          }.accessibilityIdentifier("assistant-attachment-\(item.id)")
        }
      }.padding(.horizontal, 4).padding(.vertical, 4)
    }
    .quickLookPreview($previewURL)
    .accessibilityIdentifier(removable ? "assistant-draft-attachments" : "assistant-message-attachments")
  }
}

private struct AssistantAttachmentThumbnail: View {
  @Environment(AppStore.self) private var store
  let attachment: AssistantAttachment
  @State private var image: UIImage?
  var body: some View {
    Group {
      if let image { Image(uiImage: image).resizable().scaledToFill() }
      else {
        Image(systemName: attachment.kind == .image ? "photo" : "doc.text")
          .font(.system(size: 24)).foregroundStyle(DS.primary)
      }
    }
    .frame(width: 48, height: 48).clipped().clipShape(RoundedRectangle(cornerRadius: 10))
    .task(id: attachment.id) {
      guard let path = attachment.thumbnailPath else { return }
      let url = store.attachmentURL(path)
      let data = await Task.detached(priority: .utility) { try? Data(contentsOf: url) }.value
      guard !Task.isCancelled else { return }
      image = data.flatMap(UIImage.init(data:))
    }
  }
}
