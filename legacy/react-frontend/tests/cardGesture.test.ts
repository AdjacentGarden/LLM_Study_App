import assert from "node:assert/strict";
import test from "node:test";
import { swipeDirection, nextCardIndex } from "../src/components/cardGesture.ts";

test("swipe left advances and swipe right goes back",()=>{
  assert.equal(swipeDirection(-100,5),1); assert.equal(swipeDirection(100,-5),-1);
});
test("normal taps and vertical scrolling never change the card",()=>{
  for(const [x,y] of [[0,0],[20,1],[59,0],[70,80],[120,140],[-100,100]]) assert.equal(swipeDirection(x,y),0);
});
test("threshold and diagonal intent are deterministic",()=>{
  assert.equal(swipeDirection(-60,0),1); assert.equal(swipeDirection(90,60),-1);
  assert.equal(swipeDirection(90,61),0);
});
test("card navigation is bounded even for zero or one card",()=>{
  assert.equal(nextCardIndex(0,-1,7),0); assert.equal(nextCardIndex(6,1,7),6);
  assert.equal(nextCardIndex(0,1,1),0); assert.equal(nextCardIndex(0,1,0),0);
  assert.equal(nextCardIndex(2,-1,7),1); assert.equal(nextCardIndex(2,1,7),3);
});
