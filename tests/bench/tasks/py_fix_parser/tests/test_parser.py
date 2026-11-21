from parser import parse_numbers


def test_empty():
    assert parse_numbers("") == []


def test_single():
    assert parse_numbers("1") == [1]


def test_multiple():
    assert parse_numbers("1,2,3") == [1, 2, 3]


def test_spaces():
    assert parse_numbers("1, 2, 3") == [1, 2, 3]


def test_trailing_comma():
    assert parse_numbers("1,2,3,") == [1, 2, 3]
