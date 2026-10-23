"""Measure local HTTP latency. Uses only local demo data; never sends orders externally."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import http.cookiejar
import json
from pathlib import Path
import re
import statistics
import time
import urllib.parse
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--base-url', default='http://127.0.0.1:8000')
parser.add_argument('--clients', type=int, default=50)
args = parser.parse_args()
if urllib.parse.urlparse(args.base_url).hostname not in ('127.0.0.1', 'localhost'):
    raise SystemExit('Benchmark is restricted to the local demo.')
root = Path(__file__).resolve().parent.parent
credentials = (root / '.demo-credentials.txt').read_text(encoding='utf-8')
password = re.search(r'manager / (\S+)', credentials).group(1)
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login = args.base_url + '/login/'
opener.open(login, timeout=15).read()
csrf = next(c.value for c in jar if c.name == 'csrftoken')
body = urllib.parse.urlencode({'username': 'manager', 'password': password, 'csrfmiddlewaretoken': csrf}).encode()
response = opener.open(urllib.request.Request(login, body, headers={'Referer': login}), timeout=30)
if '/login' in response.url:
    raise SystemExit('Demo sign-in failed.')
cookie = '; '.join(f'{c.name}={c.value}' for c in jar)
def measure(path):
    start = time.perf_counter()
    request = urllib.request.Request(args.base_url + path, headers={'Cookie': cookie})
    try:
        with urllib.request.urlopen(request, timeout=60) as result:
            result.read()
            status = result.status if '/login' not in result.url else 401
    except Exception as exc:
        return {'seconds': time.perf_counter()-start, 'status': 0, 'error': type(exc).__name__}
    return {'seconds': time.perf_counter()-start, 'status': status}
results = []
for path in ['/', '/recommendations/']:
    measure(path)
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        rows = list(pool.map(measure, [path] * args.clients))
    times = sorted(r['seconds'] for r in rows)
    results.append({'path': path, 'concurrent_clients': args.clients, 'requests': len(rows),
                    'successes': sum(r['status']==200 for r in rows), 'p50_seconds': round(statistics.median(times),3),
                    'p95_seconds': round(times[max(0, int(len(times)*.95)-1)],3), 'max_seconds': round(max(times),3)})
output = root / 'docs' / 'benchmark.json'
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
