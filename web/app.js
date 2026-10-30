'use strict';
// Contacts: list → contact → edit, all through the documented API. Owner text only ever goes through textContent.
const $ = id => document.getElementById(id);
const NETWORKS = {whatsapp: 'WhatsApp', telegram: 'Telegram', signal: 'Signal', instagram: 'Instagram', facebook: 'Messenger', slack: 'Slack',
  line: 'LINE', linkedin: 'LinkedIn', twitter: 'X', gmessages: 'SMS', discord: 'Discord', email: 'Email', wizard: 'Wizard Messenger', private: 'Wizard Messenger'};
// Share links per network: what a QR code opens on someone else's phone, and how pasted links are read back.
const LINKS = {
  whatsapp: v => /^\+?\d{6,15}$/.test(v) ? 'https://wa.me/' + v.replace(/\D/g, '') : '',
  signal: v => /^\+?\d{6,15}$/.test(v) ? 'https://signal.me/#p/+' + v.replace(/\D/g, '') : '',
  telegram: v => /^[A-Za-z][\w]{3,31}$/.test(v) ? 'https://t.me/' + v : '',
  instagram: v => /^[\w.]{1,30}$/.test(v) && !/^\d+$/.test(v) ? 'https://instagram.com/' + v : '',
  line: v => /^[\w.@-]{2,40}$/.test(v) ? 'https://line.me/ti/p/~' + v : '',
  linkedin: v => /^[\w-]{3,100}$/.test(v) && !/^\d+$/.test(v) ? 'https://www.linkedin.com/in/' + v : '',
  twitter: v => /^\w{1,15}$/.test(v) && !/^\d+$/.test(v) ? 'https://x.com/' + v : '',
  facebook: v => /^[\w.]{3,50}$/.test(v) && !/^\d+$/.test(v) ? 'https://m.me/' + v : '',
  wizard: v => /^(https:\/\/|npub1)/.test(v) ? v : '',
};
const READ_LINK = [
  [/^(?:https?:\/\/)?(?:api\.)?wa\.me\/(\d{6,15})/i, 'whatsapp', m => '+' + m[1]], [/^(?:https?:\/\/)?(?:t\.me|telegram\.me)\/([A-Za-z]\w{3,31})/i, 'telegram'],
  [/^(?:https?:\/\/)?(?:www\.)?instagram\.com\/([\w.]{1,30})/i, 'instagram'], [/^(?:https?:\/\/)?line\.me\/ti\/p\/~?([\w.@-]+)/i, 'line'],
  [/^(?:https?:\/\/)?(?:[a-z]+\.)?linkedin\.com\/in\/([\w-]+)/i, 'linkedin'], [/^(?:https?:\/\/)?(?:www\.)?(?:x|twitter)\.com\/(\w{1,15})/i, 'twitter'],
  [/^(?:https?:\/\/)?(?:m\.me|(?:www\.)?facebook\.com)\/([\w.]{3,50})/i, 'facebook'], [/^(?:https?:\/\/)?signal\.me\/#p\/(\+?\d{6,15})/i, 'signal'],
];
function readLink(text) {
  text = (text || '').trim();
  for (const [re, net, val] of READ_LINK) { const m = re.exec(text); if (m) return {network: net, value: val ? val(m) : m[1]}; }
  return null;
}
const shareLink = (a, p) => {
  if (a.network === 'whatsapp' && !LINKS.whatsapp(a.value) && p?.phones?.[0]) return LINKS.whatsapp(p.phones[0].value);
  return (LINKS[a.network] || (() => ''))(a.value);
};
const BASE = location.pathname.replace(/\/(index\.html)?$/, '');
function qrImage(text, label) {
  if (typeof window.qrcode !== 'function') return el('p', 'ct-help', 'QR codes are unavailable here.');
  qrcode.stringToBytes = qrcode.stringToBytesFuncs['UTF-8'];
  try { const q = qrcode(0, text.length > 300 ? 'L' : 'M'); q.addData(text); q.make();
    const img = el('img', 'ct-qr'); img.alt = label || 'QR code'; img.src = q.createDataURL(8, 2); return img; }
  catch { return el('p', 'ct-help', 'This card is too large for a QR code. Export it as a vCard instead.'); }
}
function cardForQR(p) {
  const esc = v => String(v).replace(/([\\,;])/g, '\\$1').replace(/\n/g, '\\n');
  const lines = ['BEGIN:VCARD', 'VERSION:3.0', 'FN:' + esc(p.name)];
  if (p.company) lines.push('ORG:' + esc(p.company));
  for (const x of p.phones.slice(0, 3)) lines.push('TEL:' + x.value);
  for (const x of p.emails.slice(0, 2)) lines.push('EMAIL:' + x.value);
  for (const a of p.accounts) { const u = shareLink(a, p); if (/^https:/.test(u)) lines.push('URL:' + u); }
  lines.push('END:VCARD'); return lines.join('\n');
}
const netName = n => NETWORKS[n] || (n ? n[0].toUpperCase() + n.slice(1) : '');
let all = [], query = '';

function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function say(text, kind = '') { const s = $('status'); s.classList.toggle('wz-sr', !kind); s.classList.toggle('error', kind === 'error'); s.textContent = text; if (kind) setTimeout(() => { if (s.textContent === text) say(''); }, 5000); }
function toast(text) { if (window.WizardApp?.toast) WizardApp.toast(text); else say(text, 'note'); }
async function api(path, body) {
  const init = body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)};
  const r = await fetch(path, init);
  let data = {}; try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) throw new Error(data.error || 'Contacts could not do that. Try again.');
  return data;
}

const initials = name => (name || '?').trim().split(/\s+/).slice(0, 2).map(w => [...w][0] || '').join('').toUpperCase() || '?';
function face(p, size = '') {
  const f = el('span', 'ct-face' + (size ? ' ' + size : ''), initials(p.name));
  f.dataset.tone = String([...(p.id || p.name || '')].reduce((a, c) => a + c.charCodeAt(0), 0) % 6);
  if (p.photo) { const img = el('img'); img.alt = ''; img.loading = 'lazy'; img.src = 'api/contacts/' + p.id + '/photo?v=' + p.updated; img.onerror = () => img.remove(); f.append(img); }
  return f;
}
const subtitle = p => [p.company, p.phones[0]?.value, p.emails[0]?.value].filter(Boolean)[0] || p.accounts.map(a => netName(a.network)).filter((v, i, a) => a.indexOf(v) === i).join(' · ');

function render() {
  const list = $('list'); list.replaceChildren();
  const q = query.trim().toLowerCase();
  const shown = all;
  $('empty').hidden = !!all.length || !!q;
  if (!shown.length && q) list.append(el('p', 'ct-none', 'No one matches “' + query.trim() + '”.'));
  const favs = q ? [] : shown.filter(p => p.favorite);
  const section = (title, people) => {
    if (!people.length) return;
    const sec = el('section', 'ct-group'); sec.append(el('h2', 'ct-letter', title));
    const ul = el('ul', 'wz-list'); for (const p of people) ul.append(row(p)); sec.append(ul); list.append(sec);
  };
  section('Favourites', favs);
  if (q) { section(shown.length + (shown.length === 1 ? ' contact' : ' contacts'), shown); return; }
  const groups = new Map();
  for (const p of shown) { const c = (p.name.normalize('NFKD')[0] || '#').toUpperCase(); const k = /[A-Z]/.test(c) ? c : '#'; if (!groups.has(k)) groups.set(k, []); groups.get(k).push(p); }
  for (const [k, people] of [...groups].sort((a, b) => (a[0] === '#') - (b[0] === '#') || a[0].localeCompare(b[0]))) section(k, people);
}
function row(p) {
  const li = el('li'), b = el('button', 'wz-row ct-row'); b.type = 'button';
  const main = el('span', 'wz-main'); main.append(el('b', '', p.name));
  const sub = subtitle(p); if (sub) main.append(el('small', '', sub));
  b.append(face(p), main);
  if (p.favorite) b.append(el('span', 'ct-star', '★'));
  b.setAttribute('aria-label', p.name + (sub ? ', ' + sub : ''));
  b.onclick = () => openContact(p.id);
  li.append(b); return li;
}

async function load() {
  try { all = (await api('api/contacts?q=' + encodeURIComponent(query))).contacts; render(); }
  catch (e) { say(e.message, 'error'); }
}
let timer; $('q').addEventListener('input', e => { query = e.target.value; clearTimeout(timer); timer = setTimeout(load, 120); });

// ---- One contact ----
async function openContact(id) {
  let p; try { p = (await api('api/contacts/' + id)).contact; } catch (e) { say(e.message, 'error'); return; }
  const d = $('view'); d.replaceChildren();
  const top = el('div', 'ct-top'); top.append(face(p, 'big'));
  const name = el('h2', '', p.name); name.id = 'view-name'; top.append(name);
  const sub = [p.nickname && '“' + p.nickname + '”', [p.title, p.company].filter(Boolean).join(', ')].filter(Boolean).join(' · ');
  if (sub) top.append(el('p', 'ct-sub', sub));
  d.append(top);
  const acts = el('div', 'ct-acts');
  const act = (label, href) => { const a = el('a', 'ct-act', label); a.href = href; a.target = '_blank'; a.rel = 'noopener'; acts.append(a); };
  if (p.phones[0]) { act('Call', 'tel:' + p.phones[0].value); act('Text', 'sms:' + p.phones[0].value); }
  if (p.emails[0]) act('Email', 'mailto:' + p.emails[0].value);
  const fav = el('button', 'ct-act' + (p.favorite ? ' on' : ''), p.favorite ? '★ Favourite' : '☆ Favourite'); fav.type = 'button';
  fav.onclick = async () => { try { await api('api/contacts/' + p.id, {favorite: !p.favorite}); await load(); openContact(p.id); } catch (e) { say(e.message, 'error'); } };
  acts.append(fav); d.append(acts);
  const card = el('div', 'ct-card');
  const line = (label, value, href) => { const r = el(href ? 'a' : 'div', 'ct-line'); if (href) { r.href = href; r.target = '_blank'; r.rel = 'noopener'; } r.append(el('small', '', label), el('span', '', value)); card.append(r); };
  for (const x of p.phones) line(x.label || 'Phone', x.value, 'tel:' + x.value);
  for (const x of p.emails) line(x.label || 'Email', x.value, 'mailto:' + x.value);
  for (const x of p.accounts) line(netName(x.network), x.label || x.value);
  if (p.birthday) line('Birthday', new Date((p.birthday.startsWith('--') ? '2000-' + p.birthday.slice(2) : p.birthday) + 'T12:00:00').toLocaleDateString(undefined, p.birthday.startsWith('--') ? {month: 'long', day: 'numeric'} : {year: 'numeric', month: 'long', day: 'numeric'}));
  if (p.notes) { const n = el('div', 'ct-line notes'); n.append(el('small', '', 'Notes'), el('span', '', p.notes)); card.append(n); }
  if (card.children.length) d.append(card);
  const bar = el('div', 'ct-bar');
  const edit = el('button', 'wz-button', 'Edit'); edit.type = 'button'; edit.onclick = () => { d.close(); editContact(p); };
  const merge = el('button', 'wz-button secondary', 'Merge…'); merge.type = 'button'; merge.onclick = () => { d.close(); pickMerge(p); };
  const share = el('button', 'wz-button secondary', 'Share'); share.type = 'button'; share.onclick = () => { d.close(); shareCard(p, false); };
  bar.append(edit, share, merge); d.append(bar);
  WzSheet.open(d);
}

// ---- Share a card as QR codes: the whole card (adds to any phone's contacts) or one network's link ----
function shareCard(p, mine) {
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', mine ? 'My card' : 'Share ' + p.name); h.id = 'tools-title'; d.append(h);
  const options = [['card', 'Contact card', cardForQR(p)], ...p.accounts.map(a => [a.network + ':' + a.value, netName(a.network), shareLink(a, p)]).filter(o => o[2])];
  const seen = new Set(); const opts = options.filter(o => !seen.has(o[2]) && seen.add(o[2]));
  const chips = el('div', 'ct-chips'), stage = el('div', 'ct-qr-stage');
  const show = i => {
    [...chips.children].forEach((c, j) => c.setAttribute('aria-pressed', String(i === j)));
    const [, label, text] = opts[i]; stage.replaceChildren(qrImage(text, label + ' QR code'));
    stage.append(el('p', 'ct-help', i === 0 ? 'Point a phone camera here to add ' + (mine ? 'you' : p.name) + ' to its contacts.' : 'Scan to open ' + (mine ? 'your' : p.name + '’s') + ' ' + label + '.'));
    if (i) { const copy = el('button', 'wz-button secondary', 'Copy link'); copy.type = 'button'; copy.onclick = async () => { try { await navigator.clipboard.writeText(text); toast('Link copied'); } catch { say(text, 'note'); } }; stage.append(copy); }
  };
  opts.forEach((o, i) => { const c = el('button', 'wz-chip ct-chip', o[1]); c.type = 'button'; c.onclick = () => show(i); chips.append(c); });
  d.append(chips, stage); show(0);
  const bar = el('div', 'ct-bar');
  const download = el('a', 'wz-button secondary', mine ? 'Download my card' : 'Download contact'); download.href = 'api/contacts/' + p.id + '/card.vcf'; download.download = 'contact.vcf'; bar.append(download);
  if (mine) {
    d.append(el('p', 'ct-help ct-note', 'Accounts you connect in Messenger appear here automatically.'));
    const edit = el('button', 'wz-button secondary', 'Edit my card'); edit.type = 'button'; edit.onclick = () => { d.close(); editContact(p); }; bar.append(edit);
  }
  d.append(bar);
  WzSheet.open(d);
}
async function myCard() {
  let me = null; try { me = (await api('api/me')).contact; } catch (e) { /* none */ }
  if (me) return shareCard(me, true);
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', 'Which contact is you?'); h.id = 'tools-title'; d.append(h);
  d.append(el('p', 'ct-help', 'Your card holds your own numbers and usernames, so you can share them as QR codes.'));
  const create = el('button', 'wz-button', 'Create my card'); create.type = 'button';
  create.onclick = async () => { const name = prompt.value.trim(); if (!name) { prompt.focus(); return; } try { const c = (await api('api/contacts', {name})).contact; await api('api/me', {contact_id: c.id}); d.close(); await load(); editContact(c); } catch (e) { say(e.message, 'error'); } };
  const prompt = el('input', 'wz-input'); prompt.placeholder = 'Your name'; prompt.setAttribute('aria-label', 'Your name');
  const q = el('input', 'wz-input'); q.type = 'search'; q.placeholder = 'Or find yourself'; q.setAttribute('aria-label', 'Find yourself');
  const ul = el('ul', 'wz-list ct-pick');
  const paint = () => { ul.replaceChildren(); const t = q.value.trim().toLowerCase(); if (!t) return;
    for (const x of all.filter(x => x.name.toLowerCase().includes(t)).slice(0, 30)) { const li = el('li'), b = el('button', 'wz-row'); b.type = 'button'; const m = el('span', 'wz-main'); m.append(el('b', '', x.name)); b.append(face(x), m);
      b.onclick = async () => { try { await api('api/me', {contact_id: x.id}); d.close(); myCard(); } catch (e) { say(e.message, 'error'); } }; li.append(b); ul.append(li); } };
  q.oninput = paint;
  const row = el('div', 'ct-inline'); row.append(prompt, create);
  d.append(row, q, ul); WzSheet.open(d); prompt.focus();
}

// ---- Add / edit ----
function editContact(p) {
  const fresh = !p; p = p || {name: '', nickname: '', company: '', title: '', notes: '', birthday: '', phones: [], emails: [], accounts: []};
  const d = $('edit'); d.replaceChildren();
  const h = el('h2', '', fresh ? 'New contact' : 'Edit contact'); h.id = 'edit-title'; d.append(h);
  const form = el('form', 'ct-form'); form.autocomplete = 'off'; form.noValidate = true;
  const input = (label, name, value, type = 'text', max = 120) => { const l = el('label', 'ct-field'); l.append(el('span', '', label)); const i = el('input', 'wz-input'); i.name = name; i.type = type; i.value = value || ''; i.maxLength = max; l.append(i); form.append(l); return i; };
  const nameIn = input('Name', 'name', p.name);
  input('Nickname', 'nickname', p.nickname, 'text', 80);
  input('Company', 'company', p.company);
  input('Job title', 'title', p.title);
  const multi = (title, items, kind) => {
    const box = el('fieldset', 'ct-multi'); box.append(el('legend', '', title));
    const rows = el('div'); box.append(rows);
    const addRow = (item = {}) => {
      const r = el('div', 'ct-multi-row');
      if (kind === 'account' && item.network) { const net = el('span', 'ct-net', netName(item.network)); r.append(net); r.dataset.network = item.network; r.dataset.value = item.value; r.append(el('span', 'ct-acc', item.label || item.value)); }
      else if (kind === 'account') {
        const sel = el('select', 'wz-input ct-lab'); sel.setAttribute('aria-label', 'Network');
        for (const n of ['whatsapp', 'telegram', 'instagram', 'signal', 'line', 'linkedin', 'twitter', 'facebook']) { const o = el('option', '', netName(n)); o.value = n; sel.append(o); }
        const v = el('input', 'wz-input'); v.placeholder = 'Username, number or link'; v.setAttribute('aria-label', 'Account');
        v.addEventListener('change', () => { const got = readLink(v.value); if (got) { sel.value = got.network; v.value = got.value; } });
        r.dataset.free = '1'; r.append(sel, v);
      }
      else { const v = el('input', 'wz-input'); v.type = kind === 'email' ? 'email' : 'tel'; v.value = item.value || ''; v.placeholder = kind === 'email' ? 'name@example.com' : '+1 555 123 4567'; v.setAttribute('aria-label', title); const lab = el('input', 'wz-input ct-lab'); lab.value = item.label || ''; lab.placeholder = 'Label'; lab.maxLength = 40; lab.setAttribute('aria-label', title + ' label'); r.append(v, lab); }
      const x = el('button', 'ct-x', '−'); x.type = 'button'; x.setAttribute('aria-label', 'Remove'); x.onclick = () => r.remove(); r.append(x);
      rows.append(r);
    };
    items.forEach(addRow);
    const more = el('button', 'ct-link', '＋ Add ' + (kind === 'email' ? 'email' : kind === 'phone' ? 'phone' : 'account (WhatsApp, Telegram, Instagram…)')); more.type = 'button';
    more.onclick = () => { addRow(); rows.lastChild.querySelector('input')?.focus(); }; box.append(more);
    form.append(box); return rows;
  };
  const phones = multi('Phones', p.phones, 'phone'), emails = multi('Emails', p.emails, 'email'), accounts = multi('Accounts', p.accounts, 'account');
  const bday = input('Birthday', 'birthday', p.birthday, 'text', 10); bday.placeholder = '1990-05-31';
  const notes = el('label', 'ct-field'); notes.append(el('span', '', 'Notes')); const ta = el('textarea', 'wz-input ct-notes'); ta.name = 'notes'; ta.maxLength = 4000; ta.value = p.notes || ''; ta.rows = 3; notes.append(ta); form.append(notes);
  const err = el('p', 'ct-err'); err.setAttribute('role', 'alert'); form.append(err);
  const bar = el('div', 'ct-bar');
  const save = el('button', 'wz-button', fresh ? 'Add contact' : 'Save'); save.type = 'submit'; bar.append(save);
  if (!fresh) {
    const del = el('button', 'wz-button secondary danger', 'Delete'); del.type = 'button'; let armed = false;
    del.onclick = async () => {
      if (!armed) { armed = true; del.textContent = 'Tap again to delete'; setTimeout(() => { armed = false; del.textContent = 'Delete'; }, 3000); return; }
      try { await api('api/contacts/' + p.id + '/delete', {}); d.close(); await load(); toast(p.name + ' deleted'); } catch (e) { err.textContent = e.message; }
    };
    bar.append(del);
  }
  if (!fresh) {
    const pic = el('button', 'wz-button secondary', p.photo ? 'Change photo' : 'Add photo'); pic.type = 'button';
    pic.onclick = () => choosePhoto(p.id, () => { d.close(); load().then(() => openContact(p.id)); });
    bar.append(pic);
  }
  form.append(bar);
  form.onsubmit = async e => {
    e.preventDefault(); err.textContent = '';
    const val = n => form.elements[n].value;
    const body = {name: val('name'), nickname: val('nickname'), company: val('company'), title: val('title'), birthday: val('birthday'), notes: ta.value,
      phones: [...phones.children].map(r => ({value: r.children[0].value, label: r.children[1].value})).filter(x => x.value.trim()),
      emails: [...emails.children].map(r => ({value: r.children[0].value, label: r.children[1].value})).filter(x => x.value.trim())};
    body.accounts = [...accounts.children].map(r => r.dataset.free ? {network: r.querySelector('select').value, value: (readLink(r.querySelector('input').value)?.value || r.querySelector('input').value).trim().replace(/^@/, ''), label: ''}
      : {network: r.dataset.network, value: r.dataset.value, label: r.querySelector('.ct-acc')?.textContent || ''}).filter(x => x.value);
    save.disabled = true;
    try {
      const r = fresh ? await api('api/contacts', body) : await api('api/contacts/' + p.id, body);
      d.close(); await load(); toast(fresh ? 'Contact added' : 'Saved');
      if (fresh) openContact(r.contact.id);
    } catch (x) { err.textContent = x.message; }
    finally { save.disabled = false; }
  };
  d.append(form); WzSheet.open(d); if (fresh) nameIn.focus();
}

function choosePhoto(id, done) {
  const f = el('input'); f.type = 'file'; f.accept = 'image/*';
  f.onchange = async () => {
    const file = f.files[0]; if (!file) return;
    try {
      const bmp = await createImageBitmap(file); const s = Math.min(1, 512 / Math.max(bmp.width, bmp.height));
      const c = el('canvas'); c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s); c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
      await api('api/contacts/' + id + '/photo', {data: c.toDataURL('image/jpeg', 0.86)}); done();
    } catch (e) { say(e.message || 'That photo could not be used.', 'error'); }
  };
  f.click();
}

// ---- Merge two contacts (owner picks; never automatic) ----
function pickMerge(p) {
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', 'Merge with…'); h.id = 'tools-title'; d.append(h);
  d.append(el('p', 'ct-help', 'Pick the same person. Their phones, emails and accounts join ' + p.name + '.'));
  const q = el('input', 'wz-input'); q.type = 'search'; q.placeholder = 'Search'; q.setAttribute('aria-label', 'Search contacts'); d.append(q);
  const ul = el('ul', 'wz-list ct-pick'); d.append(ul);
  const paint = () => {
    ul.replaceChildren(); const t = q.value.trim().toLowerCase();
    for (const x of all.filter(x => x.id !== p.id && (!t || x.name.toLowerCase().includes(t))).slice(0, 80)) {
      const li = el('li'), b = el('button', 'wz-row'); b.type = 'button';
      const main = el('span', 'wz-main'); main.append(el('b', '', x.name)); const s = subtitle(x); if (s) main.append(el('small', '', s));
      b.append(face(x), main);
      b.onclick = async () => { if (!b.dataset.confirm) { b.dataset.confirm = '1'; b.append(el('small', '', 'Tap again to confirm merge. This cannot be undone.')); return; } try { await api('api/contacts/' + p.id + '/merge', {source_id: x.id}); d.close(); await load(); toast('Merged'); openContact(p.id); } catch (e) { say(e.message, 'error'); } };
      li.append(b); ul.append(li);
    }
  };
  q.oninput = paint; paint(); WzSheet.open(d); q.focus();
}

// ---- Import / export / duplicates ----
$('more').onclick = async () => {
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', 'Contacts'); h.id = 'tools-title'; d.append(h);
  const list = el('div', 'ct-menu');
  const item = (label, help, run) => { const b = el('button', 'ct-menu-item'); b.type = 'button'; b.append(el('b', '', label), el('small', '', help)); b.onclick = run; list.append(b); };
  item('My card & QR codes', 'Share your details or individual network links', () => { d.close(); myCard(); });
  item('Phone sync', 'Keep contacts on your Wizard and sync with your phone', () => { d.close(); phoneSync(); });
  item('Add from a link', 'Paste a Telegram, WhatsApp, Instagram or other profile link', () => { d.close(); addFromLink(); });
  item('Import from a file', 'A .vcf file exported from your phone, Google or iCloud', () => { d.close(); $('file').click(); });
  item('Export all', 'Download every contact as one .vcf file', () => { const a = el('a'); a.href = 'api/export.vcf'; a.download = 'contacts.vcf'; document.body.append(a); a.click(); a.remove(); });
  d.append(list);
  const dup = el('div', 'ct-dups'); d.append(dup);
  WzSheet.open(d);
  try {
    const r = await api('api/duplicates');
    if (!r.duplicates.length) return;
    dup.append(el('h3', 'ct-letter', 'Possible duplicates'));
    for (const g of r.duplicates.slice(0, 30)) {
      const b = el('div', 'ct-dup'); const names = el('span', 'wz-main'); names.append(el('b', '', g.contacts.map(c => c.name).join(' + ')), el('small', '', g.reason)); b.append(names);
      const go = el('button', 'wz-button secondary', 'Merge'); go.type = 'button';
      go.onclick = async () => { if (!go.dataset.confirm) { go.dataset.confirm = '1'; go.textContent = 'Tap again to merge'; return; } try { for (const id of g.ids.slice(1)) await api('api/contacts/' + g.ids[0] + '/merge', {source_id: id}); b.remove(); await load(); toast('Merged'); } catch (e) { say(e.message, 'error'); } };
      const no = el('button', 'wz-button secondary', 'Not the same'); no.type = 'button'; no.onclick = async () => { try { await api('api/duplicates/dismiss', {ids: g.ids}); b.remove(); toast('Kept separate'); } catch (e) { say(e.message, 'error'); } };
      b.append(go, no); dup.append(b);
    }
  } catch (e) { /* the menu still works */ }
};
$('file').onchange = async () => {
  const file = $('file').files[0]; $('file').value = ''; if (!file) return;
  try { const r = await api('api/import', {vcf: await file.text()}); await load(); toast('Imported: ' + r.added + ' new, ' + r.updated + ' updated'); }
  catch (e) { say(e.message, 'error'); }
};
$('add').onclick = () => editContact(null);
$('mine').onclick = () => myCard();

async function addFromLink() {
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', 'Add from a link'); h.id = 'tools-title'; d.append(h);
  d.append(el('p', 'ct-help', 'Paste a person’s network link. We will suggest an existing contact, not merge people automatically.'));
  const link = el('input', 'wz-input'); link.type = 'url'; link.placeholder = 'https://t.me/username'; link.setAttribute('aria-label', 'Profile link');
  const name = el('input', 'wz-input'); name.placeholder = 'Their name'; name.setAttribute('aria-label', 'Their name');
  const help = el('p', 'ct-help'), matches = el('div', 'ct-dups');
  const add = el('button', 'wz-button', 'Add person'); add.type = 'button';
  const found = () => readLink(link.value);
  link.oninput = () => {
    const a = found(); help.textContent = a ? netName(a.network) + ' · ' + a.value : 'That link is not recognized. Try a WhatsApp, Telegram, Instagram, Signal, LINE, LinkedIn, X or Messenger profile link.';
    matches.replaceChildren(); if (!a) return;
    const possible = all.filter(p => p.name.toLowerCase() === name.value.trim().toLowerCase() || p.accounts.some(x => x.network === a.network && x.value === a.value));
    if (possible.length) { matches.append(el('p', 'ct-help', 'Possible existing person — open and edit instead of making a duplicate:'));
      for (const p of possible.slice(0, 5)) { const b = el('button', 'wz-button secondary', p.name); b.type = 'button'; b.onclick = () => { d.close(); openContact(p.id); }; matches.append(b); } }
  };
  name.oninput = link.oninput;
  add.onclick = async () => { const a = found(); if (!a) { link.oninput(); link.focus(); return; } if (!name.value.trim()) { name.focus(); return; }
    try { const r = await api('api/contacts', {name: name.value.trim(), accounts: [a]}); d.close(); await load(); openContact(r.contact.id); } catch (e) { say(e.message, 'error'); } };
  const area = el('div', 'ct-add-link'); area.append(link, name, help, matches, add); d.append(area); WzSheet.open(d); link.focus();
}

async function phoneSync() {
  const d = $('tools'); d.replaceChildren();
  const h = el('h2', '', 'Phone sync'); h.id = 'tools-title'; d.append(h);
  const status = el('p', 'ct-help', 'Checking…'); d.append(status);
  d.append(el('p', 'ct-help', 'Your Wizard holds the address book. Your phone needs Tailscale access to this Wizard to sync. Keep iCloud Contacts on until you confirm edits work both ways.'));
  let state; try { state = await api('api/sync'); } catch (e) { status.textContent = e.message; WzSheet.open(d); return; }
  status.textContent = state.enabled ? 'Sync is on' + (state.last_sync ? ' · last connected ' + new Date(state.last_sync).toLocaleString() : ' · no phone has connected yet') : 'Sync is off';
  const setup = el('button', 'wz-button', state.enabled ? 'Replace sync password' : 'Set up phone sync'); setup.type = 'button';
  setup.onclick = async () => {
    if (state.enabled && !setup.dataset.confirm) { setup.dataset.confirm = '1'; setup.textContent = 'Tap again — old phones will disconnect'; return; }
    try {
      const r = await api('api/sync/setup', {server: location.origin + BASE});
      state = r; status.textContent = 'Sync is on · no phone has connected with this password yet';
      secret.replaceChildren();
      secret.append(el('p', 'ct-help', 'iPhone: open the one-time setup file below in Safari, then Settings → Profile Downloaded → Install. Review the account and keep the file private. Android: use a CardDAV client such as DAVx⁵.'));
      const link = el('a', 'wz-button', 'Download iPhone setup'); link.href = 'api/sync/profile/' + r.profile_token; link.download = 'Wizard-Contacts.mobileconfig'; secret.append(link);
      const manual = el('p', 'ct-help', 'Manual CardDAV server: ' + location.origin + BASE + '/dav/principal/ · user: contacts'); secret.append(manual);
      const pass = el('code', 'ct-secret', r.password); secret.append(el('p', 'ct-help', 'Sync password (shown once):'), pass);
      const copy = el('button', 'wz-button secondary', 'Copy password'); copy.type = 'button'; copy.onclick = async () => { await navigator.clipboard.writeText(r.password); toast('Copied'); }; secret.append(copy);
      setTimeout(() => { pass.textContent = 'Password hidden; replace it to see a new one.'; }, 60000);
    } catch (e) { say(e.message, 'error'); }
  };
  const secret = el('div', 'ct-sync-secret'); d.append(setup, secret);
  if (state.enabled) { const off = el('button', 'wz-button secondary', 'Turn off sync'); off.type = 'button'; off.onclick = async () => { if (!off.dataset.confirm) { off.dataset.confirm = '1'; off.textContent = 'Tap again to stop phone sync'; return; } try { await api('api/sync/off', {}); d.close(); phoneSync(); } catch (e) { say(e.message, 'error'); } }; d.append(off); }
  WzSheet.open(d);
}

if (!window.WizardApp || WizardApp.version !== 1) {
  say('Wizard system functions are unavailable. Open Contacts through Wizard.', 'error');
} else {
  WizardApp.ready({name: 'contacts', version: '0.3.1'});
  WizardApp.back.register({canGoBack: () => false, back: () => false});
}
load();
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
