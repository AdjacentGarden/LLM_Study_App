import AVKit
import SwiftUI

/// Presented only from the existing illustration action. Closing this view stops
/// local polling; a submitted provider task continues and is recovered on reopen.
struct LearningMediaSheet: View {
  @Environment(\.dismiss) private var dismiss
  @Environment(\.scenePhase) private var scenePhase
  @State private var model: CloudMediaModel
  @State private var actionTask: Task<Void, Never>?

  init(api: CloudAPI, context: LearningMediaContext) {
    _model = State(initialValue: CloudMediaModel(api: api, context: context))
  }

  var body: some View {
    NavigationStack {
      ScrollView {
        VStack(alignment: .leading, spacing: 20) {
          Label("看懂这张图", systemImage: "play.circle")
            .font(DS.font(23, weight: .bold)).foregroundStyle(DS.primary)
          Text(model.context.chapterTitle).font(DS.font(18, weight: .bold))
          Text(model.context.excerpt).font(DS.font(16)).foregroundStyle(DS.secondary)
            .lineLimit(8)
          if model.loading {
            ProgressView("正在连接视频服务").accessibilityIdentifier("lesson.media.loading")
          } else if let job = model.job {
            result(job)
          }
          if let error = model.error {
            Text(error).font(DS.font(16)).foregroundStyle(DS.danger)
              .accessibilityIdentifier("lesson.media.error")
            BCButton(model.job == nil ? "重新连接" : "刷新状态", style: .secondary) {
              perform { await model.refresh() }
            }.accessibilityIdentifier("lesson.media.refresh")
          }
          if !model.loading, model.job == nil || model.job?.status == "failed" {
            confirmation
          }
          if !model.history.isEmpty {
            Divider()
            Text("本书的视频任务").font(DS.font(17, weight: .bold))
            ForEach(model.history) { job in
              Button {
                model.select(job)
              } label: {
                HStack(alignment: .top) {
                  VStack(alignment: .leading, spacing: 4) {
                    Text(job.result["title"]?.stringValue ?? job.chapter_title)
                    Text(job.excerpt).font(DS.font(13)).lineLimit(2).foregroundStyle(DS.secondary)
                  }
                  Spacer()
                  Text(job.statusText).font(DS.font(13))
                }.frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 8)
              }.buttonStyle(.plain).disabled(model.submitting)
                .accessibilityIdentifier("lesson.media.history.\(job.id)")
            }
          }
        }.padding(24).frame(maxWidth: 760).frame(maxWidth: .infinity)
      }
      .background(DS.surface)
      .navigationBarTitleDisplayMode(.inline)
      .toolbar {
        ToolbarItem(placement: .principal) {
          Text("讲解视频").font(DS.font(18, weight: .bold))
        }
        ToolbarItem(placement: .confirmationAction) {
          Button("关闭") { dismiss() }.accessibilityIdentifier("lesson.media.close")
        }
      }
    }
    .foregroundStyle(DS.text)
    .task { await model.load() }
    .task(id: "\(model.job?.id ?? "")-\(model.refreshID)-\(scenePhase == .active)") {
      if scenePhase == .active { await model.follow() }
    }
    .onDisappear {
      actionTask?.cancel()
      model.clearAsset()
    }
  }

  private var confirmation: some View {
    VStack(alignment: .leading, spacing: 12) {
      if let capabilities = model.capabilities, capabilities.video {
        Text("根据当前图示说明和章节内容，生成约 \(capabilities.video_seconds ?? 6) 秒的讲解短片。")
          .font(DS.font(16))
        if let estimate = capabilities.video_estimate {
          Text("预计服务费用 ¥\(estimate, specifier: "%.2f")，以服务方实际计费为准。")
            .font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        Text("生成可能需要几分钟。关闭后可从这张图重新打开查看进度；重复打开不会再次生成。")
          .font(DS.font(14)).foregroundStyle(DS.secondary)
        BCButton(model.submitting ? "正在提交……" : model.job == nil ? "确认生成视频" : "重新生成视频") {
          perform { await model.submit() }
        }.disabled(model.submitting || model.context.excerpt.count < 4)
          .accessibilityIdentifier("lesson.media.generate")
      } else if model.capabilities != nil {
        Text(
          model.capabilities?.media_notice?.isEmpty == false
            ? model.capabilities!.media_notice! : "视频生成服务暂未就绪，请检查后端的视频服务配置。"
        )
        .font(DS.font(16)).foregroundStyle(DS.secondary)
        .accessibilityIdentifier("lesson.media.unavailable")
        BCButton("刷新服务状态", style: .secondary) { perform { await model.load() } }
      }
    }
  }

  @ViewBuilder private func result(_ job: CloudMediaJob) -> some View {
    VStack(alignment: .leading, spacing: 14) {
      HStack {
        if job.isRunning || model.downloading { ProgressView() }
        Text(model.downloading ? "正在下载视频" : job.statusText)
          .font(DS.font(18, weight: .bold)).accessibilityIdentifier("lesson.media.status")
      }
      if job.isRunning {
        Text("任务已提交，准备好后会显示在这里。你可以关闭窗口，稍后再查看。")
          .font(DS.font(15)).foregroundStyle(DS.secondary)
      }
      if let error = job.error, !error.isEmpty {
        Text(error).font(DS.font(16)).foregroundStyle(DS.danger)
          .accessibilityIdentifier("lesson.media.jobError")
      }
      if job.status == "uncertain" {
        Text("任务已保留。为避免重复计费，请先刷新确认结果。")
          .font(DS.font(15)).foregroundStyle(DS.secondary)
        BCButton("刷新状态", style: .secondary) { perform { await model.refresh() } }
      }
      if let url = model.videoURL { GeneratedLearningVideo(url: url) }
      if job.status == "succeeded" {
        if let title = job.result["title"]?.stringValue {
          Text(title).font(DS.font(18, weight: .bold))
        }
        if let explanation = job.result["explanation"]?.stringValue {
          Text(explanation).font(DS.font(16))
        }
        if let caution = job.result["caution"]?.stringValue, !caution.isEmpty {
          Text(caution).font(DS.font(14)).foregroundStyle(DS.secondary)
        }
        Text("AI 辅助讲解，请结合教材核对。")
          .font(DS.font(14)).foregroundStyle(DS.secondary)
      }
    }.padding(18).frame(maxWidth: .infinity, alignment: .leading)
      .background(DS.primarySoft, in: RoundedRectangle(cornerRadius: 16))
  }

  private func perform(_ operation: @escaping @MainActor () async -> Void) {
    actionTask?.cancel()
    actionTask = Task { await operation() }
  }
}

private struct GeneratedLearningVideo: View {
  let url: URL
  @State private var player: AVPlayer?
  @State private var seconds: Double = 0
  @State private var playbackError: String?
  @State private var observer: Any?
  @State private var statusObserver: NSKeyValueObservation?

  var body: some View {
    VStack(alignment: .leading) {
      if let player {
        VideoPlayer(player: player).aspectRatio(16 / 9, contentMode: .fit)
          .accessibilityIdentifier("lesson.media.player")
          .accessibilityValue(String(format: "%.2f", seconds))
      }
      if let playbackError {
        Text(playbackError).font(DS.font(14)).foregroundStyle(DS.danger)
      }
    }
    .task(id: url) {
      stop()
      let item = AVPlayerItem(url: url)
      let next = AVPlayer(playerItem: item)
      player = next
      statusObserver = item.observe(\.status, options: [.initial, .new]) { observed, _ in
        let failed = observed.status == .failed
        Task { @MainActor in
          if failed { playbackError = "视频暂时无法播放，请关闭后重新打开。" }
        }
      }
      observer = next.addPeriodicTimeObserver(
        forInterval: CMTime(seconds: 0.2, preferredTimescale: 600), queue: .main
      ) { time in
        Task { @MainActor in if time.seconds.isFinite { seconds = time.seconds } }
      }
      next.play()
    }
    .onDisappear { stop() }
  }

  private func stop() {
    player?.pause()
    if let observer { player?.removeTimeObserver(observer) }
    observer = nil
    statusObserver?.invalidate()
    statusObserver = nil
    player = nil
  }
}
