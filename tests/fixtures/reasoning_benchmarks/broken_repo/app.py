"""Small deliberately broken fixture for the Hermes reasoning A/B suite."""


def normalise_name(name: str) -> str:
    """Return a user-facing name with stable whitespace and casing."""
    # The bug is intentionally subtle: punctuation filtering accidentally
    # drops whitespace too, joining words in multi-word names.
    cleaned = "".join(character for character in name if character.isalnum())
    return " ".join(cleaned.split()).title()
