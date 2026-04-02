#!/usr/bin/env python3
import glob
import os
import re
import sys
import random
import subprocess
import requests

# Run natively on Wayland if available, fall back to X11 otherwise
if os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
from PyQt6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QComboBox
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
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


def _scan_installed_appids():
    """Return a set of appids currently installed across all Steam library folders."""
    steam_root = os.path.expanduser("~/.local/share/Steam")
    library_paths = [os.path.join(steam_root, "steamapps")]
    vdf_path = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf_path) as f:
            for path in re.findall(r'"path"\s+"([^"]+)"', f.read()):
                library_paths.append(os.path.join(path, "steamapps"))
    except Exception:
        pass
    installed = set()
    for lib in library_paths:
        for acf in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            m = re.search(r"appmanifest_(\d+)\.acf", acf)
            if m:
                installed.add(int(m.group(1)))
    return installed


STEAM_API_KEY = "A2B1B59F6F16FA3CD3107378AE737C3D"
STEAM_ID = "76561198000382373"

IMG_W = 460
IMG_H = 215
MARGIN = 20
TOP_ROW_H = 46        # refresh button (28) + gap (4) + cooldown label (14)
TITLE_H = 30
STATUS_H = 20
DICE_H = 100
SPACING = 12
PLAY_BTN_H = 34
REFRESH_COOLDOWN = 60  # seconds
WIN_W = IMG_W + MARGIN * 2
WIN_H = (MARGIN + TOP_ROW_H + SPACING + TITLE_H + SPACING
         + IMG_H + SPACING + PLAY_BTN_H + SPACING + STATUS_H + SPACING + DICE_H + MARGIN)

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

COMBO_STYLE = """
    QComboBox {
        background-color: #2a3f5f;
        color: #c6d4df;
        border: 1px solid #3d5a7a;
        border-radius: 4px;
        padding: 2px 8px;
        min-width: 115px;
    }
    QComboBox:hover { border-color: #5a8ab0; }
    QComboBox:disabled { color: #4a5a6a; border-color: #2a3a50; }
    QComboBox::drop-down { border: none; width: 20px; }
    QComboBox::down-arrow {
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 5px solid #c6d4df;
        width: 0; height: 0;
    }
    QComboBox QAbstractItemView {
        background-color: #2a3f5f;
        color: #c6d4df;
        selection-background-color: #3d6b9e;
        border: 1px solid #3d5a7a;
        outline: none;
    }
"""

REFRESH_STYLE = """
    QPushButton {
        background: transparent;
        border: none;
        padding: 2px;
        border-radius: 4px;
    }
    QPushButton:hover { background: rgba(255, 255, 255, 0.08); }
    QPushButton:pressed { background: rgba(255, 255, 255, 0.04); }
    QPushButton:disabled { opacity: 0.3; }
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
        self.all_games = []
        self.games = []
        self.installed_appids = set()
        self.image_thread = None
        self.cooldown_remaining = 0
        self.current_appid = None

        self.setWindowTitle("Steam Dice")
        self.setFixedSize(WIN_W, WIN_H)
        self.setStyleSheet(STYLE)

        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.setSpacing(SPACING)

        # Top row: filter dropdown (left) | stretch | refresh button (right)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All games", "Installed", "Not installed"])
        self.filter_combo.setFixedHeight(28)
        self.filter_combo.setStyleSheet(COMBO_STYLE)
        self.filter_combo.setEnabled(False)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        top_row.addWidget(self.filter_combo, alignment=Qt.AlignmentFlag.AlignTop)

        top_row.addStretch()

        refresh_col = QVBoxLayout()
        refresh_col.setSpacing(4)
        refresh_col.setContentsMargins(0, 0, 0, 0)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_btn.setIconSize(self.refresh_btn.sizeHint())
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setStyleSheet(REFRESH_STYLE)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Refresh game library")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self._refresh)
        refresh_col.addWidget(self.refresh_btn, alignment=Qt.AlignmentFlag.AlignRight)

        self.cooldown_label = QLabel()
        self.cooldown_label.setFixedHeight(14)
        cooldown_font = QFont()
        cooldown_font.setPointSize(8)
        self.cooldown_label.setFont(cooldown_font)
        self.cooldown_label.setStyleSheet("color: #4a5a6a;")
        self.cooldown_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.cooldown_label.setVisible(False)
        refresh_col.addWidget(self.cooldown_label)

        top_row.addLayout(refresh_col)
        layout.addLayout(top_row)

        self._cooldown_timer = QTimer()
        self._cooldown_timer.setInterval(1000)
        self._cooldown_timer.timeout.connect(self._on_cooldown_tick)

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

        # Play button
        self.play_btn = QPushButton("  Play")
        self.play_btn.setIcon(QIcon.fromTheme("media-playback-start"))
        play_font = QFont()
        play_font.setPointSize(11)
        play_font.setBold(True)
        self.play_btn.setFont(play_font)
        self.play_btn.setFixedHeight(34)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setStyleSheet("""
            QPushButton {
                background-color: #4c6b22;
                color: #c6d4df;
                border: none;
                border-radius: 4px;
                padding: 0 16px;
            }
            QPushButton:hover { background-color: #5a7d27; }
            QPushButton:pressed { background-color: #3d5620; }
        """)
        self.play_btn.setVisible(False)
        self.play_btn.clicked.connect(self._launch_game)
        layout.addWidget(self.play_btn)

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
        self.all_games = games
        self.installed_appids = _scan_installed_appids()
        self.filter_combo.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self._apply_filter()

    def _on_library_error(self, msg):
        self.status_label.setText(f"Error loading library: {msg}")
        self.refresh_btn.setEnabled(True)

    def _apply_filter(self):
        idx = self.filter_combo.currentIndex()
        if idx == 1:
            self.games = [g for g in self.all_games if g["appid"] in self.installed_appids]
        elif idx == 2:
            self.games = [g for g in self.all_games if g["appid"] not in self.installed_appids]
        else:
            self.games = list(self.all_games)
        count = len(self.games)
        if count:
            self.status_label.setText(f"{count} games — roll the dice!")
        else:
            self.status_label.setText("No games match this filter.")
        self.dice_btn.setEnabled(bool(self.games))

    def _refresh(self):
        self.refresh_btn.setEnabled(False)
        self.dice_btn.setEnabled(False)
        self.status_label.setText("Refreshing library…")
        self.cooldown_remaining = REFRESH_COOLDOWN
        self.cooldown_label.setText(f"{self.cooldown_remaining}s")
        self.cooldown_label.setVisible(True)
        self._cooldown_timer.start()
        self._fetch_library()

    def _on_cooldown_tick(self):
        self.cooldown_remaining -= 1
        if self.cooldown_remaining <= 0:
            self._cooldown_timer.stop()
            self.cooldown_label.setVisible(False)
            self.refresh_btn.setEnabled(True)
        else:
            self.cooldown_label.setText(f"{self.cooldown_remaining}s")

    def roll(self):
        if not self.games:
            return

        game = random.choice(self.games)
        self.current_appid = game["appid"]
        self.dice_btn.setText(random.choice(DICE_FACES))
        self.dice_btn.setEnabled(False)

        self.title_label.setText(game["name"])
        self.title_label.setVisible(True)
        self.image_label.setText("Loading…")
        self.image_label.setVisible(True)
        self.play_btn.setVisible(False)
        self.status_label.setText("")

        self.image_thread = FetchImageThread(game["appid"])
        self.image_thread.done.connect(self._on_image_loaded)
        self.image_thread.start()

    def _on_image_loaded(self, pixmap):
        self.dice_btn.setEnabled(True)
        self.play_btn.setVisible(True)
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

    def _launch_game(self):
        if self.current_appid is not None:
            subprocess.Popen(["xdg-open", f"steam://rungameid/{self.current_appid}"])


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon.fromTheme("codes.nora.gDiceRoller"))
    win = SteamDice()
    win.show()
    sys.exit(app.exec())
