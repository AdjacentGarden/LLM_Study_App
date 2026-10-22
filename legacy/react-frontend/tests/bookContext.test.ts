import { test } from "node:test";
import assert from "node:assert/strict";
import { selectBook, suggestedQuestions, safeGet, safeSet, hasAdditionalExplanation } from "../src/components/bookContext.ts";
import type { BookCatalogItem, BookStructure, InterviewResponse } from "../src/types/api.ts";
const books=[{book_id:"math"},{book_id:"history"}] as BookCatalogItem[];
test("restore the session's book instead of blindly using catalog[0]",()=>{
  const session={profile:{book_id:"history"}} as InterviewResponse;
  assert.equal(selectBook(books,session,null)?.book_id,"history");
  assert.equal(selectBook(books,session,"math")?.book_id,"math");
  assert.equal(selectBook([],session,null),null);
});
test("recommendations follow the actual subject and never hard-code biology",()=>{
  for(const topic of ["二分查找的条件","辛亥革命的影响","条件概率的定义"]){
    const structure={chapters:[{title:"第一章",knowledge_points:[topic]}]} as BookStructure;
    const suggestions=suggestedQuestions(structure);
    assert.ok(suggestions[0].includes(topic));
    assert.ok(!suggestions.join().includes("减数分裂"));
  }
  assert.ok(suggestedQuestions(null).length>0);
});
test("disabled browser storage does not crash the app",()=>{
  assert.equal(safeGet("anything"),null);
  assert.doesNotThrow(()=>safeSet("x","value"));
});
test("deduplicate identical labels but preserve extra conditions and English symbols",()=>{
  assert.equal(hasAdditionalExplanation("二分查找需要有序","二分查找需要有序。"),false);
  assert.equal(hasAdditionalExplanation("二分查找","二分查找需要有序。"),true);
  assert.equal(hasAdditionalExplanation("class S","class S。"),false);
  assert.equal(hasAdditionalExplanation("class S","classS。"),true);
});
