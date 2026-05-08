#!/usr/bin/env python3
import glob
import json
import os
import re
import sys
import random
import subprocess
import requests
import keyring

# Run natively on Wayland if available, fall back to X11 otherwise
if os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QPushButton, QLabel, QComboBox, QDialog, QDialogButtonBox, QLineEdit,
                              QMessageBox)
from PyQt6.QtCore import Qt, QSettings, QThread, QTimer, pyqtSignal
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


# Steam's stable numeric genre IDs. Cross-referenced with the appdetails API.
# Unknown IDs (e.g. 34, 73, internal/deprecated) are silently dropped.
STEAM_GENRE_NAMES = {
    1: "Action", 2: "Strategy", 3: "RPG", 4: "Casual", 9: "Racing",
    18: "Sports", 23: "Indie", 25: "Adventure", 28: "Simulation",
    29: "Massively Multiplayer", 37: "Free To Play",
    51: "Animation & Modeling", 52: "Audio Production",
    53: "Design & Illustration", 54: "Education", 55: "Photo Editing",
    56: "Software Training", 57: "Utilities", 58: "Video Production",
    59: "Web Publishing", 60: "Game Development",
    70: "Early Access",
}

APPINFO_PATHS = [
    "~/.local/share/Steam/appcache/appinfo.vdf",
    "~/.steam/steam/appcache/appinfo.vdf",
    "~/.steam/root/appcache/appinfo.vdf",
]


def _load_taxonomy_from_appinfo(owned_appids, tags_table):
    """Read genre + store_tag data for owned games from Steam's local appinfo.vdf.
    Returns {appid_str: {"genres": [...], "tags": [...]}} or None if unavailable.
    `tags_table` is {tagid_str: name}; pass {} to skip tag translation."""
    try:
        from steam.utils.appcache import parse_appinfo
    except ImportError:
        return None
    wanted = set(owned_appids)
    for path in (os.path.expanduser(p) for p in APPINFO_PATHS):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as f:
                _, apps_iter = parse_appinfo(f, mapper=dict)
                result = {}
                for app in apps_iter:
                    appid = app.get("appid")
                    if appid not in wanted:
                        continue
                    common = (app.get("data", {})
                              .get("appinfo", {})
                              .get("common", {}))
                    genres = []
                    raw_g = common.get("genres") or {}
                    if isinstance(raw_g, dict):
                        for v in raw_g.values():
                            try:
                                gid = int(v)
                            except (TypeError, ValueError):
                                continue
                            name = STEAM_GENRE_NAMES.get(gid)
                            if name and name not in genres:
                                genres.append(name)
                    tags = []
                    raw_t = common.get("store_tags") or {}
                    if isinstance(raw_t, dict):
                        for v in raw_t.values():
                            try:
                                tid = int(v)
                            except (TypeError, ValueError):
                                continue
                            name = tags_table.get(str(tid))
                            if name and name not in tags:
                                tags.append(name)
                    result[str(appid)] = {"genres": genres, "tags": tags}
                return result
        except Exception:
            continue
    return None


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "steam-dice")


def _taxonomy_cache_path():
    return os.path.join(_cache_dir(), "taxonomy.json")


def _tags_table_path():
    return os.path.join(_cache_dir(), "tags.json")


def _load_taxonomy_cache():
    try:
        with open(_taxonomy_cache_path()) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _merge_taxonomy_into(target, source):
    """Merge {appid: {genres, tags}} entries; preserve non-empty fields per appid."""
    for aid, entry in source.items():
        if not isinstance(entry, dict):
            continue
        existing = target.get(aid)
        merged = dict(existing) if isinstance(existing, dict) else {}
        for k in ("genres", "tags"):
            new_val = entry.get(k)
            if new_val:
                merged[k] = new_val
            elif k not in merged:
                merged[k] = []
        target[aid] = merged


def _save_taxonomy_cache(cache):
    """Merge `cache` into the on-disk cache so concurrent writers don't shrink it."""
    path = _taxonomy_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    merged = _load_taxonomy_cache()
    _merge_taxonomy_into(merged, cache)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(merged, f)
    os.replace(tmp, path)


def _load_tags_table():
    """{tagid_str: tag_name} for translating store_tag IDs to readable names."""
    try:
        with open(_tags_table_path()) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_tags_table(table):
    path = _tags_table_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(table, f)
    os.replace(tmp, path)


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

    def __init__(self, api_key, steam_id):
        super().__init__()
        self.api_key = api_key
        self.steam_id = steam_id

    def run(self):
        try:
            url = (
                "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
                f"?key={self.api_key}&steamid={self.steam_id}&include_appinfo=1&format=json"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            games = r.json()["response"].get("games", [])
            self.done.emit(games)
        except Exception as e:
            msg = str(e).replace(self.api_key, "[REDACTED]")
            self.error.emit(msg)


class FetchTagsTableThread(QThread):
    """One-shot fetch of Steam's full tag-id → name table."""
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, api_key):
        super().__init__()
        self.api_key = api_key

    def run(self):
        try:
            url = (
                "https://api.steampowered.com/IStoreService/GetTagList/v1/"
                f"?key={self.api_key}&language=english"
            )
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            tags = r.json().get("response", {}).get("tags", [])
            table = {
                str(t["tagid"]): t["name"]
                for t in tags if "tagid" in t and "name" in t
            }
            self.done.emit(table)
        except Exception as e:
            msg = str(e).replace(self.api_key, "[REDACTED]")
            self.error.emit(msg)


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


class FetchGenresThread(QThread):
    progress = pyqtSignal(int, int, dict)  # done, total, cache snapshot
    finished_ok = pyqtSignal(dict)         # final cache

    REQUEST_INTERVAL_MS = 2000  # ~30 req/min, well under Steam's ~200/5min cap
    SAVE_EVERY = 10

    def __init__(self, appids, existing_cache):
        super().__init__()
        self.appids = list(appids)
        self.cache = dict(existing_cache)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        total = len(self.appids)
        for i, appid in enumerate(self.appids):
            if self._stop:
                break
            try:
                url = (
                    "https://store.steampowered.com/api/appdetails"
                    f"?appids={appid}&filters=genres"
                )
                r = requests.get(url, timeout=10)
                if r.status_code == 429:
                    self.msleep(60_000)
                    continue
                r.raise_for_status()
                entry = r.json().get(str(appid), {})
                genres = []
                if entry.get("success") and entry.get("data"):
                    genres = [
                        g["description"] for g in entry["data"].get("genres", [])
                    ]
                # Preserve any tags the appinfo path may have already populated.
                prev = self.cache.get(str(appid))
                prev_tags = prev.get("tags", []) if isinstance(prev, dict) else []
                self.cache[str(appid)] = {"genres": genres, "tags": prev_tags}
            except Exception:
                pass  # leave unfetched; retry on next session

            if (i + 1) % self.SAVE_EVERY == 0:
                _save_taxonomy_cache(self.cache)
            self.progress.emit(i + 1, total, dict(self.cache))
            self.msleep(self.REQUEST_INTERVAL_MS)

        _save_taxonomy_cache(self.cache)
        self.finished_ok.emit(self.cache)


class LazyComboBox(QComboBox):
    """QComboBox that intercepts the dropdown popup until permission is granted."""
    popup_blocked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._allow_popup = False

    def set_allow_popup(self, allow):
        self._allow_popup = allow

    def showPopup(self):
        if not self._allow_popup:
            self.popup_blocked.emit()
            return
        super().showPopup()


DIALOG_STYLE = """
    QDialog, QWidget { background-color: #1b2838; }
    QLabel { color: #c6d4df; }
    QLineEdit {
        background-color: #2a3f5f;
        color: #c6d4df;
        border: 1px solid #3d5a7a;
        border-radius: 4px;
        padding: 4px 8px;
    }
    QLineEdit:focus { border-color: #5a8ab0; }
    QPushButton {
        background-color: #2a3f5f;
        color: #c6d4df;
        border: 1px solid #3d5a7a;
        border-radius: 4px;
        padding: 4px 14px;
        min-width: 60px;
    }
    QPushButton:hover { background-color: #3d5a7a; }
    QPushButton:pressed { background-color: #1e3050; }
    a { color: #5a8ab0; }
"""


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Steam Dice — Settings")
        self.setModal(True)
        self.setFixedWidth(440)
        self.setStyleSheet(DIALOG_STYLE)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- API Key ---
        layout.addWidget(QLabel("<b>Steam API Key</b>"))

        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        settings = QSettings("butter", "steam-dice")
        self.key_edit = QLineEdit(keyring.get_password("steam-dice", "api_key") or "")
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste your 32-character key here…")
        key_row.addWidget(self.key_edit)

        show_btn = QPushButton()
        show_btn.setIcon(QIcon.fromTheme("password-show-on"))
        show_btn.setFixedSize(30, 30)
        show_btn.setCheckable(True)
        show_btn.setToolTip("Show / hide key")
        show_btn.toggled.connect(lambda on: self.key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        ))
        key_row.addWidget(show_btn)
        layout.addLayout(key_row)

        key_help = QLabel(
            'Get your free key at '
            '<a href="https://steamcommunity.com/dev/apikey">steamcommunity.com/dev/apikey</a>.'
            '<br>Log in with Steam, enter any domain name (e.g. <i>localhost</i>), and copy the key shown.'
        )
        key_help.setOpenExternalLinks(True)
        key_help.setWordWrap(True)
        key_help.setStyleSheet("color: #8f98a0; font-size: 9pt; padding-bottom: 10px;")
        layout.addWidget(key_help)

        # --- Steam ID ---
        layout.addWidget(QLabel("<b>Steam ID (64-bit)</b>"))
        self.id_edit = QLineEdit(settings.value("steam_id", ""))
        self.id_edit.setPlaceholderText("17-digit number only, e.g. 76561198000000000")
        layout.addWidget(self.id_edit)

        id_help = QLabel(
            'Paste the <b>17-digit number only</b> — not the full URL. '
            'Find it by opening your Steam profile in a browser: the number in '
            '<i>steamcommunity.com/profiles/<b>XXXXXXXXXXXXXXXXX</b></i> is your ID.<br>'
            'Using a custom profile URL? Look it up at '
            '<a href="https://steamid.io">steamid.io</a>.'
        )
        id_help.setOpenExternalLinks(True)
        id_help.setWordWrap(True)
        id_help.setStyleSheet("color: #8f98a0; font-size: 9pt; padding-bottom: 10px;")
        layout.addWidget(id_help)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self):
        api_key = self.key_edit.text().strip()
        steam_id = self.id_edit.text().strip()
        self.key_edit.setStyleSheet("")
        self.id_edit.setStyleSheet("")
        key_valid = bool(re.fullmatch(r"[0-9A-Fa-f]{32}", api_key))
        id_valid = bool(re.fullmatch(r"\d{17}", steam_id))
        if not key_valid or not id_valid:
            if not key_valid:
                self.key_edit.setStyleSheet("border: 1px solid #a04040;")
            if not id_valid:
                self.id_edit.setStyleSheet("border: 1px solid #a04040;")
            return
        keyring.set_password("steam-dice", "api_key", api_key)
        settings = QSettings("butter", "steam-dice")
        settings.remove("api_key")  # migrate away from plaintext storage
        settings.setValue("steam_id", steam_id)
        self.accept()


class SteamDice(QMainWindow):
    def __init__(self):
        super().__init__()
        self.all_games = []
        self.games = []
        self.installed_appids = set()
        self.image_thread = None
        self.cooldown_remaining = 0
        self.current_appid = None
        self.taxonomy_cache = _load_taxonomy_cache()
        self.tags_table = _load_tags_table()
        self.genres_thread = None
        self.tags_table_thread = None

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
        top_row.setSpacing(8)

        COMBO_W = 115

        def _combo_column(combo, sub_widget):
            """Combo with a 14px sub-row underneath (progress label or spacer)
            so all three filter columns share identical geometry."""
            col = QVBoxLayout()
            col.setSpacing(4)
            col.setContentsMargins(0, 0, 0, 0)
            combo.setFixedHeight(28)
            combo.setFixedWidth(COMBO_W)
            combo.setStyleSheet(COMBO_STYLE)
            col.addWidget(combo)
            sub_widget.setFixedHeight(14)
            col.addWidget(sub_widget)
            return col

        # Install-status filter
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All games", "Installed", "Not installed"])
        self.filter_combo.setEnabled(False)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        top_row.addLayout(_combo_column(self.filter_combo, QLabel()))

        # Genre filter (with progress sub-label for first-time fetch)
        self.genre_combo = LazyComboBox()
        self.genre_combo.addItem("All genres")
        self.genre_combo.setEnabled(False)
        self.genre_combo.currentIndexChanged.connect(self._apply_filter)
        self.genre_combo.popup_blocked.connect(self._prompt_genre_fetch)

        # Always visible (empty text when idle) so the column height stays the
        # same as the filter and tag columns — keeps all three combos aligned.
        self.genre_progress_label = QLabel()
        progress_font = QFont()
        progress_font.setPointSize(8)
        self.genre_progress_label.setFont(progress_font)
        self.genre_progress_label.setStyleSheet("color: #4a5a6a;")
        top_row.addLayout(_combo_column(self.genre_combo, self.genre_progress_label))

        # Tag filter (uses store_tags from appinfo.vdf)
        self.tag_combo = LazyComboBox()
        self.tag_combo.addItem("All tags")
        self.tag_combo.setEnabled(False)
        self.tag_combo.currentIndexChanged.connect(self._apply_filter)
        self.tag_combo.popup_blocked.connect(self._prompt_tags_fetch)
        top_row.addLayout(_combo_column(self.tag_combo, QLabel()))

        # When the cache already has data, allow the dropdowns to open immediately.
        if any(isinstance(e, dict) and e.get("genres") for e in self.taxonomy_cache.values()):
            self.genre_combo.set_allow_popup(True)
        if any(isinstance(e, dict) and e.get("tags") for e in self.taxonomy_cache.values()):
            self.tag_combo.set_allow_popup(True)

        top_row.addStretch()

        refresh_col = QVBoxLayout()
        refresh_col.setSpacing(4)
        refresh_col.setContentsMargins(0, 0, 0, 0)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        btn_row.setContentsMargins(0, 0, 0, 0)

        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(QIcon.fromTheme("configure"))
        self.settings_btn.setFixedSize(28, 28)
        self.settings_btn.setStyleSheet(REFRESH_STYLE)
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setToolTip("Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        btn_row.addWidget(self.settings_btn)

        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(QIcon.fromTheme("view-refresh"))
        self.refresh_btn.setFixedSize(28, 28)
        self.refresh_btn.setStyleSheet(REFRESH_STYLE)
        self.refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_btn.setToolTip("Refresh game library")
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.clicked.connect(self._refresh)
        btn_row.addWidget(self.refresh_btn)

        refresh_col.addLayout(btn_row)

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

        # Auto-open settings on first launch if credentials are missing
        s = QSettings("butter", "steam-dice")
        if not keyring.get_password("steam-dice", "api_key") or not s.value("steam_id"):
            QTimer.singleShot(0, self._open_settings)
        else:
            self._fetch_library()

    def _fetch_library(self):
        settings = QSettings("butter", "steam-dice")
        api_key = keyring.get_password("steam-dice", "api_key") or ""
        steam_id = settings.value("steam_id", "")
        if not api_key or not steam_id:
            self.status_label.setText("No credentials — click ⚙ to configure.")
            return
        if hasattr(self, "fetch_thread") and self.fetch_thread.isRunning():
            self.fetch_thread.done.disconnect()
            self.fetch_thread.error.disconnect()
        self.fetch_thread = FetchLibraryThread(api_key, steam_id)
        self.fetch_thread.done.connect(self._on_library_loaded)
        self.fetch_thread.error.connect(self._on_library_error)
        self.fetch_thread.start()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.status_label.setText("Loading library…")
            self.dice_btn.setEnabled(False)
            self.filter_combo.setEnabled(False)
            self.refresh_btn.setEnabled(False)
            self.cooldown_remaining = REFRESH_COOLDOWN
            self.cooldown_label.setText(f"{self.cooldown_remaining}s")
            self.cooldown_label.setVisible(True)
            self._cooldown_timer.start()
            self._fetch_library()

    def _on_library_loaded(self, games):
        self.all_games = games
        self.installed_appids = _scan_installed_appids()
        self.filter_combo.setEnabled(True)
        self.genre_combo.setEnabled(True)
        self.tag_combo.setEnabled(True)

        # Try Steam's local appinfo.vdf cache first — instant, no network.
        # Genres populate immediately even before the tag-id table is available;
        # tags require a tags_table for translation, so they're empty if it's missing.
        self._read_appinfo_into_cache()

        self._rebuild_genre_combo()
        self._rebuild_tag_combo()
        self.refresh_btn.setEnabled(True)
        self._apply_filter()

        # Fetch the tag-id → name table once if we don't have it yet, then re-read
        # appinfo so tag names land in the taxonomy cache.
        if not self.tags_table:
            self._fetch_tags_table()

    def _read_appinfo_into_cache(self):
        owned_ids = {g["appid"] for g in self.all_games}
        local = _load_taxonomy_from_appinfo(owned_ids, self.tags_table)
        if not local:
            return
        _merge_taxonomy_into(self.taxonomy_cache, local)
        try:
            _save_taxonomy_cache(self.taxonomy_cache)
        except OSError:
            pass
        if any(e.get("genres") for e in local.values()):
            self.genre_combo.set_allow_popup(True)
        if any(e.get("tags") for e in local.values()):
            self.tag_combo.set_allow_popup(True)

    def _fetch_tags_table(self):
        if self.tags_table_thread and self.tags_table_thread.isRunning():
            return
        api_key = keyring.get_password("steam-dice", "api_key") or ""
        if not api_key:
            return
        self.tags_table_thread = FetchTagsTableThread(api_key)
        self.tags_table_thread.done.connect(self._on_tags_table_loaded)
        self.tags_table_thread.error.connect(lambda _msg: None)  # silent; tags are optional
        self.tags_table_thread.start()

    def _on_tags_table_loaded(self, table):
        if not table:
            return
        self.tags_table = table
        try:
            _save_tags_table(table)
        except OSError:
            pass
        # Re-read appinfo now that we can translate tag IDs.
        self._read_appinfo_into_cache()
        self._rebuild_tag_combo()
        self._apply_filter()

    def _on_library_error(self, msg):
        self.status_label.setText(f"Error loading library: {msg}")
        self._cooldown_timer.stop()
        self.cooldown_label.setVisible(False)
        self.refresh_btn.setEnabled(True)

    def _apply_filter(self):
        idx = self.filter_combo.currentIndex()
        if idx == 1:
            games = [g for g in self.all_games if g["appid"] in self.installed_appids]
        elif idx == 2:
            games = [g for g in self.all_games if g["appid"] not in self.installed_appids]
        else:
            games = list(self.all_games)

        genre = self.genre_combo.currentData()
        if genre:
            games = [
                g for g in games
                if genre in self.taxonomy_cache.get(str(g["appid"]), {}).get("genres", [])
            ]

        tag = self.tag_combo.currentData()
        if tag:
            games = [
                g for g in games
                if tag in self.taxonomy_cache.get(str(g["appid"]), {}).get("tags", [])
            ]

        self.games = games
        count = len(self.games)
        if count:
            self.status_label.setText(f"{count} games — roll the dice!")
        else:
            self.status_label.setText("No games match this filter.")
        self.dice_btn.setEnabled(bool(self.games))

    def _rebuild_genre_combo(self):
        """Rebuild the genre dropdown from the current cache, preserving selection."""
        current = self.genre_combo.currentData()
        all_genres = sorted({
            g for entry in self.taxonomy_cache.values()
            if isinstance(entry, dict)
            for g in entry.get("genres", [])
        })
        self.genre_combo.blockSignals(True)
        self.genre_combo.clear()
        self.genre_combo.addItem("All genres", None)
        for g in all_genres:
            self.genre_combo.addItem(g, g)
        if current:
            idx = self.genre_combo.findData(current)
            if idx >= 0:
                self.genre_combo.setCurrentIndex(idx)
        self.genre_combo.blockSignals(False)

    def _rebuild_tag_combo(self):
        """Rebuild the tag dropdown from the current cache, preserving selection."""
        current = self.tag_combo.currentData()
        all_tags = sorted({
            t for entry in self.taxonomy_cache.values()
            if isinstance(entry, dict)
            for t in entry.get("tags", [])
        })
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem("All tags", None)
        for t in all_tags:
            self.tag_combo.addItem(t, t)
        if current:
            idx = self.tag_combo.findData(current)
            if idx >= 0:
                self.tag_combo.setCurrentIndex(idx)
        self.tag_combo.blockSignals(False)

    def _prompt_genre_fetch(self):
        if self.genres_thread and self.genres_thread.isRunning():
            return
        if not self.all_games:
            QMessageBox.information(
                self, "Steam Dice",
                "Library is still loading — try again in a moment."
            )
            return
        missing = [g["appid"] for g in self.all_games if str(g["appid"]) not in self.taxonomy_cache]
        if not missing:
            self.genre_combo.set_allow_popup(True)
            self.genre_combo.showPopup()
            return

        seconds = (len(missing) * FetchGenresThread.REQUEST_INTERVAL_MS) // 1000
        minutes = seconds // 60
        eta = f"~{minutes} min" if minutes >= 1 else f"~{seconds} sec"
        reply = QMessageBox.question(
            self, "Load genres from Steam?",
            f"Genre filtering needs to fetch genre data for {len(missing)} games "
            f"from Steam's store API.\n\n"
            f"This takes {eta} (rate-limited to one request every "
            f"{FetchGenresThread.REQUEST_INTERVAL_MS // 1000}s) and is cached "
            f"afterward. The app stays usable while it runs.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.genres_thread = FetchGenresThread(missing, self.taxonomy_cache)
        self.genres_thread.progress.connect(self._on_genres_progress)
        self.genres_thread.finished_ok.connect(self._on_genres_done)
        self.genre_combo.set_allow_popup(True)
        self.genre_progress_label.setText(f"0 / {len(missing)}")
        self.genres_thread.start()

    def _prompt_tags_fetch(self):
        # The tag combo only has data when both the tag-id table AND appinfo.vdf
        # are available. There's no API fallback for tags, so just explain the
        # situation if we can't populate it.
        try:
            from steam.utils.appcache import parse_appinfo  # noqa: F401
            has_steam = True
        except ImportError:
            has_steam = False

        if not has_steam:
            QMessageBox.information(
                self, "Tag filtering unavailable",
                "Tag filtering reads Steam's local appinfo.vdf cache via the "
                "<i>python-steam</i> package, which isn't installed.<br><br>"
                "Install it (e.g. <code>pacman -S python-steam</code>) and "
                "restart Steam Dice to enable this filter."
            )
            return

        if not self.tags_table:
            if self.tags_table_thread and self.tags_table_thread.isRunning():
                QMessageBox.information(
                    self, "Steam Dice",
                    "Loading tag list from Steam — try again in a moment."
                )
            else:
                self._fetch_tags_table()
            return

        # Tags table is loaded but cache is empty — likely the appinfo.vdf path
        # didn't exist when we tried earlier.
        QMessageBox.information(
            self, "Steam Dice",
            "No tag data found in Steam's local cache. Launch Steam at least "
            "once to populate appinfo.vdf, then refresh."
        )

    def _on_genres_progress(self, done, total, cache_snapshot):
        _merge_taxonomy_into(self.taxonomy_cache, cache_snapshot)
        self.genre_progress_label.setText(f"{done} / {total}")
        # Refresh the dropdown periodically as new genres are discovered.
        if done % FetchGenresThread.SAVE_EVERY == 0:
            self._rebuild_genre_combo()

    def _on_genres_done(self, cache):
        _merge_taxonomy_into(self.taxonomy_cache, cache)
        self.genre_progress_label.setText("")
        self._rebuild_genre_combo()
        self._apply_filter()

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

        if self.image_thread is not None:
            self.image_thread.done.disconnect()
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

    def closeEvent(self, a0):
        if self.genres_thread and self.genres_thread.isRunning():
            self.genres_thread.stop()
            self.genres_thread.wait(12000)  # cover in-flight 10s HTTP timeout + final save
        if self.tags_table_thread and self.tags_table_thread.isRunning():
            self.tags_table_thread.wait(15000)
        super().closeEvent(a0)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setDesktopFileName("io.github.silvernode.SteamDice")
    icon = QIcon.fromTheme("io.github.silvernode.SteamDice")
    if icon.isNull():
        local_icon = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "io.github.silvernode.SteamDice.svg",
        )
        if os.path.exists(local_icon):
            icon = QIcon(local_icon)
    app.setWindowIcon(icon)
    win = SteamDice()
    win.show()
    sys.exit(app.exec())
