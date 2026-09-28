const CJK_SENTENCE = /[^。！？!?；;\n]+[。！？!?；;]?/g;
const ENGLISH_BOUNDARY = /(?<=[.!?])\s+(?=[A-Z0-9])/;

function sentences(block: string): string[] {
  const chinese = block.match(CJK_SENTENCE)?.map((part) => part.trim()).filter(Boolean) ?? [];
  if (chinese.length > 1) return chinese;
  return block.split(ENGLISH_BOUNDARY).map((part) => part.trim()).filter(Boolean);
}

function balanceBlock(block: string): string[] {
  const parts = sentences(block);
  const total = parts.reduce((sum, part) => sum + part.length, 0);
  if (parts.length < 2 || total < 96) return [block];

  const desired = Math.min(5, Math.max(2, Math.ceil(total / 105)));
  const target = total / desired;
  const groups: string[][] = [];
  let current: string[] = [];
  let length = 0;

  parts.forEach((part, index) => {
    current.push(part);
    length += part.length;
    const remainingSentences = parts.length - index - 1;
    const remainingGroups = desired - groups.length - 1;
    const ready = length >= Math.max(54, target * .76);
    if (ready && remainingGroups > 0 && remainingSentences >= remainingGroups) {
      groups.push(current);
      current = [];
      length = 0;
    }
  });
  if (current.length) groups.push(current);

  const last = groups.at(-1);
  const previous = groups.at(-2);
  if (last && previous && last.join("").length < 38 && previous.length > 1) {
    last.unshift(previous.pop()!);
  }
  return groups.map((group) => group.join(""));
}

/** Turn stored one-block summaries into readable prose without changing their wording. */
export function splitReadableParagraphs(value: string): string[] {
  const normalized = value
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+/g, " ")
    .trim();
  if (!normalized) return [];

  return normalized
    .split(/\n\s*\n+/)
    .map((block) => block
      .replace(/([\u3400-\u9fff，。！？；：])\s*\n\s*(?=[\u3400-\u9fff])/g, "$1")
      .replace(/\s*\n\s*/g, " ")
      .trim())
    .filter(Boolean)
    .flatMap(balanceBlock);
}

export type GeneratedTextBlock =
  | { kind: "paragraph"; text: string }
  | { kind: "ordered-list" | "unordered-list"; items: string[] };

const LIST_ITEM = /^\s*(?:(\d+)[.)、]|[-*•])\s+(.+)$/;

/**
 * Present model output as product copy: preserve wording, recover paragraphs and
 * real lists, and hide accidental Markdown heading markers. No content is cut.
 */
export function structureGeneratedText(value: string): GeneratedTextBlock[] {
  const normalized = value
    .replace(/\r\n?/g, "\n")
    .replace(/[\u200b\u200c\u200d\ufeff]/g, "")
    .trim();
  if (!normalized) return [];

  const blocks: GeneratedTextBlock[] = [];
  let prose: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushProse = () => {
    if (!prose.length) return;
    const cleaned = prose
      .join("\n")
      .replace(/^\s{0,3}#{1,6}\s+/gm, "")
      .trim();
    blocks.push(
      ...splitReadableParagraphs(cleaned).map((text) => ({
        kind: "paragraph" as const,
        text,
      })),
    );
    prose = [];
  };
  const flushList = () => {
    if (!list) return;
    blocks.push({
      kind: list.ordered ? "ordered-list" : "unordered-list",
      items: list.items,
    });
    list = null;
  };

  for (const line of normalized.split("\n")) {
    const item = line.match(LIST_ITEM);
    if (item) {
      flushProse();
      const ordered = Boolean(item[1]);
      if (list && list.ordered !== ordered) flushList();
      list ??= { ordered, items: [] };
      list.items.push(item[2].trim());
      continue;
    }
    flushList();
    if (!line.trim()) {
      flushProse();
      continue;
    }
    prose.push(line);
  }
  flushList();
  flushProse();
  return blocks;
}
