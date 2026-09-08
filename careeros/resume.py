"""Phase 4: DOCX resume builder.

CareerOS stays deterministic at runtime (see CLAUDE.md decision #4): this
module does not decide what content goes into the resume. It takes a
content file that an LLM tailoring pass (following data/resume_rules.md)
already produced -- Summary, Personal Projects, and per-employer Experience
bullets -- and mechanically writes it into a copy of Alex's resume
template, preserving the template's formatting.

Two structural quirks in the template that broke earlier hand-run scripts,
now handled generically here rather than special-cased per document:

1. Some paragraphs split their visible text across 2-5 runs (old
   spell-check/formatting artifacts). Writing new text into a paragraph
   must clear every run beyond the first, not just overwrite the first one,
   or leftover text from the old runs stays glued onto the end.
2. The Professional Summary lives inside a floating text box
   (w:txbxContent), invisible to python-docx's Document.paragraphs API.
   It's located structurally (the paragraph immediately after the literal
   "PROFESSIONAL SUMMARY" label, wherever that label lives in the document
   tree) rather than by matching old summary text, so this works on any
   template that uses the same label, not just the one seen so far.
"""

import copy
import json
from pathlib import Path
from typing import Any

import docx
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from careeros import db, facts, jobs
from careeros.config import APPLICATIONS_DIR
from careeros.tailor import _slugify


class ResumeBuildError(ValueError):
    pass


# --- low-level paragraph helpers --------------------------------------------

def _set_paragraph_text(para: Paragraph, text: str) -> None:
    """Overwrite a paragraph's visible text, preserving its formatting.

    Sets run 0's text and clears every other run. Clearing (not just
    overwriting run 0) matters: paragraphs with 2+ runs from old
    spell-check/formatting splits will otherwise leave old text glued onto
    the end of the new text.
    """
    if not para.runs:
        raise ResumeBuildError(f"paragraph has no runs to write into: {para.text!r}")
    para.runs[0].text = text
    for r in para.runs[1:]:
        r.text = ""


def _delete_paragraph(para: Paragraph) -> None:
    para._p.getparent().remove(para._p)


def _clone_paragraph_after(para: Paragraph) -> Paragraph:
    """Duplicate a paragraph's XML (with its formatting) and insert it
    immediately after. Returns the new Paragraph."""
    new_p = copy.deepcopy(para._p)
    para._p.addnext(new_p)
    return Paragraph(new_p, para._parent)


def _resize_bullet_block(paras: list[Paragraph], n_needed: int) -> list[Paragraph]:
    """Grow or shrink a contiguous list of single-unit paragraphs (e.g. one
    bullet per paragraph) to exactly n_needed, cloning the last paragraph's
    formatting when growing or deleting from the end when shrinking."""
    paras = list(paras)
    if len(paras) > n_needed:
        for p in paras[n_needed:]:
            _delete_paragraph(p)
        return paras[:n_needed]
    last = paras[-1]
    while len(paras) < n_needed:
        new_p = _clone_paragraph_after(last)
        paras.append(new_p)
        last = new_p
    return paras


# --- Experience bullets ------------------------------------------------------

def set_employer_bullets(doc: docx.Document, employer_name: str, bullets: list[str]) -> None:
    """Replace the bullet list under an employer's Experience header.

    Locates the header by scanning for a bold "Normal"-style paragraph
    containing both the employer name and the "•" role/employer
    separator (e.g. "SENIOR SOLUTIONS CONSULTANT • Arteria AI"), then
    takes every immediately-following "List Paragraph"-style paragraph as
    that employer's bullets. Resizes to len(bullets) exactly, growing by
    cloning the last bullet's formatting or shrinking by deleting extras.
    """
    paragraphs = doc.paragraphs
    header_idx = None
    for i, p in enumerate(paragraphs):
        if p.style.name == "Normal" and employer_name in p.text and "•" in p.text:
            header_idx = i
            break
    if header_idx is None:
        raise ResumeBuildError(f"could not find an Experience header containing {employer_name!r}")

    bullet_idxs = []
    i = header_idx + 1
    while i < len(paragraphs) and paragraphs[i].style.name == "List Paragraph":
        bullet_idxs.append(i)
        i += 1
    if not bullet_idxs:
        raise ResumeBuildError(f"no bullet paragraphs found under {employer_name!r}'s header")

    existing = [paragraphs[idx] for idx in bullet_idxs]
    if not bullets:
        raise ResumeBuildError(f"refusing to leave {employer_name!r} with zero bullets")

    resized = _resize_bullet_block(existing, len(bullets))
    for para, text in zip(resized, bullets):
        _set_paragraph_text(para, text)


# --- Professional Summary (lives in a floating text box) -------------------

# The summary box is a FIXED-SIZE shape (does not grow to fit text); overflow
# is silently clipped at render time rather than raising any error, which is
# exactly what happened 2026-09-07: three summaries in a row (Decagon 481,
# Supabase 481, Linear 492 chars) were long enough to wrap onto a 4th line
# that never rendered, cutting the summary off mid-sentence with no warning.
# The box measures 547.5pt wide (from the template's w:drawing/a:ext,
# 6953061 EMU / 12700 EMU-per-point) at an 8pt font (w:sz val="16" half-
# points). There's no reliable font-metrics library available here (no
# Pillow, and Calibri's real metrics aren't guaranteed present anyway), so
# rather than estimate line-wrapping from font metrics, this is calibrated
# directly against the Linear overflow: its three rendered lines measured
# 159/148/160 characters before the 4th line's ~23 characters got clipped.
# SUMMARY_CHARS_PER_LINE is set below that observed range (not at its
# average) so a summary landing close to the ceiling still has margin
# rather than riding the exact edge that just failed.
SUMMARY_BOX_MAX_LINES = 3
SUMMARY_BOX_CHARS_PER_LINE = 150


def _summary_line_count(text: str, chars_per_line: int = SUMMARY_BOX_CHARS_PER_LINE) -> int:
    """Greedy word-wrap simulation: count lines a space-separated text would
    take at chars_per_line, the same way a real line-breaking renderer packs
    words (word-boundary-aware, not a flat len(text) // chars_per_line
    division, which would understate wrapping for text with many short
    words and overstate it for text with few long ones)."""
    lines = 1
    current_len = 0
    for word in text.split():
        add_len = len(word) + (1 if current_len else 0)  # +1 for the space before it
        if current_len + add_len > chars_per_line:
            lines += 1
            current_len = len(word)
        else:
            current_len += add_len
    return lines


def set_summary(doc: docx.Document, new_text: str) -> None:
    """Replace the Professional Summary paragraph.

    The summary lives inside a w:txbxContent text box, not reachable via
    doc.paragraphs. Located structurally: the paragraph immediately
    following the literal "PROFESSIONAL SUMMARY" label, found by walking
    the raw XML tree (which does include text-box content) rather than by
    matching the current summary's text.

    The template has TWO "PROFESSIONAL SUMMARY" labels (a Word
    AlternateContent shape typically carries both a primary DrawingML
    version and an older VML fallback version of the same content) --
    replacing only the first left the old text visible from the second.
    Every match gets replaced, not just the first.

    Raises ResumeBuildError before writing anything if new_text would
    overflow the box's fixed height (see SUMMARY_BOX_MAX_LINES/
    SUMMARY_BOX_CHARS_PER_LINE above) -- the box clips silently otherwise,
    which is the failure mode this check exists to catch.
    """
    line_count = _summary_line_count(new_text)
    if line_count > SUMMARY_BOX_MAX_LINES:
        max_safe_chars = SUMMARY_BOX_CHARS_PER_LINE * SUMMARY_BOX_MAX_LINES
        raise ResumeBuildError(
            f"Summary is {len(new_text)} chars and simulates to {line_count} wrapped lines, "
            f"but the summary box only fits {SUMMARY_BOX_MAX_LINES} lines before silently "
            f"clipping the rest. Shorten it (safe ceiling is roughly {max_safe_chars} chars, "
            "but depends on word lengths -- shorten and re-check rather than trusting the "
            "character count alone)."
        )

    body = doc.element.body
    replaced = 0
    for p_elem in body.iter(qn("w:p")):
        t_elems = p_elem.findall(".//" + qn("w:t"))
        combined = "".join(t.text or "" for t in t_elems).strip()
        if combined != "PROFESSIONAL SUMMARY":
            continue
        next_elem = p_elem.getnext()
        if next_elem is None or next_elem.tag != qn("w:p"):
            raise ResumeBuildError("found a PROFESSIONAL SUMMARY label but no paragraph after it")
        target_t_elems = next_elem.findall(".//" + qn("w:t"))
        if not target_t_elems:
            raise ResumeBuildError("summary paragraph has no text runs to write into")
        target_t_elems[0].text = new_text
        for t in target_t_elems[1:]:
            t.text = ""
        replaced += 1
    if replaced == 0:
        raise ResumeBuildError('could not find a "PROFESSIONAL SUMMARY" label in the document')


# --- Personal Projects (title/description pairs) ---------------------------

def set_personal_projects(doc: docx.Document, projects: list[dict[str, str]]) -> None:
    """Replace the Personal Projects block with the given (title, description) pairs.

    Locates the "PERSONAL PROJECTS" header, then every non-empty paragraph
    after it up to the first blank paragraph as alternating title/
    description pairs. Resizes in pairs (clones the last title paragraph
    and last description paragraph together as a unit when growing).
    """
    if not projects:
        raise ResumeBuildError("refusing to leave Personal Projects empty")

    paragraphs = doc.paragraphs
    header_idx = None
    for i, p in enumerate(paragraphs):
        if p.text.strip() == "PERSONAL PROJECTS":
            header_idx = i
            break
    if header_idx is None:
        raise ResumeBuildError('could not find a "PERSONAL PROJECTS" header')

    idxs = []
    i = header_idx + 1
    while i < len(paragraphs) and paragraphs[i].text.strip() != "":
        idxs.append(i)
        i += 1
    if len(idxs) < 2 or len(idxs) % 2 != 0:
        raise ResumeBuildError(
            f"Personal Projects block has {len(idxs)} paragraphs; expected an even, non-zero "
            "count of title/description pairs"
        )

    existing = [paragraphs[idx] for idx in idxs]
    n_existing_pairs = len(existing) // 2
    n_new_pairs = len(projects)

    if n_new_pairs < n_existing_pairs:
        keep = existing[: n_new_pairs * 2]
        for p in existing[n_new_pairs * 2:]:
            _delete_paragraph(p)
        existing = keep
    elif n_new_pairs > n_existing_pairs:
        title_template, desc_template = existing[-2], existing[-1]
        last = existing[-1]
        for _ in range(n_new_pairs - n_existing_pairs):
            new_title = _clone_paragraph_after(last)
            new_desc = _clone_paragraph_after(new_title)
            existing.append(new_title)
            existing.append(new_desc)
            last = new_desc
        # cloning copies formatting; content gets overwritten below regardless

    flat = []
    for proj in projects:
        flat.append(proj["title"])
        flat.append(proj["description"])
    for para, text in zip(existing, flat):
        _set_paragraph_text(para, text)


# --- Hard Skills subcategories (header + one content line each) ------------

def set_hard_skills_subcategory(doc: docx.Document, subcategory: str, content: str) -> None:
    """Replace (or insert) one Hard Skills subcategory's content line.

    If a subcategory paragraph with this exact header text already exists,
    replaces the content line immediately after it. If not, inserts a new
    (header, content) pair as the first subcategory under "HARD SKILLS",
    cloning the first existing subcategory pair's formatting.
    """
    paragraphs = doc.paragraphs
    for i, p in enumerate(paragraphs):
        if p.text.strip() == subcategory and i + 1 < len(paragraphs):
            _set_paragraph_text(paragraphs[i + 1], content)
            return

    header_idx = None
    for i, p in enumerate(paragraphs):
        if p.text.strip() == "HARD SKILLS":
            header_idx = i
            break
    if header_idx is None:
        raise ResumeBuildError('could not find a "HARD SKILLS" header, and no existing '
                                f"subcategory named {subcategory!r} to replace")
    first_sub_header = paragraphs[header_idx + 1]
    first_sub_content = paragraphs[header_idx + 2]

    new_header = _clone_paragraph_after(first_sub_content)
    # move new_header to be BEFORE first_sub_content's old position by inserting
    # right after first_sub_header instead, then clone content after that.
    new_header._p.getparent().remove(new_header._p)
    first_sub_header._p.addnext(new_header._p)
    new_content = _clone_paragraph_after(new_header)

    _set_paragraph_text(new_header, subcategory)
    _set_paragraph_text(new_content, content)


# --- filename convention -----------------------------------------------------

def _company_display_name(company_slug: str) -> str:
    """Look up a company's proper display name for filenames (e.g. "vantageanalytics"
    -> "Vantage Analytics"). Falls back to a title-cased slug if not found."""
    with db.connect() as conn:
        row = conn.execute("SELECT name FROM companies WHERE slug = ?", (company_slug,)).fetchone()
    if row and row["name"]:
        return row["name"]
    return company_slug.replace("-", " ").title()


def _strip_empty_list_paragraphs(doc: docx.Document) -> None:
    """Delete any paragraph styled "List Paragraph" (a bullet) that has no
    text at all.

    The template ships with one of these baked in, sitting right before the
    "EXPERIENCE" header (a leftover from whatever the template's original
    author was doing) -- confirmed present in the base template itself, not
    something any of the set_*() functions introduce. A bullet-styled
    paragraph typically still renders its list marker even with empty text,
    which can show up as a stray floating bullet with nothing after it.
    Harmless to remove: there is no text in these paragraphs to lose."""
    for para in list(doc.paragraphs):
        if para.style.name == "List Paragraph" and not para.text.strip():
            _delete_paragraph(para)


def default_filename(job: dict, doc_type: str) -> str:
    """Naming convention: '<Your Name> [Resume|Cover Letter] - [Role Title] -
    [Company Name].docx'. Name comes from data/facts.yaml (personal.name).
    Used as the default whenever --out isn't given, for both resume.py and
    cover_letter.py."""
    name = facts.get("personal.name") or "Candidate"
    company = _company_display_name(job["company_slug"])
    title = job["title"].strip()
    return f"{name} {doc_type} - {title} - {company}.docx"


# --- top-level build ---------------------------------------------------------

def build(job_id: str, content_path: str, out_path: str | None = None) -> str:
    """Build a tailored resume DOCX from a content JSON file.

    content JSON shape:
        {
          "template": "/path/to/alex's/existing/tailored/resume.docx",
          "summary": "...",                                  (optional)
          "personal_projects": [{"title": "...", "description": "..."}],  (optional)
          "hard_skills": {"Subcategory Name": "content line"},  (optional)
          "experience": {"Arteria AI": ["bullet1", ...], "Braze": [...], "Loopio": [...]}
        }

    Any top-level key may be omitted to leave that section untouched.
    Returns the path to the written file.
    """
    job = jobs.get(job_id)
    content = json.loads(Path(content_path).read_text(encoding="utf-8"))

    template = content.get("template")
    if not template:
        raise ResumeBuildError('content file must include a "template" path')
    template_path = Path(template).expanduser()
    if not template_path.exists():
        raise ResumeBuildError(f"template not found: {template_path}")

    if out_path:
        dest = Path(out_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        # Same folder tailor.py uses for this job's tailoring_package.md,
        # so the resume lands next to it rather than in a new location.
        folder_slug = f"{job['company_slug']}-{_slugify(job['title'])}"
        folder = APPLICATIONS_DIR / folder_slug
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / default_filename(job, "Resume")

    import shutil
    shutil.copy(template_path, dest)

    doc = docx.Document(dest)

    if "summary" in content:
        set_summary(doc, content["summary"])

    if "experience" in content:
        for employer, bullets in content["experience"].items():
            set_employer_bullets(doc, employer, bullets)

    if "personal_projects" in content:
        set_personal_projects(doc, content["personal_projects"])

    if "hard_skills" in content:
        for subcategory, line in content["hard_skills"].items():
            set_hard_skills_subcategory(doc, subcategory, line)

    _strip_empty_list_paragraphs(doc)

    doc.save(dest)
    return str(dest)
