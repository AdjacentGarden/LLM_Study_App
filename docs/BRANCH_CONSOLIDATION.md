# Branch consolidation

This integration starts from main `af7fa207` (2026-10-23). The active client is
`frontend/BookCourse.xcodeproj`; the API root returns service metadata. Historical
React changes stay under `legacy/react-frontend`. The separate `ios/CloudPath`
client is retained as a compatibility implementation, not the active app target.

## Branch disposition

| Remote branch | Tip inspected | Treatment |
| --- | --- | --- |
| main | af7fa207 | Baseline: SwiftUI, account-owned learning designs, PostgreSQL, current image/video APIs |
| codex/native-personalized-learning | 8017c4b4 | Already included in main |
| codex/swiftui-frontend-replacement | 05bed924 | Already included in main |
| feature/adaptive-social-20260906 | ea081e63 | Already included in main |
| feature/iphone-single-user | 4978441a | Merge reviewed teaching publication, asynchronous course jobs, bounded model admission, note-review and upload reliability |
| codex/whole-book-ingestion | 6e5c9b32 | Merge portable resumable preparation, complete OCR coverage checks, deadline-aware chapter execution and repairable private indexes |

Existing branch refs are retained for traceability. Integration uses ordinary
merge commits, preserving the original commit ancestry; no main history rewrite
or replacement of the native frontend is required.

## Compatibility decisions

- Keep main's per-workspace chapter snapshots and effective six-step learning
  configuration. A generated course must pass the merged source/fact/teaching
  publication checks. Public native teaching units are projected from the final
  reviewed components, preserving layout and stable identifiers.
- Preserve synchronous course creation used by the current native client while
  also exposing the durable background course-job API. Reading a published
  course never starts a paid regeneration request.
- Use the shared database adapter for both new business queues. PostgreSQL
  schema version 6 explicitly adds preparation/course queues and note request
  aliases; startup does not create PostgreSQL business tables. An intentionally
  configured but unavailable PostgreSQL database never falls back to SQLite.
- Use platform-specific local worker locks on Windows/POSIX, and dedicated
  PostgreSQL advisory-lock sessions for shared queue ownership. Only the owning
  worker recovers interrupted jobs. Disconnecting that owner prevents it from
  publishing via a newly acquired database connection.
- Artifact index locks use a separate local SQLite file on all three operating
  systems. Native and compatibility upload APIs remain available. Invalid or
  incomplete artifacts can be rebuilt; source/model/dimension changes have
  distinct fingerprints.
- Keep main's PUCODING image and MiniMax H3 Max V2 video integrations and media
  lease fencing; preserve bounded provider requests and avoid replaying an
  ambiguous media submission.

## Validation boundaries

Backend regressions use deterministic model adapters and disposable data.
PostgreSQL contracts use an isolated PostgreSQL 17 container and temporary schemas.
Windows acceptance uses a separate runtime before replacing the existing owned
service. Tests must retain historical quota/attempt counters and user assets.
No real model-quality or new-book latency claim follows from these tests.

Windows hosts the backend. Building/signing/installing the active SwiftUI app
still requires macOS/Xcode and signing. GPU OCR, local ASR and local embedding
models require their actual model/filesystem adapters; Linux absolute paths are
not a Windows deployment configuration. Existing 4090 services and the separate
Windows model relay are not replaced by this consolidation.

Historical `optimization-*-20261023` reports describe their original frozen
candidate and measurements. They are not acceptance evidence for this merged
revision or a signed iPhone release.
