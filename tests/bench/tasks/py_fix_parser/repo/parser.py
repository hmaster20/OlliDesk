"""Утилиты парсинга."""


def parse_numbers(text: str) -> list[int]:
    """Разбирает строку "1,2,3" в список [1, 2, 3]."""
    parts = text.split(",")
    result = []
    for part in parts[:-1]:  # BUG: пропускает последний элемент
        stripped = part.strip()
        if stripped:
            result.append(int(stripped))
    return result
