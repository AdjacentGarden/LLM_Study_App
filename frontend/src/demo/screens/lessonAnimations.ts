export type LessonAnimation = {
  animationId: string;
  triggerLabel: string;
  title: string;
  description: string;
  durationLabel: string;
  videoUrl: string;
  posterUrl: string;
};

const meiosisExplainer: LessonAnimation = {
  animationId: "animation_meiosis_overview_v1",
  triggerLabel: "看懂减数分裂",
  title: "看懂减数分裂",
  description: "沿着一次复制、两次分裂，观察同源染色体和姐妹染色单体先后发生的变化。",
  durationLabel: "7 秒",
  videoUrl: "/assets/lesson/meiosis-explainer-demo-v1.mp4",
  posterUrl: "/assets/lesson/meiosis-overview-v2.webp"
};

const lessonAnimationByAssetId: Readonly<Record<string, LessonAnimation>> = {
  asset_ai_meiosis_dna_replication_v1: meiosisExplainer
};

export function lessonAnimationForAsset(assetId: string | null | undefined) {
  if (!assetId) return null;
  return lessonAnimationByAssetId[assetId] ?? null;
}
