#!/usr/bin/env python3

#for systems that use NANO ONLY! ( which is usually in Linux )

import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QPoint, QSize
from PyQt6.QtGui import QGuiApplication, QIcon, QFont, QColor
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame, QFileDialog, QMessageBox,
    QGraphicsDropShadowEffect, QStackedWidget, QSizePolicy, QCheckBox
)

RESIZE_MARGIN = 6  # px, hot-zone around the window edge used for resizing

# Folders that are usually huge and irrelevant to a manual file search.
# Skipping these is what actually makes scanning fast (walking all of
# AppData/node_modules/.git otherwise dwarfs everything else in time).
JUNK_DIRS = {
    "appdata", "node_modules", ".git", ".svn", ".hg", "__pycache__",
    "$recycle.bin", "system volume information", ".cache", ".cargo",
    "venv", ".venv", "env", ".idea", ".vscode", "site-packages",
    "windows", "programdata", "program files", "program files (x86)",
    ".npm", ".gradle", "recycle bin",
}


# --------------------------------------------------------------------------- #
# Background search worker
# --------------------------------------------------------------------------- #
class SearchWorker(QThread):
    match_found = pyqtSignal(str, str)   # filename, full_path -- emitted live
    status = pyqtSignal(str)
    finished_scan = pyqtSignal(int)      # total files scanned

    def __init__(self, root: str, query: str, skip_junk: bool = True, recursive: bool = True):
        super().__init__()
        self.root = root
        self.query = query.lower()
        self.skip_junk = skip_junk
        self.recursive = recursive
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        scanned_dirs = 0
        scanned_files = 0

        if not self.recursive:
            # Shallow search: only the chosen folder itself. Near-instant.
            try:
                with os.scandir(self.root) as it:
                    for entry in it:
                        if self._stop:
                            return
                        if entry.is_file(follow_symlinks=False):
                            scanned_files += 1
                            if self.query in entry.name.lower():
                                self.match_found.emit(entry.name, entry.path)
            except (PermissionError, FileNotFoundError, OSError):
                pass
            self.finished_scan.emit(scanned_files)
            return

        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True, onerror=lambda e: None):
            if self._stop:
                return

            if self.skip_junk:
                dirnames[:] = [
                    d for d in dirnames
                    if d.lower() not in JUNK_DIRS and not d.startswith(".")
                ]

            for f in filenames:
                scanned_files += 1
                if self.query in f.lower():
                    self.match_found.emit(f, os.path.join(dirpath, f))

            scanned_dirs += 1
            if scanned_dirs % 25 == 0:
                self.status.emit(f"Scanned {scanned_files} files… {dirpath}")

        self.finished_scan.emit(scanned_files)


# --------------------------------------------------------------------------- #
# Helper: turn a full path into a readable breadcrumb sentence
# --------------------------------------------------------------------------- #
def breadcrumb(path: str) -> str:
    p = Path(path)
    name = p.name
    parents = list(p.parent.parts)
    # Drop drive/root noise like "C:\\" or "/" for readability, keep the rest
    parents = [part.strip("\\/") for part in parents if part.strip("\\/")]
    parents_reversed = list(reversed(parents))
    if not parents_reversed:
        return name
    phrase = f"{name} from {parents_reversed[0]}"
    for part in parents_reversed[1:]:
        phrase += f" in {part}"
    return phrase


# --------------------------------------------------------------------------- #
# Custom title bar
# --------------------------------------------------------------------------- #
class TitleBar(QWidget):
    def __init__(self, parent_window):
        super().__init__()
        self.win = parent_window
        self.setFixedHeight(42)
        self.setObjectName("TitleBar")
        self._drag_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 8, 0)
        layout.setSpacing(8)

        icon = QLabel("🔎")
        icon.setStyleSheet("font-size: 16px;")
        title = QLabel("File Finder")
        title.setObjectName("TitleLabel")

        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addStretch()

        self.btn_min = QPushButton("—")
        self.btn_max = QPushButton("❐")
        self.btn_close = QPushButton("✕")

        for b, name in ((self.btn_min, "MinBtn"), (self.btn_max, "MaxBtn"), (self.btn_close, "CloseBtn")):
            b.setObjectName(name)
            b.setFixedSize(34, 28)
            layout.addWidget(b)

        self.btn_min.clicked.connect(self.win.showMinimized)
        self.btn_max.clicked.connect(self.toggle_max_restore)
        self.btn_close.clicked.connect(self.win.close)

    def toggle_max_restore(self):
        if self.win.isMaximized():
            self.win.showNormal()
            self.btn_max.setText("❐")
        else:
            self.win.showMaximized()
            self.btn_max.setText("❒")

    # ---- window dragging ----
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self.win.isMaximized():
                self.win.showNormal()
                self.btn_max.setText("❐")
            self.win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self.toggle_max_restore()


# --------------------------------------------------------------------------- #
# Result row widget (a clickable "card")
# --------------------------------------------------------------------------- #
class ResultCard(QPushButton):
    def __init__(self, title, subtitle=""):
        super().__init__()
        self.setObjectName("ResultCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(56)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("CardTitle")
        lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("CardSubtitle")
            lay.addWidget(s)


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class FileFinder(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(560, 640)
        self.setMinimumSize(420, 480)

        desktop = Path.home() / "Desktop"
        self.search_root = str(desktop) if desktop.exists() else str(Path.home())
        self.worker = None
        self.matches_cache = {}      # filename -> [paths]
        self.result_cards = {}       # filename -> ResultCard (for live updates)
        self.selected_path = None
        self._resize_dir = None

        self._build_ui()
        self._apply_styles()
        self.setMouseTracking(True)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)  # room for drop shadow
        outer.setSpacing(0)

        self.card = QFrame()
        self.card.setObjectName("Card")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.title_bar = TitleBar(self)
        card_layout.addWidget(self.title_bar)

        self.stack = QStackedWidget()
        card_layout.addWidget(self.stack, 1)

        self.stack.addWidget(self._build_search_page())
        self.stack.addWidget(self._build_disambiguation_page())
        self.stack.addWidget(self._build_action_page())

        outer.addWidget(self.card)
        self.setMouseTracking(True)
        self.card.setMouseTracking(True)

    def _build_search_page(self):
        page = QWidget()
        page.setMouseTracking(True)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(12)

        header = QLabel("Find a file")
        header.setObjectName("Header")
        lay.addWidget(header)

        row = QHBoxLayout()
        self.folder_btn = QPushButton(f"📁 {self._short(self.search_root)}")
        self.folder_btn.setObjectName("FolderBtn")
        self.folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_btn.clicked.connect(self.choose_folder)
        row.addWidget(self.folder_btn)
        lay.addLayout(row)

        search_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Type a file name, e.g. file4")
        self.search_input.returnPressed.connect(self.run_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.setObjectName("PrimaryBtn")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.clicked.connect(self.on_search_button)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_btn)
        lay.addLayout(search_row)

        options_row = QHBoxLayout()
        self.chk_skip_junk = QCheckBox("Skip junk folders (AppData, node_modules, .git…) — faster")
        self.chk_skip_junk.setChecked(True)
        self.chk_recursive = QCheckBox("Include subfolders")
        self.chk_recursive.setChecked(True)
        options_row.addWidget(self.chk_skip_junk)
        lay.addLayout(options_row)
        options_row2 = QHBoxLayout()
        options_row2.addWidget(self.chk_recursive)
        lay.addLayout(options_row2)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Status")
        lay.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("ResultsScroll")
        self.results_container = QWidget()
        self.results_layout = QVBoxLayout(self.results_container)
        self.results_layout.setSpacing(8)
        self.results_layout.addStretch()
        scroll.setWidget(self.results_container)
        lay.addWidget(scroll, 1)

        return page

    def _build_disambiguation_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(12)

        top = QHBoxLayout()
        back = QPushButton("← Back")
        back.setObjectName("GhostBtn")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        top.addWidget(back)
        top.addStretch()
        lay.addLayout(top)

        self.disambig_header = QLabel("Which one is it?")
        self.disambig_header.setObjectName("Header")
        lay.addWidget(self.disambig_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("ResultsScroll")
        self.disambig_container = QWidget()
        self.disambig_layout = QVBoxLayout(self.disambig_container)
        self.disambig_layout.setSpacing(8)
        self.disambig_layout.addStretch()
        scroll.setWidget(self.disambig_container)
        lay.addWidget(scroll, 1)

        return page

    def _build_action_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 18, 20, 20)
        lay.setSpacing(14)

        top = QHBoxLayout()
        back = QPushButton("← Back")
        back.setObjectName("GhostBtn")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        top.addWidget(back)
        top.addStretch()
        lay.addLayout(top)

        header = QLabel("Found it!")
        header.setObjectName("Header")
        lay.addWidget(header)

        self.found_path_label = QLabel("")
        self.found_path_label.setObjectName("FoundPath")
        self.found_path_label.setWordWrap(True)
        lay.addWidget(self.found_path_label)

        lay.addSpacing(6)
        q = QLabel("What would you like to do?")
        q.setObjectName("Sub")
        lay.addWidget(q)

        btn_open_loc = QPushButton("📂  Open file location")
        btn_open_file = QPushButton("📄  Open file")
        btn_copy_path = QPushButton("📋  Copy file path")
        btn_delete = QPushButton("🗑️  Delete file")
        btn_delete.setObjectName("DangerBtn")

        for b in (btn_open_loc, btn_open_file, btn_copy_path, btn_delete):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(44)
            lay.addWidget(b)

        btn_open_loc.clicked.connect(self.open_file_location)
        btn_open_file.clicked.connect(self.open_file)
        btn_copy_path.clicked.connect(self.copy_path)
        btn_delete.clicked.connect(self.delete_file)

        self.action_status = QLabel("")
        self.action_status.setObjectName("Status")
        lay.addWidget(self.action_status)

        lay.addStretch()
        return page

    def _short(self, path, limit=40):
        return path if len(path) <= limit else "…" + path[-(limit - 1):]

    # -------------------------------------------------------------- styling
    def _apply_styles(self):
        self.setStyleSheet("""
            #Card {
                background-color: #1b1d27;
                border-radius: 14px;
                border: 1px solid #2c2f3d;
            }
            #TitleBar {
                background-color: #14151d;
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }
            #TitleLabel { color: #e5e6ee; font-size: 13px; font-weight: 600; }
            QPushButton#MinBtn, QPushButton#MaxBtn, QPushButton#CloseBtn {
                background: transparent; color: #9a9db3; border: none; border-radius: 6px; font-size: 13px;
            }
            QPushButton#MinBtn:hover, QPushButton#MaxBtn:hover { background: #2a2d3c; color: #ffffff; }
            QPushButton#CloseBtn:hover { background: #e5484d; color: #ffffff; }

            QWidget { color: #e5e6ee; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
            #Header { font-size: 19px; font-weight: 700; color: #ffffff; }
            #Sub { color: #a7aac0; font-size: 13px; }
            #Status { color: #7b7fa6; font-size: 12px; }
            #FoundPath { color: #8be9fd; font-size: 13px; background: #14151d; padding: 10px 12px; border-radius: 8px; }

            QLineEdit {
                background-color: #14151d; border: 1px solid #2c2f3d; border-radius: 8px;
                padding: 10px 12px; color: #ffffff;
            }
            QLineEdit:focus { border: 1px solid #7c5cff; }

            QPushButton {
                background-color: #262838; border: 1px solid #33364a; border-radius: 8px;
                padding: 8px 14px; color: #e5e6ee;
            }
            QPushButton:hover { background-color: #313349; }
            QPushButton:pressed { background-color: #222430; }

            QPushButton#PrimaryBtn { background-color: #7c5cff; border: none; font-weight: 600; color: #ffffff; }
            QPushButton#PrimaryBtn:hover { background-color: #8f72ff; }

            QPushButton#GhostBtn { background: transparent; border: none; color: #9a9db3; }
            QPushButton#GhostBtn:hover { color: #ffffff; }

            QPushButton#FolderBtn { text-align: left; color: #a7aac0; }

            QPushButton#DangerBtn { background-color: #3a1c22; border: 1px solid #6b2530; color: #ff8a95; }
            QPushButton#DangerBtn:hover { background-color: #5a2530; }

            QPushButton#ResultCard { text-align: left; background-color: #20222f; border: 1px solid #2c2f3d; }
            QPushButton#ResultCard:hover { background-color: #272a3a; border: 1px solid #7c5cff; }
            #CardTitle { font-size: 14px; font-weight: 600; color: #ffffff; }
            #CardSubtitle { font-size: 11px; color: #8b8ea8; }

            QScrollArea { border: none; background: transparent; }
            QScrollBar:vertical { background: transparent; width: 8px; }
            QScrollBar::handle:vertical { background: #33364a; border-radius: 4px; }
        """)

    # ------------------------------------------------------------ behaviour
    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose folder to search", self.search_root)
        if folder:
            self.search_root = folder
            self.folder_btn.setText(f"📁 {self._short(folder)}")

    def on_search_button(self):
        if self.worker and self.worker.isRunning():
            self.stop_search()
        else:
            self.run_search()

    def run_search(self):
        query = self.search_input.text().strip()
        if not query:
            return

        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()

        self._clear_layout(self.results_layout)
        self.matches_cache = {}
        self.result_cards = {}
        self.status_label.setText("Searching…")
        self.search_btn.setText("Stop")
        self.folder_btn.setEnabled(False)

        self.worker = SearchWorker(
            self.search_root, query,
            skip_junk=self.chk_skip_junk.isChecked(),
            recursive=self.chk_recursive.isChecked(),
        )
        self.worker.match_found.connect(self.on_match_found)
        self.worker.status.connect(lambda s: self.status_label.setText(s))
        self.worker.finished_scan.connect(self.on_scan_finished)
        self.worker.start()

    def stop_search(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
        self.search_btn.setText("Search")
        self.folder_btn.setEnabled(True)
        count = len(self.matches_cache)
        self.status_label.setText(f"Stopped. Found {count} matching name(s) so far.")

    def on_match_found(self, name: str, path: str):
        self.matches_cache.setdefault(name, []).append(path)
        paths = self.matches_cache[name]
        subtitle = "1 location" if len(paths) == 1 else f"{len(paths)} locations — which one is it?"

        if name in self.result_cards:
            # Update the existing card's subtitle (more locations found for it)
            card = self.result_cards[name]
            sub_label = card.findChild(QLabel, "CardSubtitle")
            if sub_label:
                sub_label.setText(subtitle)
        else:
            card = ResultCard(name, subtitle)
            card.clicked.connect(lambda checked=False, n=name: self.pick_name(n))
            self.result_cards[name] = card
            self.results_layout.insertWidget(self.results_layout.count() - 1, card)
            self.status_label.setText(f"Found {len(self.matches_cache)} matching name(s)… still searching")

    def on_scan_finished(self, scanned_files: int):
        self.search_btn.setText("Search")
        self.folder_btn.setEnabled(True)
        if not self.matches_cache:
            self.status_label.setText(f"No files found. ({scanned_files} files scanned)")
        else:
            self.status_label.setText(
                f"Found {len(self.matches_cache)} matching name(s). ({scanned_files} files scanned)"
            )

    def pick_name(self, name):
        paths = self.matches_cache.get(name, [])
        if len(paths) == 1:
            self.selected_path = paths[0]
            self.show_action_page()
        elif len(paths) > 1:
            self.show_disambiguation(name, paths)

    def show_disambiguation(self, name, paths):
        self.disambig_header.setText(f"Which one is it, “{name}”?")
        self._clear_layout(self.disambig_layout)
        for path in paths:
            card = ResultCard(breadcrumb(path), path)
            card.clicked.connect(lambda checked=False, p=path: self.pick_path(p))
            self.disambig_layout.insertWidget(self.disambig_layout.count() - 1, card)
        self.stack.setCurrentIndex(1)

    def pick_path(self, path):
        self.selected_path = path
        self.show_action_page()

    def show_action_page(self):
        self.found_path_label.setText(self.selected_path)
        self.action_status.setText("")
        self.stack.setCurrentIndex(2)

    def _clear_layout(self, layout):
        while layout.count() > 1:
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    # -------------------------------------------------------------- actions
    def open_file_location(self):
        path = self.selected_path
        if not path or not os.path.exists(path):
            self.action_status.setText("File no longer exists.")
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(f'explorer /select,"{os.path.normpath(path)}"')
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", path])
            else:
                subprocess.run(["xdg-open", os.path.dirname(path)])
            self.action_status.setText("Opened file location.")
        except Exception as e:
            self.action_status.setText(f"Couldn't open location: {e}")

    def open_file(self):
        path = self.selected_path
        if not path or not os.path.exists(path):
            self.action_status.setText("File no longer exists.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
            self.action_status.setText("Opened file.")
        except Exception as e:
            self.action_status.setText(f"Couldn't open file: {e}")

    def copy_path(self):
        if self.selected_path:
            QGuiApplication.clipboard().setText(self.selected_path)
            self.action_status.setText("Path copied to clipboard.")

    def delete_file(self):
        path = self.selected_path
        if not path or not os.path.exists(path):
            self.action_status.setText("File no longer exists.")
            return
        reply = QMessageBox.question(
            self, "Delete file",
            f"Are you sure you want to permanently delete:\n{path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(path)
                self.action_status.setText("File deleted.")
            except Exception as e:
                self.action_status.setText(f"Couldn't delete file: {e}")

    # --------------------------------------------------- frameless resizing
    def _edge_at(self, pos: QPoint):
        rect = self.rect()
        left = pos.x() <= RESIZE_MARGIN
        right = pos.x() >= rect.width() - RESIZE_MARGIN
        top = pos.y() <= RESIZE_MARGIN
        bottom = pos.y() >= rect.height() - RESIZE_MARGIN
        return left, top, right, bottom

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            left, top, right, bottom = self._edge_at(event.position().toPoint())
            if any((left, top, right, bottom)):
                self._resize_dir = (left, top, right, bottom)
                self._resize_start_geo = self.geometry()
                self._resize_start_pos = event.globalPosition().toPoint()
        event.accept()

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if event.buttons() & Qt.MouseButton.LeftButton and self._resize_dir:
            left, top, right, bottom = self._resize_dir
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = self._resize_start_geo
            x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
            if right:
                w = max(self.minimumWidth(), geo.width() + delta.x())
            if bottom:
                h = max(self.minimumHeight(), geo.height() + delta.y())
            if left:
                new_w = max(self.minimumWidth(), geo.width() - delta.x())
                x = geo.x() + (geo.width() - new_w)
                w = new_w
            if top:
                new_h = max(self.minimumHeight(), geo.height() - delta.y())
                y = geo.y() + (geo.height() - new_h)
                h = new_h
            self.setGeometry(x, y, w, h)
        else:
            left, top, right, bottom = self._edge_at(pos)
            if (left and top) or (right and bottom):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif (right and top) or (left and bottom):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif left or right:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif top or bottom:
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._resize_dir = None
        event.accept()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(1500)
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("File Finder")
    window = FileFinder()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
