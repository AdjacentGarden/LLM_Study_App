import SwiftUI
import UIKit

/// Measure the glass input itself, rather than the wider voice controls or footer.
struct AssistantComposerBoundsKey: PreferenceKey {
  static let defaultValue: Anchor<CGRect>? = nil
  static func reduce(value: inout Anchor<CGRect>?, nextValue: () -> Anchor<CGRect>?) {
    value = nextValue() ?? value
  }
}

struct AssistantAttachmentMenu: View {
  let chooseCamera: () -> Void
  let choosePhotos: () -> Void
  let chooseFiles: () -> Void

  var body: some View {
    VStack(alignment: .leading, spacing: 4) {
      Text("添加到对话")
        .appFont(.cardTitle).foregroundStyle(DS.text)
        .padding(.horizontal, 10).padding(.top, 8).padding(.bottom, 8)
        .accessibilityAddTraits(.isHeader)
      option("相机", symbol: "camera", identifier: "assistant-attachment-camera", action: chooseCamera)
      option("图片", symbol: "photo", identifier: "assistant-attachment-photos", action: choosePhotos)
      option("文件", symbol: "doc", identifier: "assistant-attachment-files", action: chooseFiles)
    }
    .padding(14)
    .modifier(AssistantGlassSurface(radius: 28))
    .shadow(color: .black.opacity(0.08), radius: 20, y: 8)
    .accessibilityElement(children: .contain)
    .accessibilityAddTraits(.isModal)
    .accessibilityIdentifier("assistant-attachment-menu")
  }

  private func option(_ title: String, symbol: String, identifier: String,
    action: @escaping () -> Void) -> some View {
    Button(action: action) {
      HStack(spacing: 16) {
        Image(systemName: symbol)
          .font(.system(size: 21, weight: .medium))
          .foregroundStyle(DS.primary)
          .iconButtonFrame()
          .background(DS.primary.opacity(0.08), in: Circle())
          .accessibilityHidden(true)
        Text(title).appFont(.body, weight: .medium).foregroundStyle(DS.text)
        Spacer(minLength: 0)
      }
      .padding(.horizontal, 8)
      .buttonFrame(fillsWidth: true)
      .contentShape(RoundedRectangle(cornerRadius: 18))
    }
    .buttonStyle(.plain)
    .accessibilityLabel(title).accessibilityIdentifier(identifier)
  }
}

struct AssistantCameraPicker: UIViewControllerRepresentable {
  let complete: (Result<Data, Error>) -> Void
  let cancel: () -> Void

  func makeCoordinator() -> Coordinator { Coordinator(parent: self) }

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let picker = UIImagePickerController()
    picker.sourceType = .camera
    picker.cameraCaptureMode = .photo
    picker.allowsEditing = false
    picker.delegate = context.coordinator
    return picker
  }

  func updateUIViewController(_ controller: UIImagePickerController, context: Context) { }

  final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
    let parent: AssistantCameraPicker
    init(parent: AssistantCameraPicker) { self.parent = parent }

    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.cancel() }

    func imagePickerController(_ picker: UIImagePickerController,
      didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
      guard let image = info[.originalImage] as? UIImage,
        let data = image.jpegData(compressionQuality: 0.9) else {
        parent.complete(.failure(ContentError.invalid("照片未能读取，请重新拍摄。")))
        return
      }
      parent.complete(.success(data))
    }
  }
}
