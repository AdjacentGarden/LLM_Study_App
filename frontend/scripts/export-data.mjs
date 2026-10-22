import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL,fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
const source=process.env.BOOKCOURSE_SOURCE;
if(!source)throw Error('Set BOOKCOURSE_SOURCE to the read-only React repository');
const target=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),dataDir=path.join(target,'Resources/Data');
await fs.mkdir(dataDir,{recursive:true});
const load=name=>import(pathToFileURL(path.join(source,'src',name)).href);
const [mock,math,dirs,shelf,previews,stickers,react,server,nav,sticker]=await Promise.all([load('data/mockBook.ts'),load('data/demoMathCourse.ts'),load('data/demoDirectoryCourses.ts'),load('data/demoShelfBooks.ts'),load('data/demoShelfStudyPreviews.ts'),load('components/icons/stickerArtwork.ts'),import('react'),import('react-dom/server'),load('components/icons/NavigationIcon.tsx'),load('components/icons/StickerIcon.tsx')]);
const json=async(name,value)=>fs.writeFile(path.join(dataDir,name+'.json'),JSON.stringify(value,null,2)+'\n');
const seed=JSON.parse(await fs.readFile(path.join(source,'src/data/generated/demo-state.json'),'utf8'));
const biology={summary:{book_id:seed.book.id,title:seed.book.title,filename:seed.book.fileName,cover_url:'/assets/book-covers/biology-required-2.webp',status:'ready',page_count:seed.book.pages,chapter_count:seed.book.chapterCount,chunk_count:seed.chunks.length,asset_count:seed.assets.length,average_confidence:93,updated_at:1785638400},chapters:seed.chapters,lessons:seed.lessons,chunks:seed.chunks,assets:seed.assets,flashcards:seed.flashcards,quizzes:seed.quizzes,plan:seed.studyPlan};
const empty=(summary,chapters,plan)=>({summary,chapters,plan,lessons:[],chunks:[],assets:[],flashcards:[],quizzes:[]});
const sources=[biology,{summary:math.demoMathSummary,chapters:math.demoMathChapters,lessons:math.demoMathLessons,chunks:math.demoMathChunks,assets:math.demoMathAssets,flashcards:math.demoMathFlashcards,quizzes:math.demoMathQuizzes,plan:math.demoMathStudyPlan},...dirs.demoDirectoryCourses.map(d=>empty(d.summary,d.chapters,d.plan))];
const {mergeFrontEndMockStudyTasks}=await load('screens/studyPlanPresentation.ts');
biology.plan=structuredClone(seed.studyPlan);biology.plan.tasks=mergeFrontEndMockStudyTasks(biology.plan.tasks,{bookId:biology.summary.book_id,name:seed.book.fileName},'local_user');
await Promise.all([json('sources',sources),json('community',mock.communityBooks),json('shelf',shelf.demoShelfBooks),json('shelf-previews',previews.demoShelfStudyPreviews),json('demo-state',seed),json('mock-book',Object.fromEntries(Object.entries(mock).filter(([k,v])=>typeof v!=='function'))),json('sticker-artwork',stickers.stickerArtwork)]);
const iconDir=path.join(target,'Resources/Content/icons');await fs.mkdir(iconDir,{recursive:true});
for(const name of ['home','study','community','profile'])for(const selected of [false,true]){let svg=server.renderToStaticMarkup(react.createElement(nav.NavigationIcon,{name,selected,size:256}));svg=svg.replaceAll('currentColor',selected?'#7c3aed':'#596780');await fs.writeFile(path.join(iconDir,`nav-${name}-${selected?'selected':'normal'}.svg`),svg);if(selected)await fs.writeFile(path.join(iconDir,`nav-${name}-selected-white.svg`),svg.replaceAll('#7c3aed','#ffffff'));}
for(const name of Object.keys(stickers.stickerArtwork))await fs.writeFile(path.join(iconDir,`sticker-${name}.svg`),server.renderToStaticMarkup(react.createElement(sticker.StickerIcon,{name,size:96})));
const {rankTextbookChunks}=await load('rag/retrievalMath.ts'),ragDir=path.join(source,'public/rag/biology-required-2-rag-v1');
const chunks=JSON.parse(await fs.readFile(path.join(ragDir,'chunks.json'),'utf8')).chunks,bm25=JSON.parse(await fs.readFile(path.join(ragDir,'bm25.json'),'utf8')),manifest=JSON.parse(await fs.readFile(path.join(ragDir,'manifest.json'),'utf8'));
const queries=['减数分裂','你好','同源染色体','DNA半保留复制','人类遗传病','苹果手机怎么充电','基因突变和基因重组有什么区别'];
await json('rag-golden',queries.map(query=>({query,hits:rankTextbookChunks({query,chunks,bm25,dimension:512,weights:manifest.retrieval.weights,limit:5,reliableThreshold:manifest.retrieval.lexical_fallback_threshold,semanticEnabled:false}).map(h=>({id:h.chunk.chunk_id,score:h.score,reliable:h.reliable}))})));
const records=[];
async function walk(root,rel=''){for(const item of await fs.readdir(root,{withFileTypes:true})){const relative=path.join(rel,item.name),full=path.join(root,item.name);if(item.isDirectory())await walk(full,relative);else{const bytes=await fs.readFile(full);records.push({path:relative,bytes:bytes.length,sha256:createHash('sha256').update(bytes).digest('hex')});}}}
await walk(path.join(source,'src'),'src');await walk(path.join(source,'public'),'public');records.sort((a,b)=>a.path.localeCompare(b.path,'en'));
await fs.writeFile(path.join(target,'References/source-manifest.json'),JSON.stringify({source,commit:execFileSync('git',['rev-parse','HEAD'],{cwd:source,encoding:'utf8'}).trim(),files:records},null,2)+'\n');
console.log(`Exported ${sources.length} sources, ${mock.communityBooks.length} community entries, ${chunks.length} RAG chunks and original icon geometry.`);
// Include the original Lucide outlines used by all screens and native feature controls.
const lucide=await import('lucide-react'),ts=(await import('typescript')).default;
const names=new Set(['Plus','Save','Mic2','FileDown','ClipboardCheck','Layers3','BookX','NotebookPen','Sparkles','BookOpen','Clock3','AlarmClock','CalendarDays','Coffee','Puzzle','Target','TrendingUp','Heart','Search','ListChecks','Lightbulb','Globe2','Dumbbell','MessageCircle','Video','RotateCcw','PenLine','CircleAlert','Shuffle']);
for(const file of records.filter(r=>r.path.endsWith('.tsx')||r.path.endsWith('.ts'))) {
 const contents=await fs.readFile(path.join(source,file.path),'utf8'),tree=ts.createSourceFile(file.path,contents,ts.ScriptTarget.Latest,true);
 for(const node of tree.statements)if(ts.isImportDeclaration(node)&&node.moduleSpecifier.text==='lucide-react')for(const item of node.importClause?.namedBindings?.elements??[])names.add((item.propertyName??item.name).text);
}
for(const name of names)if(lucide[name])await fs.writeFile(path.join(iconDir,`lucide-${name}.svg`),server.renderToStaticMarkup(react.createElement(lucide[name],{size:24,color:'#20263a',strokeWidth:1.7})));
console.log(`Exported ${names.size} referenced Lucide icon names.`);
// Preserve the source repository's one-time handwritten demonstration note.
const noteCode=await fs.readFile(path.join(source,'src/features/studyNotes/repository.ts'),'utf8');
const noteTree=ts.createSourceFile('repository.ts',noteCode,ts.ScriptTarget.Latest,true);
let noteExpression;
function findDemo(node){if(ts.isVariableDeclaration(node)&&node.name.getText(noteTree)==='uncertainInkNote')noteExpression=node.initializer.getText(noteTree);ts.forEachChild(node,findDemo);}
findDemo(noteTree);
if(!noteExpression)throw Error('Source demonstration note initializer not found');
const noteModule=ts.transpileModule('const now=1785638400000;export default '+noteExpression,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText;
const note=(await import('data:text/javascript;base64,'+Buffer.from(noteModule).toString('base64'))).default;
await json('demo-notes',[note]);
