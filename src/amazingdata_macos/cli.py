from __future__ import annotations

import argparse
import json
import sys

from .client import Client
from .errors import AmazingDataMacOSError


def _json_object(value: str) -> dict:
    data = json.loads(value)
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("params must be a JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amazingdata-macos")
    parser.add_argument("--url", default=None)
    parser.add_argument("--api-key", default=None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health")
    commands.add_parser("schema")
    query = commands.add_parser("query")
    query.add_argument("namespace")
    query.add_argument("method")
    query.add_argument("--params", type=_json_object, default={})
    args = parser.parse_args(argv)
    client = Client(base_url=args.url, api_key=args.api_key)
    try:
        if args.command == "health":
            result = client.health()
        elif args.command == "schema":
            result = client.schema()
        else:
            result = client.query(
                args.namespace, args.method, args.params, raw=True
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except AmazingDataMacOSError as exc:
        print(str(exc), file=sys.stderr)
        return 1
