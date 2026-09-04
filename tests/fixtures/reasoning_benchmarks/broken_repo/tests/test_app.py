from app import normalise_name


def test_normalise_name_preserves_word_boundaries():
    assert normalise_name("Ada! Lovelace") == "Ada Lovelace"


def test_normalise_name_collapses_whitespace():
    assert normalise_name("  grace   hopper ") == "Grace Hopper"
