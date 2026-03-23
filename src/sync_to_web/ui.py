from __future__ import annotations

from typing import Any
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sync_to_web.claude_bridge import ClaudeBridge
from sync_to_web.config_store import ConfigStore
from sync_to_web.logging_bus import LogBus
from sync_to_web.models import ProjectConfig, TransferProtocol, default_port_for_protocol
from sync_to_web.sync_engine import SyncManager


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, manager: SyncManager, log_bus: LogBus) -> None:
        super().__init__()
        self.store = store
        self.manager = manager
        self.log_bus = log_bus
        self.projects: list[ProjectConfig] = []
        self.statuses: dict[str, str] = {}

        self.setWindowTitle("Sync to Web")
        self.resize(1100, 760)

        self._build_ui()
        self.log_bus.message.connect(self._append_log)
        self.manager.project_status_changed.connect(self._on_status_changed)

        self._bridge = ClaudeBridge(self)
        self._bridge.claude_working.connect(self.manager.handle_claude_working)
        self._bridge.claude_done.connect(self.manager.handle_claude_done)

        self._load_projects()
        self._start_enabled_projects()

    def _build_ui(self) -> None:
        self.project_list = QListWidget()
        self.project_list.currentItemChanged.connect(self._on_project_selected)

        self.name_input = QLineEdit()
        self.local_path_input = QLineEdit()
        self.remote_path_input = QLineEdit("/")
        self.protocol_input = QComboBox()
        for protocol in TransferProtocol:
            self.protocol_input.addItem(protocol.value.upper(), protocol.value)
        self.protocol_input.currentIndexChanged.connect(self._on_protocol_changed)
        self.host_input = QLineEdit()
        self.port_input = QLineEdit("21")
        self.username_input = QLineEdit()
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.ignore_input = QPlainTextEdit()
        self.ignore_input.setPlaceholderText("One glob pattern per line")
        self.delete_remote_input = QCheckBox("Delete remote files when local files are deleted")
        self.auto_sync_input = QCheckBox("Watch and sync in realtime")
        self.auto_sync_input.setChecked(True)
        self.enabled_input = QCheckBox("Enabled")
        self.enabled_input.setChecked(True)
        self.claude_sync_input = QCheckBox("Pause per-file uploads while Claude Code is working")
        self.claude_sync_input.setChecked(False)
        self.build_command_input = QLineEdit()
        self.build_command_input.setPlaceholderText("e.g. npm run build  (leave empty to skip build step)")
        self.build_output_path_input = QLineEdit()
        self.build_output_path_input.setPlaceholderText("e.g. dist  (leave empty to upload source folder)")
        self.watch_paths_input = QPlainTextEdit()
        self.watch_paths_input.setPlaceholderText(
            "Leave empty to watch all files and folders\n"
            "One path per line — folders are watched recursively, e.g.:\n"
            "assets\nsrc\nindex.html\nrobots.txt"
        )

        browse_button = QPushButton("Browse")
        browse_button.clicked.connect(self._browse_local_path)

        add_button = QPushButton("New Project")
        add_button.clicked.connect(self._new_project)
        save_button = QPushButton("Save Project")
        save_button.clicked.connect(self._save_selected_project)
        delete_button = QPushButton("Delete Project")
        delete_button.clicked.connect(self._delete_selected_project)
        test_button = QPushButton("Test Connection")
        test_button.clicked.connect(self._test_connection)
        full_sync_button = QPushButton("Full Sync")
        full_sync_button.clicked.connect(self._full_sync)
        start_button = QPushButton("Start")
        start_button.clicked.connect(self._start_selected_project)
        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self._stop_selected_project)

        form = QFormLayout()
        local_row = QWidget()
        local_layout = QHBoxLayout(local_row)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.addWidget(self.local_path_input)
        local_layout.addWidget(browse_button)

        form.addRow("Project Name", self.name_input)
        form.addRow("Local Folder", local_row)
        form.addRow("Remote Folder", self.remote_path_input)
        form.addRow("Protocol", self.protocol_input)
        form.addRow("Host", self.host_input)
        form.addRow("Port", self.port_input)
        form.addRow("Username", self.username_input)
        form.addRow("Password", self.password_input)
        form.addRow("Ignore Patterns", self.ignore_input)
        form.addRow("", self.delete_remote_input)
        form.addRow("", self.auto_sync_input)
        form.addRow("", self.enabled_input)
        form.addRow("", self.claude_sync_input)
        form.addRow("Build Command", self.build_command_input)
        form.addRow("Build Output Folder", self.build_output_path_input)
        form.addRow("Watch Paths", self.watch_paths_input)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        for button in [
            add_button,
            save_button,
            delete_button,
            test_button,
            full_sync_button,
            start_button,
            stop_button,
        ]:
            button_layout.addWidget(button)

        self.status_label = QLabel("Status: not started")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        form_container = QWidget()
        form_layout = QVBoxLayout(form_container)
        form_layout.addWidget(button_row)
        form_layout.addLayout(form)
        form_layout.addWidget(self.status_label)

        details_splitter = QSplitter(Qt.Orientation.Vertical)
        details_splitter.addWidget(form_container)
        details_splitter.addWidget(self.log_view)
        details_splitter.setStretchFactor(0, 2)
        details_splitter.setStretchFactor(1, 1)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(self.project_list)
        main_splitter.addWidget(details_splitter)
        main_splitter.setStretchFactor(0, 1)
        main_splitter.setStretchFactor(1, 3)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.addWidget(main_splitter)
        self.setCentralWidget(root)
        self.statusBar().showMessage("Ready")

    def _load_projects(self) -> None:
        self.projects = self.store.load_projects()
        if not self.projects:
            self.projects = [ProjectConfig(enabled=False)]
        self._refresh_project_list(select_id=self.projects[0].id)

    def _start_enabled_projects(self) -> None:
        for project in self.projects:
            password = self.store.load_password(project)
            if project.enabled and project.local_path.strip():
                try:
                    self.manager.start_project(project, password)
                except Exception as exc:  # noqa: BLE001
                    self._append_log("", project.id, "ERROR", f"Failed to start watcher: {exc}")

    def _refresh_project_list(self, select_id: str | None = None) -> None:
        current_id = select_id or self._selected_project_id()
        self.project_list.clear()
        for project in self.projects:
            status = self.statuses.get(project.id, "stopped")
            item = QListWidgetItem(f"{project.name} [{status}]")
            item.setData(Qt.ItemDataRole.UserRole, project.id)
            self.project_list.addItem(item)
            if project.id == current_id:
                self.project_list.setCurrentItem(item)
        if self.project_list.count() and self.project_list.currentItem() is None:
            self.project_list.setCurrentRow(0)

    def _selected_project_id(self) -> str | None:
        item = self.project_list.currentItem()
        if not item:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _selected_project(self) -> ProjectConfig | None:
        project_id = self._selected_project_id()
        if not project_id:
            return None
        return next((project for project in self.projects if project.id == project_id), None)

    def _on_project_selected(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if not current:
            return
        project_id = current.data(Qt.ItemDataRole.UserRole)
        project = next((item for item in self.projects if item.id == project_id), None)
        if not project:
            return
        self._load_project_into_form(project)

    def _load_project_into_form(self, project: ProjectConfig) -> None:
        self.name_input.setText(project.name)
        self.local_path_input.setText(project.local_path)
        self.remote_path_input.setText(project.remote_path)
        self.protocol_input.setCurrentIndex(self.protocol_input.findData(project.protocol.value))
        self.host_input.setText(project.host)
        self.port_input.setText(str(project.port))
        self.username_input.setText(project.username)
        self.password_input.setText(self.store.load_password(project))
        self.ignore_input.setPlainText("\n".join(project.ignore_patterns))
        self.delete_remote_input.setChecked(project.delete_remote)
        self.auto_sync_input.setChecked(project.auto_sync)
        self.enabled_input.setChecked(project.enabled)
        self.claude_sync_input.setChecked(project.claude_sync)
        self.build_command_input.setText(project.build_command)
        self.build_output_path_input.setText(project.build_output_path)
        self.watch_paths_input.setPlainText("\n".join(project.watch_paths))
        status = self.statuses.get(project.id, "stopped")
        self.status_label.setText(f"Status: {status}")

    def _project_from_form(self, existing: ProjectConfig | None = None) -> ProjectConfig:
        protocol = TransferProtocol(self.protocol_input.currentData())
        port_text = self.port_input.text().strip()
        port = int(port_text) if port_text else default_port_for_protocol(protocol)
        ignore_patterns = [line.strip() for line in self.ignore_input.toPlainText().splitlines() if line.strip()]
        return ProjectConfig(
            id=existing.id if existing else str(uuid4()),
            credential_key=existing.credential_key if existing else "",
            name=self.name_input.text().strip() or "New Project",
            local_path=self.local_path_input.text().strip(),
            remote_path=self.remote_path_input.text().strip() or "/",
            protocol=protocol,
            host=self.host_input.text().strip(),
            port=port,
            username=self.username_input.text().strip(),
            ignore_patterns=ignore_patterns,
            delete_remote=self.delete_remote_input.isChecked(),
            auto_sync=self.auto_sync_input.isChecked(),
            enabled=self.enabled_input.isChecked(),
            claude_sync=self.claude_sync_input.isChecked(),
            build_command=self.build_command_input.text().strip(),
            build_output_path=self.build_output_path_input.text().strip(),
            watch_paths=[l.strip() for l in self.watch_paths_input.toPlainText().splitlines() if l.strip()],
        )

    def _save_selected_project(self, autostart: bool | None = None) -> None:
        current = self._selected_project()
        try:
            project = self._project_from_form(current)
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number.")
            return

        if current:
            index = self.projects.index(current)
            self.projects[index] = project
        else:
            self.projects.append(project)

        self.store.save_projects(self.projects)
        password = self.password_input.text()
        if password:
            self.store.save_password(project, password)
        else:
            self.store.delete_password(project)

        should_start = project.enabled if autostart is None else autostart

        if should_start:
            try:
                self.manager.start_project(project, password)
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "Start Failed", str(exc))
        else:
            self.manager.stop_project(project.id)

        self._refresh_project_list(select_id=project.id)
        self.statusBar().showMessage("Project saved", 3000)

    def _new_project(self) -> None:
        project = ProjectConfig(name=f"Project {len(self.projects) + 1}", enabled=False)
        self.projects.append(project)
        self._refresh_project_list(select_id=project.id)
        self.statusBar().showMessage("New project created", 3000)

    def _delete_selected_project(self) -> None:
        project = self._selected_project()
        if not project:
            return
        response = QMessageBox.question(
            self,
            "Delete Project",
            f"Delete '{project.name}' and remove its saved password?",
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        self.manager.stop_project(project.id)
        self.store.delete_password(project)
        self.projects = [item for item in self.projects if item.id != project.id]
        if not self.projects:
            self.projects = [ProjectConfig(enabled=False)]
        self.store.save_projects(self.projects)
        self._refresh_project_list(select_id=self.projects[0].id)
        self.statusBar().showMessage("Project deleted", 3000)

    def _start_selected_project(self) -> None:
        self.enabled_input.setChecked(True)
        self._save_selected_project(autostart=True)
        self.statusBar().showMessage("Project started", 3000)

    def _stop_selected_project(self) -> None:
        project = self._selected_project()
        if not project:
            return
        self.manager.stop_project(project.id)
        self.statusBar().showMessage("Project stopped", 3000)

    def _test_connection(self) -> None:
        project = self._selected_project()
        if not project:
            return
        try:
            staged = self._project_from_form(project)
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number.")
            return
        self.manager.test_connection(staged, self.password_input.text())
        self.statusBar().showMessage("Connection test started", 3000)

    def _full_sync(self) -> None:
        self._save_selected_project()
        project = self._selected_project()
        if not project:
            return
        if project.id not in self.manager.runners:
            self.manager.start_project(project, self.password_input.text())
        self.manager.schedule_full_sync(project.id)
        self.statusBar().showMessage("Full sync queued", 3000)

    def _browse_local_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select local project folder")
        if selected:
            self.local_path_input.setText(selected)

    def _on_protocol_changed(self) -> None:
        protocol = TransferProtocol(self.protocol_input.currentData())
        self.port_input.setText(str(default_port_for_protocol(protocol)))

    def _on_status_changed(self, project_id: str, status: str) -> None:
        self.statuses[project_id] = status
        if project_id == self._selected_project_id():
            self.status_label.setText(f"Status: {status}")
        self._refresh_project_list(select_id=project_id)

    def _append_log(self, timestamp: str, project_id: str, level: str, message: str) -> None:
        project_name = next((item.name for item in self.projects if item.id == project_id), "Unknown")
        entry = f"[{timestamp}] [{project_name}] {level}: {message}"
        self.log_view.appendPlainText(entry)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.manager.stop_all()
        super().closeEvent(event)


def run_app() -> int:
    app = QApplication.instance() or QApplication([])
    store = ConfigStore()
    log_bus = LogBus()
    manager = SyncManager(log_bus)
    window = MainWindow(store, manager, log_bus)
    window.show()
    return app.exec()
