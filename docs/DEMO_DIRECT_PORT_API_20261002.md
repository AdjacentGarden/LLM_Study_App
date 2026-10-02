# Demo direct port HTTP contract (2026-10-02)

All routes use existing HttpOnly account cookies and enforce account ownership. Errors are non-2xx JSON `{detail:string}`. Browser requests never include model credentials.

- `POST /api/demo/bookcourse/{method}` body `{args:unknown[]}`: methods and exact typed arguments/results are the public structural `BookCourseRepository` in `frontend/src/demo/api/bookcourseApi.ts`, derived from public methods of `services/DemoRepository.ts`. Private fixture state is not part of the interface.
- `POST /api/demo/bookcourse/uploadFile/{bookId}` multipart `file`: `FileSaveResponse`.
- `GET /api/demo/course-state`, `PUT /api/demo/course-state` direct `CourseState` from `features/courses/model.ts`. Initial server load before mounting App; writes serialized. Course completion awaits server confirmation. Draft edits may remain pending until acknowledgement; failed saves produce visible error.
- `GET /api/demo/study-notes` returns `StudyNote[]`; `GET /api/demo/study-notes/{id}` returns a note, or 404 when absent (mapped to null by the client); `PUT` direct StudyNote; `DELETE` JSON confirmation. Types in `features/studyNotes/types.ts`.
- `PUT /api/demo/study-notes/{audioId}/audio` multipart `file`; GET original audio bytes; DELETE confirmation.
- `POST /api/demo/study-notes/{id}/{transcribe|organize|recognize}` returns updated real StudyNote; unavailable external service must return explicit capability error.
- `GET /api/demo/credits`: `CreditState` (`version:1,balance,transactions`). Reserve POST `{action,id}` returns `{state,reservationId}`; complete/refund POST `{id}` returns state. Transaction fields: id, action chat/video, amount, createdAt, status reserved/spent. All amounts and grants are decided by server.
- `GET /api/demo/report/{bookId}?chapter_id=...`: `{mastery:number|null,minutes:number,mistakes:number,summary:string}` from saved evidence.
- `POST /api/demo/export` `{bookId,chapterId,modules:string[]}`: real PDF response; client downloads only successful nonempty response.
- `POST /api/demo/exports` `{book_id,chapter_id,modules:string[]}`: returns `{id,download_url,size_bytes}`. Modules are `lessons`, `notes`, `citations`, `review`, `report`; selected chapter limits both lesson content and notes. `GET /api/demo/exports/{id}` returns the account-owned PDF. The singular `/export` route translates Demo module names and returns the same PDF directly.

Source files are read only. No sample notes, sample voice transcript, local_user identity, fixture score or local credit ledger may become production account state.

Account isolation: every direct request carries `X-Demo-Account-Id` with the captured public account id. The server validates it against the cookie owner. The browser rejects an old owner before sending and rejects responses after switching accounts. Queued ink/text mutations, voice save/audio operations and processing polls retain their originating owner. Course PUT carries the latest acknowledged revision; the ACK revision is retained for the next serialized write.

- Note GET 404 is creation empty-state; other read errors propagate. Audio upload requires the voice note to be saved first and accepts its `audioId` or note id.
- Processing POST sends explicit `consent:true`; recognition returns `pipelinePhase:needs_confirmation`. Server `transcript` and `recognizedText` are immutable generated evidence. User edits are `confirmedTranscript`; organize sends `{transcript:confirmedTranscript,consent:true}`. GET polls pending pipeline phases until confirmation/complete/error.
- `GET /api/demo/books/{bookId}/pages/{page}` supplies original `source_text`/`image_url`; `GET /api/demo/bookcourse/file/{bookId}` downloads the account-owned original file.
- `buildFlashcards` and `buildQuizzes` may return `{job_id,status:pending}`; the repository polls `getJob` and returns real job `items` only when complete.
- `reviewFlashcard(bookId,cardId,{rating,event_id})` returns the saved card; rating is again/good.
- `generateLessonVideo(bookId,lessonId,{reservation_id,goal,chapter_id})` and `getVideoJob(id)` return task status and an actual `asset_url` when complete.
- RAG queries carry `reservation_id`; no-match/refunded results do not finalize a spent charge. Assignment submissions carry a stable per-attempt `event_id`, measured `response_seconds` and nullable confidence.
- Report additionally includes `minutes_recorded`; absent measurement renders an empty state. PDF download validates nonempty PDF magic before reporting success.
