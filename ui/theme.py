"""Темы оформления OlliDesk (светлая / тёмная)."""

LIGHT_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #ffffff;
    color: #000000;
}

QMenuBar {
    background-color: #f5f5f5;
    color: #000000;
}

QMenuBar::item:selected {
    background-color: #e0e0e0;
}

QMenu {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #d0d0d0;
}

QMenu::item:selected {
    background-color: #e3f2fd;
}

QLabel {
    color: #000000;
}

QComboBox {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #ccc;
    border-radius: 4px;
    padding: 4px;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #000000;
    selection-background-color: #e3f2fd;
}

QTextEdit {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #ccc;
    border-radius: 4px;
}

QListWidget {
    background-color: #ffffff;
    color: #000000;
    border: 1px solid #ccc;
    border-radius: 4px;
}

QPushButton {
    background-color: #e0e0e0;
    color: #000000;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
}

QPushButton:hover {
    background-color: #d0d0d0;
}

QStatusBar {
    background-color: #f5f5f5;
    color: #000000;
}

QProgressBar {
    background-color: #e0e0e0;
    border: none;
    border-radius: 4px;
    text-align: center;
}

QProgressBar::chunk {
    background-color: #1976d2;
    border-radius: 4px;
}

QSplitter::handle {
    background-color: #e0e0e0;
}

QTabWidget::pane {
    border: none;
    background-color: #ffffff;
}

QTabBar::tab {
    background-color: #f0f0f0;
    color: #000000;
    padding: 6px 16px;
    border: none;
}

QTabBar::tab:selected {
    background-color: #ffffff;
    border-bottom: 2px solid #1976d2;
}
"""

DARK_STYLESHEET = """
QMainWindow, QWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
}

QMenuBar {
    background-color: #2d2d2d;
    color: #d4d4d4;
}

QMenuBar::item:selected {
    background-color: #3e3e3e;
}

QMenu {
    background-color: #2d2d2d;
    color: #d4d4d4;
    border: 1px solid #3e3e3e;
}

QMenu::item:selected {
    background-color: #264f78;
}

QLabel {
    color: #d4d4d4;
}

QComboBox {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 4px;
    padding: 4px;
}

QComboBox QAbstractItemView {
    background-color: #2d2d2d;
    color: #d4d4d4;
    selection-background-color: #264f78;
}

QTextEdit {
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 4px;
}

QListWidget {
    background-color: #1e1e1e;
    color: #d4d4d4;
    border: 1px solid #555;
    border-radius: 4px;
}

QPushButton {
    background-color: #3c3c3c;
    color: #d4d4d4;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
}

QPushButton:hover {
    background-color: #4c4c4c;
}

QStatusBar {
    background-color: #2d2d2d;
    color: #d4d4d4;
}

QProgressBar {
    background-color: #3c3c3c;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: #d4d4d4;
}

QProgressBar::chunk {
    background-color: #4e9a06;
    border-radius: 4px;
}

QSplitter::handle {
    background-color: #3e3e3e;
}

QTabWidget::pane {
    border: none;
    background-color: #1e1e1e;
}

QTabBar::tab {
    background-color: #2d2d2d;
    color: #999;
    padding: 6px 16px;
    border: none;
}

QTabBar::tab:selected {
    background-color: #1e1e1e;
    color: #d4d4d4;
    border-bottom: 2px solid #4e9a06;
}
"""
