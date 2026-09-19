# Development verification

- Use targeted tests by default: select regressions for changed behavior and the
  directly affected callers or integration boundaries. Explain the selected scope.
- Do not rerun the full suite after every edit, commit, or review fix. Expand testing
  only for broad/shared changes, release checkpoints, or an explicit user request.
- Run relevant lint and whitespace checks. Preserve existing hosted CI requirements;
  this preference does not authorize disabling required checks.
- Never use live research/model calls as a substitute for offline regressions without
  separate authorization. Preserve historical reports and evidence packets.
