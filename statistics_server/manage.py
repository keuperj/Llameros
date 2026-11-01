"""Server-local voucher issuance and moderation: python -m statistics_server.manage --help."""

import argparse
import json
from pathlib import Path

from .database import issue_voucher, moderate, review_entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    issue = commands.add_parser("issue-voucher", help="Print a secret single-use voucher once")
    issue.add_argument("--max-entries", type=int, default=10)
    issue.add_argument("--hours", type=int, default=72)
    review = commands.add_parser("review", help="List private moderation records as JSON")
    review.add_argument("--status", choices=["pending", "approved", "rejected"], default="pending")
    review.add_argument("--limit", type=int, default=100)
    for action in ("approve", "reject"):
        command = commands.add_parser(action)
        command.add_argument("digest", help="Public-payload content hash from review output")
    args = parser.parse_args()
    state = args.state_dir.expanduser().absolute()
    try:
        if args.command == "issue-voucher":
            print(issue_voucher(state, max_entries=args.max_entries, hours=args.hours))
        elif args.command == "review":
            print(json.dumps(review_entries(state, args.status, args.limit), indent=2))
        else:
            moderate(state, args.digest, "approved" if args.command == "approve" else "rejected")
            print("Moderation status updated. Approval is review, not proof of genuine hardware.")
    except ValueError:
        parser.exit(
            1, "Invalid operation: check arguments, entry digest, and v2 measurement evidence.\n"
        )


if __name__ == "__main__":
    main()
