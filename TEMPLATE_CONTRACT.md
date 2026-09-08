# Resume / cover letter template contract

`resume.py` and `cover_letter.py` don't parse your document semantically -- they find
sections by matching exact text and paragraph styles, then overwrite or clone paragraphs in
place. If your own `.docx` template doesn't match this contract, `resume-build` /
`cover-letter-build` will raise a `ResumeBuildError` telling you what it couldn't find,
rather than silently producing something wrong.

There's no bundled starter template in this repo (resume design is personal and this repo
stays unopinionated about it) -- take your own resume, in Word, and make sure it matches the
structure below. Existing resumes usually need only small adjustments: renaming a header to
match exactly, or making sure your bullets use Word's actual "List Paragraph" style rather
than a manually-typed bullet character.

## Resume (`resume.py`)

- **Professional Summary**: a paragraph containing exactly `PROFESSIONAL SUMMARY` as its own
  line/label, with the summary text in the paragraph immediately after it. If your summary
  lives inside a floating text box (common when a resume template uses a colored banner),
  that's fine -- the code walks the raw document XML, not just `doc.paragraphs`, specifically
  to reach text boxes. If the label appears twice (some Word templates duplicate text-box
  content as a fallback for older Word versions), every occurrence gets updated.
- **Fixed-size text boxes clip silently.** If your summary lives in a text box with a fixed
  height (not "resize shape to fit text"), text past the box's visible capacity won't render
  and won't error either. `set_summary()` runs a conservative word-wrap simulation
  (`SUMMARY_BOX_MAX_LINES` / `SUMMARY_BOX_CHARS_PER_LINE` in `resume.py`) and refuses to
  write text that would overflow. Recalibrate those two constants against your own template's
  actual box size and font if you hit false positives/negatives.
- **Per-employer bullets**: a `"Normal"`-styled paragraph containing both the employer name
  and a `•` character somewhere in it (e.g. `"SENIOR ENGINEER • Acme Corp ... 2022 - 2024"`)
  is the header the code searches for. Every immediately-following paragraph styled
  `"List Paragraph"` is treated as one bullet, up to the first non-list paragraph. Growing or
  shrinking the bullet count clones/deletes from the end of that run.
- **Personal Projects**: a paragraph with the exact text `PERSONAL PROJECTS`, followed by
  alternating title/description paragraphs (any style, just non-empty) up to the first blank
  paragraph. Must be an even, non-zero count of paragraphs (title, description, title,
  description, ...).
- **Hard Skills**: a paragraph with the exact text `HARD SKILLS`, followed by
  (subcategory-header, content-line) pairs. Each subcategory is matched by exact text (e.g.
  `"Programming & Data"`) -- keep your subcategory names stable across edits, since the code
  replaces a subcategory's content by finding its header text, and inserts a new one (cloning
  the first pair's formatting) if no match is found.
- **Stray empty bullets**: any paragraph styled `"List Paragraph"` with zero text anywhere in
  the document gets deleted automatically at the end of every build. This is a known Word
  quirk (an empty bullet left over from editing, which still renders its marker) -- if you
  intentionally use empty `"List Paragraph"`-styled paragraphs for spacing anywhere, don't;
  use empty `"Normal"`-styled paragraphs for spacing instead, since those are left alone.
- **Top Achievements** (or any other section not listed above) isn't touched by the tooling
  at all -- it stays exactly as it is in whatever template file you pass in. Edit it directly
  in Word if it needs to change per application.

## Cover letter (`cover_letter.py`)

- **Greeting**: a paragraph starting with `"Hi "` and containing `"Team,"` (e.g.
  `"Hi Acme Corp Team,"`).
- **Body**: every paragraph between the greeting and the sign-off is replaced with your
  supplied paragraphs, growing/shrinking the block as needed.
- **Sign-off**: a paragraph whose text is `"Best,"` marks the end of the body block.

## General tips

- Multi-run paragraphs (common after spell-check or manual formatting edits in Word) are
  handled: writing new text clears every run beyond the first, so old text can't stay glued
  onto the end. You don't need to "clean up" your template's runs manually.
- If `resume-build` errors with something like `could not find a "X" header`, that means the
  exact text it searched for isn't in your document -- check for a typo, extra whitespace, or
  a different capitalization/punctuation than what your content JSON or the code expects.
- Test against your own template early with a tiny throwaway content JSON before trusting it
  for a real application -- these functions raise loudly on a structural mismatch, but it's
  much faster to catch that on a one-line test than mid-tailoring-session.
