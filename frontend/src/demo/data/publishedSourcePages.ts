import publishedSourcePageManifest from "./published-citation-source-page-assets.json";

type PublishedSourcePageAsset = {
  book_id?: unknown;
  pdf_page?: unknown;
  url?: unknown;
  sha256?: unknown;
};

const publishedSourcePages = new Map<string, string>();
const publishedSourcePageUrls = new Set<string>();

(publishedSourcePageManifest.assets as PublishedSourcePageAsset[]).forEach((asset) => {
  if (
    typeof asset.book_id !== "string"
    || !Number.isInteger(asset.pdf_page)
    || (asset.pdf_page as number) < 1
    || typeof asset.url !== "string"
    || !asset.url.startsWith("/assets/textbook/pages/")
    || typeof asset.sha256 !== "string"
    || !/^[a-f0-9]{64}$/iu.test(asset.sha256)
  ) {
    return;
  }

  const url = asset.url.trim();
  publishedSourcePages.set(`${asset.book_id}:${asset.pdf_page}`, url);
  publishedSourcePageUrls.add(url);
});

export function getPublishedSourcePageImageUrl(bookId: string, pdfPage: number) {
  if (!bookId.trim() || !Number.isInteger(pdfPage) || pdfPage < 1) return undefined;
  return publishedSourcePages.get(`${bookId}:${pdfPage}`);
}

export function isPublishedSourcePageImage(value: unknown): value is string {
  return typeof value === "string" && publishedSourcePageUrls.has(value.trim());
}
