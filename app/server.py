"""Contacts: one private contact list every Wizard app can read and add to.

People, their phones, emails and accounts on each network (WhatsApp, Telegram, email, ...), a photo, notes.
Messenger and other apps reach it over their approved app link; the view and MCP share this service layer.
Stdlib only. The host gateway owns authentication; direct ports stay loopback.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parent.parent if '__file__' in globals() else Path(os.environ.get('APP_ROOT', '.')).resolve()
DATA = Path(os.environ.get('DATA_DIR', ROOT / '.data'))
APP_ID = 'contacts'
VERSION = '0.3.0'
STATIC_TYPES = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml'}
NETWORK = re.compile(r'[a-z][a-z0-9_-]{0,30}')
PHOTO_MAX = 400 * 1024
TEXT = {'name': 120, 'nickname': 80, 'company': 120, 'title': 120, 'notes': 4000, 'birthday': 10}


class Problem(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        DATA.chmod(0o700)
    except OSError:
        pass
    c = sqlite3.connect(DATA / 'contacts.sqlite3', timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.executescript('''
        CREATE TABLE IF NOT EXISTS contact(id TEXT PRIMARY KEY, name TEXT NOT NULL, nickname TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
            birthday TEXT NOT NULL DEFAULT '', favorite INTEGER NOT NULL DEFAULT 0, photo BLOB, photo_type TEXT,
            created INTEGER NOT NULL, updated INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS field(contact_id TEXT NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
            kind TEXT NOT NULL, network TEXT NOT NULL DEFAULT '', value TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
            pos INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(contact_id, kind, network, value));
        CREATE INDEX IF NOT EXISTS field_lookup ON field(kind, network, value);
        CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    cols = {r[1] for r in c.execute('PRAGMA table_info(contact)')}
    for col in ('uid', 'href', 'raw'):
        if col not in cols:
            c.execute('ALTER TABLE contact ADD COLUMN %s TEXT' % col)
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS contact_href ON contact(href)')
    return c


def kv_get(name, default=None):
    c = connect()
    try:
        row = c.execute('SELECT value FROM kv WHERE key=?', (name,)).fetchone()
        return row[0] if row else default
    finally:
        c.close()


def kv_put(name, value):
    c = connect()
    try:
        with c:
            if value is None:
                c.execute('DELETE FROM kv WHERE key=?', (name,))
            else:
                c.execute('INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)', (name, value))
    finally:
        c.close()


def now():
    return int(time.time() * 1000)


def key(text):
    """Comparable form: accents, case and punctuation dropped (search and duplicate checks)."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    return ' '.join(re.sub(r'[^\w\s@.+]', ' ', text.casefold()).split())


def clean_phone(value):
    value = str(value or '').strip()
    digits = re.sub(r'[^\d+]', '', value)
    if len(re.sub(r'\D', '', digits)) < 5:
        raise Problem(400, 'That phone number looks too short.')
    return ('+' + digits.lstrip('+')) if digits.startswith('+') else digits


def clean_email(value):
    value = str(value or '').strip()
    if not re.fullmatch(r'[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}', value):
        raise Problem(400, 'That email address does not look right.')
    return value.lower()


def clean_fields(body):
    """phones / emails / accounts from a request, validated; None for any list not sent."""
    out = {}
    for name, kind in (('phones', 'phone'), ('emails', 'email'), ('accounts', 'account')):
        items = body.get(name)
        if items is None:
            continue
        if not isinstance(items, list) or len(items) > 40:
            raise Problem(400, name + ' must be a list.')
        seen, rows = set(), []
        for item in items:
            item = item if isinstance(item, dict) else {'value': item}
            label = str(item.get('label') or '')[:40]
            if kind == 'phone':
                value, network = clean_phone(item.get('value')), ''
            elif kind == 'email':
                value, network = clean_email(item.get('value')), ''
            else:
                network = str(item.get('network') or '').lower()
                value = str(item.get('value') or item.get('address') or '').strip()[:300]
                if not NETWORK.fullmatch(network) or not value:
                    raise Problem(400, 'Each account needs a network and an address.')
                label = str(item.get('label') or '')[:120]
            if (network, value) in seen:
                continue
            seen.add((network, value))
            rows.append((kind, network, value, label))
        out[kind] = rows
    return out


def clean_text(body, partial=False):
    out = {}
    for name, limit in TEXT.items():
        if name not in body:
            continue
        value = body[name]
        if not isinstance(value, str):
            raise Problem(400, name + ' must be text.')
        value = value.strip()
        if len(value) > limit:
            raise Problem(400, '%s is too long (at most %d characters).' % (name.capitalize(), limit))
        if name == 'birthday' and value and not re.fullmatch(r'(\d{4}-|--)\d{2}-\d{2}', value):
            raise Problem(400, 'Birthday must look like 1990-05-31 (or --05-31 without a year).')
        out[name] = value
    if 'name' in out and not out['name']:
        raise Problem(400, 'A contact needs a name.')
    if not partial and not out.get('name'):
        raise Problem(400, 'A contact needs a name.')
    if 'favorite' in body:
        if not isinstance(body['favorite'], bool):
            raise Problem(400, 'favorite must be true or false.')
        out['favorite'] = int(body['favorite'])
    return out


def shape(c, row, full=True):
    fields = c.execute('SELECT kind,network,value,label FROM field WHERE contact_id=? ORDER BY kind,pos,rowid', (row['id'],)).fetchall()
    out = {'id': row['id'], 'name': row['name'], 'nickname': row['nickname'], 'company': row['company'], 'title': row['title'],
           'favorite': bool(row['favorite']), 'photo': bool(row['photo']), 'updated': row['updated'],
           'phones': [{'value': f['value'], 'label': f['label']} for f in fields if f['kind'] == 'phone'],
           'emails': [{'value': f['value'], 'label': f['label']} for f in fields if f['kind'] == 'email'],
           'accounts': [{'network': f['network'], 'value': f['value'], 'label': f['label']} for f in fields if f['kind'] == 'account']}
    if full:
        out.update(notes=row['notes'], birthday=row['birthday'], created=row['created'])
    return out


def get_row(c, contact_id):
    row = c.execute('SELECT * FROM contact WHERE id=?', (str(contact_id or ''),)).fetchone()
    if not row:
        raise Problem(404, 'Contact not found.')
    return row


def list_contacts(q='', limit=5000):
    c = connect()
    try:
        rows = c.execute('SELECT * FROM contact ORDER BY name COLLATE NOCASE').fetchall()
        people = [shape(c, r, full=False) for r in rows]
    finally:
        c.close()
    q = key(q)
    if q:
        def hit(p):
            hay = key(' '.join([p['name'], p['nickname'], p['company']] + [x['value'] for x in p['phones'] + p['emails']] + [x['label'] for x in p['accounts']]))
            digits = re.sub(r'\D', '', q)
            return all(w in hay for w in q.split()) or (len(digits) >= 4 and any(digits in re.sub(r'\D', '', x['value']) for x in p['phones']))
        people = [p for p in people if hit(p)]
    return {'contacts': people[:limit], 'total': len(people)}


def get_contact(contact_id):
    c = connect()
    try:
        return {'contact': shape(c, get_row(c, contact_id))}
    finally:
        c.close()


def write_fields(c, contact_id, fields, replace=True):
    for kind, rows in fields.items():
        if replace:
            c.execute('DELETE FROM field WHERE contact_id=? AND kind=?', (contact_id, kind))
        start = c.execute('SELECT COALESCE(MAX(pos),-1)+1 FROM field WHERE contact_id=? AND kind=?', (contact_id, kind)).fetchone()[0]
        for i, (k, network, value, label) in enumerate(rows):
            c.execute('INSERT OR IGNORE INTO field(contact_id,kind,network,value,label,pos) VALUES(?,?,?,?,?,?)', (contact_id, k, network, value, label, start + i))


def add_contact(body):
    text = clean_text(body)
    fields = clean_fields(body)
    cid = 'c_' + secrets.token_hex(6)
    stamp = now()
    c = connect()
    try:
        with c:
            c.execute('INSERT INTO contact(id,name,nickname,company,title,notes,birthday,favorite,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (cid, text['name'], text.get('nickname', ''), text.get('company', ''), text.get('title', ''), text.get('notes', ''),
                       text.get('birthday', ''), text.get('favorite', 0), stamp, stamp))
            write_fields(c, cid, fields)
        return {'contact': shape(c, get_row(c, cid))}
    finally:
        c.close()


def update_contact(contact_id, body):
    text = clean_text(body, partial=True)
    fields = clean_fields(body)
    c = connect()
    try:
        with c:
            get_row(c, contact_id)
            if text:
                c.execute('UPDATE contact SET %s,updated=MAX(updated+1,?) WHERE id=?' % ','.join(k + '=?' for k in text), (*text.values(), now(), contact_id))
            if fields:
                write_fields(c, contact_id, fields)
                c.execute('UPDATE contact SET updated=MAX(updated+1,?) WHERE id=?', (now(), contact_id))
        return {'contact': shape(c, get_row(c, contact_id))}
    finally:
        c.close()


def delete_contact(contact_id):
    c = connect()
    try:
        with c:
            get_row(c, contact_id)
            c.execute('DELETE FROM contact WHERE id=?', (contact_id,))
        return {'deleted': contact_id}
    finally:
        c.close()


def merge_contacts(target, source):
    """Source joins target: every phone, email and account; text fields fill only what target lacks."""
    if not source or source == target:
        raise Problem(400, 'Pick another contact to merge.')
    c = connect()
    try:
        with c:
            t, s = get_row(c, target), get_row(c, source)
            fill = {k: s[k] for k in ('nickname', 'company', 'title', 'birthday') if not t[k] and s[k]}
            if s['notes'] and s['notes'] not in t['notes']:
                fill['notes'] = (t['notes'] + '\n\n' + s['notes']).strip()[:TEXT['notes']]
            if not t['photo'] and s['photo']:
                fill.update(photo=s['photo'], photo_type=s['photo_type'])
            if s['favorite']:
                fill['favorite'] = 1
            if fill:
                c.execute('UPDATE contact SET %s WHERE id=?' % ','.join(k + '=?' for k in fill), (*fill.values(), target))
            c.execute('UPDATE OR IGNORE field SET contact_id=?, pos=pos+1000 WHERE contact_id=?', (target, source))
            c.execute('DELETE FROM contact WHERE id=?', (source,))
            c.execute('UPDATE contact SET updated=MAX(updated+1,?) WHERE id=?', (now(), target))
        return {'contact': shape(c, get_row(c, target))}
    finally:
        c.close()


def decode_photo(data):
    if not isinstance(data, str):
        raise Problem(400, 'Send the photo as base64.')
    data = data.split(',', 1)[1] if data.startswith('data:') else data
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise Problem(400, 'The photo is not valid base64.') from None
    if len(raw) > PHOTO_MAX:
        raise Problem(413, 'Photos must be under 400 KB.')
    kind = 'image/jpeg' if raw[:3] == b'\xff\xd8\xff' else 'image/png' if raw[:8] == b'\x89PNG\r\n\x1a\n' else 'image/webp' if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP' else None
    if not kind:
        raise Problem(400, 'Use a JPEG, PNG or WebP photo.')
    return raw, kind


def set_photo(contact_id, body):
    c = connect()
    try:
        with c:
            get_row(c, contact_id)
            if body.get('data') in (None, ''):
                c.execute('UPDATE contact SET photo=NULL,photo_type=NULL,updated=MAX(updated+1,?) WHERE id=?', (now(), contact_id))
            else:
                raw, kind = decode_photo(body['data'])
                c.execute('UPDATE contact SET photo=?,photo_type=?,updated=MAX(updated+1,?) WHERE id=?', (raw, kind, now(), contact_id))
        return {'contact': shape(c, get_row(c, contact_id))}
    finally:
        c.close()


def get_photo(contact_id):
    c = connect()
    try:
        row = get_row(c, contact_id)
        if not row['photo']:
            raise Problem(404, 'No photo.')
        return bytes(row['photo']), row['photo_type']
    finally:
        c.close()


def lookup(network=None, value=None, email=None, phone=None):
    """The contact holding this account, email or phone (apps show the contact's name), or null."""
    c = connect()
    try:
        if email:
            row = c.execute("SELECT contact_id FROM field WHERE kind='email' AND value=?", (clean_email(email),)).fetchone()
        elif phone:
            p = clean_phone(phone)
            row = c.execute("SELECT contact_id FROM field WHERE kind='phone' AND (value=? OR substr(replace(value,'+',''),-9)=substr(replace(?,'+',''),-9))", (p, p)).fetchone()
        else:
            row = c.execute("SELECT contact_id FROM field WHERE kind='account' AND network=? AND value=?", (str(network or '').lower(), str(value or ''))).fetchone()
        return {'contact': shape(c, get_row(c, row[0])) if row else None}
    finally:
        c.close()


def save_from_app(body):
    """An app's "Add to Contacts": find the person by any of their accounts/phones/emails, else create them.
    New details are added; the owner's own edits (name, notes) are never overwritten."""
    fields = clean_fields(body)
    name = str(body.get('name') or '').strip()[:TEXT['name']]
    c = connect()
    try:
        found = None
        if isinstance(body.get('contact_id'), str):
            found = c.execute('SELECT id FROM contact WHERE id=?', (body['contact_id'],)).fetchone()
        for kind, rows in fields.items():
            for k, network, value, _ in rows:
                if not found:
                    found = c.execute('SELECT contact_id FROM field WHERE kind=? AND network=? AND value=?', (k, network, value)).fetchone()
        with c:
            if found:
                cid = found[0]
                write_fields(c, cid, fields, replace=False)
                c.execute('UPDATE contact SET updated=MAX(updated+1,?) WHERE id=?', (now(), cid))
                created = False
            else:
                if not name:
                    raise Problem(400, 'A contact needs a name.')
                cid, stamp, created = 'c_' + secrets.token_hex(6), now(), True
                c.execute('INSERT INTO contact(id,name,created,updated) VALUES(?,?,?,?)', (cid, name, stamp, stamp))
                write_fields(c, cid, fields)
            if body.get('photo') and not c.execute('SELECT photo FROM contact WHERE id=?', (cid,)).fetchone()[0]:
                try:
                    raw, kind = decode_photo(body['photo'])
                    c.execute('UPDATE contact SET photo=?,photo_type=? WHERE id=?', (raw, kind, cid))
                except Problem:
                    pass
        return {'contact': shape(c, get_row(c, cid)), 'created': created}
    finally:
        c.close()


def unlink_account(contact_id, body):
    c = connect()
    try:
        with c:
            get_row(c, contact_id)
            c.execute("DELETE FROM field WHERE contact_id=? AND kind='account' AND network=? AND value=?", (contact_id, str(body.get('network') or ''), str(body.get('value') or '')))
        return {'contact': shape(c, get_row(c, contact_id))}
    finally:
        c.close()


def duplicates():
    """Contacts that share a name, phone or email: offered to the owner, never merged automatically."""
    people = list_contacts()['contacts']
    groups, seen = [], set()
    by = {}
    for p in people:
        keys = {'n:' + key(p['name'])} | {'p:' + re.sub(r'\D', '', x['value'])[-9:] for x in p['phones']} | {'e:' + x['value'] for x in p['emails']}
        for k in keys:
            if len(k) > 4:
                by.setdefault(k, []).append(p)
    dismissed = set(json.loads(kv_get('dup_dismissed', '[]')))
    for k, group in by.items():
        ids = tuple(sorted({p['id'] for p in group}))
        if len(ids) > 1 and ids not in seen and '|'.join(ids) not in dismissed:
            seen.add(ids)
            groups.append({'ids': list(ids), 'reason': {'n': 'Same name', 'p': 'Same phone', 'e': 'Same email'}[k[0]], 'contacts': [p for p in group if p['id'] in ids]})
    return {'duplicates': groups}


def dismiss_duplicate(ids):
    ids = sorted(str(x) for x in (ids or []) if isinstance(x, str))
    if len(ids) < 2:
        raise Problem(400, 'Send the two or more contact ids.')
    seen = json.loads(kv_get('dup_dismissed', '[]'))
    kv_put('dup_dismissed', json.dumps((seen + ['|'.join(ids)])[-5000:]))
    return {'dismissed': True}


def get_me():
    cid = kv_get('me')
    if cid:
        try:
            return get_contact(cid)
        except Problem:
            kv_put('me', None)
    return {'contact': None}


def set_me(contact_id):
    if contact_id in (None, ''):
        kv_put('me', None)
        return {'contact': None}
    get_contact(contact_id)
    kv_put('me', contact_id)
    return get_me()


def set_connected(body):
    """Messenger reports the owner's own connected accounts; they become (and follow) My card's accounts.

    Accounts the owner typed in by hand stay. Accounts that were connected and no longer are, go."""
    accounts = body.get('accounts')
    if not isinstance(accounts, list) or len(accounts) > 30:
        raise Problem(400, 'accounts must be a list.')
    fresh = clean_fields({'accounts': [a for a in accounts if isinstance(a, dict)]}).get('account', [])
    me = get_me()['contact']
    if not me:
        if not fresh:
            return {'contact': None}
        name = str(body.get('name') or '').strip()[:TEXT['name']] or 'Me'
        me = add_contact({'name': name})['contact']
        kv_put('me', me['id'])
    before = set(json.loads(kv_get('me_connected', '[]')))
    now_keys = {'%s\n%s' % (n, v) for _, n, v, _ in fresh}
    c = connect()
    try:
        with c:
            for k in before - now_keys:
                n, v = k.split('\n', 1)
                c.execute("DELETE FROM field WHERE contact_id=? AND kind='account' AND network=? AND value=?", (me['id'], n, v))
            have = {(r[0], r[1]) for r in c.execute("SELECT network,value FROM field WHERE contact_id=? AND kind='account'", (me['id'],))}
            add = [row for row in fresh if (row[1], row[2]) not in have]
            if add:
                write_fields(c, me['id'], {'account': add}, replace=False)
            if add or before - now_keys:
                c.execute('UPDATE contact SET updated=MAX(updated+1,?) WHERE id=?', (now(), me['id']))
    finally:
        c.close()
    kv_put('me_connected', json.dumps(sorted(now_keys)))
    return get_me()


# --- vCard 3.0 import/export (the format every phone and mail app speaks) ---
# Fields Contacts understands. Everything else a phone sends (addresses, websites, related names, ...) is kept
# verbatim in `raw` and written back out, so syncing with a phone never loses what this app does not edit.
KNOWN = {'BEGIN', 'END', 'VERSION', 'PRODID', 'UID', 'REV', 'FN', 'N', 'NICKNAME', 'ORG', 'TITLE', 'BDAY', 'TEL', 'EMAIL',
         'NOTE', 'PHOTO', 'X-WIZARD-ACCOUNT'}


def vcard_escape(value):
    return str(value).replace('\\', '\\\\').replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;')


def unfold(text):
    return re.sub(r'\r?\n[ \t]', '', text or '')


def card_lines(text):
    """[(group, NAME, params, value, line)] for one unfolded card."""
    out = []
    for line in unfold(text).splitlines():
        if ':' not in line:
            continue
        head, value = line.split(':', 1)
        name, *params = head.split(';')
        group, _, name = name.rpartition('.')
        out.append((group.lower(), name.upper(), params, value, line))
    return out


def keep_raw(raw, has_photo=False):
    """The raw card minus what Contacts writes itself (and minus whole groups tied to those fields)."""
    lines = card_lines(raw)
    tied = {g for g, n, _, _, _ in lines if g and n in KNOWN}
    return [l for g, n, _, _, l in lines if (n not in KNOWN or (n == 'PHOTO' and not has_photo)) and not (g and g in tied)]


def fold(line):
    raw = line.encode()
    if len(raw) <= 75:
        return line
    parts, cur = [], b''
    for ch in line:
        e = ch.encode()
        if len(cur) + len(e) > (75 if not parts else 74):
            parts.append(cur.decode()); cur = b''
        cur += e
    parts.append(cur.decode())
    return '\r\n '.join(parts)


def card_text(c, row):
    p = shape(c, row)
    raw = row['raw'] or ''
    lines = ['BEGIN:VCARD', 'VERSION:3.0', 'PRODID:-//Wizard//Contacts//EN', 'UID:' + (row['uid'] or row['id']), 'FN:' + vcard_escape(p['name'])]
    raw_fn = next((v for g, n, _, v, _ in card_lines(raw) if n == 'FN'), None)
    raw_n = next((l.split(':', 1)[1] for g, n, _, v, l in card_lines(raw) if n == 'N'), None)
    if raw_n and raw_fn is not None and re.sub(r'\\(.)', r'\1', raw_fn).strip() == p['name']:
        lines.append('N:' + raw_n)
    else:
        words = p['name'].split()
        lines.append('N:' + (vcard_escape(words[-1]) + ';' + vcard_escape(' '.join(words[:-1])) if len(words) > 1 else ';' + vcard_escape(p['name'])) + ';;;')
    if p['nickname']: lines.append('NICKNAME:' + vcard_escape(p['nickname']))
    if p['company']: lines.append('ORG:' + vcard_escape(p['company']) + ';')
    if p['title']: lines.append('TITLE:' + vcard_escape(p['title']))
    if p['birthday']: lines.append('BDAY;X-APPLE-OMIT-YEAR=1604:1604' + p['birthday'][1:] if p['birthday'].startswith('--') else 'BDAY:' + p['birthday'])
    for x in p['phones']: lines.append('TEL' + (';TYPE=' + vcard_escape(x['label']).upper() if x['label'] else '') + ':' + x['value'])
    for x in p['emails']: lines.append('EMAIL;TYPE=INTERNET' + (';TYPE=' + vcard_escape(x['label']).upper() if x['label'] else '') + ':' + x['value'])
    for x in p['accounts']: lines.append('X-WIZARD-ACCOUNT;NETWORK=' + x['network'] + ':' + vcard_escape(x['value']))
    if p['notes']: lines.append('NOTE:' + vcard_escape(p['notes']))
    lines += keep_raw(raw, bool(row['photo']))
    if row['photo']:
        lines.append('PHOTO;ENCODING=b;TYPE=' + row['photo_type'].split('/')[1].upper() + ':' + base64.b64encode(bytes(row['photo'])).decode())
    lines.append('REV:' + time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(row['updated'] / 1000)))
    lines.append('END:VCARD')
    return '\r\n'.join(fold(l) for l in lines) + '\r\n'


def export_vcf():
    c = connect()
    try:
        return ''.join(card_text(c, row) for row in c.execute('SELECT * FROM contact ORDER BY name COLLATE NOCASE').fetchall()).encode()
    finally:
        c.close()


def parse_card(card):
    """(body for add/update, photo base64 or None, uid, raw-without-photo) from one vCard."""
    body = {'phones': [], 'emails': [], 'accounts': []}
    photo = uid = None
    labels = {}
    lines = card_lines(card)
    for g, n, params, value, _ in lines:
        if g and n == 'X-ABLABEL':
            labels[g] = re.sub(r'^_\$!<(.*)>!\$_$', r'\1', value).strip()
    for g, name, params, value, _ in lines:
        val = re.sub(r'\\([\\,;:])', r'\1', value).replace('\\n', '\n').replace('\\N', '\n').strip()
        types = [t.split('=', 1)[1] if '=' in t else t for t in params if t.upper().startswith('TYPE=') or '=' not in t]
        ptype = next((t.lower() for t in ','.join(types).split(',') if t and t.lower() not in ('pref', 'voice', 'internet', 'x400')), '')
        label = labels.get(g) or ptype
        if name == 'FN': body['name'] = val[:TEXT['name']]
        elif name == 'N' and not body.get('name'):
            parts = value.split(';')
            body['name'] = ' '.join(x for x in [parts[1] if len(parts) > 1 else '', parts[0]] if x).strip()[:TEXT['name']]
        elif name == 'UID': uid = val[:200]
        elif name == 'NICKNAME': body['nickname'] = val[:TEXT['nickname']]
        elif name == 'ORG': body['company'] = val.split(';')[0].strip()[:TEXT['company']]
        elif name == 'TITLE': body['title'] = val[:TEXT['title']]
        elif name == 'NOTE': body['notes'] = val[:TEXT['notes']]
        elif name == 'BDAY':
            v = val.split('T')[0]
            if re.fullmatch(r'\d{4}-?\d{2}-?\d{2}', v):
                v = v.replace('-', ''); body['birthday'] = v[:4] + '-' + v[4:6] + '-' + v[6:8]
                if body['birthday'].startswith('1604-'): body['birthday'] = '--' + body['birthday'][5:]
            elif re.fullmatch(r'--\d{2}-?\d{2}', v):
                body['birthday'] = '--' + v[2:].replace('-', '')[:2] + '-' + v[2:].replace('-', '')[2:]
        elif name == 'TEL': body['phones'].append({'value': val, 'label': label[:40]})
        elif name == 'EMAIL': body['emails'].append({'value': val, 'label': label[:40]})
        elif name == 'X-WIZARD-ACCOUNT':
            net = next((p.split('=', 1)[1] for p in params if p.upper().startswith('NETWORK=')), '')
            body['accounts'].append({'network': net.lower(), 'value': val})
        elif name == 'PHOTO':
            if value.startswith('data:'):
                photo = value
            elif any('ENCODING=B' in p.upper() or p.upper() == 'BASE64' for p in params):
                photo = value.strip()
    if not body.get('name'):
        body['name'] = (body.get('company') or (body['emails'] or body['phones'] or [{'value': ''}])[0]['value'])[:TEXT['name']]
    good = {'phones': [], 'emails': [], 'accounts': []}
    for k in good:
        for item in body[k]:
            try:
                clean_fields({k: [item]}); good[k].append(item)
            except Problem:
                pass
    body.update(good)
    raw = '\r\n'.join(l for g, n, _, _, l in lines if n != 'PHOTO' or photo is None)
    return body, photo, uid, raw


def store_raw(c, cid, uid, raw):
    c.execute('UPDATE contact SET raw=COALESCE(?,raw), uid=COALESCE(uid,?) WHERE id=?', (raw, uid, cid))


def import_vcf(text):
    if not isinstance(text, str) or len(text) > 8 * 1024 * 1024:
        raise Problem(400, 'Send the vCard file as text (up to 8 MB).')
    added = updated = 0
    for card in re.findall(r'BEGIN:VCARD(.*?)END:VCARD', unfold(text), re.S | re.I):
        body, photo, uid, raw = parse_card(card)
        if not body.get('name'):
            continue
        try:
            existing = None
            for x in body['emails']:
                existing = existing or lookup(email=x['value'])['contact']
            for x in body['phones']:
                existing = existing or lookup(phone=x['value'])['contact']
            # Only the same person (same name) folds together; a shared family or office number with another
            # name stays a separate contact and shows up under Duplicates for the owner to decide.
            if existing and key(existing['name']) != key(body['name']):
                existing = None
            if existing:
                save_from_app({'contact_id': existing['id'], **{k: body[k] for k in ('phones', 'emails', 'accounts')}})
                updated += 1
                continue
            cid = add_contact(body)['contact']['id']
            added += 1
            c = connect()
            try:
                with c:
                    store_raw(c, cid, uid, raw)
            finally:
                c.close()
            if photo:
                try: set_photo(cid, {'data': photo})
                except Problem: pass
        except Problem:
            continue
    return {'added': added, 'updated': updated}


def attach_raw(text):
    """One-time: give already-imported contacts the full card from the same export (addresses, websites, ...)."""
    c = connect()
    done = 0
    try:
        with c:
            for card in re.findall(r'BEGIN:VCARD(.*?)END:VCARD', unfold(text), re.S | re.I):
                body, _, uid, raw = parse_card(card)
                if not body.get('name'):
                    continue
                for row in c.execute('SELECT id FROM contact WHERE raw IS NULL AND name=?', (body['name'],)).fetchall():
                    have = {f[0] for f in c.execute("SELECT value FROM field WHERE contact_id=? AND kind IN ('phone','email')", (row[0],))}
                    want = set()
                    for item in body['phones']:
                        want.add(clean_phone(item['value']))
                    for item in body['emails']:
                        want.add(clean_email(item['value']))
                    if want and not (want & have):
                        continue
                    store_raw(c, row[0], uid, raw)
                    done += 1
                    break
    finally:
        c.close()
    return {'attached': done}


# --- Phone sync: CardDAV (what iPhone, macOS and Android/DAVx5 use for contacts) ---
# One address book, owner-only, behind its own password (phones cannot use the Wizard sign-in).
DAV_USER = 'contacts'
PROFILE_TTL = 15 * 60
_profiles = {}  # one-time iPhone setup-profile links: token -> (expires, server, password)


def _hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 200_000).hex()


def sync_status():
    return {'enabled': bool(kv_get('dav_hash')), 'username': DAV_USER, 'last_sync': int(kv_get('dav_seen', '0')) or None}


def sync_setup(server):
    """New sync password (replaces the old one); returned once, plus a one-time iPhone profile link."""
    password = '-'.join(secrets.token_hex(3) for _ in range(4))
    salt = secrets.token_hex(16)
    kv_put('dav_hash', salt + ':' + _hash(password, salt))
    token = secrets.token_urlsafe(18)
    now_s = time.time()
    for t in [t for t, v in _profiles.items() if v[0] < now_s]:
        _profiles.pop(t, None)
    _profiles[token] = (now_s + PROFILE_TTL, server, password)
    return {**sync_status(), 'password': password, 'profile_token': token}


def sync_off():
    kv_put('dav_hash', None)
    _profiles.clear()
    return sync_status()


def dav_auth_ok(header):
    stored = kv_get('dav_hash')
    if not stored or not (header or '').startswith('Basic '):
        return False
    try:
        user, sep, password = base64.b64decode(header[6:], validate=True).decode().partition(':')
        if not sep:
            return False
    except (ValueError, UnicodeError, base64.binascii.Error):
        return False
    salt, digest = stored.split(':', 1)
    ok = hmac.compare_digest(user.lower(), DAV_USER) & hmac.compare_digest(_hash(password, salt), digest)
    if ok:
        kv_put('dav_seen', str(now()))
    return ok


def mobileconfig(token):
    item = _profiles.pop(token, None)
    if not item or item[0] < time.time():
        raise Problem(404, 'This setup link has expired. Make a new one in Contacts.')
    _, server, password = item
    host = urlsplit(server).hostname
    port = urlsplit(server).port or 443
    uid = lambda: str(__import__('uuid').uuid4()).upper()
    esc = xml_escape
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>PayloadContent</key><array><dict>
<key>CardDAVAccountDescription</key><string>Wizard Contacts</string>
<key>CardDAVHostName</key><string>{esc(host)}</string>
<key>CardDAVPort</key><integer>{port}</integer>
<key>CardDAVPrincipalURL</key><string>{esc(urlsplit(server).path.rstrip('/'))}/dav/principal/</string>
<key>CardDAVUseSSL</key><true/>
<key>CardDAVUsername</key><string>{DAV_USER}</string>
<key>CardDAVPassword</key><string>{esc(password)}</string>
<key>PayloadDescription</key><string>Syncs your contacts with your Wizard</string>
<key>PayloadDisplayName</key><string>Wizard Contacts</string>
<key>PayloadIdentifier</key><string>os.wizard.contacts.carddav</string>
<key>PayloadType</key><string>com.apple.carddav.account</string>
<key>PayloadUUID</key><string>{uid()}</string>
<key>PayloadVersion</key><integer>1</integer>
</dict></array>
<key>PayloadDisplayName</key><string>Wizard Contacts</string>
<key>PayloadIdentifier</key><string>os.wizard.contacts</string>
<key>PayloadRemovalDisallowed</key><false/>
<key>PayloadType</key><string>Configuration</string>
<key>PayloadUUID</key><string>{uid()}</string>
<key>PayloadVersion</key><integer>1</integer>
</dict></plist>
"""
    return plist.encode()


def book_ctag(c):
    rows = c.execute('SELECT id,updated FROM contact ORDER BY id').fetchall()
    return hashlib.sha1(''.join('%s:%s;' % (r[0], r[1]) for r in rows).encode()).hexdigest()[:16]


def card_href(row):
    return row['href'] or (row['id'] + '.vcf')


def card_by_href(c, name):
    return c.execute('SELECT * FROM contact WHERE href=? OR (href IS NULL AND id||\'.vcf\'=?)', (name, name)).fetchone()


def dav_put(name, text, if_match=None, if_none_match=None):
    if not re.fullmatch(r'[A-Za-z0-9._@%+-]{1,200}\.vcf', name):
        raise Problem(400, 'Bad card name.')
    m = re.search(r'BEGIN:VCARD(.*?)END:VCARD', unfold(text), re.S | re.I)
    if not m or unfold(text).upper().count('BEGIN:VCARD') != 1 or unfold(text).upper().count('END:VCARD') != 1:
        raise Problem(400, 'Expected exactly one vCard.')
    body, photo, uid, raw = parse_card(m.group(1))
    if not body.get('name'):
        body['name'] = 'No name'
    c = connect()
    try:
        row = card_by_href(c, name)
        # A moved card with the same unique UID is the same person, not a new contact. Never infer identity by a shared number.
        if not row and uid:
            known = c.execute('SELECT * FROM contact WHERE uid=?', (uid,)).fetchall()
            if len(known) == 1 and not known[0]['href']:
                row = known[0]
        if row and if_none_match == '*':
            raise Problem(412, 'Card exists.')
        if row and if_match and if_match.strip('"') != str(row['updated']):
            raise Problem(412, 'Card changed on the Wizard; sync again.')
        if not row and if_match:
            raise Problem(412, 'Card is gone.')
    finally:
        c.close()
    fields = {k: body[k] for k in ('phones', 'emails')}
    if body['accounts']:
        fields['accounts'] = body['accounts']
    text_fields = {k: body.get(k, '') for k in ('name', 'nickname', 'company', 'title', 'notes', 'birthday')}
    if row:
        update_contact(row['id'], {**text_fields, **fields})
        cid, created = row['id'], False
    else:
        cid, created = add_contact({**text_fields, **fields})['contact']['id'], True
    c = connect()
    try:
        with c:
            c.execute('UPDATE contact SET raw=?, uid=COALESCE(?,uid), href=? WHERE id=?', (raw, uid, name if not row or row['href'] else None, cid))
    finally:
        c.close()
    if photo:
        try:
            set_photo(cid, {'data': photo})
        except Problem:
            pass
    c = connect()
    try:
        return created, '"%s"' % c.execute('SELECT updated FROM contact WHERE id=?', (cid,)).fetchone()[0]
    finally:
        c.close()


def dav_delete(name):
    c = connect()
    try:
        row = card_by_href(c, name)
    finally:
        c.close()
    if not row:
        raise Problem(404, 'No such card.')
    delete_contact(row['id'])


def _ms(responses):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<d:multistatus xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav" '
            'xmlns:cs="http://calendarserver.org/ns/">' + ''.join(responses) + '</d:multistatus>').encode()


def _resp(href, props):
    return '<d:response><d:href>%s</d:href><d:propstat><d:prop>%s</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>' % (xml_escape(href), props)


def dav_propfind(base, path, depth):
    """base: public prefix (e.g. /contacts). path: /dav/... inside the app."""
    me = base + '/dav/principal/'
    home = base + '/dav/home/'
    book = base + '/dav/home/book/'
    priv = '<d:current-user-privilege-set><d:privilege><d:all/></d:privilege><d:privilege><d:read/></d:privilege><d:privilege><d:write/></d:privilege></d:current-user-privilege-set>'
    common = '<d:current-user-principal><d:href>%s</d:href></d:current-user-principal><d:principal-URL><d:href>%s</d:href></d:principal-URL>' \
             '<card:addressbook-home-set><d:href>%s</d:href></card:addressbook-home-set><d:owner><d:href>%s</d:href></d:owner>' % (me, me, home, me)
    c = connect()
    try:
        ctag = book_ctag(c)
        book_props = ('<d:resourcetype><d:collection/><card:addressbook/></d:resourcetype><d:displayname>Wizard Contacts</d:displayname>'
                      '<cs:getctag>%s</cs:getctag><d:getetag>"%s"</d:getetag>'
                      '<d:supported-report-set><d:supported-report><d:report><card:addressbook-multiget/></d:report></d:supported-report>'
                      '<d:supported-report><d:report><card:addressbook-query/></d:report></d:supported-report></d:supported-report-set>'
                      '<card:supported-address-data><card:address-data-type content-type="text/vcard" version="3.0"/></card:supported-address-data>'
                      '<card:max-resource-size>1048576</card:max-resource-size>') % (ctag, ctag) + priv + common
        out = []
        if path in ('/dav/', '/dav/principal/', '/dav'):
            out.append(_resp(base + path if path.endswith('/') else base + path + '/', '<d:resourcetype><d:collection/><d:principal/></d:resourcetype><d:displayname>Owner</d:displayname>' + common + priv))
        elif path == '/dav/home/':
            out.append(_resp(home, '<d:resourcetype><d:collection/></d:resourcetype><d:displayname>Address books</d:displayname>' + common + priv))
            if depth != '0':
                out.append(_resp(book, book_props))
        elif path == '/dav/home/book/':
            out.append(_resp(book, book_props))
            if depth != '0':
                for row in c.execute('SELECT id,href,updated FROM contact').fetchall():
                    out.append(_resp(book + card_href(row), '<d:getetag>"%s"</d:getetag><d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype><d:resourcetype/>' % row['updated']))
        elif path.startswith('/dav/home/book/'):
            row = card_by_href(c, unquote(path.rsplit('/', 1)[1]))
            if not row:
                raise Problem(404, 'No such card.')
            out.append(_resp(book + card_href(row), '<d:getetag>"%s"</d:getetag><d:getcontenttype>text/vcard; charset=utf-8</d:getcontenttype><d:resourcetype/>' % row['updated']))
        else:
            raise Problem(404, 'Not found.')
        return _ms(out)
    finally:
        c.close()


def dav_report(base, body):
    book = base + '/dav/home/book/'
    c = connect()
    try:
        if 'addressbook-multiget' in body:
            names = [unquote(h.strip().rsplit('/', 1)[-1]) for h in re.findall(r'<(?:\w+:)?href>([^<]+)</(?:\w+:)?href>', body)]
            rows = [(n, card_by_href(c, n)) for n in names]
        else:
            rows = [(None, r) for r in c.execute('SELECT * FROM contact').fetchall()]
        out = []
        for name, row in rows:
            if not row:
                out.append('<d:response><d:href>%s</d:href><d:status>HTTP/1.1 404 Not Found</d:status></d:response>' % xml_escape(book + (name or '')))
                continue
            out.append(_resp(book + card_href(row), '<d:getetag>"%s"</d:getetag><card:address-data>%s</card:address-data>' % (row['updated'], xml_escape(card_text(c, row)))))
        return _ms(out)
    finally:
        c.close()


TOOLS = [
    {'name': 'contacts_search', 'description': "Search the owner's contacts by name, nickname, company, phone, email or account. Empty query lists everyone.",
     'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 500}}}},
    {'name': 'contacts_get', 'description': 'One contact with phones, emails, accounts per network, notes and birthday.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
    {'name': 'contacts_lookup', 'description': 'Find who owns an email, a phone number, or an account on a network (network + value).',
     'inputSchema': {'type': 'object', 'properties': {'email': {'type': 'string'}, 'phone': {'type': 'string'}, 'network': {'type': 'string'}, 'value': {'type': 'string'}}}},
    {'name': 'contacts_add', 'description': 'Add a contact. phones/emails: [{value,label}], accounts: [{network,value,label}].',
     'inputSchema': {'type': 'object', 'properties': {'name': {'type': 'string'}, 'nickname': {'type': 'string'}, 'company': {'type': 'string'}, 'title': {'type': 'string'},
                     'notes': {'type': 'string'}, 'birthday': {'type': 'string', 'description': 'YYYY-MM-DD or --MM-DD'},
                     'phones': {'type': 'array'}, 'emails': {'type': 'array'}, 'accounts': {'type': 'array'}}, 'required': ['name']}},
    {'name': 'contacts_update', 'description': 'Change a contact. Only fields sent change; a list sent (phones, emails, accounts) replaces that list.',
     'inputSchema': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'name': {'type': 'string'}, 'nickname': {'type': 'string'}, 'company': {'type': 'string'},
                     'title': {'type': 'string'}, 'notes': {'type': 'string'}, 'birthday': {'type': 'string'}, 'favorite': {'type': 'boolean'},
                     'phones': {'type': 'array'}, 'emails': {'type': 'array'}, 'accounts': {'type': 'array'}}, 'required': ['id']}},
]


def call_tool(n, a):
    if n == 'contacts_search':
        limit = a.get('limit') if isinstance(a.get('limit'), int) else 50
        return list_contacts(str(a.get('query') or ''), max(1, min(limit, 500)))
    if n == 'contacts_get':
        return get_contact(a.get('id'))
    if n == 'contacts_lookup':
        return lookup(a.get('network'), a.get('value'), a.get('email'), a.get('phone'))
    if n == 'contacts_add':
        return add_contact(a)
    if n == 'contacts_update':
        return update_contact(str(a.get('id') or ''), {k: v for k, v in a.items() if k != 'id'})
    return None


def rpc(msg):
    if not isinstance(msg, dict):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Expected a JSON-RPC object.'}}
    m, i, p = msg.get('method'), msg.get('id'), msg.get('params') or {}
    if 'id' not in msg:
        return None
    try:
        if m == 'initialize':
            r = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': APP_ID, 'version': VERSION}}
        elif m == 'tools/list':
            r = {'tools': TOOLS}
        elif m == 'tools/call':
            value = call_tool(p.get('name'), p.get('arguments') or {})
            if value is None:
                return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32602, 'message': 'Unknown tool.'}}
            r = {'content': [{'type': 'text', 'text': json.dumps(value)}], 'structuredContent': value, 'isError': False}
        elif m == 'ping':
            r = {}
        else:
            return {'jsonrpc': '2.0', 'id': i, 'error': {'code': -32601, 'message': 'Unknown method.'}}
    except Problem as e:
        r = {'content': [{'type': 'text', 'text': e.message}], 'isError': True}
    return {'jsonrpc': '2.0', 'id': i, 'result': r}


def _csv(name):
    return {x.strip().lower() for x in os.environ.get(name, '').split(',') if x.strip()}


# Who may talk to this app: the Host must be one this app was configured for (stops DNS rebinding), and a
# browser Origin on writes must be configured too. `contacts:<port>` is the name linked apps use on their
# owner-approved app network.
_PORT = os.environ.get('PORT', '8799')
_LOOPBACK = {f'127.0.0.1:{_PORT}', f'localhost:{_PORT}', f'{APP_ID}:{_PORT}'}
TRUSTED_HOSTS = _csv('WIZARD_TRUSTED_HOSTS') | _LOOPBACK
TRUSTED_ORIGINS = _csv('WIZARD_TRUSTED_ORIGINS') | {'http://127.0.0.1:' + _PORT, 'http://localhost:' + _PORT}


def contact_route(path):
    parts = path.strip('/').split('/')
    if len(parts) >= 3 and parts[:2] == ['api', 'contacts'] and re.fullmatch(r'c_[0-9a-f]{12}', parts[2]):
        return parts[2], parts[3:]
    return None, None


class Handler(BaseHTTPRequestHandler):
    def base(self):
        # Wizard serves this app under /contacts/ (the prefix is stripped before it reaches us).
        host = (self.headers.get('Host') or '').lower()
        return '' if host in _LOOPBACK else '/' + APP_ID

    def dav(self):
        """CardDAV for phones: own Basic auth, every method. Returns True when handled."""
        path = urlsplit(self.path).path
        if path == '/.well-known/carddav':
            if self.refused():
                return True
            self.send_response(301); self.send_header('Location', self.base() + '/dav/principal/'); self.send_header('Content-Length', '0'); self.end_headers()
            return True
        if not (path == '/dav' or path.startswith('/dav/')):
            return False
        if self.refused(write=self.command in ('PUT', 'DELETE')):
            return True
        if not dav_auth_ok(self.headers.get('Authorization')):
            time.sleep(0.5)
            self.send(401, {'error': 'Sign in with the phone sync password from Contacts.'}, extra={'WWW-Authenticate': 'Basic realm="Wizard Contacts"'})
            return True
        try:
            size = int(self.headers.get('Content-Length', '0') or 0)
            if not 0 <= size <= 2 * 1024 * 1024:
                return self.send(413, {'error': 'Too large.'}) or True
            body = self.rfile.read(size).decode('utf-8', 'replace') if size else ''
            m, base = self.command, self.base()
            dav_head = {'DAV': '1, 3, addressbook', 'Allow': 'OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, REPORT'}
            if m == 'OPTIONS':
                self.send(200, b'', 'text/plain', dav_head)
            elif m == 'PROPFIND':
                self.send(207, dav_propfind(base, path if path.endswith('/') or path.endswith('.vcf') else path + '/', self.headers.get('Depth', '1')), 'application/xml; charset=utf-8', dav_head)
            elif m == 'REPORT':
                self.send(207, dav_report(base, body), 'application/xml; charset=utf-8', dav_head)
            elif m in ('GET', 'HEAD') and path.startswith('/dav/home/book/') and path.endswith('.vcf'):
                c = connect()
                try:
                    row = card_by_href(c, unquote(path.rsplit('/', 1)[1]))
                    if not row:
                        raise Problem(404, 'No such card.')
                    self.send(200, card_text(c, row).encode(), 'text/vcard; charset=utf-8', {'ETag': '"%s"' % row['updated']})
                finally:
                    c.close()
            elif m == 'PUT' and path.startswith('/dav/home/book/'):
                created, etag = dav_put(unquote(path.rsplit('/', 1)[1]), body, self.headers.get('If-Match'), self.headers.get('If-None-Match'))
                self.send(201 if created else 204, b'', 'text/plain', {'ETag': etag})
            elif m == 'DELETE' and path.startswith('/dav/home/book/'):
                name = unquote(path.rsplit('/', 1)[1])
                match = self.headers.get('If-Match')
                if match:
                    c = connect()
                    try:
                        row = card_by_href(c, name)
                        if not row or match.strip('"') != str(row['updated']):
                            raise Problem(412, 'Card changed on the Wizard; sync again.')
                    finally:
                        c.close()
                dav_delete(name)
                self.send(204, b'', 'text/plain')
            else:
                self.send(405, {'error': 'Not allowed here.'}, extra=dav_head)
        except Problem as e:
            self.send(e.code, {'error': e.message})
        return True

    def do_OPTIONS(self):
        if not self.dav():
            self.send(204, b'', 'text/plain', {'Allow': 'GET, POST, HEAD, OPTIONS'})

    def do_PROPFIND(self):
        if not self.dav():
            self.send(404, {'error': 'Not found.'})

    do_REPORT = do_PUT = do_DELETE = do_PROPFIND

    def refused(self, write=False):
        if (self.headers.get('Host') or '').lower() not in TRUSTED_HOSTS:
            self.send(403, {'error': 'Unknown host.'})
            return True
        if write:
            origin = (self.headers.get('Origin') or '').lower()
            if origin and origin not in TRUSTED_ORIGINS:
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
            if not origin and (self.headers.get('Sec-Fetch-Site') or '') == 'cross-site':
                self.send(403, {'error': 'Cross-origin writes refused.'})
                return True
        return False

    def send(self, code, body=None, content_type='application/json', extra=None):
        b = body if isinstance(body, bytes) else b'' if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; base-uri 'self'; frame-ancestors 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(b)

    def do_GET(self):
        if self.dav():
            return
        if self.refused():
            return
        u = urlsplit(self.path)
        path, q = u.path, parse_qs(u.query)
        arg = lambda k: (q.get(k) or [''])[0]
        try:
            if path == '/healthz':
                return self.send(200, {'ok': True, 'app': APP_ID, 'version': VERSION})
            if path == '/api/contacts':
                return self.send(200, list_contacts(arg('q')))
            if path == '/api/lookup':
                return self.send(200, lookup(arg('network'), arg('value'), arg('email'), arg('phone')))
            if path == '/api/duplicates':
                return self.send(200, duplicates())
            if path == '/api/me':
                return self.send(200, get_me())
            if path == '/api/sync':
                return self.send(200, sync_status())
            if path.startswith('/api/sync/profile/'):
                return self.send(200, mobileconfig(path.rsplit('/', 1)[1]), 'application/x-apple-aspen-config',
                                 {'Content-Disposition': 'attachment; filename="Wizard-Contacts.mobileconfig"'})
            if path == '/api/export.vcf':
                return self.send(200, export_vcf(), 'text/vcard; charset=utf-8', {'Content-Disposition': 'attachment; filename="contacts.vcf"'})
            cid, rest = contact_route(path)
            if cid and rest == []:
                return self.send(200, get_contact(cid))
            if cid and rest == ['card.vcf']:
                c = connect()
                try:
                    return self.send(200, card_text(c, get_row(c, cid)).encode(), 'text/vcard; charset=utf-8', {'Content-Disposition': 'attachment; filename=contact.vcf'})
                finally:
                    c.close()
            if cid and rest == ['photo']:
                raw, kind = get_photo(cid)
                return self.send(200, raw, kind)
        except Problem as e:
            return self.send(e.code, {'error': e.message})
        name = 'index.html' if path == '/' else path[1:]
        kind = STATIC_TYPES.get(Path(name).suffix)
        if kind and '/' not in name and not name.startswith('.') and (ROOT / 'web' / name).is_file():
            return self.send(200, (ROOT / 'web' / name).read_bytes(), kind)
        if path == '/openapi.json':
            return self.send(200, (ROOT / 'openapi.json').read_bytes())
        self.send(405 if path == '/mcp' else 404, {'error': 'Use POST /mcp.' if path == '/mcp' else 'Not found.'})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.dav():
            return
        if self.refused(write=True):
            return
        path = urlsplit(self.path).path
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.send(415, {'error': 'Use application/json.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            limit = 9 * 1024 * 1024 if path == '/api/import' else 1024 * 1024
            if not 0 <= size <= limit:
                return self.send(413, {'error': 'Request too large.'})
            body = json.loads(self.rfile.read(size))
            if path == '/mcp':
                if not isinstance(body, dict):
                    return self.send(400, {'error': 'Expected a JSON-RPC object.'})
                out = rpc(body)
                return self.send(202 if out is None else 200, out)
            if not isinstance(body, dict):
                return self.send(400, {'error': 'Expected a JSON object.'})
            if path == '/api/contacts':
                return self.send(201, add_contact(body))
            if path == '/api/save':
                return self.send(200, save_from_app(body))
            if path == '/api/import':
                return self.send(200, import_vcf(body.get('vcf')))
            if path == '/api/attach-raw':
                return self.send(200, attach_raw(body.get('vcf') or ''))
            if path == '/api/me':
                return self.send(200, set_me(body.get('contact_id')))
            if path == '/api/me/connected':
                return self.send(200, set_connected(body))
            if path == '/api/duplicates/dismiss':
                return self.send(200, dismiss_duplicate(body.get('ids')))
            if path == '/api/sync/setup':
                server = str(body.get('server') or '')
                u = urlsplit(server)
                if u.scheme != 'https' or u.username or u.password or u.query or u.fragment or u.path != '/contacts' or (u.netloc or '').lower() != (self.headers.get('Host') or '').lower() or u.netloc.lower() not in TRUSTED_HOSTS or u.hostname in ('localhost', '127.0.0.1'):
                    raise Problem(400, 'Open Contacts through your Wizard address (https) to set up phone sync.')
                return self.send(200, sync_setup(server))
            if path == '/api/sync/off':
                return self.send(200, sync_off())
            cid, rest = contact_route(path)
            if cid and rest == []:
                return self.send(200, update_contact(cid, body))
            if cid and rest == ['delete']:
                return self.send(200, delete_contact(cid))
            if cid and rest == ['merge']:
                return self.send(200, merge_contacts(cid, str(body.get('source_id') or '')))
            if cid and rest == ['photo']:
                return self.send(200, set_photo(cid, body))
            if cid and rest == ['unlink']:
                return self.send(200, unlink_account(cid, body))
            self.send(404, {'error': 'Not found.'})
        except Problem as e:
            self.send(e.code, {'error': e.message})
        except (ValueError, TypeError, AttributeError):
            self.send(400, {'error': 'Invalid JSON request.'})

    def log_message(self, format, *args):
        pass


if __name__ == '__main__':
    connect().close()
    for f in DATA.glob('contacts.sqlite3*'):
        try:
            f.chmod(0o600)
        except OSError:
            pass
    ThreadingHTTPServer((os.environ.get('BIND', '0.0.0.0'), int(os.environ.get('PORT', '8799'))), Handler).serve_forever()
