# Cover Letter Rules

Instructions for any assistant helping you write a cover letter for a specific job
application.

Copy this file to `cover_letter_rules.md` (gitignored). Companion to `resume_rules.md`:
shares the same person, the same Bank, the same no-fabrication standard. Doesn't duplicate
the accuracy-trap checklist -- keep that in one place (`resume_rules.md`), not two.

## Load first, in this order

Do not draft anything until these are in hand:

1. **A baseline cover letter** to calibrate tone and structure against. Paste text or upload
   a docx/PDF -- ideally one you've actually sent before, so the structure and voice are
   proven, not invented. Reuse it as the structural template unless you provide a different
   one.
2. **Job description**: pasted text or URL to fetch.
3. **Length ceiling**: see below. Reuse if already established earlier in the conversation.
4. **Any known accuracy corrections** you flag at the start.

## Absolute rules

Same as `resume_rules.md`:
- Never use em dashes anywhere in output. Use parenthetical asides, commas, or colons instead.
- Only propose content you have actually done. Cross-check every claim against the Bank and
  any context you've provided. If uncertain, ask; do not invent.
- Never fabricate metrics, tools, employers, dates, or experience.
- Never mix attribution between employers.
- No sycophancy. No hedging language. No "still learning" or gap-flagging in the letter itself.
- Check every claim against `resume_rules.md`'s accuracy-trap checklist before finalizing.

## Structure

Five parts, in this order (adjust to match your own baseline letter once you have one):

1. **Greeting**: "Hi [Company] Team,"
2. **Hook paragraph**: why this role, why this company specifically. Ties the JD's own
   framing of the role to your actual background. Not generic -- should read as written for
   this JD, not reusable verbatim elsewhere.
3. **Evidence paragraph(s), 1-2**: the strongest, most JD-relevant Bank material, in prose
   rather than bullet form. Draw from the same Bank records used in the resume for this
   application, not a separate improvised set of claims. Headline metrics stay attached to
   their real employer, same as the resume.
4. **Closing ask**: one sentence, forward-looking, inviting the conversation rather than
   restating qualifications.
5. **Sign-off**: "Best,\n[Your name]"

## Length ceiling

Set a working baseline once you have a real letter you're happy with: measure its character
count and word count across the four body paragraphs (greeting and sign-off excluded), and
use that as your target going forward. This isn't a number to guess at -- calibrate it from
a real example, the same way resume section budgets get corrected once you know what
actually fits.

**Character counting**: use Python to count exactly before returning a draft. Do not eyeball
it. Print each paragraph's count and the total against the ceiling.

## Output format

Same discipline as `resume_rules.md`: a complete, paste-ready replacement for the whole
letter body, not a diff or narrative description of changes. If reusing a paragraph verbatim
from the baseline because nothing in the JD warrants changing it, say so explicitly rather
than silently repeating it without comment.

## Iteration rules

Same as `resume_rules.md`: don't argue, make the change; if a claim is called out as
fabricated or misattributed, fix it and audit the rest of the draft for the same error
class; if length is called out as wrong, adjust to the new target next turn without
re-litigating the ceiling.

## What to close with

Confidence flags for anything below 90% confidence, same format as `resume_rules.md`. Skip
if everything is at or above 90%.
