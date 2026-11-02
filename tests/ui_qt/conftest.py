"""Тесты интерфейса идут только если PySide6 установлен и есть на чём рисовать."""

from __future__ import annotations

import gc
import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QApplication

from alchimist.services import build_app


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def window_settings(tmp_path, monkeypatch):
    """Геометрия окна — во временный файл, а не в настоящий конфиг пользователя.

    Окно при закрытии запоминает свой размер. Без подмены тесты писали его в
    `~/.config/AlchimistWorld` (на Windows — в реестр), а следующий тест
    восстанавливал оттуда чужой размер окна.
    """
    from alchimist.ui_qt.main_window import MainWindow

    path = str(tmp_path / "window.ini")
    monkeypatch.setattr(
        MainWindow, "_settings", lambda self: QSettings(path, QSettings.Format.IniFormat)
    )


@pytest.fixture(autouse=True)
def clean_widgets(qapp):
    """Не оставлять живых виджетов между тестами.

    Смена темы перекрашивает все виджеты приложения, и накопившиеся от прошлых
    тестов окна превращают пару сравнений цвета в десятки секунд.
    """
    yield
    for widget in list(qapp.topLevelWidgets()):
        widget.close()
        widget.deleteLater()
    qapp.processEvents()
    # deleteLater откладывает удаление до возврата в цикл событий, которого в
    # тестах нет: без этого виджеты копятся от теста к тесту.
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


@pytest.fixture
def ui_app(tmp_path, catalog, qapp):
    service = build_app(tmp_path)
    service.catalog.replace_all(catalog)
    return service


@pytest.fixture
def play_as(ui_app):
    """Сделать активным игрока, который знает все рецепты справочника (FR-14.5)."""
    from alchimist.core.models import Kit

    def play(name: str = "Гримли", kits: tuple = (Kit.ALCHEMIST,)):
        return ui_app.create_character(
            name, kits, known=ui_app.catalog.recipe_ids(), seed_catalog=False
        )

    return play


@pytest.fixture
def window(ui_app, qapp):
    from alchimist.ui_qt.main_window import MainWindow

    win = MainWindow(ui_app)
    # Окно показывается, иначе страницы считают себя скрытыми и не перерисовываются.
    win.show()
    # Размер — как у окна по умолчанию: экран offscreen всего 800 px, и при показе
    # окно ужимается до него, а тогда, например, кнопки строк «Могу сварить»
    # уезжают за видимую часть списка.
    win.resize(1180, 760)
    qapp.processEvents()
    yield win
    win.close()
