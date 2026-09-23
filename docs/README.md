# Project documentation — start here

For day-to-day deep-research work, read **[Current status and next deliverables](DEEP-RESEARCH-STATUS.md)**.
It is the only active priority queue. The other documents have distinct jobs:

| Your question | Read this | Role |
| --- | --- | --- |
| Where are we, what is next, and what needs my input? | [Current roadmap](DEEP-RESEARCH-STATUS.md) | Current state, deliverables and acceptance gates |
| What system did we agree to build? | [Original system design](DEEP-RESEARCH-DESIGN.md) | Preserved architecture and product scope |
| What was the implementation and acceptance plan? | [Original implementation plan](DEEP-RESEARCH-PLAN.md) | Preserved M0–M6 requirements; not a current status report |
| What has actually been implemented and tested? | [Implementation record](DEEP-RESEARCH-PROGRESS.md) | Concise completed checkpoints and links to evidence |
| How do I run or diagnose it? | [Deep-research operating guide](DEEP-RESEARCH-USAGE.md) | Commands, configuration and operational boundaries |
| Why was a decision made, or what happened in an earlier run? | [Historical archive](archive/deep-research/README.md) | Dated workpapers, validation results and full history—not an active queue |

## Other project workflows

These are separate references, not extra deep-research implementation plans:

- [Codex integration](CODEX-INTEGRATION.md): subscription backend setup, isolation,
  capabilities and compatibility. Its Stage 1–5 labels describe that integration,
  not the deep-research roadmap stages.
- [Model profiles](../MODEL-PROFILES.md): exact role/model/effort assignments.
- [Weekly reports](WEEKLY-REPORTS.md): existing weekly application and deferred
  Chinese token/quality comparison TODO.
- [Weekly automation](WEEKLY-AUTOMATION.md): scheduling and recovery runbook;
  scheduling is outside the core research engine.
- [Prompt evaluation](../PROMPT-EVALUATION.md): existing analyst evaluation notes.
- [Project README](../README.md) and [changelog](../CHANGELOG.md): general setup
  and project release history.

## Which document wins?

- Design and original plan define scope and acceptance requirements. The current
  roadmap orders work and reports progress; it does not waive those requirements.
- The implementation record states what was delivered, not what is accepted.
- Archive records establish what happened at a particular time. Their old “next,”
  “latest,” and run-authorization statements are not today's instructions.
- A diagnostic, completed reader, eligible financial conclusion and accepted
  release are different outcomes. See the roadmap for the current gate status.

## Updating docs

Update the existing roadmap, implementation record or operating guide according
to its role. Do not create a new top-level plan for each PR. Keep substantial
dated evidence in the archive and link it from the implementation record.
Preserve original designs/plans and immutable local reports, evidence and usage;
archiving documentation does not authorize a live run or move any report artifact.
