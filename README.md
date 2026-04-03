# Steam Dice

A small desktop app that picks a random game from your Steam library and lets you launch it directly. Stop staring at your backlog — let the dice decide.

![Steam Dice screenshot placeholder](https://cdn.cloudflare.steamstatic.com/steam/apps/400/header.jpg)

## Features

- Rolls a random game from your Steam library and displays its header art
- **Installed / Not installed / All** filter so you can limit rolls to games you can actually play right now
- **Play** button launches the selected game immediately via Steam
- Settings dialog for your API key and Steam ID, persisted across sessions
- Refresh button with a 60-second cooldown to avoid hammering the Steam API
- Clean Steam-themed dark UI built with PyQt6
- Wayland-native with X11 fallback

## Requirements

- Python 3.8+
- [PyQt6](https://pypi.org/project/PyQt6/)
- [requests](https://pypi.org/project/requests/)
- Steam installed locally (for the "Installed" filter and launching games)

Install dependencies with pip:

```bash
pip install PyQt6 requests
```

Or with your distro's package manager, e.g. on Void Linux:

```bash
xbps-install python3-PyQt6 python3-requests
```

## Setup

### 1. Steam API Key

You need a free Steam Web API key to fetch your library.

1. Go to [steamcommunity.com/dev/apikey](https://steamcommunity.com/dev/apikey) and log in.
2. Enter any domain name (e.g. `localhost`) and click **Register**.
3. Copy the 32-character key shown on the page.

### 2. Steam ID (64-bit)

Your Steam ID is the 17-digit number in your profile URL:

```
steamcommunity.com/profiles/76561198000000000
                             ^^^^^^^^^^^^^^^^^
                             this is your ID
```

If you use a custom profile URL (e.g. `steamcommunity.com/id/yourname`), look up your numeric ID at [steamid.io](https://steamid.io).

## Usage

```bash
python steam_dice.py
```

On first launch the settings dialog will open automatically. Enter your API key and Steam ID, then click **Save**. Your library loads in the background.

Once loaded:

- Click the **dice** button to roll a random game
- Use the **filter dropdown** to restrict rolls to installed or uninstalled games
- Click **Play** to launch the rolled game via Steam
- Click the **refresh** button (⟳) to re-fetch your library (60s cooldown applies)
- Click the **settings** button (⚙) to update your credentials at any time

## License

Steam Dice is free software released under the [GNU General Public License v2.0](LICENSE).
