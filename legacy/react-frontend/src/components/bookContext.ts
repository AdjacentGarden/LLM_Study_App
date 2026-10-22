import type { BookCatalogItem, BookStructure, InterviewResponse } from "../types/api";

export function selectBook(catalog: BookCatalogItem[], saved: InterviewResponse | null, preferred: string | null) {
  return catalog.find(item => item.book_id === preferred)
    ?? catalog.find(item => item.book_id === saved?.profile.book_id) ?? catalog[0] ?? null;
}

export function suggestedQuestions(structure: BookStructure | null): string[] {
  const chapter = structure?.chapters[0];
  const topic = chapter?.knowledge_points.find(point => point.trim().length > 0 && point.trim().length <= 24);
  if (topic && chapter) return [
    `请结合原文解释：${topic.replace(/[。！？]+$/, "")}`,
    `“${chapter.title}”的核心结论是什么？`,
    `请说明“${chapter.title}”中一个重要结论的成立条件。`,
  ];
  if (chapter) return [`“${chapter.title}”主要讲了什么？`, `请解释“${chapter.title}”的核心概念。`];
  return ["这本书主要讨论什么问题？", "请结合原文解释一个核心概念。"];
}

let storagePrefix = "";
export function setStorageIdentity(id:string,migrateLegacy=false){
  storagePrefix=`account:${id}:`;
  if(migrateLegacy)try{const assigned=localStorage.getItem('zhiwo.storage-legacy-owner');if(!assigned||assigned===id){localStorage.setItem('zhiwo.storage-legacy-owner',id);for(const key of Object.keys(localStorage)){if(key.startsWith('zhiwo.')&&key!=='zhiwo.storage-legacy-owner'&&localStorage.getItem(storagePrefix+key)===null)localStorage.setItem(storagePrefix+key,localStorage.getItem(key)!);}}}catch{/* Storage can be disabled. */}
}
export function captureStorage(){const prefix=storagePrefix;return {safeGet:(key:string)=>{try{return localStorage.getItem(prefix+key);}catch{return null;}},safeSet:(key:string,value:string|null)=>{try{if(value===null)localStorage.removeItem(prefix+key);else localStorage.setItem(prefix+key,value);}catch{/* Keep old async writes scoped to their original user. */}}};}
export function savedLearningSessions():string[]{
  try{return [...new Set(Object.keys(localStorage).filter(key=>key.startsWith(storagePrefix+'zhiwo.active-session')).map(key=>localStorage.getItem(key)!).filter(Boolean))].slice(0,30);}catch{return [];}
}
export function safeGet(key: string): string | null {
  try { return localStorage.getItem(storagePrefix+key); } catch { return null; }
}
export function safeSet(key: string, value: string | null) {
  try { if (value === null) localStorage.removeItem(storagePrefix+key); else localStorage.setItem(storagePrefix+key, value); } catch { /* Private mode can disable persistence; in-memory use still works. */ }
}

export function hasAdditionalExplanation(title: string, explanation: string): boolean {
  const normalize = (text: string) => text.trim().replace(/。$/, "");
  return normalize(title) !== normalize(explanation);
}
