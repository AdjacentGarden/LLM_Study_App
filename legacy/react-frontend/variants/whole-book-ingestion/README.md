# Whole-book upload presentation variant

This directory preserves the whole-book branch's alternate React upload screen and its matching UI scenarios. The active archived React app continues using `src/components/BookUpload.tsx` and `uploadFlow.ts`, which preserve captured account storage and late upload receipts. The native production client remains `../../../../frontend/BookCourse.xcodeproj`.

`BookUpload.tsx` imports the shared archived API and components through adjusted relative paths, so it can be evaluated explicitly as a presentation alternative. Its three Playwright scenarios target this alternative's stage messages and labels; they are kept beside the alternative instead of being registered against the different active UI. These scenarios are preserved unchanged and are not part of the active UI suite; the active upload-flow and reliability suites continue to cover the selected implementation.

The branch's reusable `src/components/bookUploadFlow.ts` helper and its unit tests remain in the shared archive. The merged API accepts optional cancellation signals while retaining the feature branch's durable course-job methods. The alternate screen is not selected by the application, CI, or native deployment.

Dependency declarations did not change in the whole-book branch; the previously tested lockfile is retained rather than applying unrelated transitive dependency upgrades.

To check this preserved screen against the shared archive without selecting it in the application:

```sh
node node_modules/typescript/bin/tsc -p variants/whole-book-ingestion/tsconfig.json
```
