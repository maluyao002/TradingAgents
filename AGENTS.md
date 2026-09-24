# Development verification

- Use targeted tests by default: select regressions for changed behavior and the
  directly affected callers or integration boundaries. Explain the selected scope.
- Do not rerun the full suite after every edit, commit, or review fix. Expand testing
  only for broad/shared changes, release checkpoints, or an explicit user request.
- Run relevant lint and whitespace checks. Preserve existing hosted CI requirements;
  this preference does not authorize disabling required checks.
- Never use live research/model calls as a substitute for offline regressions without
  separate authorization. Preserve historical reports and evidence packets.

# PR merge closeout

- Treat documentation reconciliation as a default part of every PR merge; do not
  wait for a separate user request. Check both `docs/DEEP-RESEARCH-PROGRESS.md`
  (implementation record) and `docs/DEEP-RESEARCH-STATUS.md` (active roadmap).
- In the implementation record, capture the verified PR number/link, merge date
  and target, merge commit, delivered changes, review fixes, relevant test/CI
  evidence, and branch cleanup or retention. Mark earlier open-PR statements as
  historical rather than leaving them as current status.
- In the roadmap, mark only actually completed deliverables as done, reconcile
  the next sequence and remaining gaps, and distinguish engineering work from
  user approval/acceptance. A merge never renews live-run budgets, accepts a
  reader/financial model, or authorizes production activation.
- Prepare known delivery updates in the implementation PR when practical; verify
  the actual merge result before recording its receipt. Use the normal separate
  `codex/` branch and reviewed-PR workflow for post-merge edits, not direct writes
  to the integration branch or main. Do not claim a pending PR has merged.
- For documentation-only merges whose entries and roadmap are already accurate,
  verify that explicitly; do not create recursive bookkeeping PRs merely to log
  the bookkeeping merge itself. Mention any documentation update still pending
  integration in the final handoff.
- Preserve the original design and implementation plan, dated archive history,
  reports and evidence. Keep these two active records current instead of adding
  another competing roadmap. Link the relevant updated records in the handoff.
