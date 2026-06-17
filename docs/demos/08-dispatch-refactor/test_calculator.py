import pytest
from calculator import calculate

def test_add():
    assert calculate('add', 3, 5) == 8

def test_sub():
    assert calculate('sub', 10, 4) == 6

def test_mul():
    assert calculate('mul', 6, 7) == 42

def test_div():
    assert calculate('div', 15, 3) == 5.0

def test_div_by_zero():
    with pytest.raises(ValueError, match='division by zero'):
        calculate('div', 10, 0)

def test_unknown_command():
    with pytest.raises(ValueError, match='unknown command: pow'):
        calculate('pow', 2, 3)
