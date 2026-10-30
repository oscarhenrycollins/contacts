"""Re-embed app/ + web/ + openapi.json into the inline compose recipe. Run after every source edit.
Plain text replacement of the base64 blob: every other compose line (env, DATA_DIR=/data/private, port) stays
byte-identical. (`wam package-inline` regenerates the template recipe and would drop this app's environment.)"""
import base64, json, re, sys, zlib
from pathlib import Path
root = Path(__file__).resolve().parent.parent
compose = root / 'runtipi' / 'docker-compose.yml'
text = compose.read_text()
m = re.search(r"b64decode\('([^']+)'\)", text)
if not m:
    sys.exit('inline blob not found; template changed')
raw = base64.b64decode(m.group(1))
packed = raw[:1] == b'x'
old = json.loads(zlib.decompress(raw) if packed else raw)
files = {name: (root / name).read_text() for name in old}
files['web/qr.js'] = (root / 'web/qr.js').read_text()
data = json.dumps(files).encode()
blob = base64.b64encode(zlib.compress(data, 9) if packed else data).decode()
compose.write_text(text[:m.start(1)] + blob + text[m.end(1):])
print('embedded', ', '.join(files), '->', compose)
