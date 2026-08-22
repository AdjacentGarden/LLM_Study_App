# Third-party notices

This inventory is part of the release evidence. It is not legal advice and
does not replace review of the complete dependency SBOM in
`backend/sbom.cdx.json` or the installed packages' license texts.

## Components requiring explicit release-owner review

- **PyMuPDF / MuPDF** — the package metadata describes dual licensing under
  GNU AGPL v3 or a commercial Artifex license. The release owner must record
  which valid route applies before distributing or operating this build.
- **BAAI/bge-m3** and **BAAI/bge-reranker-v2-m3** — model artifacts are
  downloaded separately at frozen revisions. Their model-card licenses,
  notices, and acceptable-use requirements must be retained with the deployed
  artifacts.

All remaining direct and transitive Python dependencies are enumerated in the
CycloneDX SBOM. Frontend package versions are recorded in `package-lock.json`;
their license texts must be retained with the release evidence.
No dependency entry grants a license to the BookCourse application source
itself; the repository owner must separately choose and publish that license.

## Community catalog source texts

The built-in community catalog contains complete PDF editions from Project
Gutenberg, not placeholder course records. The importer retains each source
PDF unchanged and verifies its byte length and SHA-256 digest before saving it.

- **Calculus Made Easy**, Silvanus P. Thompson — Project Gutenberg ebook
  [#33283](https://www.gutenberg.org/ebooks/33283).
- **The First Six Books of the Elements of Euclid**, Euclid / John Casey —
  Project Gutenberg ebook [#21076](https://www.gutenberg.org/ebooks/21076).
- **Utility of Quaternions in Physics**, Alexander McAulay — Project
  Gutenberg ebook [#26262](https://www.gutenberg.org/ebooks/26262).

Project Gutenberg marks these texts as not restricted by U.S. copyright law
and distributes them under its [license terms](https://www.gutenberg.org/policy/license).
The catalog UI shows that notice and reminds users outside the United States
to check the law in their own jurisdiction. Project Gutenberg names and
license headers remain part of the imported PDFs.
