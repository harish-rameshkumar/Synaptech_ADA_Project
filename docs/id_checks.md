# IMDb ID checks

*Data Lead's note for the Data section. Every number was recomputed from the files on 23 October 2026 (`title.basics.tsv.gz`, `title.principals.tsv.gz`, the cached TMDB labels and the Parquet credit tables). The IDs in all credit tables are exactly the IDs shipped in the IMDb files: no ID was corrected, merged or remapped.*

## 1. Title IDs in `title.principals` that are missing from `title.basics`

* 325 distinct title IDs occur in `title.principals` (actor or actress rows, billed in the top 10) but not in `title.basics`; they carry 1,446 credit rows. We computed this twice, with pandas' default quote handling and with quoting disabled, and got the same 325 IDs and 1,446 rows.
* Such an ID is typically one that IMDb has merged into another title or removed. IMDb ships no redirect table, so we cannot tell which title replaced it, and we cannot map the credits to a surviving ID without guessing.
* **Action: dropped.** The credits are absent from every Parquet version because a title that is not in `title.basics` has no type or year and so cannot enter the filter.

## 2. IDs duplicated within a file

* `title.basics`: 12,881,968 rows, 0 repeated `tconst`. All match `tt` followed by digits.
* Repeated title–actor pairs in `title.principals`: 49,365 (3,207,781 top-10 actor or actress rows on titles from 1980 became 3,158,416 after dropping them). The same actor appears twice on a title, normally under two different roles. We keep one row per pair (the first), so every `(title_id, actor_id)` is unique in the credit tables (0 repeats in `credits_final_v2`, `credits_final`, `credits`, `credits_pre_review`). The pair then carries one billing position: that of the row we kept.
* Cached label table: 174,014 candidate titles, 0 repeated title IDs.

## 3. T1 titles that share a TMDB ID

* Among the 118,852 titles resolved by TMDB (T1), 7 TMDB IDs are each shared by two IMDb IDs (14 titles). All 14 are in the credit tables.
* In every one of the 7 cases, one title was matched to a TMDB *movie* and the other to a TMDB *TV* record. TMDB numbers movies and TV shows in separate ID spaces, so the same number can name two unrelated records. These are therefore ID-space collisions, not duplicate or merged IMDb titles, and the pairs have different years and, in two cases, different languages (`tt0464129`/`tt2248765`, `tt0356153`/`tt0871977`). Our earlier quality table described them as "likely merged/duplicate"; that wording was too strong and is not supported by the data.
* **Action: none.** We did not merge, drop or relabel any of them.

| TMDB ID | IMDb IDs (match type, year, label) |
| :--- | :--- |
| 201245 | tt0464129 (TV, 2005, de); tt2248765 (movie, 2012, es) |
| 207774 | tt17212540 (TV, 2021, en); tt2781832 (movie, 2013, en) |
| 246180 | tt0167183 (movie, 2000, en); tt30281955 (TV, 2023, en) |
| 47018 | tt0112637 (movie, 1995, en); tt0451203 (TV, 1994, en) |
| 77074 | tt0086205 (movie, 1983, en); tt0144937 (TV, 1998, en) |
| 80650 | tt0356153 (TV, 2003, it); tt0871977 (movie, 2007, ja) |
| 87436 | tt0305108 (TV, 2002, en); tt1935896 (movie, 2012, en) |

## 4. Corrections

No IDs were corrected. The Parquet files therefore contain no ID corrections. The title and actor ID sets are identical in `credits_pre_review`, `credits`, `credits_final` and `credits_final_v2` (same `(title_id, actor_id)` pairs). The only manual corrections applied are two *language* corrections, Danny Jigar to `gu` and Kanulu Therichinaa Kanulu Moosinaa to `te`, which change a label, not an ID.

## 5. What was not checked

* Whether a surviving `title.basics` ID is itself the product of a merge (we only see current IDs).
* Actor IDs (`nconst`): they all match `nm` followed by digits, but IMDb ships no redirect table for them either, so a person split across two IDs would be invisible to us.
