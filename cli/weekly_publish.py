"""Local commands for staging and recording weekly Google Docs publication."""

from __future__ import annotations

import argparse
import json

from tradingagents.weekly_publication import (
    mark_verified,
    prepare_publication,
    publication_resume_plan,
    record_document,
    record_folder,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare and reconcile weekly report publication")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--summary-file", help="Optional grounded scheduler Markdown summary")
    resume = subparsers.add_parser("resume-plan")
    resume.add_argument("--manifest", required=True)
    folder = subparsers.add_parser("record-folder")
    folder.add_argument("--manifest", required=True)
    folder.add_argument("--folder-id", required=True)
    document = subparsers.add_parser("record-document")
    document.add_argument("--manifest", required=True)
    document.add_argument("--name", required=True, help="Ticker, or 'digest' with --digest")
    document.add_argument("--document-id", required=True)
    document.add_argument("--url", required=True)
    document.add_argument("--digest", action="store_true")
    verified = subparsers.add_parser("mark-verified")
    verified.add_argument("--manifest", required=True)
    verified.add_argument("--name", required=True)
    verified.add_argument("--digest", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "prepare":
        value = prepare_publication(args.manifest, args.summary_file)
    elif args.command == "resume-plan":
        value = publication_resume_plan(args.manifest)
    elif args.command == "record-folder":
        record_folder(args.manifest, args.folder_id)
        value = {"recorded": "folder"}
    elif args.command == "record-document":
        record_document(args.manifest, args.name, args.document_id, args.url, digest=args.digest)
        value = {"recorded": "document"}
    else:
        mark_verified(args.manifest, args.name, digest=args.digest)
        value = {"verified": args.name}
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
