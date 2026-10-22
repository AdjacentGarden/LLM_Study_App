import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL, fileURLToPath } from 'node:url';
if (!process.env.BOOKCOURSE_SOURCE) throw new Error('Set BOOKCOURSE_SOURCE explicitly; the native build does not need this loader.');
const require = createRequire(path.join(path.resolve(process.env.BOOKCOURSE_SOURCE),'package.json'));
const ts = require('typescript');
export async function resolve(specifier, context, nextResolve) {
 if(specifier.startsWith('.')) {const target=new URL(specifier,context.parentURL);if(!path.extname(target.pathname))for(const ext of ['.ts','.tsx','.json','/index.ts'])try{await fs.access(fileURLToPath(target)+ext);return {url:pathToFileURL(fileURLToPath(target)+ext).href,shortCircuit:true};}catch{}}
 if(!specifier.startsWith('.')&&!specifier.startsWith('/')&&!specifier.startsWith('file:')&&!specifier.startsWith('node:'))try{return {url:pathToFileURL(require.resolve(specifier)).href,shortCircuit:true};}catch{}
 return nextResolve(specifier,context);
}
export async function load(url,context,nextLoad) {
 if(/\.tsx?$/.test(url)){const code=await fs.readFile(new URL(url),'utf8');return {format:'module',source:ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.ReactJSX}}).outputText,shortCircuit:true};}
 if(url.endsWith('.json'))return {format:'module',source:'export default '+await fs.readFile(new URL(url),'utf8'),shortCircuit:true};
 return nextLoad(url,context);
}
