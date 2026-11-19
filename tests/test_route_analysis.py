from modules.route_analysis import (
    classify_route_certainty,
    classify_route_node,
    extract_explicit_transitions,
    extract_route_mentions,
)


def test_certainty_is_preserved_without_assuming_unspecified_is_confirmed():
    assert classify_route_certainty("[\u78ba\u5b9a\u3057\u305f\u51fa\u53e3]") == "Confirmed"
    assert classify_route_certainty("[\u4e0d\u78ba\u5b9a\u306a\u5165\u53e3]") == "Uncertain"
    assert classify_route_certainty("[\u5165\u53e3]") == "Unspecified"


def test_frontrooms_as_common_object_reference_is_not_a_route_node():
    line = "Level 8\u3067\u4e00\u822c\u7684\u306bFrontrooms\u3067\u898b\u3089\u308c\u308b\u96fb\u8a71\u30dc\u30c3\u30af\u30b9\u306b\u5165\u308b\u3068Level 9\u3078\u79fb\u52d5\u3059\u308b\u3002"
    targets = [item[0] for item in extract_route_mentions(line)]
    assert "Level 8" in targets
    assert "Level 9" in targets
    assert "The Frontrooms" not in targets


def test_explicit_mixed_level_transition_uses_sentence_origin_and_destination():
    line = "Level 8\u3067\u4e00\u822c\u7684\u306bFrontrooms\u3067\u898b\u3089\u308c\u308b\u96fb\u8a71\u30dc\u30c3\u30af\u30b9\u306b\u5165\u308b\u3068Level 9\u3078\u79fb\u52d5\u3059\u308b\u3002"
    assert extract_explicit_transitions(line) == [("Level 8", "Level 9")]


def test_frontrooms_can_be_a_real_endpoint_when_explicitly_named_as_one():
    line = "Level 2\u304b\u3089The Frontrooms\u3078\u623b\u308b\u3053\u3068\u304c\u3067\u304d\u308b\u3002"
    assert extract_explicit_transitions(line) == [("Level 2", "The Frontrooms")]
    assert classify_route_node("The Frontrooms") == "Frontrooms"
    assert classify_route_node("Level 2") == "Level"


def test_wiki_link_url_recovers_level_even_when_display_label_is_not_level_name():
    line = "\u6249\u306b\u5165\u308b\u3068[[https://backrooms.fandom.com/ja/wiki/Level_1000_%281%29|\u5225\u306e\u968e\u5c64]]\u3078\u79fb\u52d5\u3059\u308b\u3002"
    targets = [item[0] for item in extract_route_mentions(line)]
    assert "Level 1000" in targets
