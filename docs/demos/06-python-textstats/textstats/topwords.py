from collections import Counter
import re

def top_words(text: str, n: int = 10) -> list[tuple[str, int]]:
    """Return the top n most frequent words and their counts.
    Words are case-insensitive; punctuation is stripped.
    """
    words = re.findall(r"[a-zA-Z']+", text.lower())
    return Counter(words).most_common(n)
