"""Phase 4: cover letter DOCX builder.

Mirrors resume.py's approach and reuses its low-level paragraph helpers
(clearing every run on write, cloning/deleting paragraphs to resize a
block). The cover letter template is structurally much simpler than the
resume's -- plain body paragraphs throughout, no floating text box, no
duplicated labels -- so this module is thin.

Does not decide what to write, same as resume.py: an LLM tailoring pass
against data/cover_letter_rules.md produces the content JSON; this module
only writes it into a copy of the template.
"""

import json
import shutil
from pathlib import Path

import docx

from careeros import jobs
from careeros.config import APPLICATIONS_DIR
from careeros.resume import ResumeBuildError, _resize_bullet_block, _set_paragraph_text, default_filename
from careeros.tailor import _slugify


def set_greeting(doc: docx.Document, company_name: str) -> None:
    """Replace the "Hi <Company> Team," greeting line."""
    for p in doc.paragraphs:
        if p.text.strip().startswith("Hi ") and "Team," in p.text:
            _set_paragraph_text(p, f"Hi {company_name} Team,")
            return
    raise ResumeBuildError('could not find a "Hi ... Team," greeting paragraph')


def set_body_paragraphs(doc: docx.Document, paragraphs_text: list[str]) -> None:
    """Replace the body paragraphs between the greeting and the "Best," sign-off.

    Resizes to len(paragraphs_text) exactly, same growth/shrink behavior as
    resume.py's employer bullet blocks.
    """
    paragraphs = doc.paragraphs
    greeting_idx = None
    signoff_idx = None
    for i, p in enumerate(paragraphs):
        if p.text.strip().startswith("Hi ") and "Team," in p.text:
            greeting_idx = i
        if p.text.strip() == "Best,":
            signoff_idx = i
            break
    if greeting_idx is None:
        raise ResumeBuildError('could not find the "Hi ... Team," greeting paragraph')
    if signoff_idx is None:
        raise ResumeBuildError('could not find a "Best," sign-off paragraph')

    body_idxs = list(range(greeting_idx + 1, signoff_idx))
    if not body_idxs:
        raise ResumeBuildError("no body paragraphs found between the greeting and sign-off")
    if not paragraphs_text:
        raise ResumeBuildError("refusing to leave the cover letter body empty")

    existing = [paragraphs[i] for i in body_idxs]
    resized = _resize_bullet_block(existing, len(paragraphs_text))
    for para, text in zip(resized, paragraphs_text):
        _set_paragraph_text(para, text)


def build(job_id: str, content_path: str, out_path: str | None = None) -> str:
    """Build a tailored cover letter DOCX from a content JSON file.

    content JSON shape:
        {
          "template": "/path/to/existing/cover/letter.docx",
          "greeting_company": "Docker",                  (optional)
          "paragraphs": ["hook...", "evidence 1...", "evidence 2...", "ask..."]  (optional)
        }

    Any top-level key may be omitted to leave that part untouched.
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
        # Same folder tailor.py/resume.py use for this job, so the cover
        # letter lands next to the tailoring package and resume draft.
        folder_slug = f"{job['company_slug']}-{_slugify(job['title'])}"
        folder = APPLICATIONS_DIR / folder_slug
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / default_filename(job, "Cover Letter")

    shutil.copy(template_path, dest)
    doc = docx.Document(dest)

    if "greeting_company" in content:
        set_greeting(doc, content["greeting_company"])
    if "paragraphs" in content:
        set_body_paragraphs(doc, content["paragraphs"])

    doc.save(dest)
    return str(dest)
