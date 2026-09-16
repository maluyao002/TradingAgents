# Weekly watchlist reports

The weekly runner uses the same full research graph and acceptance gate as the
interactive CLI. It has no broker integration and never falls back from Codex to
an API provider. Publishing is a separate, resumable step performed through the
connected Google Drive tools in Codex; the Python code has no Google credentials.

## Run locally

From the repository with its locked environment installed:

```sh
.venv/bin/python -m cli.weekly --config config/weekly-watchlist.json --dry-run
.venv/bin/python -m cli.weekly --config config/weekly-watchlist.json --resume
```

The first command validates and displays the configuration without inference or
provider requests. The second starts this week's batch or resumes its unfinished
work. Keep the computer awake and online during execution. On macOS, `caffeinate
-i` can wrap the second command to prevent idle sleep while it runs.

The sample watchlist contains AMD, INTC, NVDA, AVGO, and TSM (the US-listed ADR).
It selects the Codex backend, Balanced profile, English, all four analysts, and
two research and risk discussion rounds. Edit the JSON for subsequent batches;
an existing batch uses its saved configuration. Paths in the configuration are
resolved relative to the configuration file, not the shell's current directory.

For a separate pilot, without claiming a scheduled weekly batch:

```sh
.venv/bin/python -m cli.weekly --config config/weekly-watchlist.json \
  --batch-id pilot-2026-09-16 --tickers AMD INTC --resume
```

The analysis date is the actual local execution day in America/Los_Angeles.
The reporting-week identifier is not an assertion that evidence was available at
Friday's close. Retrieval timestamps, provider publication dates, missing-data
flags, and provisional price warnings retain their existing meanings. Delayed
runs must be described as catch-up research with their actual analysis dates.

## Authentication and recovery

Use the existing dedicated Codex runtime home, normally
`~/.tradingagents/codex`. It must already be authenticated with ChatGPT. The runner
does not copy the desktop app's credentials or initiate interactive login.
Balanced model/effort availability is checked before research.

The deployment's FRED credential is selected explicitly by its macOS Keychain
label and account. Credential values stay out of configuration, manifests, and
logs. An environment-provided FRED key is also supported. An OpenAI API key is
not required. Configure any other provider credentials only when selecting that
provider.

Each batch contains an atomic `manifest.json`, per-company attempt state, report
exports, and a `publication` staging directory. The manifest is the source of
truth for progress. Do not edit it while a runner or publication command is
active. Locks reject overlapping runs; interruption releases the operating-system
lock rather than requiring a stale lock-file deletion.

Successful research is saved before report export. A retry can therefore recover
an export or upload without buying the research again. Each company has a
30-minute allowance, including at most one retry of a transient inference error;
individual model calls retain their 300-second deadline. Authentication and
explicit quota exhaustion stop new inference for the remaining companies.
Company-specific failures leave other companies eligible to run.

Reports marked **Needs review** are diagnostics from the existing acceptance
gate. The digest displays `REVIEW` rather than treating their raw decision prose
as an accepted rating. Completed, degraded, failed, blocked, and unfinished
companies remain visible independently.

## Google Drive publication

Prepare importable HTML and a digest from the saved reports:

```sh
.venv/bin/python -m cli.weekly_publish prepare --manifest /absolute/path/manifest.json
.venv/bin/python -m cli.weekly_publish resume-plan --manifest /absolute/path/manifest.json
```

The HTML is a format conversion of the full Markdown report, not a rewritten or
shortened report. It preserves headings, lists, tables, links, and inline evidence
references. Raw evidence, model telemetry, checkpoint state, and credentials are
not uploaded. Those diagnostics remain local.

Use Codex's connected Drive tools to import each HTML file as a native Google
Doc. Record returned IDs immediately using `record-folder` and
`record-document`, then read back the actual document and use `mark-verified`
only when content verification succeeds. See `python -m cli.weekly_publish
--help` and each subcommand's help for arguments.

Always reconcile uncertain imports by exact title inside the recorded folder.
Do not create a replacement merely because a tool response was interrupted.
Readback must confirm the complete report's section order, text, tables, and
links. The final digest is prepared again after company links are recorded so it
can link to the verified reports. Failed or incomplete companies are named
explicitly. No publication step reruns analysis.

## Schedule

Use a Codex task automation on Saturdays at 09:00 America/Los_Angeles. The saved
prompt should follow [the automation runbook](WEEKLY-AUTOMATION.md), with the
absolute checkout, configuration, and private Drive parent-folder ID supplied
in its deployment settings. Keep machine-specific IDs and live manifests outside
the repository.

Local scheduled tasks need the computer on and the desktop app running. A missed
schedule is not a guaranteed historical backfill; use the runner's resume command
when available and label any delayed research. Existing unrelated automations,
including the separate AMD incremental update, are unchanged.

Enable scheduling only after an unattended AMD/INTC pilot, a full watchlist run,
and a verified Drive publication have succeeded. Offline tests verify failure
handling and report conversion; they do not prove provider availability or model
research quality.
