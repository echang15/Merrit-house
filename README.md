# Merritt House · On Tap

A touchscreen beer board for the kitchen kegerator. It runs on a Raspberry Pi 4
with an official Raspberry Pi touchscreen (Touch Display 2 in 5", 7" or 10", or
the original 7") and shows what's pouring on each tap, with label art, style,
ABV/IBU and a tasting note. Tap a beer to swap it, search for a new
one online, or kick the keg and send it to the archive.

Everything is one small Flask app plus a vanilla-JS front-end: no build step,
no accounts, no cloud. Your phone can open the same page over Wi-Fi to manage the
taps from the couch.

![The board on the 10" Touch Display 2](docs/board.png)

![Searching with the on-screen keyboard](docs/search.png)

## Features

- **Splash screen** – the house crest fills the screen on boot; one tap
  reveals the board. It comes back after five minutes without a touch. The
  same crest sits faintly behind the tap cards.
- **Read-only board, separate admin page** – `/` is for guests and the
  touchscreen: tap a card to read about the beer, browse the archive and the
  stats, nothing can be changed. `/admin` is the same app with the controls:
  change what's on tap, edit, score, kick, add and delete.
- **Board** – three big cards (configurable), label artwork with a blurred glow,
  style pill, ABV/IBU, description and how long it's been on tap. Live-updates
  when anything changes from another device.
- **Search** – type a beer, brewery or style. Results come from your own library
  first, then Open Food Facts (free, no key). Add a free Catalog.beer key for
  clean style, ABV, IBU and description data, or Untappd credentials for
  labels and descriptions. Tapping a result pours it straight onto the chosen
  tap; the pencil on a row opens the editor first if you want to tweak it.
- **Labels and brewery logos** – every beer has two artwork slots, the beer
  label and the brewery logo. Pick which the board shows (label, logo, or the
  label with the logo as a badge) from the tap menu. Artwork is downloaded and
  cached on the Pi the moment a beer is tapped, so the board works offline and
  re-tapping a beer never fetches again. A download that fails (offline, slow
  host) is retried in the background until it lands. The editor shows whether
  each image is saved locally, with **Refresh** to download it again from its
  source and **Remove** to delete it. No artwork at all and a generated label
  is drawn in the beer's colour.
- **Find a logo** – one tap looks the brewery up on Open Brewery DB and pulls
  its site icon. Untappd results carry the brewery logo directly.
- **Edit anything** – name, brewery, style, ABV, IBU, description, and upload a
  photo of the can from your phone.
- **Archive** – kicked kegs land in the cellar with how many times they've been
  on and for how long. They sit at the top of every search, so re-tapping a
  favourite is three touches: the tap card, *Change beer*, the beer.
- **Scores** – rate any beer 1 to 5 stars from its tap menu or the editor. The
  score shows on the board card, in the archive and on the stats page.
- **Stats** – the bar-chart button opens a dashboard of everything that has
  been on tap: kegs poured, beers tried, days on tap, average score, top
  rated, most tapped, longest running, styles and breweries, kegs per month,
  and the recent pour history.
- **On-screen keyboard** – kiosk Chromium has no keyboard, so the app ships a
  touch one. Phones use their own; toggle with the keyboard icon.
- **Kiosk-ready** – systemd service, Chromium kiosk launcher, autostart entry,
  screen-blanking off. One install script.

## Try it on your laptop

```bash
git clone https://github.com/echang15/merritt-house.git
cd merritt-house
pip install -r requirements.txt
python3 app.py --demo        # seeds three sample beers on an empty board
```

Open <http://localhost:8080>. Resize the window to 1920×1200 (10" Touch Display 2)
or 800×480 (original 7") to preview the Pi layouts.

## Install on the Raspberry Pi

Flash Raspberry Pi OS **with desktop** (Bullseye or Bookworm), connect the
touchscreen, get on Wi-Fi, then:

```bash
git clone https://github.com/echang15/merritt-house.git ~/merritt-house
cd ~/merritt-house
./install/install.sh
sudo reboot
```

The installer:

1. installs `python3-flask`, `python3-requests` and `chromium` via apt;
2. installs and starts `taplist.service` (the web app on port 8080, restarts on
   failure, starts on boot);
3. registers `install/kiosk.sh` to open Chromium full-screen when the desktop
   logs in (XDG autostart, plus labwc / wayfire autostart on Bookworm);
4. disables screen blanking with `raspi-config`.

After the reboot the board fills the screen. See **Manage it from your phone**
below for opening the same app from any other device on your Wi-Fi.

Useful commands:

```bash
sudo systemctl status taplist      # is the app up?
journalctl -u taplist -f           # logs
sudo systemctl restart taplist     # after editing the code or taplist.env
```

If the Pi is set to boot to the console instead of the desktop, enable desktop
autologin with `sudo raspi-config` → System Options → Boot / Auto Login.

## Manage it from your phone

The app listens on every network interface, so any browser on the same Wi-Fi
gets the same board and the same editing controls as the touchscreen. There is
no login; anyone on your home network can change the taps.

- **Address.** The kiosk's splash screen shows it, and the installer prints
  it: `http://<pi-ip>:8080/admin` or `http://<hostname>.local:8080/admin` (the
  Pi's default hostname is `raspberrypi`, so `http://raspberrypi.local:8080/admin`;
  the `.local` name needs mDNS, which Pi OS ships and phones support out of
  the box). Give the Pi a fixed IP in your router if you want the numeric
  address to stay put. Without `/admin` you get the same read-only board the
  touchscreen shows; the gear icon in its top bar jumps to the admin page.
- **Editing.** On the admin page, tap a card to change what's on that tap,
  score it, edit its details, upload a photo of the can, or kick it. The **+**
  button adds a beer, the box icon is the archive. Every change shows up on
  the touchscreen within a few seconds.
- **No password.** Anyone on your Wi-Fi can open `/admin`; the split is there
  to keep guests and the touchscreen from changing things by accident, not to
  keep people out.
- **Keyboard.** Phones, tablets and laptops use their own keyboard. The kiosk's
  own on-screen keyboard only defaults on for the touchscreen itself; the
  keyboard icon in any editor toggles it either way.
- **Home-screen shortcut.** In Safari or Chrome, share → *Add to Home Screen*
  gives you a full-screen app icon.

### Touch Display 2 orientation

Touch Display 2 panels are portrait by default (720×1280 on the 5" and 7",
1200×1920 on the 10"). The board is designed for landscape, so rotate the screen
once: open **Preferences → Screen Configuration**, right-click the DSI display,
choose **Orientation → Right** (or Left, depending on which way the cable comes
out), then Apply. Touch input follows the rotation automatically. For a headless
console setup instead, add `video=DSI-1:1200x1920@60,rotate=90` (or `720x1280`
for the 7") to `/boot/firmware/cmdline.txt`.

The UI scales itself to the panel: 800×480 on the original display, 1280×720
and 1920×1200 on Touch Display 2, and anything larger on an HDMI monitor.

## Configuration

Create `taplist.env` next to `app.py` (the service reads it) or export the
variables in your shell:

| Variable | Default | What it does |
|---|---|---|
| `TAPLIST_HOUSE` | `Merritt House` | Name shown at the top of the board |
| `TAPLIST_HOST` | `0.0.0.0` | Interface to listen on (`127.0.0.1` to allow only the Pi itself) |
| `TAPLIST_TAPS` | `3` | Number of taps |
| `TAPLIST_DATA` | `./data` | Where the SQLite database and cached labels live |
| `TAPLIST_PORT` | `8080` | Port to listen on |
| `TAPLIST_CATALOG_BEER_KEY` | unset | Enables [Catalog.beer](https://catalog.beer/) search. Create a free account, then copy the API key from your Account page |
| `TAPLIST_UNTAPPD_CLIENT_ID` / `TAPLIST_UNTAPPD_CLIENT_SECRET` | unset | Enables Untappd search (better descriptions and labels) |
| `TAPLIST_DISABLE_OFF` | unset | Set to `1` to turn off Open Food Facts search |
| `TAPLIST_SEARCH_TIMEOUT` | `8` | Seconds to wait for an online search |

**Splash and watermark.** Both use `taplist/static/img/splash.jpg`; replace
the file to change them. The watermark strength is `.watermark { opacity }` in
`taplist/static/style.css`, and how long the board waits before showing the
splash again is `SPLASH_RETURN_MS` at the top of `taplist/static/app.js`
(set it to `0` to only show the splash on boot).

**Fonts.** The UI uses the system font stack. Drop a `display.woff2` (or
`display.ttf`) into `taplist/static/fonts/` and the whole UI switches to it.

## How the pieces fit

```
app.py                    entry point / CLI (--port, --demo)
taplist/api.py            Flask app and JSON API
taplist/db.py             SQLite: beers (with scores), taps, tap_history, metrics
taplist/providers.py      Open Food Facts, Catalog.beer and Untappd search
taplist/labels.py         label download and upload storage
taplist/static/           index.html, style.css, app.js (the UI)
install/                  systemd unit, kiosk launcher, autostart, installer
tests/test_api.py         unit tests (python3 -m unittest discover -s tests)
```

### API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/state` | Taps with their beers, house name, version |
| `GET` | `/api/search?q=` | Library + online results |
| `POST` | `/api/taps/<n>` | `{beer_id}` or `{beer:{…}}` — put a beer on tap *n*, archiving the previous one |
| `DELETE` | `/api/taps/<n>` | Kick the keg on tap *n* |
| `GET` | `/api/archive` | Every beer not on tap, with pour history |
| `POST` | `/api/beers` | Create a beer |
| `PUT` | `/api/beers/<id>` | Edit a beer (`display_art`: `label`, `brewery` or `both`) |
| `DELETE` | `/api/beers/<id>` | Remove a beer (not while it's on tap) |
| `POST` | `/api/beers/<id>/label` | Upload beer label artwork (multipart field `label`) |
| `POST` | `/api/beers/<id>/brewery-logo` | Upload a brewery logo (multipart field `label`) |
| `POST` | `/api/beers/<id>/art/<label\|brewery>/refresh` | Download that artwork again from its source URL |
| `DELETE` | `/api/beers/<id>/art/<label\|brewery>` | Delete that artwork (cached file and URL) |
| `GET` | `/api/brewery-logo?q=` | Logo candidates for a brewery name |
| `GET` | `/api/history` | Chronological tap log |
| `GET` | `/api/metrics` | Aggregates for the stats page: totals, score counts, leaderboards, kegs per month, recent pours |

Data lives in `data/taplist.db` and `data/labels/`; back those up and you have
everything.

## A note on beer data

Open Food Facts is community-maintained, so coverage skews toward supermarket
beers and descriptions are often just the ingredient list. Everything is
editable before it goes on the tap, and once a beer is in your library it's
found instantly without going online. Untappd's API has the best data but they
no longer hand out keys freely; if you already have one, drop it in `taplist.env`.
