"""Server-local moderation: python -m statistics_server.manage --help."""

import argparse
import json
from pathlib import Path

from .database import moderate, review_entries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("review", help="List private moderation records as JSON")
    review.add_argument("--status", choices=["pending", "approved", "rejected"], default="pending")
    review.add_argument("--limit", type=int, default=100)
    for action in ("approve", "reject"):
        command = commands.add_parser(action)
        command.add_argument("digest", help="Public-payload content hash from review output")
    args = parser.parse_args()
    state = args.state_dir.expanduser().absolute()
    try:
        if args.command == "review":
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
