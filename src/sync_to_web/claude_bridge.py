from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, Signal

STATE_FILE = Path.home() / ".sync-to-web" / "claude-state.json"


class ClaudeBridge(QObject):
    claude_working = Signal(str)
    claude_done = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._arm_watcher()

    def _arm_watcher(self) -> None:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        dir_path = str(STATE_FILE.parent)
        if dir_path not in self._watcher.directories():
            self._watcher.addPath(dir_path)
        if STATE_FILE.exists():
            file_path = str(STATE_FILE)
            if file_path not in self._watcher.files():
                self._watcher.addPath(file_path)

    def _on_dir_changed(self, _path: str) -> None:
        self._arm_watcher()
        self._read_state()

    def _on_file_changed(self, _path: str) -> None:
        self._arm_watcher()
        self._read_state()

    def _read_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        state = data.get("state", "")
        path = data.get("path", "")
        if not path:
            return
        resolved = str(Path(path).resolve())
        if state == "working":
            self.claude_working.emit(resolved)
        elif state == "done":
            self.claude_done.emit(resolved)
