# BookCourse AI 前后端 P2 优化方案与审核标准

本文件描述 P2 阶段的两项优化：
- **P2-1**：拆分 `BookCourseScreens.tsx`（2379 行单文件）为按屏分组的多个文件，并引入 React Context 减少 prop drilling。
- **P2-2**：为 BM25 索引与向量检索路径建立缓存，避免每次 RAG 请求重复重建。

## 一、范围

影响代码：

### P2-1（前端）
- `BookCourseAI_frontend_project/src/screens/BookCourseScreens.tsx`（拆分）
- `BookCourseAI_backend/app/screens/*`（新增，按屏分组）
- `BookCourseAI_frontend_project/src/context/AppContext.tsx`（新增）
- `BookCourseAI_frontend_project/src/App.tsx`（适配 Context Provider）
- `BookCourseAI_frontend_project/src/types/app.ts`（如需补充类型）

### P2-2（后端）
- `BookCourseAI_backend/app/rag/bm25.py`
- `BookCourseAI_backend/app/rag/retrieval.py`
- `BookCourseAI_backend/app/rag/index_base.py`（ArtifactVectorIndex 缓存）
- `BookCourseAI_backend/app/rag/index_factory.py`
- `BookCourseAI_backend/app/services/artifact_store.py`（chunks 读取缓存 + 失效）
- `BookCourseAI_backend/app/core/config.py`（缓存开关与 TTL）
- 新增 `BookCourseAI_backend/app/rag/cache.py`

## 二、优化项

### P2-1 拆分 BookCourseScreens.tsx + React Context 重构

#### 问题
- `BookCourseScreens.tsx` 2379 行单文件包含 27+ 屏与多个 sheet 组件，单一文件难以维护、难以做 code review、难以并行开发。
- `SharedProps` 把全部 24 个状态通过 `<HomeScreen {...sharedProps} />` 透传给所有屏，每屏只用到 2-3 个字段，但其余字段更新都会触发无谓重渲染（`sharedProps` 是一个 object，任一字段变化则新引用）。
- 已有 `setSavedNoteCount: (fn: (count: number) => number) => void` 这种 prop 在 `Sheet` 中又要单独传，说明 prop drilling 已成负担。

#### 措施
- 拆分文件结构：
  - `src/screens/HomeScreen.tsx`
  - `src/screens/UploadScreen.tsx`
  - `src/screens/ParseReadyScreen.tsx`
  - `src/screens/ProcessingScreen.tsx`
  - `src/screens/ChapterConfirmScreen.tsx`
  - `src/screens/CourseReadyScreen.tsx`
  - `src/screens/LibraryScreen.tsx`
  - `src/screens/CommunityScreen.tsx`
  - `src/screens/BookCourseScreen.tsx`
  - `src/screens/LessonScreen.tsx`
  - `src/screens/FlashcardScreen.tsx`
  - `src/screens/SourceReaderScreen.tsx`
  - `src/screens/AssignmentScreen.tsx`
  - `src/screens/DiagnosisScreen.tsx`
  - `src/screens/MistakeBookScreen.tsx`
  - `src/screens/NotesScreen.tsx`
  - `src/screens/ExportPreviewScreen.tsx`
  - `src/screens/LessonReportScreen.tsx`
  - `src/screens/StudyPlanScreen.tsx`
  - `src/screens/ProfileScreen.tsx`
  - `src/screens/sheets/SourceSheetContent.tsx`
  - `src/screens/sheets/ChatSheetContent.tsx`
  - `src/screens/sheets/NoteSheetContent.tsx`
  - `src/screens/shared.ts`（共用工具：`backendAssetUrl`、`sourcePageImageUrl`、`sourcePageLabel`、`chapterConcepts`、`liveBookTitle`、`formatFileSize`、`getFileKind`、`apiChapterToChapter`、`averageConfidence`、`QuickAction`、`BookMini`、`CourseCover`、`SettingsRow`、`ChapterEvidenceSummary`）
- 引入 `src/context/AppContext.tsx`：
  - 定义 `AppContextValue` 包含 `App.tsx` 当前 `sharedProps` 的所有字段。
  - 提供 `useAppContext() hook`。
  - `App.tsx` 在根部用 `<AppProvider value={sharedProps}>` 包裹。
- 各屏组件签名改为 `useAppContext()` 拉取自己需要的字段；`go` `back` `openSheet` `closeSheet` `showToast` `openSourcePage` 这些 action 也走 context。
- 抽公共子组件（`QuickAction`、`BookMini`、`CourseCover`、`SettingsRow`、`ChapterEvidenceSummary`）到独立文件以便复用。
- 删除原 `BookCourseScreens.tsx`；导出从新文件汇总到 `src/screens/index.ts`（barrel），`App.tsx` 改导入。
- `SharedProps` 类型拆解为各屏需要的 `props` 子集（可选 + 默认从 context 拉取），但仅为类型迁移，不引入运行时 cost。
- 测试覆盖见审核标准。

### P2-2 BM25 / vector 检索缓存

#### 问题
- `HybridRetriever.retrieve` 每次请求 `BM25Index(chunks).search(...)`，对一本书 chunks 全量遍历并重建词频，O(chunks × tokens)，相同 book 重复请求时浪费严重。
- `ArtifactVectorIndex.search_vector` 每次都对全书 chunks 重新 embed，O(chunks × dim)，相同 book+query 极浪费。
- `read_chunks(book_id)` 每次都打开 chunks.jsonl 重新 parse，无缓存。
- 当前默认 `rag_index_provider=pgvector` 但大部分本地/demo 环境并未起 PG，会 fallback 到 `ArtifactVectorIndex`（即上面的开销路径）。

#### 措施
- 新增 `app/rag/cache.py`：
  - `LRUCache[K,V]`：基于 `collections.OrderedDict`，容量上限、`get`/`set`/`pop`/`clear`、线程安全（`threading.RLock`）。
  - `chunk_cache: LRUCache[str, list[Chunk]]`：缓存 `read_chunks(book_id)`，key 为 book_id；与 `artifact_store.write_chunks`、`delete_book` 联动失效。
  - `bm25_index_cache: LRUCache[tuple[str, str|None], BM25Index]`：key 为 `(book_id, chapter_id)`；当 chunks.jsonl mtime 变化或图书被删除时失效。
  - `artifact_embedding_cache: LRUCache[tuple[str, str|None], list[tuple[Chunk, list[float]]]]`：缓存 ArtifactVectorIndex 的 chunks+embeddings，key 同上。
- 改造 `artifact_store.read_chunks`：先查 chunk_cache；增加 `invalidate_chunks(book_id)` 用于写入路径调用。
- 改造 `BM25Index`：将 `_build_from_chunks(chunks)` 拆出，`BM25Index` 支持从 cache 返回已有实例；`search` 时若通过 cache 走，直接复用 doc_freq/doc_lengths。
- 改造 `HybridRetriever.retrieve`：从 `bm25_index_cache.get((book_id, chapter_id))` 拿 `BM25Index`，未命中则建索引并存入；chunks 走 chunk_cache。
- 改造 `ArtifactVectorIndex.search_vector`：从 `artifact_embedding_cache.get((book_id, chapter_id))` 拿 (chunks, embeddings) 对，未命中则构建（embed + cache）；query embedding 仍每次算（query 长度小）。
- 配置：
  - `BOOKCOURSE_RAG_CACHE_ENABLED`（默认 `true`，单测可关）
  - `BOOKCOURSE_RAG_CACHE_MAX_BOOKS`（默认 8）：缓存多少本 book 的 BM25/向量/chunks。
  - `BOOKCOURSE_RAG_CACHE_TTL_SECONDS`（默认 600）：超过 ttl 清理；同时在 `write_chunks`、`delete_book` 时主动失效。
- 失效策略：
  - 写入路径调用 `artifact_store.write_chunks` -> `invalidate_chunks(book_id)` + `bm25_index_cache.pop((book_id, None))` 等。
  - `delete_book` 路由调用 `RagIndex.delete_book` -> 同步从所有 cache 移除该 book。
  - 提供 `clear_rag_cache()` 用于测试与维护。
- 可观测：
  - 在 `rag/audit.py` 已有 `write_rag_audit`，增加字段 `cache_hit`（`bm25_hit`/`vector_hit`/`chunks_hit`/`none`）。
- 测试见审核标准。

## 三、审核标准（验收清单）

### A 文件拆分（P2-1）
1. `src/screens/BookCourseScreens.tsx` 文件已删除；`src/screens/index.ts` 作为 barrel 文件存在，导出全部屏与 sheet 组件。
2. `src/screens/` 目录下每个屏一个文件，单文件不超过 600 行。
3. `src/context/AppContext.tsx` 存在，导出 `AppProvider`、`useAppContext`、`AppContextValue` 类型。
4. `App.tsx` 中 `<AppShell>...</AppShell>` 内层是 `<AppProvider value={sharedProps}>` 包裹的 `renderScreen()`；不再使用 `<HomeScreen {...sharedProps} />` 形式直接透传。
5. 各屏函数签名不再要求 `SharedProps` 作为 props；改为 `useAppContext()` 拉取；不被传入 props 可与 `go(target)` 配合使用（默认 props 为空对象 `{}`）。
6. `QuickAction`、`BookMini`、`CourseCover`、`SettingsRow`、`ChapterEvidenceSummary` 已抽到 `src/screens/shared.ts` 或独立文件并被 import 引用，grep 全库每个组件名仅出现其定义处与使用处（无重复定义）。
7. 共用工具函数（`backendAssetUrl`、`sourcePageImageUrl`、`sourcePageLabel`、`chapterConcepts`、`liveBookTitle`、`formatFileSize`、`getFileKind`、`apiChapterToChapter`、`averageConfidence`）位于 `src/screens/shared.ts`，每屏 import 使用。

### B 上下文与渲染（P2-1）
8. grep `src/screens/**/*.tsx` 无 `SharedProps` 出现（类型可保留在 AppContext.tsx）。
9. 任一屏内修改自身无关的 state（如 `HomeScreen` 内修改 toast）不会触发其他屏重渲染（无需 e2e，由代码结构推断：屏通过 `useAppContext()` 选择性读取）。
10. `App.tsx` 的 `sharedProps` useMemo 保留（用于 Provider value），但不再 spread 透传给具体屏。
11. ActionSheet 的 `ChatSheetContent`、`SourceSheetContent`、`NoteSheetContent` 通过 context 拿到所需字段（或在 App.tsx 渲染 sheet 时由 `useAppContext` 拉取）。

### C 检索缓存（P2-2）
12. `app/rag/cache.py` 存在 `LRUCache`、`chunk_cache`、`bm25_index_cache`、`artifact_embedding_cache`、`clear_rag_cache`、`invalidate_book_cache(book_id)`。
13. `app/services/artifact_store.read_chunks` 优先查 `chunk_cache`，未命中读 disk 并写入；`write_chunks`、`delete_book`（如有相关路径）调用 `invalidate_chunks(book_id)`。
14. `HybridRetriever.retrieve` 在 `rag_cache_enabled=true` 时，对同一 `(book_id, chapter_id)` 第二次请求 BM25 必命中 cache（用 monkeypatch 验证：spy `BM25Index.__init__` 调用次数为 1）。
15. `ArtifactVectorIndex.search_vector` 第二次对同一 `(book_id, chapter_id)` 调用时不重新 embed（spy `embed_documents` 调用次数为 1）。
16. `write_chunks(book_id, new_chunks)` 后再次 query，必须命中新数据（即 cache 已失效并重建）。
17. `clear_rag_cache()` 调用后再次 query 必须重建索引。
18. `rag/audit.py` 写入的 audit dict 中包含 `cache_hit` 字段（值为 `bm25_hit` / `vector_hit` / `chunks_hit` / `none` 之一）。
19. `BOOKCOURSE_RAG_CACHE_ENABLED=false` 时所有缓存路径不触发，行为与改造前等价（`BM25Index` 调用次数等于请求数）。

### D 测试与回归
20. 后端 `pytest app/tests -q` 全绿（含新 P2 审计测试）。
21. 前端 `npm run lint`、`tsc -b`、`npm run build`、`vitest run` 全绿。
22. P2 新增 `app/tests/test_audit_p2.py` 覆盖审核项 12-19。

## 四、不在本阶段范围
- 锁定依赖版本、CI 文件（P3）。
- 替换 BackgroundTasks 到 Redis/Celery（P3）。
- 前端引入 React Router（评估后另行）。