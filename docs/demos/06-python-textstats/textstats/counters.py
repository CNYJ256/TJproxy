def word_count(text: str) -> int:
    """Return the number of words in the given text."""
    return len(text.split())


def char_count(text: str) -> int:
    """Return the number of characters (including spaces) in the given text."""
    return len(text)


def line_count(text: str) -> int:
    """Return the number of lines in the given text."""
    if not text:
        return 0
    return text.count('\n') + 1
