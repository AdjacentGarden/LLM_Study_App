import type { ApiAsset, LessonBlock, LessonCitation } from "../types/api";

export type LessonReadingSection = {
  block: LessonBlock;
  asset: ApiAsset | null;
  assets: ApiAsset[];
};

export type LessonConceptDetail = {
  concept: string;
  explanation: string;
  citation: LessonCitation | null;
  asset: ApiAsset | null;
};

function hasTextMatch(value: string, concept: string) {
  return value.toLocaleLowerCase().includes(concept.toLocaleLowerCase());
}

function assetPriority(asset: ApiAsset) {
  return asset.source_type === "extracted" ? 0 : 1;
}

export function isWholePageScan(asset: ApiAsset) {
  if (asset.source_type !== "extracted") return false;
  if (asset.type.toLocaleLowerCase().includes("page")) return true;
  if (asset.bbox.length < 4) return false;

  const [left, top, right, bottom] = asset.bbox;
  const isNormalizedBoundingBox = [left, top, right, bottom].every((value) => value >= 0 && value <= 1);
  return isNormalizedBoundingBox
    && left <= 0.05
    && top <= 0.05
    && right >= 0.95
    && bottom >= 0.95;
}

function uniqueAssets(assets: ApiAsset[]) {
  return [...new Map(assets.map((asset) => [asset.asset_id, asset])).values()];
}

export function buildLessonReadingSections(
  blocks: LessonBlock[],
  assets: ApiAsset[]
): LessonReadingSection[] {
  const assetsById = new Map(assets.map((asset) => [asset.asset_id, asset]));
  const candidatesByBlock = blocks.map((block) => {
    const explicitlyLinkedAssets = block.asset_ids
      .map((assetId) => assetsById.get(assetId))
      .filter((asset): asset is ApiAsset => Boolean(asset));
    const chunkLinkedAssets = assets.filter((asset) => (
      asset.source_chunk_ids.some((chunkId) => block.source_chunk_ids.includes(chunkId))
    ));
    const conceptLinkedAssets = assets.filter((asset) => (
      asset.source_chunk_ids.length === 0
      && asset.concepts.some((concept) => (
        hasTextMatch(block.title, concept) || hasTextMatch(block.content, concept)
      ))
    ));
    return uniqueAssets([
      ...explicitlyLinkedAssets.sort((left, right) => assetPriority(left) - assetPriority(right)),
      ...chunkLinkedAssets.sort((left, right) => assetPriority(left) - assetPriority(right)),
      ...conceptLinkedAssets.sort((left, right) => assetPriority(left) - assetPriority(right))
    ]).filter((asset) => !isWholePageScan(asset));
  });

  const usedAssetIds = new Set<string>();
  const selectedByBlock = blocks.map(() => [] as ApiAsset[]);

  // Give every knowledge point its first relevant illustration before filling a
  // second slot. This prevents an earlier card from consuming all shared art.
  for (let slot = 0; slot < 2; slot += 1) {
    candidatesByBlock.forEach((candidates, blockIndex) => {
      const nextAsset = candidates.find((asset) => !usedAssetIds.has(asset.asset_id));
      if (!nextAsset) return;
      selectedByBlock[blockIndex].push(nextAsset);
      usedAssetIds.add(nextAsset.asset_id);
    });
  }

  return blocks.map((block, blockIndex) => ({
    block,
    asset: selectedByBlock[blockIndex][0] ?? null,
    assets: selectedByBlock[blockIndex]
  }));
}

export function buildLessonConceptDetail(
  concept: string,
  summary: string,
  sections: LessonReadingSection[]
): LessonConceptDetail {
  const matchingSection = sections.find(({ block }) => (
    hasTextMatch(block.title, concept)
    || hasTextMatch(block.content, concept)
    || block.citations.some((citation) => hasTextMatch(citation.quote ?? "", concept))
  ));

  return {
    concept,
    explanation: matchingSection?.block.content ?? summary,
    citation: matchingSection?.block.citations[0] ?? null,
    asset: matchingSection?.asset ?? null
  };
}
