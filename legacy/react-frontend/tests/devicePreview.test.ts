import assert from "node:assert/strict";
import test from "node:test";
import {
  IPHONE_16,
  IPHONE_17,
  previewDevice,
  previewScale,
} from "../src/components/devicePreview.ts";

test("iPhone 16 screen matches the official display aspect ratio", () => {
  assert.equal(IPHONE_16.width / IPHONE_16.height, 1179 / 2556);
});
test("iPhone 17 screen matches the official display aspect ratio", () => {
  assert.equal(IPHONE_17.width / IPHONE_17.height, 1206 / 2622);
  assert.equal(previewDevice("iphone-17"), IPHONE_17);
});
test("short and narrow windows scale both axes equally without clipping", () => {
  for (const [width, height] of [[597,672],[1280,600],[360,780],[1400,1200]]) {
    const scale = previewScale(width,height);
    assert.ok(scale > 0 && scale <= 1);
    assert.ok(409 * scale <= width - 32 + .001);
    assert.ok(868 * scale <= height - 80 + .001);
    assert.ok(Math.abs((393*scale)/(852*scale) - 393/852) < .000001);
  }
});
test("iPhone 17 scales both axes equally without clipping", () => {
  for (const [width, height] of [[597,672],[1280,600],[360,780],[1400,1200]]) {
    const scale = previewScale(width,height,IPHONE_17);
    assert.ok(scale > 0 && scale <= 1);
    assert.ok((IPHONE_17.width + 2 * IPHONE_17.bezel) * scale <= width - 32 + .001);
    assert.ok((IPHONE_17.height + 2 * IPHONE_17.bezel) * scale <= height - 80 + .001);
    assert.ok(Math.abs((IPHONE_17.width*scale)/(IPHONE_17.height*scale) - 1206/2622) < .000001);
  }
});
