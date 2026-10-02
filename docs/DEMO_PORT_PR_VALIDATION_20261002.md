# Demo frontend PR integration validation — 2026-10-02

This PR directly mounts the Demo page tree and adapts the existing backend to its contracts. The frontend source snapshot is from `dee6d691d08c857c36de82230ea0a13205a11d9a` in `study_app_demo`, including the latest course browsing, study stickers and settings changes. See [API contracts](DEMO_DIRECT_PORT_API_20261002.md), [migration plan](FRONTEND_DEMO_DIRECT_PORT_PLAN_20261002.md) and [latest source synchronization](FRONTEND_DEMO_DELTA_ACCEPTANCE_20261002.md).

## Integration baseline

The PR branch is based on upstream `main` commit `6d01e7fb237f51daeef1fa4d79d0b9c5bc92a4e3`. The account, grounded QA, model deadline/refusal, content validation and text rendering improvements from the four upstream commits after the original local baseline are retained. The original checkout and its uncommitted changes are preserved.

The migration includes the complete runtime and TypeScript source closure, required branding/font assets, backend routes and persistence, and small committed Markdown/CSV browser test fixtures. Runtime databases, account cookies, generated documents, screenshots, dependencies, keys and local model/textbook artifacts are excluded.

## Behavior and integration repairs

- The default frontend uses `demo/DemoDirectEntry` and the original Demo pages/styles. Courses, uploaded materials, directory edits, notes, reports, credits and PDF exports use account-scoped server data. The fixture repository does not seed real accounts.
- Settings logout calls the real account API, blocks duplicate/pending dismissal, displays failure with retry, and restores the original account's data after a new login.
- Existing account, social, learner-profile and Studio tools remain available. Their heavy pages load on demand through the upstream lightweight Studio shell and React Suspense.
- The retained profile font control applies and persists a bounded 100–200% size locally to those tools. The surrounding Demo page layout keeps its existing defaults.
- Reconstructed child sections can lack an independent summary. The compiler now falls back to deduplicated verbatim quotations from the selected section evidence, bounded to 680 characters. Same-page section tests verify that another section and the parent summary do not leak into the result.
- Existing quality checks target the active Demo entry and styles: static import reachability, text contrast, touch targets, keyboard focus, active navigation and consumed typography.
- Browser fixtures are committed under `frontend/e2e/fixtures/materials/`. Session state and PDF downloads use Playwright's output directory. The narrow `.gitignore` exception for `frontend/src/demo/data/` includes source fixtures without including runtime data.
- Backend test portability fixes retain their functional assertions: effective proxy environment values on Windows, bounded retrieval-budget mocks, short avatar parameter IDs, and explicit optional-fixture/POSIX skips.

## Validation on the merged PR checkout

| Check | Result |
| --- | --- |
| `npm ci --no-audit --no-fund` | Passed from the committed lockfile |
| `npm run build` | TypeScript and Vite passed; heavy tools emitted as separate chunks |
| `npm run test:demo` | 55 files, **360 passed** |
| `npm run test:unit` | **34 passed**, no skips |
| Full `backend/tests` | **464 passed, 7 skipped**, 99.38 seconds |
| Ruff on the summary compiler and touched portability tests | Passed |
| Original Chromium direct-port scenario | **1 passed**, 12.8-second scenario |
| Updated Chromium settings scenario | **1 passed**, approximately 1.3 minutes |
| Independent tools browser check | Social, account, profile and Studio lazy pages opened; no page errors |
| Independent font/layout browser check | Computed text size 20px to 40px, persisted after remount; no horizontal overflow at 402/820/1440px |

Both end-to-end scenarios used separate fresh backend data directories, the real local HTTP API and the committed small input fixtures. They cover onboarding, two material types, course persistence, directory confirmation, source annotations and refresh, actual PDF download, report empty states, unavailable-provider feedback without credit loss, social access and A-to-B-to-A account isolation. The settings scenario additionally covers cancel, pending-state dismissal prevention, simulated HTTP 503 with retained identity, successful real logout, refresh and real login after the OTP cooldown.

The full backend run used the existing dependency environment with explicit `PYTHONPATH` pointing at the merged checkout, an isolated `APP_DATA_DIR`, a new pytest `--basetemp`, `-p no:cacheprovider` and `STUDIO_CJK_FONT` set to the installed Windows CJK font. The seven skips are five existing optional full-book fixtures, one missing optional biology OCR artifact and one POSIX-only process-group test on Windows. The three warnings are dependency deprecations.

## Reproduction

Install frontend dependencies with `npm ci` in `frontend`, then run `npm run build`, `npm run test:demo` and `npm run test:unit`. Install the backend development and PDF extras for a clean Python environment; run `python -m pytest backend/tests -p no:cacheprovider` with an isolated data directory and an available CJK font for the Studio renderer.

For browser tests, start the backend with a new `APP_DATA_DIR`, `AUTH_DEMO_MODE=1`, `LLM_PROVIDER=rules`, `OCR_WORKER_ENABLED=0`, and `CORS_ORIGINS` matching the frontend origin. Start Vite with `VITE_API_PROXY` targeting that backend. Set `DIRECT_PORT_E2E=1` and `DIRECT_PORT_URL` to that Vite origin, then run each explicit spec:

```text
npx playwright test e2e/demo-direct-port-20261002.spec.ts --workers=1
npx playwright test e2e/demo-delta-20261002.spec.ts --workers=1
```

Use separate fresh data directories for the two scenarios. The settings scenario deliberately respects the 60-second OTP cooldown. The specs select Chromium; its Playwright browser installation must be available. Historical tests for the replaced frontend were not used as evidence of the new page tree.

## Limits

The remaining Vite warning is the main Demo chunk exceeding 500 kB (579.52 kB minified, 180.74 kB gzip in the final build). Live external LLM, vision, OCR, speech and video-provider success was not validated with real credentials; success paths in unit tests use deterministic substitutes, and browser tests validate honest unavailable-provider feedback. The optional full-book fixtures and Linux process-group termination were not exercised on this Windows checkout. This PR does not include deployment verification.
