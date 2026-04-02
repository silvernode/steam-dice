#!/usr/bin/env python3
import os
import sys
import random
import subprocess
import requests

# Run natively on Wayland if available, fall back to X11 otherwise
if os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QFont, QIcon

VERSION = "v0.1.0"

def _get_version():
    try:
        short = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return f"{VERSION}-{short}"
    except Exception:
        return VERSION


STEAM_API_KEY = "A2B1B59F6F16FA3CD3107378AE737C3D"
STEAM_ID = "76561198000382373"

IMG_W = 460
IMG_H = 215
MARGIN = 20
TITLE_H = 30
STATUS_H = 20
DICE_H = 100
SPACING = 12
WIN_W = IMG_W + MARGIN * 2
WIN_H = MARGIN + TITLE_H + SPACING + IMG_H + SPACING + STATUS_H + SPACING + DICE_H + MARGIN

DICE_FACES = "⚀⚁⚂⚃⚄⚅"

STYLE = """
    QMainWindow, QWidget {
        background-color: #1b2838;
    }
"""

DICE_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        color: #c7d5e0;
    }
    QPushButton:hover { color: #ffffff; }
    QPushButton:pressed { color: #888; }
    QPushButton:disabled { color: #4a5a6a; }
"""


class FetchLibraryThread(QThread):
    done = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            url = (
                "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
                f"?key={STEAM_API_KEY}&steamid={STEAM_ID}&include_appinfo=1&format=json"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            games = r.json()["response"].get("games", [])
            self.done.emit(games)
        except Exception as e:
            self.error.emit(str(e))


class FetchImageThread(QThread):
    done = pyqtSignal(QPixmap)

    def __init__(self, appid):
        super().__init__()
        self.appid = appid

    def run(self):
        url = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{self.appid}/header.jpg"
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            pixmap = QPixmap()
            pixmap.loadFromData(r.content)
            self.done.emit(pixmap)
        except Exception:
            self.done.emit(QPixmap())


class SteamDice(QMainWindow):
    def __init__(self):
        super().__init__()
        self.games = []
        self.image_thread = None

        self.setWindowTitle("Steam Dice")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(SPACING)

        # Game title
        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(13)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #c7d5e0;")
        self.title_label.setFixedHeight(TITLE_H)
        self.title_label.setVisible(False)
        layout.addWidget(self.title_label)

        # Game image
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setFixedSize(IMG_W, IMG_H)
        self.image_label.setStyleSheet("color: #8f98a0;")
        self.image_label.setVisible(False)
        layout.addWidget(self.image_label)

        # Status / loading text
        self.status_label = QLabel("Loading library…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setFixedHeight(STATUS_H)
        status_font = QFont()
        status_font.setPointSize(10)
        self.status_label.setFont(status_font)
        self.status_label.setStyleSheet("color: #8f98a0;")
        layout.addWidget(self.status_label)

        # Bottom row: version (left) | spacer | dice (center) | spacer (right)
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)

        version_label = QLabel(_get_version())
        ver_font = QFont()
        ver_font.setPointSize(8)
        version_label.setFont(ver_font)
        version_label.setStyleSheet("color: #4a5a6a;")
        version_label.setFixedWidth(80)
        bottom_row.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignBottom)

        bottom_row.addStretch()

        self.dice_btn = QPushButton("⚄")
        dice_font = QFont()
        dice_font.setPointSize(52)
        self.dice_btn.setFont(dice_font)
        self.dice_btn.setFixedSize(DICE_H, DICE_H)
        self.dice_btn.setStyleSheet(DICE_STYLE)
        self.dice_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dice_btn.setEnabled(False)
        self.dice_btn.clicked.connect(self.roll)
        bottom_row.addWidget(self.dice_btn)

        bottom_row.addStretch()
        bottom_row.addSpacing(80)  # mirror version label width to keep dice centered

        layout.addLayout(bottom_row)

        self._fetch_library()

    def _fetch_library(self):
        self.fetch_thread = FetchLibraryThread()
        self.fetch_thread.done.connect(self._on_library_loaded)
        self.fetch_thread.error.connect(self._on_library_error)
        self.fetch_thread.start()

    def _on_library_loaded(self, games):
        self.games = games
        self.status_label.setText(f"{len(games)} games — roll the dice!")
        self.dice_btn.setEnabled(True)

    def _on_library_error(self, msg):
        self.status_label.setText(f"Error loading library: {msg}")

    def roll(self):
        if not self.games:
            return

        game = random.choice(self.games)
        self.dice_btn.setText(random.choice(DICE_FACES))
        self.dice_btn.setEnabled(False)

        self.title_label.setText(game["name"])
        self.title_label.setVisible(True)
        self.image_label.setText("Loading…")
        self.image_label.setVisible(True)
        self.status_label.setText("")

        self.image_thread = FetchImageThread(game["appid"])
        self.image_thread.done.connect(self._on_image_loaded)
        self.image_thread.start()

    def _on_image_loaded(self, pixmap):
        self.dice_btn.setEnabled(True)
        if pixmap.isNull():
            self.image_label.setText("No image available")
        else:
            self.image_label.setPixmap(
                pixmap.scaled(
                    IMG_W, IMG_H,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon.fromTheme("codes.nora.gDiceRoller"))
    win = SteamDice()
    win.show()
    sys.exit(app.exec())
