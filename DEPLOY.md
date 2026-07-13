# TGRadio — VPS deployment guide

## 1. Get the code onto the VPS

```bash
sudo mkdir -p /opt/tgradio
sudo chown $USER:$USER /opt/tgradio
# scp your files over, or git clone if it's in a repo
scp cache.py config.py main.py player.py telegram_client.py requirements.txt .env.example tgradio.service you@your-vps-ip:/opt/tgradio/
```

## 2. System dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg
```

## 3. Python environment

```bash
cd /opt/tgradio
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 4. Configure

```bash
cp .env.example .env
nano .env   # fill in API_ID, API_HASH, PHONE, CHANNEL_ID, ADMIN_ID
```

Get `API_ID`/`API_HASH` from https://my.telegram.org. Get your own numeric
Telegram ID for `ADMIN_ID` from @userinfobot.

## 5. First run — interactive login (required, do this before setting up systemd)

This bot logs in as a real Telegram **user account** (userbots can join
voice chats, bot accounts can't), so the first run needs an interactive
terminal to enter the login code Telegram texts/sends you, and your 2FA
password if you have one set:

```bash
source venv/bin/activate
set -a; source .env; set +a   # loads the .env vars into this shell
python main.py
```

Once you see `TGRadio v1.0 is running`, confirm it actually joins the
voice chat and plays audio, then `Ctrl+C` to stop it. This creates a
`tgradio.session` file in `/opt/tgradio` — that's your saved login,
you won't be prompted again unless it's deleted or revoked.

**Keep `tgradio.session` private.** It's equivalent to your Telegram
account password — anyone with that file can log in as you.

## 6. Run it as a persistent service

```bash
# Create a dedicated user (avoid running as root)
sudo useradd -r -s /bin/false tgradio
sudo chown -R tgradio:tgradio /opt/tgradio

sudo cp tgradio.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgradio
```

Check it's running:
```bash
sudo systemctl status tgradio
sudo journalctl -u tgradio -f     # live logs
```

## 7. Everyday operations

- **Restart after a code change:** `sudo systemctl restart tgradio`
- **Stop:** `sudo systemctl stop tgradio`
- **Update dependencies:** `source venv/bin/activate && pip install -r requirements.txt --upgrade`
- **In-chat controls** (from your admin account, DM the bot):
  - `/skip` — skip current track
  - `/stop` — stop playback, bot process stays up
  - `/shutdown` — stop the whole process (systemd will restart it per `Restart=always` — use `systemctl stop tgradio` instead if you actually want it to stay down)

## Notes

- `Restart=always` in the unit file means the bot comes back automatically
  after a crash or VPS reboot (once you `enable` it). `main.py` also has
  its own internal restart-on-error loop, so systemd is a second safety
  net for cases where the Python process dies outright.
- Cache files live in `CACHE_DIR` (default `cache/`, i.e.
  `/opt/tgradio/cache`) and are swept clean automatically on startup,
  including anything left over from an unclean shutdown.
