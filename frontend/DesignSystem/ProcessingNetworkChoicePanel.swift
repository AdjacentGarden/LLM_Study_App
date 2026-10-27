import SwiftUI

struct ProcessingNetworkChoicePanel: View {
  @Environment(AppStore.self) private var store
  let prompt: ProcessingNetworkPrompt
  var body: some View {
    VStack {
      Spacer(minLength: 16)
      VStack(alignment: .leading, spacing: 18) {
        Capsule().fill(DS.border).frame(width: 40, height: 4).frame(maxWidth: .infinity)
        HStack(alignment: .top, spacing: 12) {
          Image(systemName: "antenna.radiowaves.left.and.right")
            .font(.system(size: 24)).foregroundStyle(DS.primary)
            .frame(width: 48, height: 48).background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 16))
          VStack(alignment: .leading, spacing: 6) {
            Text("当前正在使用流量").font(DS.font(21, weight: .bold))
            Text("要改用 iPhone 本机处理吗？").font(DS.font(16)).foregroundStyle(DS.secondary)
          }
        }
        VStack(alignment: .leading, spacing: 12) {
          detail("doc.text.viewfinder", "教材文字识别", "原文件留在本机，由 Apple Vision 识别。")
          detail("waveform", "语音笔记", "本机识别录音，仅将文字发送给 DeepSeek 整理。")
          detail("text.magnifyingglass", "教材检索", "检索已缓存的教材原文，无需上传问题或教材。")
        }.padding(16).background(DS.background, in: RoundedRectangle(cornerRadius: 16))
        Text("本次选择适用于这三项功能。恢复稳定 Wi-Fi 后自动使用原有流程；短时间反复切网会沿用你的选择。")
          .font(DS.font(13)).foregroundStyle(DS.secondary).fixedSize(horizontal: false, vertical: true)
        VStack(spacing: 10) {
          BCButton("使用本机处理") { resolve(.onDevice) }
            .accessibilityIdentifier("network.useLocal")
          Button("继续使用原有流程（消耗流量）") { resolve(.existing) }
            .font(DS.font(15, weight: .semibold)).foregroundStyle(DS.primary)
            .frame(maxWidth: .infinity, minHeight: 44)
            .accessibilityIdentifier("network.useExisting")
          Button("暂不处理") { resolve(.cancelled) }
            .font(DS.font(14)).foregroundStyle(DS.secondary).frame(maxWidth: .infinity, minHeight: 36)
            .accessibilityIdentifier("network.cancel")
        }
      }.padding(24).frame(maxWidth: 540).background(DS.surface,
        in: RoundedRectangle(cornerRadius: 28, style: .continuous))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("network.choice")
        .accessibilityAddTraits(.isModal)
    }.padding(.horizontal, 12).padding(.bottom, 12)
      .onAppear {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
      }
  }
  private func detail(_ icon: String, _ title: String, _ text: String) -> some View {
    HStack(alignment: .top, spacing: 10) {
      Image(systemName: icon).foregroundStyle(DS.primary).frame(width: 22)
      VStack(alignment: .leading, spacing: 3) {
        Text(title).font(DS.font(15, weight: .semibold))
        Text(text).font(DS.font(13)).foregroundStyle(DS.secondary).fixedSize(horizontal: false, vertical: true)
      }
    }
  }
  private func resolve(_ route: ProcessingRoute) {
    store.processingPolicy.resolvePrompt(route, promptID: prompt.id)
  }
}
