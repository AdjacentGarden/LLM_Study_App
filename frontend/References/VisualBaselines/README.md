# Current-worktree WebKit baselines

Captured from the current React worktree, including its uncommitted changes. See manifest.json for exact source revision, dirty files, fixture scope, viewport, rendered routes and geometry.

Run with the old project's Vite server available at http://127.0.0.1:4178:

`node scripts/capture-web-baselines.mjs`

The script reads the old project and drives its in-memory AppProvider only. It writes screenshots and the manifest to this new repository. External/API requests are blocked. Static screenshots use Reduced Motion; test animation timing separately. Portrait captures cover all 28 routes and six sheet types. Core flows also have landscape baselines. Source simulated system bars should be excluded when comparing native system chrome.

Scenes: 159; failures: 0.
