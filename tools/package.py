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
for name in ('web/qr.js', 'web/standalone-tokens.css'):
    files[name] = (root / name).read_text()
data = json.dumps(files).encode()
blob = base64.b64encode(zlib.compress(data, 9)).decode()
compose.write_text(text[:m.start(1)] + blob + text[m.end(1):])
# Each Linux exec argument must fit below 128 KiB; verify the embedded command itself too.
import yaml
cmd = yaml.safe_load(compose.read_text())['services']['contacts']['command'][-1]
compile(cmd, str(compose), 'exec')
assert len(cmd.encode()) < 120_000, 'inline command exceeds Linux per-argument limit'
print('embedded', ', '.join(files), '->', compose)
