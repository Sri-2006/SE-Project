"""Vendor pinned browser libraries for offline operation. Run only to restore assets."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

root = Path(__file__).resolve().parent.parent / 'static' / 'vendor'
root.mkdir(parents=True, exist_ok=True)
assets = {
    'bootstrap.min.css': 'https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css',
    'htmx.min.js': 'https://cdn.jsdelivr.net/npm/htmx.org@2.0.8/dist/htmx.min.js',
    'chart.umd.js': 'https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js',
}
manifest = {}
for name, url in assets.items():
    with urlopen(url, timeout=45) as response:
        content = response.read()
    (root / name).write_bytes(content)
    manifest[name] = {'url': url, 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}
    print(f'{name}: {len(content):,} bytes')
(root / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')

