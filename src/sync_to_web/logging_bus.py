from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QObject, Signal


class LogBus(QObject):
    message = Signal(str, str, str, str)

    def emit_log(self, project_id: str, level: str, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.message.emit(timestamp, project_id, level.upper(), text)
