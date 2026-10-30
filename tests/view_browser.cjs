// Contacts screen QA: list, search, open, edit, add, more menu; phone + desktop, dark + light. node tests/view_browser.cjs <outdir>
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict'), path = require('node:path'), fs = require('node:fs');
const base = process.env.CONTACTS_URL || 'http://127.0.0.1:18822/', out = path.resolve(process.argv[2] || 'shots'); fs.mkdirSync(out, {recursive: true});
const STUB = 'window.WizardApp = {version: 1, ready() {}, toast(t) { window.__toast = t; }, update: {hold: () => ({release() {}})}, back: {register() {}, push: () => ({release() {}})}};';
(async () => {
  const browser = await chromium.launch(), done = [];
  for (const [label, viewport, scheme] of [['390-dark', {width: 390, height: 844}, 'dark'], ['1100-light', {width: 1100, height: 800}, 'light']]) {
    const ctx = await browser.newContext({viewport, colorScheme: scheme, deviceScaleFactor: 2});
    const page = await ctx.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/wizard-os-home/core/app-client.js', r => r.fulfill({contentType: 'text/javascript', body: STUB}));
    await page.goto(base);
    await page.locator('.ct-row').first().waitFor();
    assert.equal(await page.locator('.ct-letter', {hasText: 'Favourites'}).count(), 1);
    await page.screenshot({path: path.join(out, `list-${label}.png`)});
    await page.locator('#q').fill('acme');
    await page.waitForFunction(() => document.querySelectorAll('.ct-row').length === 1);
    await page.locator('.ct-row').first().click();
    await page.locator('#view[open] .ct-top h2', {hasText: 'Ana Souza'}).waitFor();
    assert.match(await page.locator('#view .ct-card').textContent(), /WhatsApp/);
    await page.screenshot({path: path.join(out, `contact-${label}.png`)});
    await page.locator('#view').getByRole('button', {name: 'Edit'}).click();
    await page.locator('#edit[open]').waitFor();
    await page.screenshot({path: path.join(out, `edit-${label}.png`)});
    await page.locator('#edit input[name=nickname]').fill('Aninha ' + label);
    await page.locator('#edit').getByRole('button', {name: 'Save'}).click();
    await page.locator('#edit[open]').waitFor({state: 'detached'}).catch(() => {});
    await page.waitForFunction(() => !document.querySelector('#edit[open]'));
    const back = await (await fetch(base + 'api/contacts?q=aninha')).json();
    assert.equal(back.contacts.length, 1, 'edit read back through the API');
    await page.locator('#q').fill('');
    await page.locator('#add').click();
    await page.locator('#edit[open] input[name=name]').fill('New Person ' + label);
    await page.locator('#edit').getByRole('button', {name: '＋ Add phone'}).click();
    await page.locator('#edit .ct-multi-row input[type=tel]').first().fill('+1 555 222 3333');
    await page.locator('#edit').getByRole('button', {name: 'Add contact'}).click();
    await page.locator('#view[open] .ct-top h2', {hasText: 'New Person'}).waitFor();
    await page.keyboard.press('Escape');
    await page.locator('#more').click();
    await page.locator('#tools .ct-dup').first().waitFor();
    await page.screenshot({path: path.join(out, `menu-${label}.png`)});
    assert.deepEqual(errors, []);
    done.push(label); await ctx.close();
  }
  await browser.close();
  console.log('PASS contacts view: ' + done.join(', ') + ' · list, search, open, edit+readback, add, duplicates menu');
})().catch(e => { console.error('FAIL', e); process.exit(1); });
