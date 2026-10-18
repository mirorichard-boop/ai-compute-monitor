"""Shared plumbing for collectors: HTTP, append-only CSV merge, secrets, config.

Every data file is an append-only CSV keyed on a tuple of columns. Merging never
drops an existing row; a new row with the same key replaces the old one (so a
re-run on the same day is idempotent and a revised settle overwrites itself).
"""
import csv
import datetime as dt
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = ROOT / "config"

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL = ssl.create_default_context()

UA = "ai-compute-monitor/1.0 (personal research dashboard)"


class SkipSource(Exception):
    """Raised when a source can't run (missing key). Logged as skipped, not failed."""


def today():
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def secret(name):
    val = os.environ.get(name, "").strip()
    env = ROOT / ".env"
    if not val and env.exists():
        for line in env.read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                val = v.strip().strip("'\"")
    return val


def get(url, params=None, headers=None, retries=3, timeout=45):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"GET failed: {url.split('?')[0]}: {last}")


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def load_yaml(name):
    import yaml
    with open(CONFIG / name) as f:
        return yaml.safe_load(f) or {}


def merge_csv(name, rows, key, fields):
    """Merge rows (list of dicts) into data/<name>, keyed on the `key` columns."""
    path = DATA / name
    existing = {}
    if path.exists():
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                existing[tuple(r[k] for k in key)] = r
    added = 0
    for r in rows:
        r = {k: ("" if r.get(k) is None else str(r.get(k))) for k in fields}
        k = tuple(r[c] for c in key)
        if k not in existing:
            added += 1
        existing[k] = r
    ordered = sorted(existing.values(), key=lambda r: tuple(r[c] for c in key))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(ordered)
    return added, len(ordered)


def blended(prompt, completion, ratio):
    """Blended USD per token, weighting input:output tokens ratio:1."""
    return (ratio * prompt + completion) / (ratio + 1)
