import { isPublishedSourcePageImage } from "../../data/publishedSourcePages";
import type { ApiAsset, Citation } from "../../types/api";

/**
 * Raw MinerU metadata can mention a page image that exists only on an author's
 * workstation. Treat it as unsafe until this explicit registry says that the
 * file is a tracked, hash-verified static release asset. A missing registry
 * entry remains unavailable even if raw fixture metadata guesses a URL.
 */
export function isPublishedCitationSourcePageImage(value: unknown): value is string {
  return isPublishedSourcePageImage(value);
}

/**
 * The local chunk text is the safe, offline-readable source view for every
 * citation. It is created from the same chunk that generated the page number;
 * a model never supplies it. Older fixture citations fall back to their quote.
 */
export function getCitationSourceText(citation: Citation): string {
  const rawText = citation.source_metadata.retrieved_chunk_text;
  return typeof rawText === "string" && rawText.trim().length > 0
    ? rawText.trim()
    : citation.quote;
}

/**
 * A citation card may show only the original textbook page supplied by an
 * extracted asset that explicitly traces back to the cited chunk. Generated
 * diagrams and cropped asset images are not original-page evidence.
 */
export function selectExtractedCitationSourcePageImage(
  citationChunkId: string,
  relatedAssets: ApiAsset[],
  isPublished: (value: unknown) => value is string = isPublishedCitationSourcePageImage
): string | undefined {
  const sourceAsset = relatedAssets.find((asset) => (
    asset.source_type === "extracted"
    && asset.source_chunk_ids.includes(citationChunkId)
    && isPublished(asset.source_page_image_url)
  ));
  return isPublished(sourceAsset?.source_page_image_url)
    ? sourceAsset.source_page_image_url
    : undefined;
}

export function getExtractedCitationSourcePageImage(
  citationChunkId: string,
  relatedAssets: ApiAsset[]
): string | undefined {
  return selectExtractedCitationSourcePageImage(citationChunkId, relatedAssets);
}
