# Phase 3 GitHub Issue Workflow

## Status

Live issue creation is complete. `gh auth status` succeeded as `gparadis`, and
the Phase 3 parent and child issue bodies were created from the tracked drafts.
The phase branch was pushed and pull request `#9` was opened against `main`.

## Parent Issue

Title: `Phase 3: Diagnostic ws3 Supply and Yield Replacement`

Body source: `planning/phase-3-issue-drafts/parent.md`

Issue number: `#2`

Child task references:

- `#3` P3.1 Establish ws3 harvest-output bridge and smoke-run baseline.
- `#4` P3.2 Build and compare BC mini supply variants.
- `#5` P3.3 Select and fixture SCANFI diagnostic inventory source.
- `#6` P3.4 Generate SCANFI diagnostic supply and LP variant.
- `#7` P3.5 Define Gauthier yield-policy gate and diagnostic evaluator.
- `#8` P3.6 Close phase with issue, branch, and PR hygiene.

## Child Issues

Each child issue body contains its subtask checklist.

| Task | Issue | Title | Body source |
| --- | --- | --- | --- |
| P3.1 | `#3` | `P3.1 Establish ws3 harvest-output bridge and smoke-run baseline` | `planning/phase-3-issue-drafts/p3-1.md` |
| P3.2 | `#4` | `P3.2 Build and compare BC mini supply variants` | `planning/phase-3-issue-drafts/p3-2.md` |
| P3.3 | `#5` | `P3.3 Select and fixture SCANFI diagnostic inventory source` | `planning/phase-3-issue-drafts/p3-3.md` |
| P3.4 | `#6` | `P3.4 Generate SCANFI diagnostic supply and LP variant` | `planning/phase-3-issue-drafts/p3-4.md` |
| P3.5 | `#7` | `P3.5 Define Gauthier yield-policy gate and diagnostic evaluator` | `planning/phase-3-issue-drafts/p3-5.md` |
| P3.6 | `#8` | `P3.6 Close phase with issue, branch, and PR hygiene` | `planning/phase-3-issue-drafts/p3-6.md` |

## Creation Commands

Run these after `gh auth status` succeeds from this repository checkout:

```bash
parent_url=$(gh issue create \
  --title "Phase 3: Diagnostic ws3 Supply and Yield Replacement" \
  --body-file planning/phase-3-issue-drafts/parent.md)

p31_url=$(gh issue create \
  --title "P3.1 Establish ws3 harvest-output bridge and smoke-run baseline" \
  --body-file planning/phase-3-issue-drafts/p3-1.md)
p32_url=$(gh issue create \
  --title "P3.2 Build and compare BC mini supply variants" \
  --body-file planning/phase-3-issue-drafts/p3-2.md)
p33_url=$(gh issue create \
  --title "P3.3 Select and fixture SCANFI diagnostic inventory source" \
  --body-file planning/phase-3-issue-drafts/p3-3.md)
p34_url=$(gh issue create \
  --title "P3.4 Generate SCANFI diagnostic supply and LP variant" \
  --body-file planning/phase-3-issue-drafts/p3-4.md)
p35_url=$(gh issue create \
  --title "P3.5 Define Gauthier yield-policy gate and diagnostic evaluator" \
  --body-file planning/phase-3-issue-drafts/p3-5.md)
p36_url=$(gh issue create \
  --title "P3.6 Close phase with issue, branch, and PR hygiene" \
  --body-file planning/phase-3-issue-drafts/p3-6.md)
```

After issue creation:

1. The parent issue body now references child issues `#3` through `#8`.
2. Each child issue body now starts with `Parent issue: #2`.
3. `ROADMAP.md` records the created parent and child issue numbers.
4. Commit the issue-number backfill.
5. Pushed `feature/p2-ffcm-ws3-diagnostic-yield` and opened pull request `#9`
   against `main`.
