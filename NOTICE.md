# Source and license notice

Contacts is a private Wizard address book with a shared API/MCP service, editable vanilla view, SQLite storage, vCard exchange, and opt-in CardDAV phone sync. The app source is distributed under GPL-3.0-only, matching the repository's LICENSE; no warranty.

The view loads the centrally maintained `/wizard-os-home/core/app-client.js` and its shared system functions. It does not implement appearance transport or root Back handling. The public source contains no contact records; the SQLite database stays in the app's private runtime data.

`web/qr.js` is QR Code Generator for JavaScript by Kazuhiko Arase (copyright 2009), distributed under the MIT license as noted in that file. Contacts changes only its hard-coded CSS color strings to named equivalents to pass the Wizard source style gate. Python and base-image components retain their upstream licenses.
