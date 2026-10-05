"""Версия приложения — единственный источник для кода.

`pyproject.toml` остаётся источником истины для релизов, а эта копия нужна,
чтобы версия была доступна всегда: и при запуске из исходников без установки
пакета, и внутри собранного PyInstaller-бинарника, где метаданные пакета
могут не попасть в сборку.

Строка `__version__` обновляется `bump-my-version` вместе с `pyproject.toml`
(см. `[tool.bumpversion]` и `docs/VERSION.md`) — руками её не менять.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

__version__ = "0.1.2"


def get_version() -> str:
    """Возвращает версию для `QApplication.setApplicationVersion`.

    Returns:
        Версию из метаданных установленного пакета `ollidesk`, если пакет
        установлен. Иначе — значение `__version__` из этого файла, которое
        `bump-my-version` держит в синхроне с `pyproject.toml`.
    """
    try:
        return _distribution_version("ollidesk")
    except PackageNotFoundError:
        return __version__
