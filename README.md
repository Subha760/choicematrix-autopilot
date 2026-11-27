# Choicematrix Autoposter

An approval-gated, single-host publisher for Facebook Pages, Instagram and Threads. It creates English afternoon and Hindi/Bengali evening artwork and optional music videos, sends the complete media and exact platform captions to the owner on Telegram, and publishes only the approved revision.

## Runtime and safety

- One persistent VM worker handles scheduling, Telegram updates, and publishing. Manual commands enqueue work on that same VM. GitHub runners never publish from a second state copy.
- SQLite is the authority for posts, Telegram offsets, jobs, publication attempts, audit events, and provider usage. Runtime databases and new approval history stay private. Existing committed history remains public until separately removed from Git history.
- Only the configured Telegram owner in their private chat can issue commands. Approval buttons bind the post ID, revision, and review message. Old slot-only buttons are rejected.
- Approval covers every ordered image and its SHA-256 hash, all captions, source, schedule, enabled platforms and destination accounts. Every public JPEG must match its local approved hash.
- Music video approval also binds the selected track, the complete MP4, its hash and audio provenance. A changed video or music choice requires a fresh preview and approval.
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

To edit a caption without regenerating the artwork, reply to the latest draft review message with `/caption fb Your new text`, `/caption ig Your new text`, `/caption threads Your new text`, or `/caption all Your new text`. Line breaks are preserved. Only enabled platforms can be edited. Threads (and `all` when Threads is enabled) allows 500 characters; Facebook/Instagram allow 2200. Text is never silently shortened. An edit creates a new revision, revokes approval, sends the full artwork and exact captions again, and requires a new approval. Expired drafts, stale review replies and drafts with publication attempts cannot be edited. A failed preview leaves the edit unapproved for a fresh preview retry.

The private HTML operations report supports text search and state/language filters, including a needs-attention view. It shows provider successes, failures and reported input/output tokens among the latest 100 usage events. Unreported tokens and billing costs are not estimated. Filters run locally without a server or external requests. Reports and database backups cannot be saved under the public `docs` directory.

## Music and video posts

The tracked configuration enables `music.enabled=true`, `music.mode="video"`, and `music.slide_seconds=7` for newly generated posts. The original carousel JPEGs remain available. The worker also creates a portrait 1080×1920 MP4, fits each complete slide without cropping, adds the selected music at a reduced volume with fades, and includes a six-second attribution end card. A six-slide post is normally 48 seconds. FFmpeg and FFprobe are required; the VM installer installs them and preflight checks their presence.

Automatic selection matches the post category/title to the curated tracks in `music_catalog.json`: electronic music for technology, reflective music for science/focus, bright music for creative/business topics, and an instrumental option for cultural topics. The selector avoids recent repeats when another matching track exists. Sensitive topics default to silence. It makes no extra AI calls and does not search trending songs or the Instagram music library.

The catalog contains five composer-listed CC BY 4.0 tracks by Kevin MacLeod with their exact source and license links. Audio downloads are restricted to the curated composer MP3 endpoint, checked for an audio stream, bounded in size, and cached privately under the runtime state directory. Music credit, source and license are shown during review; credit and an edit notice are also embedded in the finished video. Replacing the catalog requires checking the new track's source and license and retaining the appropriate attribution.

Telegram sends the finished video first, followed by all source slides and exact captions. Listen to the video and inspect the attribution before approving. Reply to the latest review message with `/music auto`, `/music none`, or `/music <track-id>` to change the selection. IDs: `shiny-tech`, `carefree`, `life-of-riley`, `dreams-become-real`, `dhaka`. The old approval is revoked before rendering a replacement, and the new revision must be approved again. `none` produces a silent video when video mode is enabled.

For a video-mode post, the worker publishes **one** approved output: an Instagram Reel, Facebook Page video, and/or Threads video on the enabled destinations. It does not also auto-publish a duplicate carousel. Known video containers resume processing without another upload. Both the public JPEGs and the exact public MP4 must be available before publishing starts. A music or render failure stops the draft rather than silently substituting a different track or format. MP4 files are capped below Telegram's standard 50 MB upload limit and are synced alongside their JPEGs when media sync is enabled.

To preserve image/carousel posting, set `music.mode="suggestion"`; track recommendations then appear in review but audio is not attached. Set `music.enabled=false` to disable automatic music selection for new drafts. Existing image-only approvals retain their fingerprints; use `/music auto` on an eligible draft to explicitly create and review its video version. Changing a track, caption or theme cannot erase an existing publication attempt.

Meta's [image/carousel publishing reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media) does not offer library music attachment for the current photo flow. Embedded video audio uses the documented video/Reel paths, rather than an invented music parameter: [Facebook Page videos](https://developers.facebook.com/docs/graph-api/reference/page/videos/), [Instagram media](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media), and [Threads video posts](https://developers.facebook.com/documentation/threads/posts).

Before deploying this update, stop the worker, install FFmpeg/FFprobe, run offline tests and online preflight, verify MP4 hosting and account permissions, then restart the one worker using the existing upgrade procedure. This implementation is tested with real local video encoding and mocked platform APIs; it does not claim a verified live social-publishing rollout.

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
# Install FFmpeg and FFprobe on PATH (Ubuntu: sudo apt-get install ffmpeg).
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
- `autoposter/music.py`, `video.py`, `music_catalog.json`: track matching, controlled source downloads and attributed video generation.

Platform analytics ingestion remains separate feature work. Music videos use the same owner approval and durable publication protections as image posts; deploying or restarting the live VM is a separate rollout step.
