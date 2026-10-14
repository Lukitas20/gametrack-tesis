#!/usr/bin/env python
"""Collect a bounded, resumable offline dataset of real Steam review votes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

import httpx
import truststore

from app.ml.steam_training_data import SteamReviewClient, collect, load_appids, positive_appid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--appids-file", type=Path, help="JSON array or object games:[{steam_appid:...}].")
    source.add_argument("--appids", help="Comma-separated Steam AppIDs.")
    parser.add_argument("--output-dir", type=Path, default=BACKEND_ROOT / "data" / "generated" / "steam_training")
    parser.add_argument("--max-games", type=int, default=50)
    parser.add_argument("--pages-per-game", type=int, default=2)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-requests", type=int, default=120, help="Includes HTTP retries.")
    parser.add_argument("--delay", type=float, default=1.0, help="Minimum seconds between request starts (>=1).")
    parser.add_argument("--retries", type=int, choices=range(4), default=2)
    args = parser.parse_args(argv)
    if min(args.max_games, args.pages_per_game, args.max_pages, args.max_requests) < 1 or args.delay < 1:
        parser.error("Budgets must be positive and --delay must be >=1 second.")
    try:
        appids = load_appids(args.appids_file) if args.appids_file else [
            value for part in args.appids.split(",") if (value := positive_appid(part.strip())) is not None
        ]
        truststore.inject_into_ssl()
        with httpx.Client(timeout=httpx.Timeout(25.0), headers={"User-Agent": "GameTrack-Thesis-Research/1.0"}, follow_redirects=False) as http:
            client = SteamReviewClient(http, delay=args.delay, max_requests=args.max_requests, retries=args.retries)
            manifest = collect(appids, args.output_dir, client, max_games=args.max_games,
                               pages_per_game=args.pages_per_game, max_pages=args.max_pages)
    except (ValueError, OSError) as exc:
        print(f"No se pudo preparar la recolección: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": manifest["status"], "summary": manifest["summary"],
                      "requests": manifest["requests_this_run"], "error": manifest["error"]}, ensure_ascii=False, indent=2))
    return 1 if manifest["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
