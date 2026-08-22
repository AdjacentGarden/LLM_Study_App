# Production release attestations

The release manager must complete and sign this record for the exact commit
and deployment environment. Unchecked items are release blockers.

- [ ] Repository owner approved the BookCourse source-code license and added
  the corresponding `LICENSE` file.
- [ ] Legal owner approved either the AGPL obligations for PyMuPDF/MuPDF or a
  valid Artifex commercial license, with evidence retained outside the repo.
- [ ] BGE-M3 and BGE reranker model-card licenses/notices were retained with
  the frozen model snapshots.
- [ ] Privacy owner approved local-device storage, retention, logout, and
  shared-device behavior for learner data.
- [ ] DBA recorded target, backup, migration verification, and rollback plan.
- [ ] Target A100 canary returned `status=PASS`; its JSON, `nvidia-smi`, driver,
  CUDA, compute capability, model revisions, and devices are attached.
- [ ] `release-manifest.json` verifies against a clean, immutable release tag
  and the deployed frontend artifacts.
- [ ] Five independent release QA reports all pass this exact commit.

Release commit: ____________________

Environment: ______________________

Approver / date: ___________________
