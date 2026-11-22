# Choicematrix Autoposter

An approval-gated, single-host publisher for Facebook Pages, Instagram and Threads. It creates English afternoon and Hindi/Bengali evening carousels, sends their complete artwork and exact platform captions to the owner on Telegram, and publishes only the approved revision.

## Runtime and safety

- One persistent VM worker handles scheduling, Telegram updates, and publishing. Manual commands enqueue work on that same VM. GitHub runners never publish from a second state copy.
- SQLite is the authority for posts, Telegram offsets, jobs, publication attempts, audit events, and provider usage. Runtime databases and new approval history stay private. Existing committed history remains public until separately removed from Git history.
- Only the configured Telegram owner in their private chat can issue commands. Approval buttons bind the post ID, revision, and review message. Old slot-only buttons are rejected.
- Approval covers every ordered image and its SHA-256 hash, all captions, source, schedule, enabled platforms and destination accounts. Every public JPEG must match its local approved hash.
- Regeneration, a new preview, and theme changes revoke previous approval. Drafts with existing publication attempts cannot be replaced through normal approval controls.
- Each external mutation has a checkpoint. Known containers resume processing. Explicit authentication/transient rejections have bounded delayed retries; uncertain or interrupted mutations stop for account reconciliation. Reapproval never resets attempts.
- Content validation covers slides and badges. Renderers add no statistics or fake charts. RSS feeds supply dated topic headlines, not verified article bodies; headline-only drafts are labeled for owner verification. This does not independently prove every generated statement.
- Chromium shapes Hindi/Bengali and checks layout. Text that does not fit fails rather than silently truncating. A Pillow fallback is available explicitly for development.

## Install and configure

Use Python 3.12+ and a persistent Ubuntu VM. Run the installer from the intended production checkout as its non-root service account:

```bash
bash deploy/setup_vm.sh
```

The installer creates the environment and systemd service without starting or restarting production. The two older cloud setup scripts delegate to this installer. No cron process is needed.

Edit the VM `.env` (mode 600), using `.env.example` as the reference:

- Telegram: `TG_BOT_TOKEN`, numeric `TG_APPROVER_ID`. Start the bot in the owner's private chat. No Telegram webhook may be active.
- Generation: `GEMINI_API_KEY` and/or `GROQ_API_KEY`. `AI_PROVIDER_ORDER` selects primary/fallback providers; `GEMINI_MODEL` and `GROQ_MODEL` select models.
- Publishing: `META_PAGE_TOKEN`, `FB_PAGE_ID`, `IG_USER_ID`; optionally `THREADS_USER_ID` and a separate `THREADS_ACCESS_TOKEN`.
- Optional illustrative stock media: `UNSPLASH_ACCESS_KEY`. Photo provenance is retained. Without it, the renderer uses its designed background instead of unrelated stock-photo fallbacks.
- Media hosting: configure Git push authentication for the service account, `GITHUB_REPOSITORY`, `MEDIA_GIT_BRANCH` and `MEDIA_SYNC_ENABLED=true`. The worker stages only its generated JPEGs, refuses pre-existing staged changes, and never rebases running source. Alternatively host exact JPEG bytes under `PUBLIC_BASE_URL`.
- On the one production host only, set `ALLOW_LOCAL_PUBLISH=true` after migration/preflight. `.env.example` defaults to false.

`settings.json` contains brand, schedule, renderer and publishing controls. `publishing_enabled` remains an explicit gate; `approval_required` must be true. The evening language alternates by date. `platforms`, if provided, must be a list chosen from `fb`, `ig`, and `threads`; otherwise Facebook/Instagram are enabled and Threads is enabled when configured.

The default Gemini model is `gemini-3.5-flash-lite`, replacing the deprecated 2.5 Flash default. Check account model availability through online preflight. References: [Google model documentation](https://ai.google.dev/gemini-api/docs/models), [Groq supported models](https://console.groq.com/docs/models), [Unsplash documentation](https://unsplash.com/documentation).

## Upgrade an existing VM safely

1. Back up the existing JSON state, `.env`, deployed source revision, and JPEGs. Stop the legacy daemon and remove the matching cron job before deploying this version. Do not run old and new workers together.
2. Use the latest remote runtime state and existing media. Keep `state/posts.json`, `state/telegram.json`, and `state/generation-attempts.json` on the VM for first startup. On first read, SQLite imports them once. If using `AUTOPILOT_STATE_DIR`, copy the legacy JSON files there first.
3. Existing approved/pending drafts become `needs_review`; existing platform attempts and published IDs remain intact. Request `preview afternoon` or `preview evening` to review an eligible legacy draft again. Expired drafts cannot be approved.
4. Install the environment and run:

   ```bash
   .venv/bin/python autopilot.py --preflight --online
   .venv/bin/python autopilot.py --status
   .venv/bin/python autopilot.py --backup state/backups/initial.sqlite3
   ```

5. Inspect the configuration, credentials, selected model availability and Git push access. Configure the one production host's publishing gate. Then start the worker:

   ```bash
   sudo systemctl enable --now autopilot-daemon.service
   journalctl -u autopilot-daemon.service -f
   ```

A code rollback requires stopping the worker first. Do not run the legacy JSON-only publisher against the new database or restore an older approval snapshot as publishable state.

## Commands and review

```bash
# Queue work for the running worker; these do not launch another publisher.
.venv/bin/python autopilot.py --action draft_now
.venv/bin/python autopilot.py --action publish_now

# Private operational views and verified backup.
.venv/bin/python autopilot.py --status
.venv/bin/python autopilot.py --report .runtime/operations-2026-11-19.html
.venv/bin/python autopilot.py --backup state/backups/snapshot.sqlite3
```

`publish_now` processes only already approved, due, eligible posts; it bypasses neither approval nor the schedule. `draft_now` creates missing drafts without publishing. A bounded retry handles generation/preview delivery failures. `--once --action ...` is an exclusive maintenance cycle and requires stopping the daemon first.

Telegram commands: `status`, `preview afternoon`, `preview evening`, `recreate afternoon`, `recreate evening`, `/topic <idea>`, `/diagnose`, `token`. Approve/reject with the current preview buttons, or reply directly to that review message with `approve`/`reject`. Incidental words such as “okay” do not approve anything. Theme changes use the revision-bound preview button.

Uncertain results must be inspected on the target account. After stopping the worker, a known published ID can be reconciled with:

```bash
.venv/bin/python autopilot.py --reconcile POST_ID fb PUBLISHED_ID
```

Reconciliation verifies the destination account and exact caption through read-only platform requests. It never creates a social post or clears attempts for automatic retry. Instagram/Threads verification currently searches the latest 100 account media items. If the result is absent or ambiguous, the attempt remains blocked.

To restore, stop the worker and use `--restore BACKUP_FILE`. Restore verifies database integrity, backs up the current database, preserves newer publication evidence and Telegram offsets, and invalidates unpublished approvals.

## GitHub manual actions

The manual workflow connects to the existing VM and queues the selected action. Configure the `production` environment and these repository/environment secrets:

`AUTOPILOT_VM_HOST`, `AUTOPILOT_VM_USER`, `AUTOPILOT_VM_DIR`, `AUTOPILOT_VM_SSH_KEY`, `AUTOPILOT_VM_KNOWN_HOSTS`.

Use the VM's verified SSH host key in `KNOWN_HOSTS`. No automatic host-key trust is added. The SSH user must own the deployment and runtime state. The separate Tests workflow has read-only repository permissions and runs offline tests, Chromium rendering and syntax checks.

## Test locally without credentials or social writes

```bash
python -m pip install -r requirements.txt
python -m playwright install chromium
AUTOPILOT_LOAD_ENV=false python -m unittest discover -s tests -v
```

PowerShell: set `$env:AUTOPILOT_LOAD_ENV='false'` before running the test command. Tests use temporary state/media, block unexpected HTTP requests, and mock publication APIs. Chromium tests exercise English, Hindi, Bengali, actual theme overrides, escaped HTML and overflow rejection. They skip only when Chromium is not installed. Never use the older root `test_*.py` exploratory scripts as the safe suite; some intentionally call live services.

## Files

- `autopilot.py`: content/calendar and artwork helpers plus compatibility entry points.
- `autoposter/review.py`: authorization, callback identities and approval manifests.
- `autoposter/storage.py`: SQLite, atomic reservations, job queue and OS lock.
- `autoposter/runtime.py`: scheduling, preflight and single worker.
- `autoposter/platforms.py`: checkpointed publishing adapters.
- `autoposter/content.py`, `rendering.py`, `media.py`: validated provider fallback and visual generation.
- `autoposter/operations.py`, `report.py`: reconciliation, restore and private operations report.

Reels/video generation and platform analytics ingestion remain separate feature work. This upgrade fixes the existing static-carousel pipeline; it does not claim those features or a verified live production rollout.
