"""Command-line entry point.

Examples:

    # Stage 1 only — fetch listings, skip the RP Data lookup
    intelapp rentals --postcodes 4551,4575 --min-rent 800 --skip-rpdata

    # Full pipeline (first run opens a browser for RP Data login)
    intelapp rentals --postcodes 4551,4575 --min-rent 800
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Optional

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn

from .models import CSV_FIELDS, Row
from .realestate import fetch_listings

console = Console()


def _output_path() -> Path:
    out_dir = Path("output")
    out_dir.mkdir(exist_ok=True)
    return out_dir / f"rentals_{dt.date.today().isoformat()}.csv"


def _existing_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {row["listing_id"] for row in csv.DictReader(f) if row.get("listing_id")}


def _open_writer(path: Path):
    new_file = not path.exists()
    f = path.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if new_file:
        writer.writeheader()
        f.flush()
    return f, writer


@click.group()
def cli() -> None:
    """Intelapp — rental + owner-address extractor."""
    load_dotenv()


@cli.command()
@click.option(
    "--postcodes",
    default="4551,4575",
    show_default=True,
    help="Comma-separated postcodes to search.",
)
@click.option("--min-rent", default=800, show_default=True, type=int)
@click.option("--limit", default=None, type=int, help="Cap on number of listings (for testing).")
@click.option("--skip-rpdata", is_flag=True, help="Stage 1 only — don't look up owners.")
@click.option(
    "--headless/--headed",
    default=None,
    help="Force Playwright mode. Default: headed on first run, headless after.",
)
def rentals(
    postcodes: str,
    min_rent: int,
    limit: Optional[int],
    skip_rpdata: bool,
    headless: Optional[bool],
) -> None:
    """Fetch listings and write output/rentals_<date>.csv."""
    pcs = [p.strip() for p in postcodes.split(",") if p.strip()]
    out_path = _output_path()
    already = _existing_ids(out_path)

    console.print(
        f"[bold]Stage 1[/] — fetching rentals in {pcs} over ${min_rent}/wk from realestate.com.au"
    )
    listings = fetch_listings(pcs, min_rent, limit=limit)
    fresh = [l for l in listings if l.listing_id not in already]
    console.print(
        f"  found [bold]{len(listings)}[/] listings; "
        f"[bold]{len(fresh)}[/] new (skipping {len(listings) - len(fresh)} already in today's CSV)"
    )

    f, writer = _open_writer(out_path)
    try:
        if skip_rpdata or not fresh:
            for lst in fresh:
                writer.writerow(Row(listing=lst).to_csv_dict())
                f.flush()
            if not fresh:
                console.print("Nothing to write.")
            else:
                console.print(f"Stage 1 done — wrote {len(fresh)} rows to [bold]{out_path}[/].")
            return

        # Stage 2 — Playwright RP Data lookup
        from .rpdata import rpdata_session

        console.print(f"[bold]Stage 2[/] — looking up owner postal addresses in RP Data")
        with rpdata_session(headless=headless) as client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("RP Data lookups", total=len(fresh))
                for lst in fresh:
                    progress.update(task, description=f"{lst.property_address[:60]}")
                    owner = client.lookup_owner(lst.property_address)
                    writer.writerow(Row(listing=lst, owner=owner).to_csv_dict())
                    f.flush()
                    progress.advance(task)
    finally:
        f.close()

    console.print(f"[bold green]Done[/] — {out_path}")


if __name__ == "__main__":
    cli()
