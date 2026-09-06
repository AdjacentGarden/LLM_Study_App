import {test} from 'node:test';
import assert from 'node:assert/strict';
import {setStorageIdentity,safeSet,safeGet,captureStorage} from '../src/components/bookContext.ts';
test('account switching and late async writes cannot cross user caches',()=>{
 const items:Record<string,string>={};
 Object.defineProperty(globalThis,'localStorage',{value:{getItem:(key:string)=>items[key]??null,setItem:(key:string,v:string)=>{items[key]=v;},removeItem:(key:string)=>{delete items[key];}},configurable:true});
 setStorageIdentity('A');safeSet('zhiwo.active-session','private-A');const old=captureStorage();
 setStorageIdentity('B');assert.equal(safeGet('zhiwo.active-session'),null);old.safeSet('zhiwo.active-session','late-A');assert.equal(safeGet('zhiwo.active-session'),null);
 safeSet('zhiwo.active-session','private-B');setStorageIdentity('A');assert.equal(safeGet('zhiwo.active-session'),'late-A');
});
