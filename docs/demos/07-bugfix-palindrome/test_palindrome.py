import pytest
from palindrome import is_palindrome

def test_empty_string():
    assert is_palindrome("") == True

def test_single_char():
    assert is_palindrome("a") == True

def test_palindrome_even():
    assert is_palindrome("abba") == True

def test_palindrome_odd():
    assert is_palindrome("racecar") == True

def test_non_palindrome():
    assert is_palindrome("hello") == False

def test_case_sensitive():
    assert is_palindrome("Aba") == False
