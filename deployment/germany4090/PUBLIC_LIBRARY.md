# Discovery material storage on the 4090

Discovery PDFs and their shared OCR/layout artifacts live under
`/root/autodl-tmp/adaptive-book-ocr/data/public-materials`.

`catalog/<group_id>/manifest.json` identifies the course title and books. Each
book subfolder contains `source.pdf`, `ocr-pages.jsonl`, `structure.json`,
`diagnostics.json`, `grading.json`, `figures.json`, and a `figures` folder.
These are verified copies/hard links to the immutable content store in `blobs`;
hard links avoid storing the same PDF twice. Individual blob names are SHA-256.

Run `/root/autodl-tmp/adaptive-book-ocr/ops/public-library-admin list` to map
course names to folders. Removing a whole `catalog/<group_id>` folder withdraws
that course from Discovery on the next lookup. Browsing never recreates it.
`public-library-admin remove <group_id>` also withdraws it immediately. Existing
uploader originals and another course's shared blobs are retained; deleting a
Discovery course does not erase users' private learning data or uploads.

To add a PDF, copy it into `public-materials/incoming` and run
`public-library-admin sync`, or run `public-library-admin add /path/book.pdf`.
The resident 4090 workers perform actual OCR, PP-DocLayout figure extraction,
indexing, chapter reconstruction and shared diagnostic preparation. The book
appears after those artifacts are ready. Repeating sync reuses the same digest
and processing job. `public-library-admin verify` checks published asset hashes.

The admin wrapper loads the existing private runtime environment and current
release. No credentials are stored in this repository. Phone login and Discovery
browsing fetch metadata; full material transfer requires confirmed import.
Uploaded courses save core OCR locally before completion. Optional image transfer
continues independently and cannot make a ready chapter falsely report missing
original text.

Fresh image/video jobs default to `STUDIO_MEDIA_REVIEW_MODE=off`. The media
planner's suitability, citation and review classifications are advisory. Actual
provider errors and invalid media files remain visible, with durable request IDs
preventing duplicate paid submissions. Previously submitted jobs keep their
recorded request and policy.
