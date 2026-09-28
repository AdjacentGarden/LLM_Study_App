import assert from "node:assert/strict";
import test from "node:test";
import {
  splitReadableParagraphs,
  structureGeneratedText,
} from "../src/components/textStructure.ts";

test("keeps concise book summaries as one paragraph", () => {
  const text = "这是一本介绍基础概念和实践方法的入门书。";
  assert.deepEqual(splitReadableParagraphs(text), [text]);
});

test("structures a long Chinese summary without rewriting or losing text", () => {
  const text = "本书首先介绍遗传学的基本问题，并梳理核心概念之间的关系。随后通过经典实验解释证据是如何建立的，以及研究者怎样排除其他可能。接下来讨论遗传信息的保存、复制和表达过程，帮助读者形成完整框架。书中还介绍变异、育种和工程应用，并说明不同方法的适用边界。最后从种群与环境角度讨论演化，让前面的知识连接成一条主线。";
  const paragraphs = splitReadableParagraphs(text);
  assert.ok(paragraphs.length >= 2);
  assert.ok(paragraphs.length <= 5);
  assert.equal(paragraphs.join(""), text);
  assert.ok(paragraphs.every((paragraph) => paragraph.length >= 38));
});

test("honours explicit author paragraphs before balancing very long blocks", () => {
  const text = "第一部分介绍问题与背景。\n\n第二部分解释方法与证据。";
  assert.deepEqual(splitReadableParagraphs(text), [
    "第一部分介绍问题与背景。",
    "第二部分解释方法与证据。",
  ]);
});

test("normalizes soft line wraps instead of showing accidental OCR line breaks", () => {
  const text = "这一行只是排版换行，\n并不是新的自然段。";
  assert.deepEqual(splitReadableParagraphs(text), ["这一行只是排版换行，并不是新的自然段。"]);
});

test("turns generated bullet lines into a real list without losing their words", () => {
  const blocks = structureGeneratedText(
    "先理解整体关系。\n\n- 第一个条件要保留\n- 第二个结论要核对",
  );
  assert.deepEqual(blocks, [
    { kind: "paragraph", text: "先理解整体关系。" },
    {
      kind: "unordered-list",
      items: ["第一个条件要保留", "第二个结论要核对"],
    },
  ]);
});

test("removes accidental markdown heading syntax from product copy", () => {
  assert.deepEqual(structureGeneratedText("### 本章结论\n这是解释。"), [
    { kind: "paragraph", text: "本章结论这是解释。" },
  ]);
});

test("removes invisible model characters before rendering", () => {
  assert.deepEqual(structureGeneratedText("基因\u200b表达。"), [
    { kind: "paragraph", text: "基因表达。" },
  ]);
});
