# CareerOS

A personal career operating system, built to run locally and be directed conversationally
via Claude Code (or another coding-agent LLM). Two pieces:

- **Job radar**: scans target companies' public ATS boards (Greenhouse, Lever, Ashby),
  filters by your own role/geo criteria, and tracks triage state (saved / rejected / applied)
  locally.
- **Application engine**: keeps a curated "Experience Bank" of your real career history in
  Notion, generates a no-fabrication tailoring package against a specific job description,
  and writes the resulting content into your resume/cover-letter `.docx` templates.

Everything that runs automatically is deterministic (rules and keyword filters, no LLM at
runtime). The LLM's job is the conversational layer on top: helping you tailor bullets, run
a gap analysis, and decide what to do next. See `CLAUDE.md.example` for the full design
rationale and the guardrails this system is built around (most importantly: never fabricate
resume content, never write to your Notion workspace without asking first).

This repo is the shareable engine. Your personal data (facts, target companies, resume
templates, Notion credentials) never ships in it -- see Setup below.

## Prerequisites

- **Python 3.11+**
- **A Notion account**, and willingness to set up one internal integration (free, a few
  minutes)
- **Claude Code** (or another LLM you can paste a prompt into) -- this project intentionally
  does not call an LLM API itself; the tailoring/coaching steps are meant to happen in
  conversation with an assistant that has read `CLAUDE.md` and your data files
- **Your own resume and cover letter in `.docx` format** -- see
  [`TEMPLATE_CONTRACT.md`](TEMPLATE_CONTRACT.md) for what structure they need to have for
  the automated builder to work with them. No starter template is bundled; resume design is
  personal.
- macOS is assumed for the optional scheduled-scan setup (`launchd`); everything else is
  cross-platform.

## Setup

**1. Clone and install:**

```bash
git clone <this-repo-url> CareerOS
cd CareerOS
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**2. Set up your personal config files** (all gitignored, so nothing personal gets committed):

```bash
cp CLAUDE.md.example CLAUDE.md
cp data/facts.example.yaml data/facts.yaml
cp data/companies.example.yaml data/companies.yaml
cp .env.example .env
```

Fill in:
- `CLAUDE.md` -- your bio, job search criteria, preferences. Read through it once; it also
  documents the whole system's design, which is worth understanding before you start.
- `data/facts.yaml` -- your real name, contact info, education, employer timeline,
  certifications.
- `data/companies.yaml` -- your actual target company list. Start small (5-10 companies)
  and expand once the pipeline is working.

**3. Set up Notion** (this is the fiddly part -- take it slowly):

1. Create an internal integration at
   [notion.so/my-integrations](https://www.notion.so/my-integrations). Copy its token into
   `.env` as `NOTION_TOKEN`.
2. Create a database in your own Notion workspace called your Experience Bank, with these
   properties (types matter -- match them exactly):

   | Property | Type |
   |---|---|
   | Name | Title |
   | Grain | Select (e.g. Role, Initiative, Skill, Award, Certification) |
   | Employer | Multi-select |
   | Timeframe | Text |
   | Bullet - Standard | Text |
   | Metrics | Text |
   | Notes | Text |
   | Tech and Tools | Multi-select |
   | Honesty Tag | Select (e.g. Strong, Working, Untagged, Never Claim) |
   | Defensible | Checkbox |

   `Honesty Tag` is worth taking seriously: whatever value you use for "don't ever surface
   this" (the example above uses `Never Claim`) needs to match what you tell your assistant
   to filter on in `CLAUDE.md` and in any Coach/tailoring logic you rely on.
3. (Optional, for application tracking) Create a second database, Applications, with:
   Name (Title), Company (Text), CareerOS Job ID (Text), Stage (Select), Job URL (URL),
   Location (Text), Remote Type (Select), Date Applied (Date), Last Synced (Date).
4. **Share both databases with your integration individually** -- each database's `...` menu
   → Connections → add your integration. A database does *not* inherit access from a
   connected parent page automatically; this is the single most common setup failure.
5. Get each database's data source ID (the 32-character string in its URL) and put them in
   `.env` as `NOTION_EXPERIENCE_BANK_DATA_SOURCE_ID` and
   `NOTION_APPLICATIONS_DATA_SOURCE_ID`.

**4. Initialize and verify:**

```bash
careeros init
careeros facts show          # sanity-check your facts loaded
careeros companies show      # sanity-check your company list loaded
careeros sync                # pull your Experience Bank from Notion
careeros status               # row counts across all tables
```

**5. Populate your Experience Bank in Notion.** This is the part that takes real time and
isn't automatable: go through your actual career history and write one solid "Standard"
bullet per accomplishment/skill/role, tagged honestly. The system is only as good as what's
in here -- garbage in, fabricated-sounding resumes out.

## Every session

```bash
cd CareerOS
source .venv/bin/activate
```

Then open the project in Claude Code (or your assistant of choice) and let it read
`CLAUDE.md` for context.

## Typical workflow

```bash
careeros sync                            # pull latest Bank state from Notion
careeros scan                            # fetch jobs across all supported companies
careeros jobs list                       # review new matches
careeros jobs view <job_id>              # dig into a specific one
careeros jobs save <job_id>              # or reject / mark-applied
careeros jobs list --status saved        # your shortlist
careeros coach analyze <job_id>          # see which Bank tech/tools match this JD
careeros tailor <job_id>                 # generate a tailoring package
# -- paste the tailoring package into your assistant, get back tailored bullets --
careeros resume-build <job_id> --content <content.json>
careeros cover-letter-build <job_id> --content <content.json>
```

`careeros jobs add <url>` also exists for a role you found yourself, outside a scan
(supports Ashby/Greenhouse/Lever URLs directly, or `--company/--title/--description-file`
for anything else).

## Command reference

```
careeros status                    # DB row counts
careeros sync                      # pull Bank from Notion
careeros facts show / reload       # immutable personal facts
careeros companies show / reload   # target company list
careeros scan [--only SLUG]        # fetch open jobs from supported ATSs
careeros jobs list [filters]       # list fetched jobs
careeros jobs view <id>            # full job details
careeros jobs save/reject/mark-applied <id>
careeros jobs stage <id> <stage>   # post-application pipeline stage (define your own)
careeros jobs set-remote-type <id> <type>
careeros jobs add <url>            # manual intake for a role you found yourself
careeros jobs clear                # delete all new-status jobs (preserves triaged)
careeros coach analyze <id>        # gap analysis vs a job's description
careeros coach compare             # rank saved jobs by Bank match strength
careeros tailor <id>               # build a Model B2 tailoring package
careeros tailor-budget <file>      # character-count check
careeros resume-build <id> --content <json>
careeros cover-letter-build <id> --content <json>
careeros vet [--all]               # local fallback for Bank vetting (usually done in Notion)
```

## Unsupported ATSs

Companies on Workday or another system this project doesn't fetch are marked
`ats_type: unsupported` in `data/companies.yaml` and skipped by `scan`, with their careers
URL surfaced for manual reference. Building a fetcher for an authenticated/custom ATS is a
real rabbit hole -- think twice before starting.

## Notes

- One-way Notion sync: Notion is the source of truth for your Experience Bank. Local SQLite
  mirrors it, never the reverse (except the separate one-way Applications *push*, local →
  Notion, which is deliberately one-directional the other way).
- Local enrichment columns and triage status are preserved across re-syncs/re-scans.
- Role and geo filtering happen at fetch time in `careeros/fetchers/base.py`. Edit
  `ROLE_KEYWORDS` / `NEGATIVE_KEYWORDS` and `geo_eligible()` there to match your own
  criteria.
- Tests run without network access (`httpx.MockTransport`); only `careeros scan` makes real
  ATS calls.

## Optional: scheduled daily scanning (macOS)

See `scripts/run_scan.sh` for the scan wrapper. Point a `launchd` LaunchAgent at it to run
daily. This only runs while your Mac is on, awake, and logged in -- a LaunchAgent, not a
root-level LaunchDaemon. A "runs even when my laptop is off" setup would need a
cloud-hosted agent with the DB made cloud-accessible, which this project doesn't attempt.

## Extending this for yourself

See the "Ideas for extending this" section in `CLAUDE.md.example`, and
`TEMPLATE_CONTRACT.md` if you need to adapt your own resume/cover-letter template to work
with the builder. This is meant to be forked and made yours -- the design decisions
documented in `CLAUDE.md.example` are a starting opinion, not a spec.
