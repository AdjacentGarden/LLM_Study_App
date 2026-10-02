export function cleanChapterTitle(title: string): string {
  return title.replace(/\s*[（(]原书正文缺失[）)]/g, "").trim();
}
