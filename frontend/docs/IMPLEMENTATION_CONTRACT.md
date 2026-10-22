# Shared implementation contract

P0 cloud integration supersedes the original offline-by-default runtime choice: normal app launches use a configurable FastAPI endpoint; `--fixture` retains the original independent offline contract. See `CLOUD_INTEGRATION.md`. Existing view entry points, routes and animations remain; CloudAPI/CloudMapping/CloudStore adapt server-owned data without exposing answer keys.

Current location: `LLM_Study_App/frontend/`, with the FastAPI backend in `../backend/`. See the root README and `docs/SWIFTUI_MIGRATION.md` for current startup, migration and validation. The following describes the original fixture contract: source revision/provenance is recorded in `References/`, iOS17 and SwiftUI, no WebView, and the historical source repository is read-only. Account mode now uses the FastAPI backend by default; `--fixture` remains offline. Source content and custom assets are preserved. The user later canceled pixel-for-pixel styling parity; current acceptance covers usable functional flows and animations. Actual Swift definitions are authoritative.

## Feature entry points
All use `@Environment(AppStore.self) private var store` and `struct ...: View`.
- CoursesRouterView(route: Route): home,onboarding,courseSetup,courseDetail,upload,parseReady,processing,courseImportProcessing,chapterConfirm,courseReady,courseImportReady,library,community,communityBook,communityImport,profile,settings.
- CourseSheetView(sheet: SheetRoute): editChapter,bookSwitcher,addMaterials.
- LearningRouterView(route: Route): study,plan,flashcards,lesson,assignment,diagnosis,mistakes,export.
- NotesRouterView(route: Route): notes,voiceNote,source.
- NoteSheetView(sheet: SheetRoute): note,noteType.
Parent handles .chat and global chrome. Feature views do not wrap another NavigationStack or draw phone system bars. Parent draws header using route.title; hide default header for onboarding,courseSetup,home,community,study,profile,lesson,source,voiceNote when feature needs custom header. Communicate desired changes explicitly.

## Models (main supplies Core/Models.swift)
Imported Codable models keep source snake_case property names to preserve exact IDs:
- Chapter: chapter_id,level,source_title,ai_title,page_start,page_end,printed_page_start:Int?,printed_page_end:Int?,parent_id:String?,confidence:Double,status,source; id=chapter_id,title=ai_title-or-source_title.
- Lesson: book_id,lesson_id,chapter_id,title,source_title,page_start,page_end,lesson_kind,status,confidence,objectives:[String],key_concepts:[String],summary,blocks:[LessonBlock],source_chunk_ids:[String],asset_ids:[String],warnings:[String]; id=lesson_id.
- LessonBlock: block_id,block_type,title,content,citations:[LessonCitation],source_chunk_ids,asset_ids,ai_generated; id=block_id.
- LessonCitation: chunk_id,page_start,page_end,printed_page_start:Int?,printed_page_end:Int?,quote:String?; id=chunk_id.
- BookAsset: asset_id,book_id,chapter_id:String?,source_type,page:Int?,type,caption,bbox:[Double]?,image_url,thumbnail_url,source_page_image_url:String?,source_chunk_ids,concepts; id=asset_id.
- SourceSummary: book_id,title,cover_url:String?,filename:String?,status,page_count,chapter_count,chunk_count,asset_count,average_confidence,updated_at,content_mode:String?; id=book_id.
- SourceContent: summary:SourceSummary,chapters:[Chapter],lessons:[Lesson],chunks:[SourceChunk],assets:[BookAsset],flashcards:[Flashcard],quizzes:[QuizQuestion],plan:StudyPlan; id=summary.book_id.
- Flashcard: card_id,book_id,lesson_id,chapter_id,front,back,concept,source_chunk_ids,page_start,page_end,printed_page_start:Int?,mastery:Double,reason; id=card_id.
- QuizQuestion: question_id,book_id,lesson_id,chapter_id,prompt,choices:[String],answer,explanation,concept,source_chunk_ids,page_start,page_end,printed_page_start:Int?,question_type:String?,source_quote:String?; id=question_id.
- StudyPlan: user_id,book_id,days,daily_minutes,tasks:[StudyTask]. StudyTask: task_id,user_id,day,title,task_type,minutes,chapter_id:String?,lesson_id:String?,status,score:Double?,weak_points:[String]; id=task_id.
- CommunityBook: exact mockBook.ts camelCase keys (id,title,catalogTitle,owner,cover,subject,grade,version,volume:String?,learners,progress,flashcardCount,recommended:Bool?,tags:[String],description,chapters:[String]).
- ShelfBook: bookId,title,filename,coverUrl; id=bookId.
- Course: id,name,resourceIDs:[String] (book IDs),activeResourceID:String?,diagnosis:[String:[String]],createdAt:Date,updatedAt:Date.
- LearnerPreferences: displayName,primaryGoal:String?,dailyTime:String?. CourseDraft: name,resourceIDs:[String],diagnosis:[String:[String]],step:Int.
- StudyNote: id,kind:NoteKind(.text,.ink,.voice),title,body,anchor:NoteAnchor?,createdAt,updatedAt,position:UnitPointValue?,audioPath:String?,duration:Double,transcript:String?,organizedText:String?,drawingPath:String?,conversation:[ChatMessage]?; initializer defaults all but kind/title/body. NoteAnchor: courseID:String?,bookID:String,chapterID:String?,page:Int,printedPage:Int?,quote:String?; defaults all but bookID/page. SourcePageTarget: bookID,title,page,pageEnd:Int?,printedPage:Int?,sourceText:String?,from:Route?; defaults all but bookID/title/page.
- Citation: bookID,chapterID,chapterTitle,page,printedPage:Int?,chunkID,quote,score:Double; id=bookID+chunkID. ChatMessage: id,role,text,citations:[Citation].

## AppStore interface (main supplies Core/AppStore.swift)
State now resides in six independent Observable types in `Core/FeatureStates.swift`: CourseState, LearningSessionState, ReaderState, StudyNoteState, ImportTaskState, AssistantConversationState. AppStore exposes forwarding accessors and coordinates navigation, persistence and async tasks. `latestDiagnosis: Diagnosis?`, `mistakes: [MistakeRecord]`, and per-book `chapterDrafts: [String:[Chapter]]` are implemented.
Properties: route:Route,path:[Route],sheet:SheetRoute?,sources:[SourceContent],community:[CommunityBook],shelf:[ShelfBook],courses:[Course],preferences:LearnerPreferences?,draft:CourseDraft,selectedCourseID:String?,selectedBookID:String?,selectedChapterID:String?,selectedLessonID:String?,selectedCommunityID:String?,sourceTarget:SourcePageTarget?,noteKind:NoteKind,notes:[StudyNote],toast:String?,credits:Int,processingProgress:Double,processingMessage:String,importedFileURL:URL?,importError:String?,assignmentAnswer:String,diagnosisText:String,selectedQuizIndex:Int,chatMessages:[ChatMessage],chatLoading:Bool,selectedQuote:String,selectedAsset:BookAsset?,isPad:Bool.
Computed: currentCourse:Course?,currentSource:SourceContent?,chapters:[Chapter],lessons:[Lesson],activeLesson:Lesson?,flashcards:[Flashcard],quizzes:[QuizQuestion],plan:StudyPlan?,currentTitle:String,chapterTitle:String.
Methods:
- go(_ route:Route),replace(_ route:Route),back(),showToast(_ message:String),openSheet(_ sheet:SheetRoute),closeSheet().
- selectSource(_ bookID:String),selectCourse(_ courseID:String),openLesson(_ lesson:Lesson),openSource(_ target:SourcePageTarget),openCitation(_ citation:LessonCitation),openCitation(_ citation:Citation).
- completeOnboarding(name:String,goal:String?,dailyTime:String?),logout(),createCourse(name:String,resourceIDs:[String],diagnosis:[String:[String]]=[:]),removeCourse(_ id:String),addSource(_ bookID:String,to courseID:String?),removeSource(_ bookID:String,from courseID:String),importCommunity(_ book:CommunityBook).
- importFile(_ url:URL) async; processImport() async; cancelImport(); confirmChapters(); updateChapter(_ chapter:Chapter); deleteChapter(_ chapterID:String); updateTask(_ taskID:String,status:String); completeLesson(); setMastery(_ questionID:String,mastered:Bool); isMastered(_ questionID:String)->Bool; submitAssignment(question:QuizQuestion,answer:String).
- saveNote(_ note:StudyNote) async throws; deleteNote(_ id:String) async; reloadNotes() async; ask(_ question:String) async; makeAnchor(page:Int?=nil)->NoteAnchor; persist(); notesForCurrentSource:[StudyNote].
- attachmentURL(_ relative:String)->URL; saveAttachment(_ data:Data,extension ext:String) async throws -> String (relative path).
- makeSampleCourse() for UI launch fixtures. SourceContent fields mutable.
- reserveCredits(_ amount:Int) async throws -> String; completeCredits(_ id:String) async; refundCredits(_ id:String) async. Persist the reservation before beginning the paid action; complete only at successful readiness, refund on failure/cancel; repository ledger recovers interrupted reservations.
Chapter edit/delete modifies `chapterDrafts` only; `confirmChapters` commits to source directories. Canceling processing uses `cancelImport` to invalidate stale callbacks and preserve the original attachment for retry. No old-device user-data migration is performed; only repository fixtures are copied.
Route raw values exactly source enum. SheetRoute string enum .chat,.note,.noteType,.editChapter,.bookSwitcher,.addMaterials; context in store properties. Source imports store.processImport uses original file content. Errors must be surfaced and saved files retained.

## DesignSystem interface
`DS` constants: primary,primaryPressed,primarySoft,background,surface,text,secondary,muted,border,warning,danger,mint,orange (Color); font(_ size:CGFloat=20,weight:Font.Weight=.regular)->Font; global/local animation. `Color(hex:UInt)`.
`BCButton(_ title:String,icon:String?=nil,style:BCButtonStyle=.primary,action:@escaping()->Void)`; style enum primary,secondary,text,danger.
`BCCard { content }`, `BCPill(_ title:String,color:Color=DS.primary)`, `BCProgress(value:Double)` (0...1), `BCHeader(_ title:String,subtitle:String?=nil,back:(()->Void)?=nil,actionTitle:String?=nil,action:(()->Void)?=nil)`.
`AssetImage(_ path:String,contentMode:ContentMode=.fit)` loads /assets paths from bundled Resources/Content (webp decoded natively).
`StickerIcon(_ name:String,tone:String="violet",size:CGFloat=40)` names from source stickerArtwork, uses original SVG geometry.
`EmptyStateView(title:String,message:String,asset:String="/assets/brand/empty-states/no-courses-v1.png")`.
`BCTextField(_ placeholder:String,text:Binding<String>)`, `BCTextEditor(text:Binding<String>,placeholder:String="")`.
Feature body content max width 1120, article 720; feature screens responsible scroll. Use DS.font consistently, white bordered cards, explicit spacing copied from source. No default Form/List chrome.
