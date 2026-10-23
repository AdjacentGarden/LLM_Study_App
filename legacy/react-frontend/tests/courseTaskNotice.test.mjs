import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const frontend = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(frontend, "package.json"));
const { build } = require("rolldown");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const bundled = await build({ input: join(frontend, "src/components/CourseTaskNotice.tsx"),
  external: ["react", "react/jsx-runtime"], write: false, output: { format: "cjs" } });
const chunk = bundled.output.find(item => item.type === "chunk");
assert.ok(chunk);
const module = { exports: {} };
new Function("require", "module", "exports", chunk.code)(require, module, module.exports);
const { CourseTaskNotice } = module.exports;
const base = { chapterId: "chapter", jobId: "known-job", status: "failed", error: "请稍后重试", retryable: false };
const props = (work = base, extra = {}) => ({ work, loading: false, onOpen() {}, onCheck() {}, ...extra });
const html = (work = base, extra = {}) => renderToStaticMarkup(React.createElement(CourseTaskNotice, props(work, extra)));

test("exhausted terminal overrides legacy retry advice and never says server continues", () => {
  const rendered = html();
  assert.match(rendered, /任务已停止|此任务已停止/);
  assert.match(rendered, /尝试次数已用完/);
  assert.match(rendered, /任务和学习记录已保留/);
  assert.match(rendered, /查看任务状态/);
  assert.doesNotMatch(rendered, /请稍后重试|后台生成|继续查看|重试本次任务/);
});

test("exhausted button selects status GET callback rather than course generation", () => {
  const calls = [];
  const tree = CourseTaskNotice(props(base, { onOpen() { calls.push("open"); }, onCheck() { calls.push("check"); } }));
  tree.props.children.find(item => item?.type === "button").props.onClick();
  assert.deepEqual(calls, ["check"]);
});

test("retryable failure retains explicit retry callback and specific cause", () => {
  const work = { ...base, retryable: true, error: "审核服务暂不可用，请明确重试" };
  assert.match(html(work), /审核服务暂不可用/);
  assert.match(html(work), /重试本次任务/);
  assert.doesNotMatch(html(work), /尝试次数已用完|查看任务状态/);
  const calls = [];
  const tree = CourseTaskNotice(props(work, { onOpen() { calls.push("retry"); }, onCheck() { calls.push("check"); } }));
  tree.props.children.find(item => item?.type === "button").props.onClick();
  assert.deepEqual(calls, ["retry"]);
});

for (const status of ["queued", "running"]) {
  test(`${status} alone retains truthful active-task hint`, () => {
    const rendered = html({ ...base, status, error: "", retryable: false });
    assert.match(rendered, /正在后台生成与审核/);
    assert.doesNotMatch(rendered, /已停止|次数已用完|重试本次任务/);
  });
}

test("paused connection and cache checking are not labelled exhausted", () => {
  assert.match(html({ ...base, status: "paused", error: "连接中断，原任务已保存" }), /连接中断/);
  assert.doesNotMatch(html({ ...base, status: "paused" }), /尝试次数已用完/);
  assert.match(html({ ...base, status: "checking" }), /正在查找已保存课程/);
});

test("succeeded is an open action without retry or background claim", () => {
  const rendered = html({ ...base, status: "succeeded", error: "" });
  assert.match(rendered, /章节学习内容已准备好|打开课程/);
  assert.doesNotMatch(rendered, /尝试次数已用完|后台生成|重试本次任务/);
});

test("loading prevents another click, missing task ID disables the status action", () => {
  assert.doesNotMatch(html(base, { loading: true }), /<button/);
  assert.match(html({ ...base, jobId: undefined }), /<button disabled/);
});

test("server text is escaped and never executed", () => {
  const rendered = html({ ...base, retryable: true, error: "<script>unsafe()</script>" });
  assert.match(rendered, /&lt;script&gt;/);
  assert.doesNotMatch(rendered, /<script>/);
});
