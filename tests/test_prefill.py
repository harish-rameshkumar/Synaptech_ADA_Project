from cinema_mobility.prefill import decide, _lang_codes

ROW = {"language": "te", "current_label_source": "T1", "primaryTitle": "Film", "reason": "label_not_in_akas_languages"}


def d(row=None, tm=None, wk=None):
    return decide({**ROW, **(row or {})}, tm or {}, wk or {})


def test_two_sources_agree_is_high_and_keeps_label():
    dub, amb, corr, conf, note = d(tm={"original": "te", "spoken": ["te"]}, wk={"match": "id", "page": "P", "languages": ["te"], "raw": "Telugu"})
    assert (dub, amb, corr, conf) == ("0", "0", "", "high")


def test_weak_wikipedia_mismatch_is_not_a_conflict():
    _, amb, corr, conf, note = d(tm={"original": "te", "spoken": ["te"]}, wk={"match": "title+year", "page": "Seven (1995 film)", "languages": ["en"], "raw": "English"})
    assert amb == "0" and corr == "" and conf == "medium" and "not treated as a conflict" in note


def test_id_verified_bilingual_source_flags_ambiguous():
    _, amb, corr, conf, _ = d(tm={"original": "te", "spoken": ["te"]}, wk={"match": "id", "page": "P", "languages": ["ta", "te"], "raw": ""})
    assert (amb, corr, conf) == ("1", "", "low")


def test_id_verified_contradiction_flags_but_does_not_change_label():
    _, amb, corr, conf, note = d(tm={"original": "te", "spoken": ["te"]}, wk={"match": "id", "page": "P", "languages": ["hi"], "raw": ""})
    assert (amb, corr, conf) == ("1", "", "low") and "conflict" in note


def test_no_evidence_leaves_label_and_says_so():
    dub, amb, corr, conf, note = d(row={"language": "", "current_label_source": "unknown", "reason": "unknown_with_target_language_akas"})
    assert (dub, amb, corr, conf) == ("0", "0", "", "low") and "no evidence found" in note


def test_multi_language_akas_alone_never_flag_ambiguous():
    for tm in ({}, {"original": "te", "spoken": ["te"]}):
        _, amb, _, _, note = d(row={"reason": "multi_language_akas"}, tm=tm)
        assert amb == "0" and "likely dubbed" in note


def test_unknown_label_filled_only_from_id_verified_wikipedia():
    row = {"language": "", "current_label_source": "unknown"}
    assert d(row=row, wk={"match": "id", "page": "P", "languages": ["kn"], "raw": ""})[2] == "kn"
    assert d(row=row, wk={"match": "title+year", "page": "P", "languages": ["kn"], "raw": ""})[2] == ""


def test_language_name_parsing():
    assert _lang_codes("Telugu<ref>x Tamil</ref> / Hindi") == ["hi", "te"]


def test_dub_flag_needs_the_entry_title_not_the_overview():
    tm = {"original": "te", "spoken": ["te"], "title": "Film", "overview": "The film is dubbed into Hindi."}
    assert d(tm=tm)[0] == "0"
    assert d(row={"primaryTitle": "Film (Hindi Dubbed)"}, tm={"original": "te", "spoken": ["te"], "title": "Film"})[0] == "1"
