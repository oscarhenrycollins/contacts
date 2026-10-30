'use strict';
// Contacts: list → contact → edit, all through the documented API. Owner text only ever goes through textContent.
const $ = id => document.getElementById(id);
const NETWORKS = {whatsapp: 'WhatsApp', telegram: 'Telegram', signal: 'Signal', instagram: 'Instagram', facebook: 'Messenger', slack: 'Slack',
  line: 'LINE', linkedin: 'LinkedIn', twitter: 'X', gmessages: 'SMS', discord: 'Discord', email: 'Email', wizard: 'Wizard Messenger', private: 'Wizard Messenger'};
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
  if (p.birthday) line('Birthday', new Date((p.birthday.startsWith('--') ? '2000' + p.birthday.slice(1) : p.birthday) + 'T12:00:00').toLocaleDateString(undefined, p.birthday.startsWith('--') ? {month: 'long', day: 'numeric'} : {year: 'numeric', month: 'long', day: 'numeric'}));
  if (p.notes) { const n = el('div', 'ct-line notes'); n.append(el('small', '', 'Notes'), el('span', '', p.notes)); card.append(n); }
  if (card.children.length) d.append(card);
  const bar = el('div', 'ct-bar');
  const edit = el('button', 'wz-button', 'Edit'); edit.type = 'button'; edit.onclick = () => { d.close(); editContact(p); };
  const merge = el('button', 'wz-button secondary', 'Merge…'); merge.type = 'button'; merge.onclick = () => { d.close(); pickMerge(p); };
  bar.append(edit, merge); d.append(bar);
  WzSheet.open(d);
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
      if (kind === 'account') { const net = el('span', 'ct-net', netName(item.network)); r.append(net); r.dataset.network = item.network; r.dataset.value = item.value; r.append(el('span', 'ct-acc', item.label || item.value)); }
      else { const v = el('input', 'wz-input'); v.type = kind === 'email' ? 'email' : 'tel'; v.value = item.value || ''; v.placeholder = kind === 'email' ? 'name@example.com' : '+1 555 123 4567'; v.setAttribute('aria-label', title); const lab = el('input', 'wz-input ct-lab'); lab.value = item.label || ''; lab.placeholder = 'Label'; lab.maxLength = 40; lab.setAttribute('aria-label', title + ' label'); r.append(v, lab); }
      const x = el('button', 'ct-x', '−'); x.type = 'button'; x.setAttribute('aria-label', 'Remove'); x.onclick = () => r.remove(); r.append(x);
      rows.append(r);
    };
    items.forEach(addRow);
    if (kind !== 'account') { const more = el('button', 'ct-link', '＋ Add ' + (kind === 'email' ? 'email' : 'phone')); more.type = 'button'; more.onclick = () => { addRow(); rows.lastChild.querySelector('input')?.focus(); }; box.append(more); }
    else if (!items.length) return null;
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
    if (accounts) body.accounts = [...accounts.children].map(r => ({network: r.dataset.network, value: r.dataset.value, label: r.querySelector('.ct-acc')?.textContent || ''}));
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
      b.onclick = async () => { try { await api('api/contacts/' + p.id + '/merge', {source_id: x.id}); d.close(); await load(); toast('Merged'); openContact(p.id); } catch (e) { say(e.message, 'error'); } };
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
      go.onclick = async () => { try { for (const id of g.ids.slice(1)) await api('api/contacts/' + g.ids[0] + '/merge', {source_id: id}); b.remove(); await load(); toast('Merged'); } catch (e) { say(e.message, 'error'); } };
      b.append(go); dup.append(b);
    }
  } catch (e) { /* the menu still works */ }
};
$('file').onchange = async () => {
  const file = $('file').files[0]; $('file').value = ''; if (!file) return;
  try { const r = await api('api/import', {vcf: await file.text()}); await load(); toast('Imported: ' + r.added + ' new, ' + r.updated + ' updated'); }
  catch (e) { say(e.message, 'error'); }
};
$('add').onclick = () => editContact(null);

if (!window.WizardApp || WizardApp.version !== 1) {
  say('Wizard system functions are unavailable. Open Contacts through Wizard.', 'error');
} else {
  WizardApp.ready({name: 'contacts', version: '0.1.1'});
  WizardApp.back.register({canGoBack: () => false, back: () => false});
}
load();
document.addEventListener('visibilitychange', () => { if (!document.hidden) load(); });
