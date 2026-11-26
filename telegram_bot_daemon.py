"""Single authorized Telegram consumer and scheduled autoposter worker."""
import copy
import json
import os
import platform
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
import autopilot
from autoposter import review
from autoposter.storage import worker_lock

ROOT = autopilot.ROOT
STATE = autopilot.STATE
TG_STATE = autopilot.TG_STATE
GEN_ERRORS = autopilot.GEN_ERRORS
ARCHETYPE_LABELS = {0: 'Cyber Grid', 1: 'Royal Luxury', 2: 'Minimalist Neo', 3: 'Radiant Solaris'}


def load_env():
    autopilot._load_env_fallback()


def read_json(path, default):
    return autopilot.read_json(path, default)


def write_json(path, data):
    autopilot.write_json(path, data)


def tg_api(method, payload=None):
    # One client: checked HTTP/body responses, credential-safe errors, plain text by default.
    return autopilot.telegram(method, payload)


def git_push_state():
    """Compatibility hook: private state is committed to SQLite, never pushed to GitHub."""
    return None


def get_system_diagnostics():
    disk = shutil.disk_usage(ROOT)
    return ('AUTOPILOT SYSTEM DIAGNOSTICS\n'
            f'Host Platform: {platform.system()}\n'
            f'Disk Space: {disk.free // (1024**2)} MiB free\n'
            f'Storage: SQLite ({autopilot.state_store().path.name})\n'
            'Runtime: one worker; owner-only private review\n')


def check_meta_token_health(force=False, reply_chat_id=None):
    state = read_json(TG_STATE, {'offset': 0})
    previous = state.get('meta_health', {})
    if not force and time.time() - previous.get('checked_at', 0) < 21600:
        return previous.get('ok', False), previous.get('detail', 'Unknown')
    token, page = autopilot.getenv('META_PAGE_TOKEN'), autopilot.getenv('FB_PAGE_ID')
    ok, detail = False, 'META_PAGE_TOKEN or FB_PAGE_ID missing'
    if token and page:
        try:
            data = autopilot.meta_request('GET', page, token, {'fields': 'id,name'})
            ok, detail = bool(data.get('id')), 'Page token is accessible' if data.get('id') else 'Page identity missing'
        except Exception:
            detail = 'Page token check failed; inspect permissions and expiry'
    state['meta_health'] = {'checked_at': time.time(), 'ok': ok, 'detail': detail}
    write_json(TG_STATE, state)
    if not ok and (not previous or previous.get('ok')):
        autopilot.safe_notify(detail)
    return ok, detail


def target_entry(posts, target_key=None, target_slot=None):
    if target_key:
        return posts.get(target_key)
    today = datetime.now(autopilot.IST).date().isoformat()
    candidates = [p for k, p in posts.items() if k.startswith(today)
                  and (target_slot is None or p.get('slot') == target_slot)]
    return candidates[0] if len(candidates) == 1 else None


def approve_pending_post(posts, approver_name='Owner', target_key=None, target_slot=None, now=None):
    entry = target_entry(posts, target_key, target_slot)
    if entry is None:
        return False, 'Choose a current draft using its preview buttons.'
    now = now or datetime.now(autopilot.IST)
    settings = read_json(ROOT / 'settings.json', {})
    try:
        review.approve(autopilot.ROOT, entry, now, autopilot.post_is_stale(entry, settings, now))
    except ValueError as exc:
        return False, str(exc)
    write_json(STATE, posts)
    autopilot.state_store().event('approved', {'post': entry['date'], 'revision': entry['revision']})
    return True, 'Approved for ' + entry['publish_time_ist'] + ' IST.\n' + review.result_text(entry)


def reject_pending_post(posts, approver_name='Owner', target_key=None, target_slot=None):
    entry = target_entry(posts, target_key, target_slot)
    if entry is None or not review.can_replace(entry):
        return False, 'Publication history exists or draft is ambiguous; use status and reconcile.'
    entry.update(status='rejected', approved_at=None, approval_hash=None)
    write_json(STATE, posts)
    return True, 'Draft rejected.'


def recreate_and_send_post(posts=None, approver_name='Owner', target_key=None, target_slot=None):
    posts = posts if posts is not None else read_json(STATE, {})
    if target_key:
        slot = (posts.get(target_key) or {}).get("slot") or (
            target_key.rsplit("_", 1)[-1] if "_" in target_key else "afternoon")
    elif target_slot:
        slot = target_slot
        target_key = datetime.now(autopilot.IST).date().isoformat() + '_' + slot
    else:
        return False, 'Specify afternoon or evening.'
    previous = posts.get(target_key)
    if not review.can_replace(previous):
        return False, 'Cannot regenerate a post with publication attempts.'
    settings = read_json(ROOT / 'settings.json', {})
    entry = autopilot.create_post(target_key, settings, posts, int((previous or {}).get('revision', 0)) + 1, slot)
    return True, f'New pending draft r{entry["revision"]}; review its new buttons.'


def switch_theme_and_send_post(posts=None, approver_name='Owner', target_key=None, target_slot=None, mode=None):
    posts = posts if posts is not None else read_json(STATE, {})
    entry = target_entry(posts, target_key, target_slot)
    if not entry or not review.can_replace(entry) or not entry.get('slides'):
        return False, 'Select an unpublished draft with slides.'
    original = copy.deepcopy(entry)
    entry.update(status='needs_review', approved_at=None, approval_hash=None, preview_delivered=False)
    write_json(STATE, posts)  # Revoke the old approval before any rendering work.
    new_mode = int(mode) % 4 if mode is not None else (int(entry.get('design_mode', 0)) + 1) % 4
    revision = entry['revision'] + 1
    settings = read_json(ROOT / 'settings.json', {})
    candidate = copy.deepcopy(entry)
    candidate.update(revision=revision, status='pending')
    candidate.pop("photo_credits", None)
    images = autopilot.render_carousel_slides(entry['date'], revision, settings, candidate,
                                              slot=entry.get('slot', 'afternoon'), forced_design_mode=new_mode)
    candidate.update(images=images, image=images[0], preview_delivered=False, review_message_id=None)
    autopilot.prepare_review(candidate, settings)
    posts[entry['date']] = candidate
    write_json(STATE, posts)
    autopilot.state_store().event('theme_changed', {'post': entry['date'], 'previous': original, 'revision': revision})
    try:
        autopilot.persist_media(candidate)
        autopilot.send_preview(candidate, settings)
    except Exception:
        candidate['status'] = 'preview_failed'
        write_json(STATE, posts)
        raise
    write_json(STATE, posts)
    return True, f'Theme changed to {ARCHETYPE_LABELS[new_mode]}; new approval required.'


def create_custom_topic_post(topic_text, slot=None, approver_name='Owner', posts=None):
    if not topic_text or len(topic_text) > 1000:
        return False, 'Provide a topic of 1–1000 characters.'
    posts = posts if posts is not None else read_json(STATE, {})
    slot = slot or ('evening' if re.search(r'[\u0900-\u09ff]', topic_text) else 'afternoon')
    key = datetime.now(autopilot.IST).date().isoformat() + '_' + slot
    if not review.can_replace(posts.get(key)):
        return False, 'Publication history exists; choose a future draft instead.'
    settings = read_json(ROOT / 'settings.json', {})
    autopilot.create_post(key, settings, posts, slot=slot, custom_topic=topic_text)
    return True, 'Custom topic draft delivered for review.'


def dispatch_update(update, posts, settings, now=None):
    now = now or datetime.now(autopilot.IST)
    owner = autopilot.getenv('TG_APPROVER_ID', True)
    query = update.get('callback_query')
    message = query.get('message', {}) if query else update.get('message', {})
    sender = query.get('from', {}) if query else message.get('from', {})
    if not review.authorized(sender, message.get('chat', {}), owner):
        return
    action, key, revision = None, None, None
    if query:
        parsed = review.parse_callback(query.get('data', ''))
        if not parsed:
            tg_api('answerCallbackQuery', {'callback_query_id': query['id'], 'text': 'Use a new draft preview.'})
            return
        action, key, revision = parsed
        entry = posts.get(key)
        if (not entry or entry.get('revision') != revision or entry.get('review_message_id') != message.get('message_id')
                or str(entry.get('review_chat_id')) != str(owner) or autopilot.post_is_stale(entry, settings, now)):
            tg_api('answerCallbackQuery', {'callback_query_id': query['id'], 'text': 'Stale preview; request a new one.'})
            return
        tg_api('answerCallbackQuery', {'callback_query_id': query['id'], 'text': 'Processing this revision.'})
    else:
        text = message.get('text', '').strip()
        normalized = re.sub(r'[^\w /:-]', '', text.lower()).strip()
        command = re.fullmatch(r'(approve|reject|recreate|regen|theme|preview)(?: (afternoon|evening))?', normalized)
        if command:
            action, slot = command.groups()
            key = now.date().isoformat() + '_' + slot if slot else None
            if action in ('approve', 'reject'):
                reply_id = message.get('reply_to_message', {}).get('message_id')
                matches = [p for p in posts.values() if reply_id and p.get('review_message_id') == reply_id
                           and str(p.get('review_chat_id')) == owner and (slot is None or p.get('slot') == slot)]
                if len(matches) != 1:
                    tg_api('sendMessage', {'chat_id': owner, 'text': 'Use the exact draft buttons, or reply to its review message.'})
                    return
                key = matches[0]['date']
        elif normalized in ('status', 'check status', '/status', '/start', '/help'):
            rows = [review.result_text(p) for k, p in sorted(posts.items()) if k.startswith(now.date().isoformat())]
            tg_api('sendMessage', {'chat_id': owner, 'text': '\n\n'.join(rows) or 'No drafts today. Use recreate afternoon/evening.'})
            return
        elif normalized in ('diagnose', 'diagnostics', '/diagnose', 'health', '/health'):
            tg_api('sendMessage', {'chat_id': owner, 'text': get_system_diagnostics()})
            return
        elif normalized in ('token', '/token'):
            _, detail = check_meta_token_health(force=True)
            from autoposter.health import threads_health
            _, threads_detail = threads_health(autopilot, force=True)
            detail += "\n" + threads_detail
            tg_api('sendMessage', {'chat_id': owner, 'text': detail})
            return
        elif text.startswith('/topic '):
            ok, result = create_custom_topic_post(text[7:].strip(), posts=posts)
            tg_api('sendMessage', {'chat_id': owner, 'text': result})
            return
        else:
            tg_api('sendMessage', {'chat_id': owner, 'text': 'Commands: status, preview afternoon/evening, recreate afternoon/evening, /topic <idea>, /diagnose. Approve using a current preview button.'})
            return
    if action == 'approve':
        _, result = approve_pending_post(posts, target_key=key, now=now)
    elif action == 'reject':
        _, result = reject_pending_post(posts, target_key=key)
    elif action in ('recreate', 'regen'):
        _, result = recreate_and_send_post(posts, target_key=key)
    elif action == 'theme':
        _, result = switch_theme_and_send_post(posts, target_key=key)
    elif action == 'preview':
        entry = posts.get(key)
        if not entry or not review.can_replace(entry):
            result = 'No reviewable draft; inspect status.'
        else:
            autopilot.send_preview(entry, settings)
            write_json(STATE, posts)
            result = 'Preview delivered; previous approval revoked.'
    else:
        return
    tg_api('sendMessage', {'chat_id': owner, 'text': result})


def poll_once(offset=None, posts=None, settings=None, now=None):
    with worker_lock(autopilot.STATE.parent):
        state = read_json(TG_STATE, {'offset': 0})
        offset = max(int(offset or 0), int(state.get('offset', 0)))
        updates = tg_api('getUpdates', {'offset': offset, 'timeout': 0,
                         'allowed_updates': json.dumps(['message', 'callback_query'])})
        posts = posts if posts is not None else read_json(STATE, {})
        settings = settings if settings is not None else read_json(ROOT / 'settings.json', {})
        for update in sorted(updates, key=lambda value: value['update_id']):
            if int(update['update_id']) < offset:
                continue
            # At-most-once command handling: a crash requires the owner to issue a fresh action.
            offset = int(update['update_id']) + 1
            state['offset'] = offset
            write_json(TG_STATE, state)
            try:
                dispatch_update(update, posts, settings, now)
            except Exception as exc:
                autopilot.state_store().event('command_failed', {'update': update['update_id'], 'error_type': type(exc).__name__})
                autopilot.safe_notify('Action failed safely. Check status and issue a new command.')
        return offset


def check_and_publish_due_posts():
    with worker_lock(autopilot.STATE.parent):
        posts = read_json(STATE, {})
        settings = read_json(ROOT / 'settings.json', {})
        autopilot.expire_old_posts(posts, settings, datetime.now(autopilot.IST))
        autopilot.process_publications(posts, settings, datetime.now(autopilot.IST))
        write_json(STATE, posts)


def main():
    # Retain this historical entry point; scheduling, polling and publishing now share one worker.
    autopilot.run_worker()


if __name__ == '__main__':
    main()
