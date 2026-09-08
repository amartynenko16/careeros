"""Interactive vetting for Bank records.

Purpose: walk records that are missing Honesty Tag or a Defensible flag and let
the user set them locally. Local vetting decisions are stored in the
`local_honesty_tag` and `local_defensible` columns; the Notion mirror
(`honesty_tag`, `defensible_in_interview`) is never touched by `vet`. Local
values take precedence when scoring, but the original Notion values are kept
for reference.

Writes are local-only in Phase 1. Two-way sync back to Notion is out of scope
for this phase.
"""

import json
from datetime import datetime, timezone

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from careeros import db


console = Console()

HONESTY_CHOICES = {
    "s": "Strong",
    "w": "Working",
    "g": "Gap",
    "n": "Never Claim",
}


def _pending_records(include_all: bool) -> list[dict]:
    """Return records needing vetting. If include_all, return every record."""
    with db.connect() as conn:
        if include_all:
            rows = conn.execute(
                "SELECT * FROM bank_records ORDER BY grain, name"
            ).fetchall()
        else:
            # Untagged in Notion AND not yet vetted locally.
            rows = conn.execute(
                """
                SELECT * FROM bank_records
                WHERE (honesty_tag IS NULL OR honesty_tag = '')
                  AND local_vetted_at IS NULL
                ORDER BY grain, name
                """
            ).fetchall()
    return [dict(r) for r in rows]


def _format_list_field(json_str: str | None) -> str:
    if not json_str:
        return ""
    try:
        items = json.loads(json_str)
        if isinstance(items, list):
            return ", ".join(str(x) for x in items)
    except (json.JSONDecodeError, TypeError):
        pass
    return json_str


def _show_record(rec: dict, index: int, total: int) -> None:
    """Print a Bank record for review."""
    header = f"Record {index} of {total}"
    lines = [
        f"[bold]{rec.get('name') or '(no name)'}[/bold]",
        f"Grain:      {rec.get('grain') or '-'}",
        f"Employers:  {_format_list_field(rec.get('employers_json'))}",
        f"Timeframe:  {rec.get('timeframe') or '-'}",
    ]
    if rec.get("tech_tools_json"):
        lines.append(f"Tech:       {_format_list_field(rec.get('tech_tools_json'))}")

    lines.append("")
    lines.append("[bold]Bullet (Standard)[/bold]")
    lines.append(rec.get("bullet_standard") or "(empty)")

    if rec.get("notes"):
        lines.append("")
        lines.append("[bold]Notes[/bold]")
        lines.append(rec["notes"])

    lines.append("")
    lines.append(f"Current Notion Honesty Tag:  {rec.get('honesty_tag') or '(none)'}")
    lines.append(
        f"Current Notion Defensible:   "
        f"{'yes' if rec.get('defensible_in_interview') == 1 else 'no / unset'}"
    )
    if rec.get("local_honesty_tag") or rec.get("local_defensible") is not None:
        lines.append(
            f"Prior local vetting:         "
            f"tag={rec.get('local_honesty_tag') or '(none)'}, "
            f"defensible={'yes' if rec.get('local_defensible') == 1 else 'no'}"
        )

    console.print(Panel("\n".join(lines), title=header, border_style="cyan"))


def _prompt_honesty() -> str | None:
    """Ask for Honesty Tag. Returns tag string or None if skipped."""
    answer = Prompt.ask(
        "Honesty Tag: [s]trong / [w]orking / [g]ap / [n]ever claim / s[k]ip",
        choices=["s", "w", "g", "n", "k"],
        default="k",
        show_choices=False,
    )
    if answer == "k":
        return None
    return HONESTY_CHOICES[answer]


def _prompt_defensible() -> int | None:
    """Ask for Defensible flag. Returns 1 / 0 / None if skipped."""
    answer = Prompt.ask(
        "Defensible in interview: [y]es / [n]o / s[k]ip",
        choices=["y", "n", "k"],
        default="k",
        show_choices=False,
    )
    if answer == "k":
        return None
    return 1 if answer == "y" else 0


def _save_vet(page_id: str, tag: str | None, defensible: int | None) -> None:
    """Persist local vetting decision. Writes only fields that were set."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    sets: list[str] = ["local_vetted_at = ?"]
    args: list[object] = [now]
    if tag is not None:
        sets.append("local_honesty_tag = ?")
        args.append(tag)
    if defensible is not None:
        sets.append("local_defensible = ?")
        args.append(defensible)
    args.append(page_id)

    with db.connect() as conn:
        conn.execute(
            f"UPDATE bank_records SET {', '.join(sets)} WHERE notion_page_id = ?",
            args,
        )
        conn.commit()


def run(include_all: bool = False) -> None:
    """Interactive vetting loop.

    Args:
        include_all: If False (default), only records missing Honesty Tag and not
            yet vetted locally are shown. If True, every record is shown.
    """
    records = _pending_records(include_all=include_all)
    total = len(records)

    if total == 0:
        with db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM bank_records").fetchone()
        bank_count = row["n"]
        if bank_count == 0:
            console.print(
                "[yellow]No Bank records in local DB yet.[/yellow] "
                "Run [bold]careeros sync[/bold] after Phase 1e is wired to pull the "
                "Experience Bank from Notion, then run [bold]careeros vet[/bold] again."
            )
        else:
            console.print(
                "[green]Nothing to vet.[/green] Every Bank record already has an "
                "Honesty Tag or has been vetted locally. Use [bold]careeros vet --all[/bold] "
                "to walk every record anyway."
            )
        return

    console.print(
        f"[bold]Vetting {total} record(s).[/bold] Press Ctrl+C to stop; progress is saved after each record.\n"
    )

    try:
        for i, rec in enumerate(records, start=1):
            _show_record(rec, i, total)
            tag = _prompt_honesty()
            defensible = _prompt_defensible()

            if tag is None and defensible is None:
                console.print("[dim]Skipped, nothing saved for this record.[/dim]\n")
                continue

            _save_vet(rec["notion_page_id"], tag, defensible)
            console.print("[green]Saved locally.[/green]\n")

            if i < total:
                cont = Prompt.ask(
                    "[c]ontinue / [q]uit",
                    choices=["c", "q"],
                    default="c",
                    show_choices=False,
                )
                if cont == "q":
                    break
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Prior saves are kept.[/yellow]")
        return

    console.print(f"\n[bold green]Vetting session complete.[/bold green]")
