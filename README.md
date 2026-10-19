# Social Autopilot — mixed daily content, approval required

**Current state:** Complete downloadable source project with 22 passing local tests (no paid tools used in tests). **Not deployed, not connected to Meta/Telegram/Gemini, not live-post tested.** Default `publishing_enabled=false` ensures no Facebook/Instagram publishing during setup.

A free-tier design for an original, **single image + caption** every day on your own Facebook Page and Instagram Professional account. No employee, editor, designer or computer running locally is needed after one-time account/API setup. **Only the user's Telegram approval can authorize a social post**. No approval = no posting.

## Weekly schedule (Asia/Kolkata)

| Day | Pillar | Content standard |
|---|---|---|
| Monday | Productivity | One actionable, original organizing/focus tip |
| Tuesday | Digital Safety | One code-selected fact from CISA |
| Wednesday | Science & Nature | One code-selected fact from NASA or USGS |
| Thursday | Everyday Life | One non-medical household or learning idea |
| Friday | Sustainable Living | One modest reuse or low-waste action |
| Saturday | Creativity & DIY | One safe, simple, original craft or creativity exercise |
| Sunday | Culture & Learning | One fact grounded in a curated UNESCO explainer |

Configuration: [`content_calendar.json`](content_calendar.json) (pillar names, directions, code-selected factual seeds, exact source URLs), [`settings.json`](settings.json) (brand, audience, language, generation/publishing times and safety flags). The selected fact rotates weekly and on owner-triggered regeneration.

## End-to-end automated process

1. GitHub Actions wakes approximately twice every hour (best effort).
2. At/after **09:00 IST**, if today has no draft, it chooses that weekday's pillar, calls Gemini 2.5 Flash-Lite to write a new English headline/subtitle/caption, rejects obvious duplications or risky/unattributed content, and generates an original **1080×1350 JPEG** using only Pillow shapes and local fonts; no online photo or copyrighted music used.
3. Telegram bot privately sends you the **full image and full caption** with **Approve / Regenerate / Reject** buttons. Curated-source facts retain the institutional source URL in the actual draft. The user ID, date and revision are validated.
4. Rejection never publishes. Regeneration changes the revision and invalidates earlier approval buttons. No response leaves the post pending. After 48 hours from the start of its scheduled day, it expires safely.
5. After owner approval and **19:00 IST**, the system verifies the exact approved text/image hash and provenance, the public JPEG URL, and publishing eligibility. It persists a *may-have-published* checkpoint to GitHub **before** making the publishing API call to avoid blind duplicate retries if the job crashes.
6. Facebook Page photo publish and Instagram container/media publish are tracked **separately**. Success and failures are sent to Telegram. When a result is ambiguous, it is blocked for manual reconciliation instead of silently reposting.
7. On changes to the artwork, the same workflow **explicitly deploys** images to GitHub Pages. This is important: standard GitHub Actions `GITHUB_TOKEN` commits do **not** trigger branch-based Pages builds.

Post time is *at/after* 19:00 IST, not guaranteed 19:00 exactly. Workflows, Instagram processing, and public image deployments can be delayed. Each approved card is submitted to both accounts with the same caption and visual.

## One-time setup (follow in order)

1. **Make a NEW, PUBLIC repository** for this project in GitHub (don't mix this into another app). Upload the contents of this folder so `autopilot.py`, `.github/workflows/automation.yml`, `content_calendar.json`, `docs/index.html`, `settings.json` and `state/*` are at the repo root. This is the only project this package assumes. The free runner/Pages approach requires public content; **drafts and images in the public repository are not private**, even before social-media approval. Never include personal information.
2. **Enable GitHub Actions write permissions.** Repository `Settings → Actions → General → Workflow permissions`: grant `Read and write permissions` so the bot can commit state. Avoid branch rules that reject the built-in bot commits.
3. **Enable Pages via GitHub Actions, NOT via the branch.** Repository `Settings → Pages → Build and deployment → Source`: choose **GitHub Actions**. The supplied workflow performs upload/deployment itself whenever new image assets are committed or you run it manually. Confirm your Page deployment URL is public and works in a private browser window. Typical URL: `https://USERNAME.github.io/REPOSITORY/` (the special `USERNAME.github.io` repository has no repo path).
4. Create a **dedicated Telegram bot** using [@BotFather](https://t.me/BotFather). Start the bot in a private chat and determine your *numeric* Telegram user ID (which is normally also your personal chat ID). Ensure the bot does not have a webhook configured: the code uses Telegram's `getUpdates` polling API. Don't share the bot token.
5. Create an API key in [Google AI Studio](https://aistudio.google.com/apikey). Select a key/project eligible for Gemini 2.5 Flash-Lite's free tier in your region. Limits and eligibility can change. Do not enable paid Gemini billing if you require an absolute cost cap. Free-tier submissions may be used to improve Google's products; **never send confidential text as draft inputs**.
6. Configure a Meta developer app and access token with required rights for the **Facebook Page** (not a personal profile) and a connected **Instagram Professional/Creator** account using *Instagram API with Facebook Login*. Meta app mode, role and permission review are account-dependent. Use an appropriate Page Access Token with necessary Page posting and Instagram publishing permissions. Obtain `FB_PAGE_ID` and the linked `IG_USER_ID`. Set a token renewal procedure: API tokens may expire or be invalidated.
7. In repository `Settings → Secrets and variables → Actions`, add these **secrets** (never plain committed files):

   | Secret | Expected value |
   |---|---|
   | `GEMINI_API_KEY` | Google Gemini API key |
   | `TG_BOT_TOKEN` | BotFather Telegram token |
   | `TG_APPROVER_ID` | Your numeric Telegram user ID |
   | `META_PAGE_TOKEN` | Authorized Meta Page token |
   | `FB_PAGE_ID` | Facebook Page ID |
   | `IG_USER_ID` | Instagram professional account ID |
   | `THREADS_USER_ID` | (Optional) Threads User ID for Threads posting |
   | `THREADS_ACCESS_TOKEN` | (Optional) Threads User Token (or use META_PAGE_TOKEN) |

   Optional **variables**: `PUBLIC_BASE_URL` (Pages root URL; detected from `GITHUB_REPOSITORY` by default), `GEMINI_MODEL` (`gemini-2.5-flash-lite` default), and `META_API_VERSION` (`v26.0` configured default, review as versions change).
8. Edit the safe placeholder brand (`"Daily Spark"`) in `settings.json` to match the name on your Page. Set audience/language if needed. **English is the only artwork typography tested**; scripts like Hindi/Bengali need font-shaping and design testing before production.
9. From `Actions → Mixed Content Social Autopilot → Run workflow`, run once, **after 09:00 IST** if you want a preview immediately. If your app's Page hosting isn't active, re-run manually to trigger Pages deploy. Check Telegram for full artwork + text. Approve, reject, and regenerate a TEST draft and confirm state changes; nothing is published with the default safety flag.
10. Only after you've inspected permissions, correct IDs, accessible image URL, and the relevant Meta API publishing setup, edit `settings.json` to set `"publishing_enabled": true`. **Warning:** any still-approved eligible current-day draft can then publish when the next run starts. Reject unwanted drafts first.

## Important boundaries and safeguards

- **No auto-approval:** Missing owner click, wrong Telegram ID, revoked approval, stale date, changed approved text, changed image bytes, missing required source and disabled publishing all prevent a new publish API call.
- **No hired people:** Only you approve. Third-party APIs and hosting execute the other steps. Account creation, first-time authentication, app permissions and occasional reauthorization still require the account owner.
- **Copyright:** The artwork is drawn programmatically with abstract shapes and locally installed fonts, not scraped photos, reposted content, celebrities or music. The AI is instructed not to reproduce quotes, articles, lyrics or slogans. Similarity filters reject repetitions **within your own history**, not against the entire internet; **no system can guarantee copyright clearance** without legal review.
- **Facts:** Curated official-source seed facts (CISA, NASA, USGS, UNESCO) are used instead of AI-invented citations. The model's words are still not independently fact-checked phrase by phrase. The person approving should check factual truth, licenses and suitability before publishing.
- **No breaking news or automated trend scraping:** This mixed-content v2 uses evergreen material and does not copy others' feeds. Live news requires verified ingestion, attribution and additional moderation; it isn't included.
- **Limited output:** Static single-photo posts only. Reels, video, music, Stories and carousels require separate tools, permissions and review.
- **No guarantees of zero charges or uptime:** Free-tier availability, rate limits, API behavior, restrictions and Meta app review requirements can change. GitHub may delay/drop scheduled runs and may disable scheduled workflows after prolonged inactivity. Source code is designed for low usage and without paid subscriptions, not guaranteed zero operational intervention.
- **Checkpoint risk:** GitHub checkpoint commits reduce blind retries after API timeouts, but still require user reconciliation after uncertain outcomes. They do not provide distributed exactly-once posting guarantees.
- **State is publicly visible** in a public repo (draft captions, state and media references). API keys, Telegram user ID and page access tokens must stay in Actions Secrets; `docs/` and repository files are never secret.

## Run local tests safely

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Twenty-two mocked/unit tests exercise weekday rotation, source seed selection, content safety rejections, image properties, full Telegram review, owner authorization, approval fingerprints, image integrity, time window, stale rejection, checkpoint failure, independent platform states, and nonretry of ambiguous outcomes. They **do not** claim real Meta publishing or live Gemini generation has been performed.

## Useful official references

- [Meta — Instagram API with Facebook Login](https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login)
- [GitHub — Scheduled workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [GitHub — Pages via Actions](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [Google — Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)

**Security:** Never paste real tokens into commits, messages, screenshots, public issues or a ZIP intended for sharing. Rotate any credential that leaks.
