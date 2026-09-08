"""CareerOS CLI entry point.

Command layout:
    careeros init                   Initialize DB, load facts and companies.
    careeros status                 DB row counts.
    careeros facts show             Print immutable facts.
    careeros facts reload           Re-read data/facts.yaml.
    careeros companies show         Print target companies.
    careeros companies reload       Re-read data/companies.yaml.
    careeros sync                   Pull Experience Bank from Notion.
    careeros vet [--all]            Walk Bank records needing Honesty / Defensible.
    careeros scan [--only SLUG]     Fetch open jobs from target companies.
    careeros jobs list [filters]    List fetched jobs.
    careeros jobs view <id>         Show job details.
    careeros jobs save <id>         Mark job saved.
    careeros jobs reject <id>       Mark job rejected.
    careeros jobs mark-applied <id> Mark job applied.
    careeros jobs add <url>         Add a role you found yourself (Ashby/Greenhouse/Lever URL).
    careeros jobs stage <id> <stage>  Set post-application pipeline stage.
    careeros notion-sync-applications  Push saved/applied jobs to Notion 📋 Applications DB.
    careeros coach analyze <id>     Match Bank records against a job's description.
    careeros coach compare          Rank jobs (default: saved) by Bank match strength.
    careeros tailor <id>            Build a Model B2 tailoring package for a job.
    careeros tailor-budget <path>   Total approved bullets against the one-page char budget.
    careeros resume-build <id>      Write tailored content (--content <json>) into a template DOCX.
    careeros cover-letter-build <id> Write tailored content (--content <json>) into a cover letter DOCX.
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from careeros import coach, companies, cover_letter, db, facts, intake, jobs, notion_apps, notion_sync, resume, scan, tailor, vet
from careeros.config import DB_PATH, FACTS_YAML_PATH


app = typer.Typer(
    help="CareerOS: personal career operating system.",
    no_args_is_help=True,
    add_completion=False,
)
facts_app = typer.Typer(help="Immutable facts (education, employers, certifications).")
companies_app = typer.Typer(help="Target companies.")
jobs_app = typer.Typer(help="Fetched jobs: list, view, triage.")
coach_app = typer.Typer(help="Deterministic gap analysis: match Bank records against a job.")

app.add_typer(facts_app, name="facts")
app.add_typer(companies_app, name="companies")
app.add_typer(jobs_app, name="jobs")
app.add_typer(coach_app, name="coach")

console = Console()


def _require_db() -> None:
    if not DB_PATH.exists():
        console.print(
            "[yellow]DB not initialized.[/yellow] Run [bold]careeros init[/bold] first."
        )
        raise typer.Exit(code=1)


def _notify_macos_scan_complete(summary: "scan.ScanSummary") -> None:
    """Fire a native macOS notification banner via osascript, every run.

    Always fires (not just when new jobs are found) -- this is the only
    signal that the scheduled scan ran at all, since it's a plain
    Python/bash process with no Claude Code involved to otherwise tell
    you anything happened. Message content still varies: a specific
    "N new role(s) at X, Y" when there's something to look at, a plain
    "0 new roles" heartbeat otherwise.

    Prints osascript's own stdout/stderr and exit code (visible in
    logs/scan.log for the scheduled run) rather than swallowing failures
    silently -- a notification that silently doesn't show is exactly the
    failure mode this exists to catch, so it needs to be diagnosable from
    the log alone rather than needing this run repeated interactively.
    """
    import json
    import subprocess

    if summary.total_new > 0:
        companies_with_new = [r.name for r in summary.per_company if r.inserted > 0]
        shown = companies_with_new[:5]
        message = f"{summary.total_new} new role(s) at " + ", ".join(shown)
        if len(companies_with_new) > 5:
            message += f" +{len(companies_with_new) - 5} more"
        subtitle = "New roles found"
    else:
        message = f"Scan complete, matched {summary.total_matched}, 0 new."
        subtitle = "No new roles"

    script = (
        f"display notification {json.dumps(message)} "
        f'with title {json.dumps("CareerOS")} '
        f'subtitle {json.dumps(subtitle)}'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script], check=False, timeout=10,
            capture_output=True, text=True,
        )
        print(f"[notify] osascript exit={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}")
    except Exception as exc:
        print(f"[notify] failed to invoke osascript: {exc!r}")


# --- init / status -----------------------------------------------------------

@app.command()
def init() -> None:
    """Initialize the local database and load immutable facts + companies."""
    console.print("Creating SQLite schema...")
    db.init_db()
    console.print(f"[green]Schema ready.[/green] DB at [cyan]{DB_PATH}[/cyan]")

    console.print("Loading immutable facts...")
    n = facts.load_from_yaml()
    console.print(f"[green]Loaded {n} facts.[/green]")

    console.print("Loading target companies...")
    n = companies.load_from_yaml()
    console.print(f"[green]Loaded {n} companies.[/green]")

    console.print(
        "\nNext: [bold]careeros sync[/bold] to pull the Bank, "
        "[bold]careeros scan[/bold] to fetch jobs."
    )


@app.command()
def status() -> None:
    """Print DB path and row counts per table."""
    _require_db()
    counts = db.table_counts()
    table = Table(title="CareerOS status")
    table.add_column("Table", style="cyan")
    table.add_column("Rows", justify="right")
    for name, n in counts.items():
        table.add_row(name, str(n))
    console.print(f"DB: [cyan]{DB_PATH}[/cyan]")
    console.print(table)


# --- facts -------------------------------------------------------------------

@facts_app.command("show")
def facts_show() -> None:
    """Print immutable facts grouped by section."""
    sections = facts.sections()
    if not sections:
        console.print(
            "[yellow]No facts loaded.[/yellow] Run [bold]careeros init[/bold] first."
        )
        raise typer.Exit(code=1)
    for section_name, entries in sections.items():
        table = Table(title=section_name, show_header=True, header_style="bold")
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        for k, v in entries.items():
            table.add_row(k, str(v) if v is not None else "")
        console.print(table)


@facts_app.command("reload")
def facts_reload() -> None:
    """Re-read data/facts.yaml into the DB."""
    n = facts.load_from_yaml()
    console.print(f"[green]Reloaded {n} facts[/green] from [cyan]{FACTS_YAML_PATH}[/cyan]")


# --- companies ---------------------------------------------------------------

@companies_app.command("show")
def companies_show() -> None:
    """Print target companies grouped by tier."""
    _require_db()
    s = companies.summary()
    console.print(
        f"[bold]{s['total']} companies[/bold]  "
        f"tiers: {dict(sorted(s['by_tier'].items()))}  "
        f"ATS: {s['by_ats']}"
    )

    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM companies ORDER BY tier, name"
        ).fetchall()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Tier", justify="center")
    table.add_column("Name", style="cyan")
    table.add_column("ATS")
    table.add_column("Slug")
    table.add_column("Notes", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["tier"]),
            row["name"],
            row["ats_type"] or "",
            row["ats_slug"] or "",
            row["notes"] or "",
        )
    console.print(table)


@companies_app.command("reload")
def companies_reload() -> None:
    """Re-read data/companies.yaml into the DB."""
    _require_db()
    n = companies.load_from_yaml()
    console.print(f"[green]Reloaded {n} companies.[/green]")


# --- sync --------------------------------------------------------------------

@app.command("sync")
def sync_cmd(
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Fetch and report counts without writing to the local DB.",
    ),
) -> None:
    """Pull the Experience Bank from Notion into local SQLite."""
    _require_db()
    try:
        summary = notion_sync.sync(dry_run=dry_run)
    except notion_sync.NotionConfigError as exc:
        console.print(f"[red]Notion config error:[/red] {exc}")
        raise typer.Exit(code=1)

    verb = "Would sync" if dry_run else "Synced"
    console.print(
        f"[green]{verb} {summary.fetched} record(s)[/green]  "
        f"(new: {summary.inserted}, updated: {summary.updated}, unchanged: {summary.unchanged})"
    )
    if summary.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for w in summary.warnings:
            console.print(f"  - {w}")


@app.command("notion-sync-applications")
def notion_sync_applications_cmd(
    status: list[str] = typer.Option(
        ["saved", "applied"],
        "--status",
        help="Job statuses to push (repeatable). Default: saved and applied.",
    ),
    update_existing: bool = typer.Option(
        False,
        "--update-existing",
        help="Also refresh bookkeeping fields (URL/Location/Date Applied/Last Synced) on rows "
        "that already exist in Notion. Default: existing rows are left completely untouched, "
        "only new rows get created -- your own edits in Notion are the only thing that "
        "changes an existing row. Only pass this when he explicitly asks for a refresh.",
    ),
) -> None:
    """Push saved/applied jobs to the Notion 📋 Applications database (one-way, local is source of truth).

    By default only adds new rows; existing rows are never overwritten unless --update-existing is passed.
    """
    _require_db()
    try:
        summary = notion_apps.push(status_filter=tuple(status), update_existing=update_existing)
    except notion_apps.NotionAppsConfigError as exc:
        console.print(f"[red]Notion config error:[/red] {exc}")
        raise typer.Exit(code=1)

    console.print(
        f"[green]Pushed {summary.considered} job(s)[/green]  "
        f"(created: {summary.created}, updated: {summary.updated}, unchanged/existing: {summary.unchanged})"
    )
    if summary.skipped:
        console.print(f"[yellow]{len(summary.skipped)} skipped:[/yellow]")
        for s in summary.skipped:
            console.print(f"  - {s}")


# --- vet ---------------------------------------------------------------------

@app.command("vet")
def vet_cmd(
    all_records: bool = typer.Option(
        False,
        "--all",
        help="Show every Bank record, not just those missing Honesty Tag.",
    ),
) -> None:
    """Walk Bank records that need Honesty Tag or Defensible flag."""
    _require_db()
    vet.run(include_all=all_records)


# --- scan --------------------------------------------------------------------

@app.command("scan")
def scan_cmd(
    only: str | None = typer.Option(
        None,
        "--only",
        help="Slug of a single company to scan (e.g. --only gitlab).",
    ),
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Fire a macOS notification banner every run, so you know the scan ran even when nothing's new.",
    ),
) -> None:
    """Fetch open jobs from target companies (Greenhouse, Lever, Ashby)."""
    _require_db()
    summary = scan.scan(only_slug=only)

    table = Table(title="Scan results", show_header=True, header_style="bold")
    table.add_column("Company", style="cyan")
    table.add_column("Fetched", justify="right")
    table.add_column("Matched", justify="right")
    table.add_column("Geo-out", justify="right")
    table.add_column("New", justify="right")
    table.add_column("Updated", justify="right")
    table.add_column("Error", overflow="fold")
    for r in summary.per_company:
        table.add_row(
            r.name,
            str(r.fetched),
            str(r.matched),
            str(r.geo_rejected),
            str(r.inserted),
            str(r.updated),
            r.error,
        )
    console.print(table)
    console.print(
        f"\n[bold]Totals[/bold]: matched {summary.total_matched}, "
        f"geo-rejected {summary.total_geo_rejected}, new {summary.total_new}."
    )

    if summary.skipped:
        console.print(
            f"\n[yellow]Skipped {len(summary.skipped)} unsupported ATS(s):[/yellow] "
            + ", ".join(summary.skipped)
        )
        console.print(
            "[dim]Add them manually via their careers pages or update ats_type in data/companies.yaml.[/dim]"
        )

    if notify:
        _notify_macos_scan_complete(summary)


# --- jobs --------------------------------------------------------------------

@jobs_app.command("list")
def jobs_list(
    status: str = typer.Option("new", "--status", help="Filter by status (new/saved/rejected/applied)."),
    company: str | None = typer.Option(None, "--company", help="Filter by company slug."),
    limit: int = typer.Option(50, "--limit", help="Max rows to show."),
) -> None:
    """List fetched jobs, most recently fetched first."""
    _require_db()
    try:
        rows = jobs.list_jobs(status=status, company_slug=company, limit=limit)
    except jobs.InvalidStatusError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not rows:
        console.print("[dim]No jobs match those filters.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", overflow="fold")
    table.add_column("Company", style="cyan")
    table.add_column("Title")
    table.add_column("Location")
    table.add_column("Remote", justify="center")
    table.add_column("Status", justify="center")
    for r in rows:
        table.add_row(
            r["id"],
            r["company_slug"],
            r["title"],
            r["location"] or "",
            r["remote_type"] or "",
            r["status"] or "new",
        )
    console.print(table)
    console.print(f"\n[dim]{len(rows)} row(s). Use `careeros jobs view <id>` for details.[/dim]")


@jobs_app.command("view")
def jobs_view(job_id: str) -> None:
    """Show full details for one job."""
    _require_db()
    try:
        job = jobs.get(job_id)
    except jobs.JobNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    header = f"[bold]{job['title']}[/bold]  ({job['company_slug']})"
    body = [
        f"Location:    {job['location'] or '-'}",
        f"Remote:      {job['remote_type'] or '-'}",
        f"Status:      {job['status'] or 'new'}",
        f"URL:         {job['ats_url'] or '-'}",
        f"Fetched:     {job['fetched_at'] or '-'}",
        "",
        job["description"] or "(no description)",
    ]
    console.print(Panel("\n".join(body), title=header, border_style="cyan"))


@jobs_app.command("save")
def jobs_save(job_id: str) -> None:
    """Mark a job saved."""
    _require_db()
    try:
        jobs.set_status(job_id, "saved")
    except (jobs.JobNotFoundError, jobs.InvalidStatusError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Saved[/green] {job_id}")


@jobs_app.command("reject")
def jobs_reject(job_id: str) -> None:
    """Mark a job rejected."""
    _require_db()
    try:
        jobs.set_status(job_id, "rejected")
    except (jobs.JobNotFoundError, jobs.InvalidStatusError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]Rejected[/yellow] {job_id}")


@jobs_app.command("mark-applied")
def jobs_mark_applied(job_id: str) -> None:
    """Mark a job applied."""
    _require_db()
    try:
        jobs.set_status(job_id, "applied")
    except (jobs.JobNotFoundError, jobs.InvalidStatusError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Marked applied[/green] {job_id}")


@jobs_app.command("stage")
def jobs_stage(job_id: str, stage: str) -> None:
    """Set a job's post-application pipeline stage (1. Phone Screen,
    2. First Round Interview, 3. Second Round Interview, 4. Final Round,
    5. Offer, Rejected by Company, Withdrawn)."""
    _require_db()
    try:
        jobs.set_application_stage(job_id, stage)
    except (jobs.JobNotFoundError, jobs.InvalidStageError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Stage set[/green] {job_id} -> {stage}")


@jobs_app.command("set-remote-type")
def jobs_set_remote_type(job_id: str, remote_type: str) -> None:
    """Correct a job's remote type (remote, hybrid, onsite, unknown).

    Fixes ATS data that got it wrong, especially on manually-added roles.
    Persists locally, so it survives the next notion-sync-applications push
    -- editing Remote Type directly in Notion does not, since that push
    overwrites every property from local SQLite on every run."""
    _require_db()
    try:
        jobs.set_remote_type(job_id, remote_type)
    except (jobs.JobNotFoundError, jobs.InvalidRemoteTypeError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Remote type set[/green] {job_id} -> {remote_type}")


@jobs_app.command("clear")
def jobs_clear(
    confirm: bool = typer.Option(
        False,
        "--yes",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Delete all jobs with status='new'. Preserves saved / rejected / applied."""
    _require_db()
    if not confirm:
        answer = typer.prompt(
            "Delete all new-status jobs? Saved/rejected/applied are preserved. (y/N)",
            default="N",
            show_default=False,
        )
        if answer.lower() not in ("y", "yes"):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(code=0)
    n = jobs.clear_new()
    console.print(f"[green]Cleared {n} new-status job(s).[/green]")


@jobs_app.command("add")
def jobs_add(
    url: str | None = typer.Argument(None, help="Job posting URL (Ashby/Greenhouse/Lever)."),
    company: str | None = typer.Option(None, "--company", help="Company slug, for --description (no URL)."),
    title: str | None = typer.Option(None, "--title", help="Job title, for --description (no URL)."),
    description_file: str | None = typer.Option(None, "--description-file", help="Path to a text file with the JD, for roles with no supported-ATS URL."),
    location: str = typer.Option("", "--location", help="Location text, for --description-file."),
    remote_type: str = typer.Option("unknown", "--remote-type", help="remote / hybrid / onsite / unknown."),
) -> None:
    """Add a role you found yourself, outside a scan. Either a URL from a
    supported ATS, or --company/--title/--description-file for anything else.
    Runs the same role/geo filters as a scan and stores the result either way."""
    _require_db()
    try:
        if url:
            result = intake.add_from_url(url)
        elif company and title and description_file:
            description = Path(description_file).read_text(encoding="utf-8")
            result = intake.add_manual(
                company_slug=company, title=title, description=description,
                location=location, remote_type=remote_type,
            )
        else:
            console.print(
                "[red]Provide either a URL, or --company, --title, and --description-file together.[/red]"
            )
            raise typer.Exit(code=1)
    except intake.IntakeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]Added[/green] {result['job_id']} | {result['title']} ({result['company_slug']})")
    console.print(f"  Location: {result['location'] or '-'}    Remote: {result['remote_type']}")
    role_style = "green" if result["role_filter_pass"] else "red"
    geo_style = "green" if result["geo_eligible"] else "red"
    console.print(f"  Role filter: [{role_style}]{'PASS' if result['role_filter_pass'] else 'FAIL'}[/{role_style}]"
                  f"    Geo eligible: [{geo_style}]{'YES' if result['geo_eligible'] else 'NO'}[/{geo_style}]")
    console.print("[dim]Stored as status 'new'. `careeros jobs view` for the full description.[/dim]")


# --- coach ---------------------------------------------------------------

_HONESTY_STYLE = {
    "Strong": "green",
    "Working": "yellow",
    "Gap": "red",
    "Untagged": "dim",
}


@coach_app.command("analyze")
def coach_analyze(job_id: str) -> None:
    """Match a job's description against Bank tech/tool vocabulary, grouped by Honesty Tag."""
    _require_db()
    try:
        report = coach.analyze(job_id)
    except jobs.JobNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    job = report["job"]
    console.print(
        Panel(
            f"Location: {job['location'] or '-'}    Remote: {job['remote_type'] or '-'}",
            title=f"[bold]{job['title']}[/bold]  ({job['company_slug']})",
            border_style="cyan",
        )
    )

    matches = report["matches"]
    if not matches:
        console.print(
            "\n[dim]No Bank tech/tool overlap found in this job's description.[/dim]"
        )
    for tag in coach.HONESTY_DISPLAY_ORDER:
        recs = matches.get(tag)
        if not recs:
            continue
        style = _HONESTY_STYLE.get(tag, "white")
        console.print(f"\n[bold {style}]Covered ({tag})[/bold {style}]")
        for m in recs:
            rec = m["record"]
            terms = ", ".join(m["matched_terms"])
            console.print(f"  - {rec['name']}  [dim][{terms}][/dim]")

    if report["unmatched_bank_tools"]:
        console.print("\n[bold]Bank tools not mentioned in this JD[/bold]")
        console.print("  " + ", ".join(report["unmatched_bank_tools"]))


@coach_app.command("compare")
def coach_compare(
    status: str = typer.Option("saved", "--status", help="Job status to compare (default: saved)."),
) -> None:
    """Rank jobs by Bank match strength (most Strong-tagged matches first)."""
    _require_db()
    try:
        results = coach.compare(status=status)
    except jobs.InvalidStatusError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)

    if not results:
        console.print(f"[dim]No jobs with status '{status}'.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", overflow="fold")
    table.add_column("Company", style="cyan")
    table.add_column("Title")
    table.add_column("Strong", justify="right")
    table.add_column("Working", justify="right")
    table.add_column("Gap", justify="right")
    table.add_column("Untagged", justify="right")
    table.add_column("Total", justify="right")
    for r in results:
        job = r["job"]
        c = r["counts"]
        table.add_row(
            job["id"],
            job["company_slug"],
            job["title"],
            str(c.get("Strong", 0)),
            str(c.get("Working", 0)),
            str(c.get("Gap", 0)),
            str(c.get("Untagged", 0)),
            str(r["total_matched_records"]),
        )
    console.print(table)
    console.print(
        f"\n[dim]{len(results)} job(s). Use `careeros coach analyze <id>` for detail.[/dim]"
    )


# --- tailor ----------------------------------------------------------------

@app.command("tailor")
def tailor_cmd(job_id: str) -> None:
    """Build a Model B2 tailoring package (JD + Bank records + no-fabrication prompt)."""
    _require_db()
    try:
        path = tailor.write_package(job_id)
    except jobs.JobNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Wrote tailoring package[/green] to [cyan]{path}[/cyan]")
    console.print(
        "[dim]Paste this file into a fresh Claude Code turn (or read it here) "
        "to get tailored, non-fabricated bullets back.[/dim]"
    )


@app.command("tailor-budget")
def tailor_budget_cmd(path: str) -> None:
    """Deterministically total the character count of an approved-bullets file
    against the one-page budget (one bullet per line; '# ...' and blank lines
    ignored)."""
    result = tailor.check_budget(path)

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Bullet", overflow="fold")
    for i, b in enumerate(result["bullets"], start=1):
        table.add_row(str(i), str(len(b)), b)
    console.print(table)

    style = "red" if result["over_budget"] else "green"
    console.print(
        f"\n[bold {style}]{result['total_chars']} / {result['budget']} characters[/bold {style}]"
        f"  ({result['count']} bullets, {result['remaining']} remaining)"
    )
    if result["over_budget"]:
        console.print(
            f"[red]Over budget by {-result['remaining']} characters.[/red]"
        )


# --- resume --------------------------------------------------------------

@app.command("resume-build")
def resume_build_cmd(
    job_id: str,
    content: str = typer.Option(..., "--content", help="Path to a content JSON file (see careeros/resume.py docstring for shape)."),
    out: str | None = typer.Option(None, "--out", help="Output path. Defaults to applications/<slug>/resume_draft.docx."),
) -> None:
    """Build a tailored resume DOCX by writing already-drafted content into a copy of an existing template."""
    _require_db()
    try:
        path = resume.build(job_id, content, out_path=out)
    except (jobs.JobNotFoundError, resume.ResumeBuildError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Built resume[/green] at [cyan]{path}[/cyan]")


# --- cover letter ----------------------------------------------------------

@app.command("cover-letter-build")
def cover_letter_build_cmd(
    job_id: str,
    content: str = typer.Option(..., "--content", help="Path to a content JSON file (see careeros/cover_letter.py docstring for shape)."),
    out: str | None = typer.Option(None, "--out", help="Output path. Defaults to applications/<slug>/cover_letter_draft.docx."),
) -> None:
    """Build a tailored cover letter DOCX by writing already-drafted content into a copy of an existing template."""
    _require_db()
    try:
        path = cover_letter.build(job_id, content, out_path=out)
    except (jobs.JobNotFoundError, resume.ResumeBuildError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Built cover letter[/green] at [cyan]{path}[/cyan]")


if __name__ == "__main__":
    app()
