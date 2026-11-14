#!/usr/bin/env python3
"""Generate local, theme-aware contribution charts from GitHub's public calendar.

Python standard library only. The public HTML is not a stable GitHub API: fail
closed if its schema changes, leaving the last successful artwork untouched.
Use --from-json assets/activity/snapshot.json to reproduce a saved render offline.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
MAX_RESPONSE_BYTES = 2_000_000
USERNAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
PALETTES = {
    "dark": {"bg": "#171a16", "surface": "#1f231e", "line": "#363b34",
             "muted": "#a7afa2", "text": "#f3f5ef", "accent": "#f2707f",
             "levels": ["#272c25", "#55272c", "#8d2934", "#c4303f", "#f2707f"]},
    "light": {"bg": "#ffffff", "surface": "#f3f5f0", "line": "#d7ddd2",
              "muted": "#586150", "text": "#171a16", "accent": "#b42637",
              "levels": ["#e8ece4", "#f9cdd1", "#ec8b96", "#c4303f", "#b42637"]},
}


class CalendarParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict] = []
        self.tooltips: dict[str, str] = {}
        self.tooltip_id: str | None = None
        self.tooltip_text: list[str] = []
        self.period: tuple[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if a.get("data-from") and a.get("data-to"):
            period = (a["data-from"][:10], a["data-to"][:10])
            if self.period and self.period != period:
                raise ValueError("Conflicting calendar periods in response")
            self.period = period
        if a.get("data-date") is not None:
            if tag != "td" or a.get("data-level") not in {"0", "1", "2", "3", "4"}:
                raise ValueError("Unexpected dated contribution-cell schema")
            if not a.get("id"):
                raise ValueError("Contribution cell has no tooltip identifier")
            self.cells.append({"date": a["data-date"], "level": int(a["data-level"]),
                               "id": a["id"]})
        if tag == "tool-tip":
            if self.tooltip_id is not None or not a.get("for"):
                raise ValueError("Unexpected contribution-tooltip schema")
            self.tooltip_id = a["for"]
            self.tooltip_text = []

    def handle_data(self, data: str) -> None:
        if self.tooltip_id is not None:
            self.tooltip_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tool-tip" and self.tooltip_id is not None:
            if self.tooltip_id in self.tooltips:
                raise ValueError("Duplicate tooltip identifier")
            self.tooltips[self.tooltip_id] = " ".join("".join(self.tooltip_text).split())
            self.tooltip_id = None


def parse_calendar(html: str, username: str, fetched_at: str) -> dict:
    parser = CalendarParser()
    parser.feed(html)
    parser.close()
    if parser.tooltip_id is not None or not parser.period:
        raise ValueError("Incomplete calendar markup or missing calendar period")
    days = []
    for cell in parser.cells:
        label = parser.tooltips.get(cell["id"], "")
        match = re.fullmatch(r"(No|\d+|[1-9]\d{0,2}(?:,\d{3})+) contributions? on (.+)\.", label)
        if not match:
            raise ValueError(f"Missing or unrecognized count for {cell['date']}")
        amount = 0 if match[1] == "No" else int(match[1].replace(",", ""))
        parsed_date = date.fromisoformat(cell["date"])
        expected_day = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", match[2])
        expected_day = re.sub(r",? \d{4}$", "", expected_day)
        if expected_day != f"{parsed_date.strftime('%B')} {parsed_date.day}":
            raise ValueError(f"Tooltip date does not match cell {cell['date']}")
        days.append({"date": cell["date"], "level": cell["level"], "count": amount})
    snapshot = {"schema_version": 1, "username": username,
                "source": f"https://github.com/users/{username}/contributions",
                "fetched_at": fetched_at, "period_start": parser.period[0],
                "period_end": parser.period[1], "days": sorted(days, key=lambda d: d["date"])}
    validate_snapshot(snapshot, username)
    return snapshot


def validate_snapshot(snapshot: dict, username: str) -> None:
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot schema")
    if snapshot.get("username") != username:
        raise ValueError("Snapshot username differs from requested username")
    if snapshot.get("source") != f"https://github.com/users/{username}/contributions":
        raise ValueError("Snapshot source does not match its public GitHub calendar")
    fetched = datetime.fromisoformat(snapshot["fetched_at"])
    if fetched.tzinfo is None:
        raise ValueError("Snapshot fetch timestamp must include a timezone")
    days = snapshot.get("days")
    if not isinstance(days, list) or not 350 <= len(days) <= 371:
        raise ValueError("Expected a complete annual calendar (350–371 dated cells)")
    previous = None
    for entry in days:
        if not isinstance(entry, dict) or set(entry) != {"date", "level", "count"}:
            raise ValueError("Unexpected daily record schema")
        current = date.fromisoformat(entry["date"])
        if previous is not None and current != previous + timedelta(days=1):
            raise ValueError("Calendar contains duplicate, missing, or unordered dates")
        if type(entry["level"]) is not int or not 0 <= entry["level"] <= 4:
            raise ValueError("Invalid contribution intensity")
        if type(entry["count"]) is not int or not 0 <= entry["count"] <= 1_000_000:
            raise ValueError("Invalid contribution count")
        if (entry["level"] == 0) != (entry["count"] == 0):
            raise ValueError("Contribution intensity and count disagree")
        previous = current
    if days[0]["date"] != snapshot["period_start"] or days[-1]["date"] != snapshot["period_end"]:
        raise ValueError("Calendar dates do not match its declared period")
    if date.fromisoformat(snapshot["period_start"]).weekday() != 6:
        raise ValueError("Expected GitHub's calendar to begin on a Sunday")
    if date.fromisoformat(snapshot["period_end"]) > fetched.date():
        raise ValueError("Calendar includes future contributions")


def fetch_calendar(username: str) -> str:
    request = Request(f"https://github.com/users/{username}/contributions",
                      headers={"User-Agent": "nerooc-profile-readme/1.0",
                               "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
    with urlopen(request, timeout=30) as response:
        if response.headers.get_content_type() != "text/html":
            raise ValueError("GitHub returned an unexpected content type")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ValueError("GitHub calendar response exceeded size limit")
    return payload.decode("utf-8")


def short_date(value: str) -> str:
    return date.fromisoformat(value).strftime("%d %b %Y")


def render_svg(snapshot: dict, theme: str, mobile: bool) -> str:
    p = PALETTES[theme]
    days = snapshot["days"]
    if mobile:
        end = date.fromisoformat(days[-1]["date"])
        last_sunday = end - timedelta(days=(end.weekday() + 1) % 7)
        first = last_sunday - timedelta(weeks=25)
        days = [d for d in days if date.fromisoformat(d["date"]) >= first]
    first_date, last_date = date.fromisoformat(days[0]["date"]), date.fromisoformat(days[-1]["date"])
    first_sunday = first_date - timedelta(days=(first_date.weekday() + 1) % 7)
    weeks = (last_date - first_sunday).days // 7 + 1
    width, height = (640, 364) if mobile else (960, 312)
    margin, grid_x, grid_y = (28, 74, 168) if mobile else (36, 76, 146)
    step, cell = (20, 15) if mobile else (16, 12)
    total = sum(d["count"] for d in days)
    active = sum(d["count"] > 0 for d in days)
    period = f"{short_date(days[0]['date'])} - {short_date(days[-1]['date'])}"
    refresh = datetime.fromisoformat(snapshot["fetched_at"]).astimezone(ZoneInfo("Europe/Warsaw")).strftime("%d %b %Y")
    title = f"{snapshot['username']}: {total:,} contributions from {period}"
    description = (f"{active} active days. GitHub public profile calendar, "
                   f"{'last 26 calendar weeks' if mobile else 'full fetched period'}. "
                   f"Refreshed {refresh} (Europe/Warsaw). Counts are contributions, not commits. "
                   "GitHub may include anonymized private contributions according to profile settings.")
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>',
           '<style>text{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}</style>',
           f'<rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="16" fill="{p["bg"]}" stroke="{p["line"]}"/>',
           f'<text x="{margin}" y="{41 if mobile else 35}" class="mono" font-size="{17 if mobile else 12}" letter-spacing="2" fill="{p["accent"]}">PUBLIC ACTIVITY</text>',
           f'<text x="{margin}" y="{87 if mobile else 82}" font-size="{36 if mobile else 38}" font-weight="650" fill="{p["text"]}">{total:,}<tspan font-size="{22 if mobile else 22}" font-weight="400"> contributions</tspan></text>',
           f'<text x="{margin}" y="{117 if mobile else 108}" font-size="{18 if mobile else 14}" fill="{p["muted"]}">{period}</text>']
    if mobile:
        out.append(f'<text x="{margin}" y="144" font-size="17" fill="{p["muted"]}">Last 26 calendar weeks · {active} active days</text>')
    else:
        out.append(f'<text x="{width-margin}" y="81" text-anchor="end" font-size="16" fill="{p["muted"]}">{active} active days</text>')
    for index, day_name in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        out.append(f'<text x="{margin}" y="{grid_y+index*step+cell-1}" font-size="{14 if mobile else 11}" fill="{p["muted"]}">{day_name}</text>')
    previous_label_week = -4
    for d in days:
        current = date.fromisoformat(d["date"])
        offset = (current-first_sunday).days
        column, row = divmod(offset, 7)
        x, y = grid_x+column*step, grid_y+row*step
        if current.day == 1 and column-previous_label_week >= 3 and column < weeks-1:
            out.append(f'<text x="{x}" y="{grid_y-8}" font-size="{14 if mobile else 11}" fill="{p["muted"]}">{current.strftime("%b")}</text>')
            previous_label_week = column
        out.append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2" fill="{p["levels"][d["level"]]}"><title>{d["date"]}: {d["count"]} contributions</title></rect>')
    footer_y = 345 if mobile else 285
    out.append(f'<text x="{margin}" y="{footer_y}" font-size="{16 if mobile else 12}" fill="{p["muted"]}">Refreshed {refresh}</text>')
    legend_x = width-margin-(143 if mobile else 126)
    out.append(f'<text x="{legend_x-38}" y="{footer_y}" font-size="{14 if mobile else 11}" fill="{p["muted"]}">Less</text>')
    for level in range(5):
        out.append(f'<rect x="{legend_x+level*17}" y="{footer_y-11}" width="12" height="12" rx="2" fill="{p["levels"][level]}"/>')
    out.append(f'<text x="{legend_x+89}" y="{footer_y}" font-size="{14 if mobile else 11}" fill="{p["muted"]}">More</text>')
    out.append('</svg>\n')
    svg = "\n".join(out)
    ElementTree.fromstring(svg)
    return svg


def write_outputs(output_dir: Path, outputs: dict[str, str]) -> None:
    """Stage all validated output first, then replace; roll back write failures."""
    output_dir.mkdir(parents=True, exist_ok=True)
    previous = {name: (output_dir/name).read_bytes() if (output_dir/name).exists() else None
                for name in outputs}
    with tempfile.TemporaryDirectory(prefix=".activity-", dir=output_dir) as temp:
        stage = Path(temp)
        for name, content in outputs.items():
            (stage/name).write_text(content, encoding="utf-8")
        replaced = []
        try:
            for name in outputs:
                os.replace(stage/name, output_dir/name)
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                if previous[name] is None:
                    (output_dir/name).unlink(missing_ok=True)
                else:
                    (stage/name).write_bytes(previous[name])
                    os.replace(stage/name, output_dir/name)
            raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="nerooc")
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--from-json", type=Path, help="Reproduce an existing snapshot offline")
    inputs.add_argument("--from-html", type=Path, help="Parse saved public GitHub calendar HTML")
    parser.add_argument("--output-dir", type=Path, default=ROOT/"assets/activity")
    args = parser.parse_args(argv)
    try:
        if not USERNAME.fullmatch(args.username):
            raise ValueError("Invalid GitHub username")
        if args.from_json:
            snapshot = json.loads(args.from_json.read_text(encoding="utf-8"))
            validate_snapshot(snapshot, args.username)
        else:
            html = args.from_html.read_text(encoding="utf-8") if args.from_html else fetch_calendar(args.username)
            snapshot = parse_calendar(html, args.username, datetime.now(timezone.utc).replace(microsecond=0).isoformat())
        outputs = {"snapshot.json": json.dumps(snapshot, indent=2, ensure_ascii=False)+"\n"}
        for theme in PALETTES:
            for mobile in (False, True):
                name = f"contributions-{'mobile-' if mobile else ''}{theme}.svg"
                outputs[name] = render_svg(snapshot, theme, mobile)
        write_outputs(args.output_dir, outputs)
        print(f"Updated {len(outputs)} files: {len(snapshot['days'])} verified days, "
              f"{sum(d['count'] for d in snapshot['days']):,} contributions, "
              f"{snapshot['period_start']} through {snapshot['period_end']}")
        return 0
    except (OSError, ValueError, KeyError, TypeError, ElementTree.ParseError) as exc:
        print(f"Activity refresh failed; previous output retained: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
