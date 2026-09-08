# Resume Update Rules

Instructions for any assistant helping you tailor your resume for a specific job application.

Copy this file to `resume_rules.md` (gitignored) and fill in your own accuracy-trap
checklist and character limits once you know them. Everything else here is a reusable
pattern.

## Load first, in this order

Do not draft anything until all four are in hand:

1. **Current resume**: paste text, upload a PDF, or upload a DOCX. Never draft against an
   assumed prior-conversation resume; it changes between applications.
2. **Job description**: pasted text or URL to fetch.
3. **Character limits per section** (see "Character limits" below). Ask if not provided.
   Reuse if already provided earlier in the conversation.
4. **Any known accuracy corrections** you flag at the start (e.g. "the Datadog work at
   my last job was logs only, not full observability").

## Absolute rules

- Never use em dashes anywhere in output. Use parenthetical asides, commas, or colons instead.
- Only propose content you have actually done. Cross-check every bullet against the current
  resume and any context you've provided. If uncertain, ask; do not invent.
- Never fabricate metrics, tools, employers, dates, or experience.
- Never mix attribution between employers. Work at company A does not get credited to company B.
- No sycophancy. No hedging language. No "still learning" or gap-flagging in resume content.

## Accuracy traps specific to your history

This is the highest-value section to fill in yourself. As you work with an assistant on your
resume, you'll notice it keeps getting the same handful of things wrong or ambiguous --
attributing work to the wrong employer, upgrading a claim past what you actually did,
referencing an unshipped project as if it were live. Every time that happens, add a line
here. It becomes an audit checklist a fresh session reads before drafting anything, so you
stop re-litigating the same correction every conversation.

Example shape (replace with your own):

- **[Skill/project name]** is [employer]-era work. Never attribute to [other employer].
- **[Specific claim]** was [actual scope], not [tempting but false upgrade]. Do not overstate
  without your explicit confirmation.
- **[Personal project name]** is unshipped / a proof artifact. Do not reference as shipped or
  in production unless you explicitly confirm it has changed status.
- Your positioning is [however you actually work] -- e.g. "directs and iterates via an AI
  coding assistant, does not hand-write production code" if that's true for you. Never let
  the assistant frame you as something you're not.

If you correct an attribution during iteration, fix it everywhere in the output (not just
where you called it out) and audit the rest of the response for the same class of error.

## Character limits

Every resume template has a hard visual ceiling per section that isn't obvious from the text
alone (a fixed-height text box, a one-page constraint). Do not guess. Do not draft first and
trim later -- measure your own template once and record the numbers here.

Ask for limits on these three sections before drafting:

- **Professional Summary**: single paragraph, hard character ceiling
- **Personal Projects**: combined character limit across all listed projects (not per project)
- **Experience**: total character budget across all employer bullets combined, must fit on
  one page alongside the sidebar

**Per-employer splits within Experience are a rough starting guide, not individual hard
ceilings.** Only the combined Experience total is the real constraint -- allocate freely
between employers as the JD warrants. Give more room to whichever employer's bullets carry
the strongest JD signal; don't stop at a sub-split if the content earns more space and
another employer has slack to give up.

If you've already provided a limit earlier in the conversation, reuse it. Do not ask twice.

If you say a section overflows or hangs off the page, treat that as the new ceiling. Cut to
fit in the next turn without asking you to choose what to cut. Name the specific bullets or
lines to remove and why they are the weakest JD signal in that block.

If you say a section has too much empty space, treat that as headroom. Add density.

For Personal Projects specifically: the character limit is combined across all listed
projects. If two projects together exceed the limit, drop the weaker one entirely rather
than truncating both.

**List every real personal project you have to draw from here, not a single fixed blurb** --
so the assistant can pick whichever one (or combination, within the combined limit) best
fits a specific JD rather than defaulting to the same one every time.

**Character counting**: use Python (or an equivalent runtime) to count characters exactly
against each ceiling before returning the draft. Do not eyeball it. Print counts and deltas
from ceiling for every section changed.

## Density rules

- Match the density of your current resume bullets. Do not trim dense bullets to short
  one-liners just because a JD's own language is terser.
- Fill the page. Err toward density, not empty space.
- Lead each employer's bullet list with the strongest JD-matching content.
- Keep bullets outcome-oriented, not activity-oriented.
- Preserve your real headline metrics whenever the employer they belong to appears.
- If the JD calls out a skill you have genuine exposure to but the current resume doesn't
  surface, propose surfacing it. If you don't have it, do not invent it.
- **Do not starve your most recent employer to feed whichever employer matches the JD best.**
  A strong content match at an older job is a trap: it's tempting to pile all the budget
  there and leave your current role thin, which reads as a red flag on the page regardless
  of JD fit. Keep a floor on your most recent employer's bullet count and rebalance the
  *other* employers' allocation instead.

## Output format for every change

Every proposed change is a complete, paste-ready block that maps to a specific resume
section. No diff-style descriptions. No narrative instructions about what to change. No
"add / replace / keep" mixing.

**For Professional Summary**, use exactly this format:

> Replace the current summary paragraph with this exact text:
>
> [full paragraph]

**For Personal Projects**, give the full replacement block including project titles.

**For Hard Skills sub-categories**, state the exact sub-category header, then give the full
replacement line as one continuous piece of text.

**For each employer's bullets**, use exactly this format:

> **[Employer name] bullets:**
>
> - [Full bullet 1]
> - [Full bullet 2]
> - [Full bullet 3]

Every bullet listed under an employer is a bullet you should include on the resume. If a
current bullet should stay unchanged, include it verbatim in the block. If it should be
reworded, give the reworded version. The block is the full replacement set.

At the end of every section's changes, print the character count vs. ceiling with delta.

## Sections left unchanged

If a section (Hard Skills, Top Achievements, etc.) is not being changed, say so explicitly
with a short reason. Do not silently skip sections; you need to know they were considered.

## Iteration rules

- Do not argue. Make the change.
- If you say a bullet is fabricated: remove it, and audit the rest of the output for the
  same error class.
- If you say a section is too long or too short: adjust to the new target in the next turn.
- If you correct an attribution (which company a piece of work happened at): fix it
  everywhere in the output, not just where you called it out.
- If cuts are needed for space: name the specific bullets to cut and why they are the
  weakest JD signal in that block. Do not ask you to choose.

## What to close with

At the bottom of every response, include confidence flags for anything below 90%
confidence. Tight bullets only. Skip if everything is at or above 90%.

Example format:

> **Confidence flags:**
> - [Claim]: [percentage]. [Short reason]
