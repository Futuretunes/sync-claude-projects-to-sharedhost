# Sync to Web

`Sync to Web` is a local desktop app that watches one or more project folders and uploads changes to shared hosting over FTP, FTPS, or SFTP.

## Features

- Per-project settings for local folder, remote folder, protocol, host, port, and username
- Passwords stored in the OS credential store through `keyring`
- Realtime upload on file create and modify
- Optional remote delete when local files are removed or renamed
- Manual full sync button
- Built-in status and log panel
- Build step integration: run a build command (e.g. `npm run build`) and upload only the build output folder (e.g. `dist`)
- Watch paths: limit which folders are watched for changes
- Claude Code integration: pause per-file uploads while Claude Code is working

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m sync_to_web
```

Or on macOS, double-click `launch.command`.

## Package as a macOS app

```bash
.venv/bin/pyinstaller \
  --name "Sync to Web" \
  --windowed \
  --noconfirm \
  --paths src \
  src/sync_to_web/__main__.py
```

Or on macOS, run `build-macos.command`.

## Notes

- Prefer `FTPS` or `SFTP` when your host supports it. Plain FTP is not encrypted.
- The app stores project metadata in `~/.sync-to-web/projects.json`.
- Ignore patterns use simple glob matching, one pattern per line (e.g. `.git/*`, `*.pyc`, `node_modules/*`).
- When a build output folder is configured, the file watcher skips files inside it — those are only synced after a successful build.
- Watch paths limit which folders trigger uploads. Leave empty to watch everything.
