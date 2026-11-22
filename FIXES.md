# Autoposter fixes — 19 November 2026

Implemented on `codex/fix-autoposter`, based on GitHub revision `1c54cbf`. The original checkout's six edited JPEGs and the newer committed runtime/media history were preserved.

## Implemented

- Owner/private-chat authorization for the active Telegram dispatcher; exact command parsing and duplicate-update handling.
- Post/revision/message-bound buttons; stale preview rejection; approval invalidation after edits, regeneration or theme changes.
- Ordered hashes for every image, exact per-platform captions, sources, schedules and destination accounts in the approval digest. Public bytes are checked before publishing.
- Private SQLite migration, unique reservations, audit events, queued manual jobs and a cross-process worker lock. Legacy approvals require fresh review; existing external IDs and uncertain attempts are preserved.
- Checkpoints around external mutations, resumable known containers, bounded retries for explicit authentication/transient rejections, and no blind retry of uncertain results.
- Read-only account reconciliation, verified backups, conservative restore and accurate partial-success notifications.
- Validation of full slide content and language, primary/fallback provider selection, usage records, configurable models, real feed timestamps and explicit headline-only source labeling.
- Removed invented visual statistics/charts and unrelated stock-photo fallbacks. Photo origin metadata is retained.
- Shared four-theme Chromium/Pillow rendering, managed-browser discovery, escaped HTML, isolated browser network access, Indic typography, and overflow rejection.
- Checked complete Telegram preview delivery before enabling review.
- A single-worker VM installer, functional manual GitHub-to-VM commands, pinned dependencies, CI and corrected ignore patterns.
- A private offline calendar/queue/status report and current setup/upgrade documentation.

## Verification

**79 tests passed locally**, including actual Chromium renders with no skipped rendering checks. The deterministic suite covers active daemon authorization, stale previews, all-slide integrity, corrupted migration state, duplicate reservations, cross-process locking, crash recovery, container resumption, retries, backup/restore, reconciliation, scheduling and rendering. It blocks unexpected HTTP requests and uses temporary state/media.

Chromium render tests cover English, Hindi, Bengali, all four theme overrides and escaped input. Local samples were visually inspected for missing glyphs and clipping. Shell syntax, Python compilation, dependency consistency and whitespace checks also passed.

Test log: `.runtime/final-test-results.log`. Visual samples: `.runtime/visual-qa/contact-sheet.jpg`. Private report preview: `.runtime/operations-preview.html`. These local QA artifacts are ignored by Git.

## Production handoff

No VM was restarted, credentials changed, branch pushed, or live social post created during this work. This checkout has no production credentials; offline preflight confirms configuration/browser availability and correctly reports missing runtime credentials/permission.

Use the upgrade procedure in README before rollout: back up legacy state/media, stop the old daemon and matching cron, deploy the new source/environment, import the latest state once, run online preflight, configure the persistent host's publishing/media gates, and start only the new worker. Configure the VM SSH secrets before using the manual GitHub workflow.

Reels/video, externally ingested platform analytics, and a hosted interactive dashboard remain separate features. The included report is an offline operations view.
