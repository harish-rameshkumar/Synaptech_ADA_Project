# Language-label review codebook

**Goal.** Decide, for each title, whether its *credits can count as a career credit in the language shown*. We are not judging film quality or the cast's usual language. Never infer language from who is in the cast.

**What you see.** `language` is our current original-language label (from TMDB, or Wikidata when TMDB had none). `akas_langs` lists Hindi/Tamil/Telugu/Malayalam/Kannada alternate titles IMDb holds for the title. `reason` is why it was flagged. Open `imdb_url`.

**What you fill in (every row, 1 = yes, 0 = no; blank = skipped).**

| column | fill with |
|---|---|
| `is_dubbed` | 1 / 0 |
| `is_ambiguous` | 1 / 0 |
| `corrected_language` | optional, ISO 639-1 (`hi ta te ml kn bn mr en ...`) only if the label is wrong and you are sure |
| `notes` | one line: the source and what it said; never invent a source, quote or URL |
| `confidence` | (pre-filled files) high = two independent sources agree, medium = one reliable source, low = weak, conflicting or no evidence |

## `is_dubbed` = 1
The title (or the release this IMDb entry describes) is a **dub** into the labelled language: the film was originally produced in a different language, so these credits are *not* performances in this language.
- IMDb entry for the Hindi-dubbed release of *Pushpa: The Rise* (made in Telugu), labelled `hi` → **1** (true language `te`; put `te` in `corrected_language` if you are sure).
- A Hollywood film with a Tamil-dubbed IMDb entry, labelled `ta` → **1**.
- *Sholay* (shot in Hindi) → **0**. A Telugu film that was *later also dubbed* into Hindi, but whose own entry is the Telugu original → **0** (the dub is a different release).

## `is_ambiguous` = 1
You **cannot tell** the original language, or it is **genuinely multilingual**, so the label would mislead.
- *Raavan* (Hindi) / *Raavanan* (Tamil): shot simultaneously with different casts → **1** if both versions sit under one entry or you cannot tell which this is.
- *Baahubali: The Beginning*: shot in Telugu and Tamil together → **1** if a single language label is not defensible.
- Sources **conflict** (e.g. TMDB says one language, Wikipedia another) and you cannot settle it in ~2 minutes → **1**.
- A source says the film is **bilingual or multilingual** (e.g. a Wikipedia infobox listing two Indian languages, or TMDB `spoken_languages` with several) → **1**.
- A title that is clearly one language → **0**, even if it has dubs, subtitles or alternate titles (`akas_langs`) in several languages elsewhere. Several akas languages by themselves are **not** a reason to flag: they usually just mean dubbed releases.

## Rules of thumb
1. **Evidence order:** TMDB (`original_language`, `spoken_languages`) → Wikipedia (film infobox "Language") → IMDb, **only by manual browsing** (Technical specs → Language, release info). **No scraping** of IMDb or any other site; use official APIs or read pages by hand. Never use the cast.
2. **Keep the current label by default.** Change it only on clear, reliable evidence. Mark `is_ambiguous` = 1 **only** when sources conflict or a source says the film is bilingual/multilingual. No evidence found → leave the label as is, write `no evidence found` in `notes`, and do not flag.
3. `is_dubbed` and `is_ambiguous` can both be 1 only if it looks like a dub *and* you cannot name the original language. Usually exactly one is 1, or neither.
4. If the label is simply wrong but the title is a clean single-language original (e.g. labelled `hi`, really `ml`), set both to 0 and fill `corrected_language`.
5. Time-box to ~2 minutes per title. Do not search for plot summaries.
6. Some rows are reviewed by all three of you, on purpose; label them like any other. Do not compare notes until everyone has finished, we measure agreement (Fleiss' kappa).

## File hygiene
Do **not** rename columns, edit `tconst`, add or delete rows, or re-sort. Save as `<yourname>_reviewed.csv` in `returned/` (keep the original). The pipeline never overwrites a reviewed file.
