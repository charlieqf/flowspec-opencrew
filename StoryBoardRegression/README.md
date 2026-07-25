# StoryBoardRegression

This folder is a copied regression bundle for Koubo StoryBoard slot matrix, Segment Truth, asset binding, and execution-state regression materials.

Current status:

- This bundle is documentation and evidence packaging, not a standalone test suite.
- Run executable regression commands from the repository root, not from the original author's local checkout.
- Copied scripts under `script/` are source snapshots. Only the items explicitly listed as runnable in `script/indexes/runbook.md` should be treated as gate candidates.
- Latest local verification on 2026-06-26: slot matrix comparison, backend contracts, archived Analysis V1 pytest, and the real Koubo StoryBoard UI runner pass (`run_id=20260626071732`, eight single-purpose UI tests).
- A regression pass can only be claimed after the runnable gate in `script/indexes/runbook.md` passes, including the real Koubo StoryBoard UI runner, and any extra UI cases touched by the change have fresh evidence.

Rules:

- Files here are copies. Original documents and scripts remain in their original locations.
- Implementation source code is not copied into this bundle.
- `docs/` and `script/` each contain exactly one level of category folders.
- Category folders contain files directly and must not contain nested folders.
- File names have source prefixes removed. When same-directory files would collide, source suffixes such as `__RootDocs` and `__OpenCrewDocs` are used.

Main entry:

- `Koubo_槽位矩阵与SegmentTruth回归测试金标准.md`

Indexes:

- `docs/indexes/source-index.md`: copied file to original source mapping.
- `docs/indexes/regression-scope.md`: what is included and excluded.
- `script/indexes/runbook.md`: current executable gate and archived-script status.
