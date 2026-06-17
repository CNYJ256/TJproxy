# textstats

A simple Python package providing basic text statistics: word count, character count, line count, and top frequent words.

## Installation

```bash
pip install .
```

## Usage

```python
from textstats import word_count, char_count, line_count, top_words

text = "Hello world! This is a test."
print(word_count(text))   # 5
print(char_count(text))   # 24
print(line_count(text))   # 1
print(top_words(text, 3)) # [('hello', 1), ('world', 1), ('this', 1)]
```

## Development

Install with test dependencies:

```bash
pip install -e ".[test]"
```

Run tests:

```bash
python -m pytest
```
