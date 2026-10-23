import assert from "node:assert/strict";
import test from "node:test";
import { acceptedCodeDelivery } from "../src/components/accountDelivery.ts";

test("real email delivery remains usable without demo mode", () => {
  assert.equal(acceptedCodeDelivery({ delivery: "email" }, "reader@example.com", false), "email");
  assert.equal(acceptedCodeDelivery({ delivery: "email" }, "reader@example.com", true), "email");
});

test("private preview accepts a real six-digit backend challenge for an allowed demo address", () => {
  assert.equal(acceptedCodeDelivery({ delivery: "demo", demo_code: "004219" }, " READER@zhiwo.test ", true), "demo");
});

test("demo challenges cannot enable a normal email or an unlisted test address", () => {
  for (const email of ["reader@example.com", "someone@zhiwo.test"]) {
    assert.equal(acceptedCodeDelivery({ delivery: "demo", demo_code: "123456" }, email, true), null);
  }
});

test("demo mode must be enabled and its backend code must be complete", () => {
  assert.equal(acceptedCodeDelivery({ delivery: "demo", demo_code: "123456" }, "reader@zhiwo.test", false), null);
  for (const demo_code of [undefined, "12345", "1234567", "12x456"]) {
    assert.equal(acceptedCodeDelivery({ delivery: "demo", demo_code }, "reader@zhiwo.test", true), null);
  }
  assert.equal(acceptedCodeDelivery({ delivery: "unknown", demo_code: "123456" }, "reader@zhiwo.test", true), null);
});
