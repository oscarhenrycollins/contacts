"""Contacts API: add, search, edit, merge, photo, app save/lookup, vCard round trip, MCP. Run: python3 -m unittest tests/test_contacts.py"""
import base64
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
PNG = base64.b64encode(bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                                     '1f15c4890000000d4944415478da63f8cfc0f01f0005050200c8a8f1d20000000049454e44ae426082')).decode()


class ContactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['DATA_DIR'] = self.tmp.name
        import server
        self.s = importlib.reload(server)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_search_edit_readback(self):
        s = self.s
        c = s.add_contact({'name': 'Ana Souza', 'company': 'Acme', 'phones': [{'value': '+55 11 9999-8888', 'label': 'mobile'}],
                           'emails': ['Ana@Example.com'], 'accounts': [{'network': 'whatsapp', 'value': '5511999988888', 'label': 'Ana'}]})['contact']
        self.assertEqual(c['emails'][0]['value'], 'ana@example.com')
        self.assertEqual(c['phones'][0]['value'], '+551199998888')
        self.assertEqual([p['id'] for p in s.list_contacts('ana')['contacts']], [c['id']])
        self.assertEqual(len(s.list_contacts('99998888')['contacts']), 1)
        self.assertEqual(len(s.list_contacts('souza acme')['contacts']), 1)  # words may hit name and company
        self.assertEqual(len(s.list_contacts('souza zzz')['contacts']), 0)
        s.update_contact(c['id'], {'nickname': 'Aninha', 'favorite': True})
        got = s.get_contact(c['id'])['contact']
        self.assertTrue(got['favorite'])
        self.assertEqual(got['accounts'][0]['network'], 'whatsapp')
        with self.assertRaises(s.Problem):
            s.add_contact({'name': ' '})
        with self.assertRaises(s.Problem):
            s.add_contact({'name': 'X', 'emails': ['not-an-email']})

    def test_app_save_finds_by_account_and_never_overwrites_name(self):
        s = self.s
        first = s.save_from_app({'name': 'Bo (WA)', 'accounts': [{'network': 'whatsapp', 'value': '66811'}], 'photo': PNG})
        self.assertTrue(first['created'])
        s.update_contact(first['contact']['id'], {'name': 'Bo Chen'})
        again = s.save_from_app({'name': 'Bo something', 'accounts': [{'network': 'whatsapp', 'value': '66811'}, {'network': 'telegram', 'value': '123'}]})
        self.assertFalse(again['created'])
        self.assertEqual(again['contact']['name'], 'Bo Chen')
        self.assertEqual(len(again['contact']['accounts']), 2)
        self.assertTrue(again['contact']['photo'])
        self.assertEqual(s.lookup('telegram', '123')['contact']['id'], first['contact']['id'])
        self.assertIsNone(s.lookup('telegram', 'nobody')['contact'])

    def test_merge_and_duplicates_and_delete(self):
        s = self.s
        a = s.add_contact({'name': 'Kim Lee', 'phones': ['+1 555 0100']})['contact']
        b = s.add_contact({'name': 'kim lee', 'emails': ['kim@x.io'], 'notes': 'met in Bangkok'})['contact']
        self.assertEqual(len(s.duplicates()['duplicates']), 1)
        m = s.merge_contacts(a['id'], b['id'])['contact']
        self.assertEqual(m['emails'][0]['value'], 'kim@x.io')
        self.assertIn('Bangkok', m['notes'])
        self.assertEqual(len(s.list_contacts()['contacts']), 1)
        s.delete_contact(a['id'])
        self.assertEqual(s.list_contacts()['contacts'], [])

    def test_photo_and_vcard_round_trip(self):
        s = self.s
        c = s.add_contact({'name': 'José Álvarez', 'birthday': '1990-05-31', 'phones': ['+34 600 111 222'], 'notes': 'line1\nline2; with, commas',
                           'accounts': [{'network': 'telegram', 'value': '42'}]})['contact']
        s.set_photo(c['id'], {'data': 'data:image/png;base64,' + PNG})
        raw, kind = s.get_photo(c['id'])
        self.assertEqual(kind, 'image/png')
        vcf = s.export_vcf().decode()
        self.assertIn('FN:José Álvarez', vcf)
        os.environ['DATA_DIR'] = tempfile.mkdtemp(dir=self.tmp.name)
        import server
        s2 = importlib.reload(server)
        self.assertEqual(s2.import_vcf(vcf), {'added': 1, 'updated': 0})
        got = s2.list_contacts()['contacts'][0]
        full = s2.get_contact(got['id'])['contact']
        self.assertEqual(full['notes'], 'line1\nline2; with, commas')
        self.assertEqual(full['birthday'], '1990-05-31')
        self.assertTrue(full['photo'])
        self.assertEqual(full['accounts'][0], {'network': 'telegram', 'value': '42', 'label': ''})
        self.assertEqual(s2.import_vcf(vcf), {'added': 0, 'updated': 1})  # same phone: updates, no duplicate

    def test_import_never_merges_different_names_sharing_a_number(self):
        s = self.s
        vcf = 'BEGIN:VCARD\nFN:Mom\nTEL:+1 555 0100\nEND:VCARD\nBEGIN:VCARD\nFN:Dad\nTEL:+1 555 0100\nEND:VCARD\nBEGIN:VCARD\nFN:mom\nEMAIL:mom@x.io\nTEL:+1 555 0100\nEND:VCARD\n'
        self.assertEqual(s.import_vcf(vcf), {'added': 2, 'updated': 1})
        self.assertEqual(sorted(c['name'] for c in s.list_contacts()['contacts']), ['Dad', 'Mom'])
        self.assertEqual(s.duplicates()['duplicates'][0]['reason'], 'Same phone')

    def test_phone_fields_survive_round_trip(self):
        s = self.s
        card = ('BEGIN:VCARD\r\nVERSION:3.0\r\nN:Lee;Kim;;;\r\nFN:Kim Lee\r\nitem1.TEL:+1 555 0100\r\nitem1.X-ABLabel:_$!<Mobile>!$_\r\n'
                'item2.ADR;type=HOME:;;1 Main St;Bangkok;;10110;Thailand\r\nitem2.X-ABLabel:_$!<Home>!$_\r\nURL:https://kim.example\r\n'
                'BDAY:1604-05-31\r\nEND:VCARD\r\n')
        s.import_vcf(card)
        c = s.list_contacts()['contacts'][0]
        full = s.get_contact(c['id'])['contact']
        self.assertEqual(full['phones'][0]['label'], 'Mobile')
        self.assertEqual(full['birthday'], '--05-31')
        out = s.export_vcf().decode()
        self.assertIn('item2.ADR;type=HOME:;;1 Main St;Bangkok;;10110;Thailand', out)
        self.assertIn('item2.X-ABLabel:_$!<Home>!$_', out)
        self.assertNotIn('item1.X-ABLabel', out)  # the phone is rewritten by Contacts, its label group with it
        self.assertIn('N:Lee;Kim;;;', out)
        self.assertIn('URL:https://kim.example', out)

    def test_carddav_over_http(self):
        import http.client, threading
        from http.server import ThreadingHTTPServer
        s = self.s
        a = s.add_contact({'name': 'Ana', 'phones': ['+1 555 0111']})['contact']
        srv = ThreadingHTTPServer(('127.0.0.1', 0), s.Handler)
        port = srv.server_address[1]
        s._LOOPBACK.add('127.0.0.1:%d' % port); s.TRUSTED_HOSTS.add('127.0.0.1:%d' % port)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def req(method, path, body=None, headers=None, auth=True):
                h = {'Host': '127.0.0.1:%d' % port, **(headers or {})}
                if auth:
                    h['Authorization'] = 'Basic ' + base64.b64encode(('contacts:' + self.pw).encode()).decode()
                c = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
                c.request(method, path, body=body, headers=h)
                r = c.getresponse(); return r.status, r.read().decode(), dict(r.getheaders())
            self.pw = s.sync_setup('https://box.example:8444/contacts')['password']
            self.assertEqual(req('PROPFIND', '/dav/principal/', auth=False, headers={'Depth': '0'})[0], 401)
            st, body, _ = req('PROPFIND', '/dav/principal/', headers={'Depth': '0'})
            self.assertEqual(st, 207); self.assertIn('/dav/home/', body)
            st, body, _ = req('PROPFIND', '/dav/home/', headers={'Depth': '1'})
            self.assertIn('<card:addressbook/>', body); self.assertIn('getctag', body)
            st, body, _ = req('PROPFIND', '/dav/home/book/', headers={'Depth': '1'})
            self.assertIn(a['id'] + '.vcf', body)
            st, body, _ = req('REPORT', '/dav/home/book/', '<card:addressbook-multiget xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav"><d:href>/dav/home/book/%s.vcf</d:href></card:addressbook-multiget>' % a['id'])
            self.assertIn('FN:Ana', body)
            new = 'BEGIN:VCARD\r\nVERSION:3.0\r\nUID:PHONE-1\r\nFN:Bo Phone\r\nTEL;type=CELL:+66 81 000 1111\r\nADR:;;2 Soi;BKK;;;TH\r\nEND:VCARD\r\n'
            st, _, h = req('PUT', '/dav/home/book/PHONE-1.vcf', new, {'If-None-Match': '*', 'Content-Type': 'text/vcard'})
            self.assertEqual(st, 201)
            st, body, h2 = req('GET', '/dav/home/book/PHONE-1.vcf')
            self.assertIn('ADR:;;2 Soi;BKK;;;TH', body); self.assertIn('UID:PHONE-1', body)
            st, _, _ = req('PUT', '/dav/home/book/PHONE-1.vcf', new.replace('Bo Phone', 'Bo Edited'), {'If-Match': '"0"'})
            self.assertEqual(st, 412)
            st, _, _ = req('PUT', '/dav/home/book/PHONE-1.vcf', new.replace('Bo Phone', 'Bo Edited'), {'If-Match': h2['ETag']})
            self.assertEqual(st, 204)
            self.assertEqual(len(s.list_contacts('Bo Edited')['contacts']), 1)
            self.assertEqual(req('DELETE', '/dav/home/book/PHONE-1.vcf')[0], 204)
            self.assertEqual(len(s.list_contacts('Bo')['contacts']), 0)
            tok = s.sync_setup('https://box.example:8444/contacts')['profile_token']
            st, prof, _ = req('GET', '/api/sync/profile/' + tok, auth=False)
            self.assertIn('com.apple.carddav.account', prof); self.assertIn('/contacts/dav/principal/', prof)
            self.assertEqual(req('GET', '/api/sync/profile/' + tok, auth=False)[0], 404)  # one-time
        finally:
            srv.shutdown(); srv.server_close()

    def test_me_and_dismissed_duplicates(self):
        s = self.s
        a = s.add_contact({'name': 'Twin'})['contact']; b = s.add_contact({'name': 'twin'})['contact']
        self.assertEqual(len(s.duplicates()['duplicates']), 1)
        s.dismiss_duplicate([a['id'], b['id']])
        self.assertEqual(s.duplicates()['duplicates'], [])
        s.set_me(a['id'])
        self.assertEqual(s.get_me()['contact']['id'], a['id'])

    def test_mcp_tools(self):
        s = self.s
        out = s.rpc({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'contacts_add', 'arguments': {'name': 'Mia', 'emails': ['mia@x.io']}}})
        cid = out['result']['structuredContent']['contact']['id']
        found = s.rpc({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'contacts_lookup', 'arguments': {'email': 'MIA@x.io'}}})
        self.assertEqual(found['result']['structuredContent']['contact']['id'], cid)
        names = [t['name'] for t in s.rpc({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/list'})['result']['tools']]
        self.assertNotIn('contacts_delete', names)


if __name__ == '__main__':
    unittest.main()
