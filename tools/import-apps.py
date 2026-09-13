#!/usr/bin/env python3
"""Discover app.json repositories and stage verified complete deb submissions."""
import argparse
import json
from pathlib import Path
import sys
from app_repository import import_apps
from store_publication import PublicationError

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('repositories', nargs='*', type=Path)
parser.add_argument('--apps-root', type=Path, help='Discover app.json in direct child repositories')
parser.add_argument('--output', type=Path, required=True, help='New output directory; never overwrite a signed source')
args = parser.parse_args()
try:
    roots = list(args.repositories)
    if args.apps_root:
        roots += sorted(p.parent for p in args.apps_root.glob('*/app.json'))
    entries = import_apps(roots, args.output)
    print(json.dumps({'output': str(args.output), 'applications': [e['id'] for e in entries],
                      'signed': False, 'uploaded': False}, indent=2))
except (OSError, PublicationError, ValueError) as exc:
    print(f'Import refused: {exc}', file=sys.stderr)
    raise SystemExit(1)
