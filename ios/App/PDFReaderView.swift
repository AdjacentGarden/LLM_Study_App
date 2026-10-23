import SwiftUI
import PDFKit

struct PDFReaderView: View {
    @EnvironmentObject var model: AppModel
    let bookID: String
    let title: String
    var page: Int = 1
    @State private var url: URL?
    @State private var error: String?
    var body: some View {
        Group {
            if let url { NativePDFView(url: url, initialPage: page) }
            else if let error { VStack(spacing: 16) { Text(error).padding(); Button("重新读取原书") { Task { await load() } } } }
            else { ProgressView("正在读取原书 PDF…").padding() }
        }.navigationTitle(title).task { await load() }
    }
    private func load() async {
        error = nil
        do { url = try await model.sourcePDF(book: bookID) } catch { self.error = error.localizedDescription }
    }
}

private final class PDFCoordinator { var loadedKey: String? }
#if os(iOS)
private struct NativePDFView: UIViewRepresentable {
    let url: URL; let initialPage: Int
    func makeCoordinator() -> PDFCoordinator { PDFCoordinator() }
    func makeUIView(context: Context) -> PDFView {
        let view = PDFView(); view.autoScales = true; view.displayMode = .singlePageContinuous; view.displayDirection = .vertical
        return view
    }
    func updateUIView(_ view: PDFView, context: Context) { update(view, coordinator: context.coordinator) }
    private func update(_ view: PDFView, coordinator: PDFCoordinator) {
        let key = url.path + ":\(initialPage)"; guard coordinator.loadedKey != key else { return }
        guard let document = PDFDocument(url: url) else { return }
        coordinator.loadedKey = key; view.document = document
        if let page = document.page(at: max(0, min(document.pageCount - 1, initialPage - 1))) { view.go(to: page) }
    }
}
#else
// This branch lets the existing macOS SDK typecheck the shared native PDF layout.
private struct NativePDFView: NSViewRepresentable {
    let url: URL; let initialPage: Int
    func makeCoordinator() -> PDFCoordinator { PDFCoordinator() }
    func makeNSView(context: Context) -> PDFView { let view = PDFView(); view.autoScales = true; view.displayMode = .singlePageContinuous; return view }
    func updateNSView(_ view: PDFView, context: Context) {
        let key = url.path + ":\(initialPage)"; guard context.coordinator.loadedKey != key else { return }
        guard let document = PDFDocument(url: url) else { return }; context.coordinator.loadedKey = key; view.document = document
        if let page = document.page(at: max(0, min(document.pageCount - 1, initialPage - 1))) { view.go(to: page) }
    }
}
#endif
