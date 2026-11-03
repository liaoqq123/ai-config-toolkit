"""JSON command-line interface for project-independent knowledge collections."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json

from config_toolkit.core.knowledge import build, query, read_note, validate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Project JSON; relative settings resolve beside this file.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("build", help="Build compact collections from explicit notes/schema indexes; no business rows read.")
    locate = commands.add_parser("query", help="Search each original note's own content and source candidates.")
    locate.add_argument("text")
    locate.add_argument("--limit", type=int, default=12)
    locate.add_argument("--source-limit", type=int, default=20)
    locate.add_argument("--source-offset", type=int, default=0)
    locate.add_argument("--no-source-check", action="store_true", help="Skip fingerprint reads for navigation-only queries.")
    read = commands.add_parser("read", help="Read exactly one original note by query result id.")
    read.add_argument("identifier")
    commands.add_parser("validate", help="Validate collection chapters, sources, links and canvases.")
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            result = build(args.project)
        elif args.command == "query":
            result = query(args.project, args.text, limit=args.limit, source_limit=args.source_limit, source_offset=args.source_offset,
                           check_sources=not args.no_source_check)
        elif args.command == "read":
            result = read_note(args.project, args.identifier)
        else:
            result = validate(args.project)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = dict(status="failed", error=str(exc), business_records_read=0, business_writes=0)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
