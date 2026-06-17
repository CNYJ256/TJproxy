import pytest
from textstats import word_count, char_count, line_count, top_words


class TestWordCount:
    def test_empty_string(self):
        assert word_count("") == 0

    def test_single_word(self):
        assert word_count("hello") == 1

    def test_multiple_words(self):
        assert word_count("hello world foo") == 3

    def test_extra_spaces(self):
        assert word_count("  hello   world  ") == 2


class TestCharCount:
    def test_empty_string(self):
        assert char_count("") == 0

    def test_simple(self):
        assert char_count("abc") == 3

    def test_with_spaces(self):
        assert char_count("a b c") == 5


class TestLineCount:
    def test_empty_string(self):
        assert line_count("") == 0

    def test_single_line(self):
        assert line_count("hello") == 1

    def test_multiple_lines(self):
        assert line_count("line1\nline2\nline3") == 3

    def test_trailing_newline(self):
        assert line_count("line1\nline2\n") == 3


class TestTopWords:
    def test_empty_text(self):
        assert top_words("") == []

    def test_simple(self):
        result = top_words("the cat and the dog", n=2)
        assert result == [("the", 2), ("cat", 1)]

    def test_case_insensitive(self):
        result = top_words("Hello HELLO hello")
        assert result[0] == ("hello", 3)

    def test_punctuation_stripped(self):
        result = top_words("hello! hello? hello.")
        assert result[0] == ("hello", 3)

    def test_apostrophe_kept(self):
        result = top_words("don't can't")
        assert ("don't", 1) in result
        assert ("can't", 1) in result

    def test_n_larger_than_unique(self):
        result = top_words("a b c", n=10)
        assert len(result) == 3
