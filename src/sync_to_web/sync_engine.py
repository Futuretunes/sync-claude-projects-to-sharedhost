from __future__ import annotations

import os
import posixpath
import queue
import subprocess
import threading
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from sync_to_web.logging_bus import LogBus
from sync_to_web.models import ProjectConfig
from sync_to_web.remote_clients import build_remote_client, normalize_remote_path


@dataclass(frozen=True, slots=True)
class SyncTask:
    action: str
    rel_path: str | None = None
    old_rel_path: str | None = None


class ProjectRunner(QObject):
    status_changed = Signal(str, str)

    def __init__(self, project: ProjectConfig, password: str, log_bus: LogBus) -> None:
        super().__init__()
        self.project = project
        self.password = password
        self.log_bus = log_bus
        self.local_root = Path(project.local_path).expanduser()
        self._observer: Observer | None = None
        self._worker_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._task_queue: queue.Queue[SyncTask | None] = queue.Queue()
        self._pending_lock = threading.Lock()
        self._pending: set[SyncTask] = set()
        self._client = None
        self._claude_paused: bool = False

    @property
    def is_running(self) -> bool:
        return self._worker_thread is not None and self._worker_thread.is_alive()

    def start(self) -> None:
        if self.is_running:
            return
        if not self.local_root.exists() or not self.local_root.is_dir():
            raise ValueError(f"Local path does not exist: {self.local_root}")

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

        if self.project.auto_sync:
            self._observer = Observer()
            self._observer.schedule(ProjectEventHandler(self), str(self.local_root), recursive=True)
            self._observer.start()

        self._set_status("running")
        self.log_bus.emit_log(self.project.id, "info", f"Watching {self.local_root}")

    def stop(self) -> None:
        self._stop_event.set()

        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

        self._task_queue.put(None)
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
            self._worker_thread = None

        self._close_client()
        self._set_status("stopped")
        self.log_bus.emit_log(self.project.id, "info", "Sync stopped.")

    def schedule_full_sync(self) -> None:
        if self.project.build_command.strip():
            self._enqueue_task(SyncTask("build_and_sync"))
        else:
            self._enqueue_task(SyncTask("full_sync"))

    def run_connection_test(self) -> None:
        threading.Thread(target=self._test_connection_worker, daemon=True).start()

    def schedule_upload(self, absolute_path: str) -> None:
        rel_path = self._relative_path_from_absolute(absolute_path)
        if not rel_path or self._should_ignore(rel_path) or not self._should_include(rel_path):
            return
        if self._is_inside_build_output(rel_path):
            return  # build output files are synced via build_and_sync
        self._enqueue_task(SyncTask("upload", rel_path=rel_path))

    def schedule_delete(self, absolute_path: str) -> None:
        if not self.project.delete_remote:
            return
        rel_path = self._relative_path_from_absolute(absolute_path)
        if not rel_path or self._should_ignore(rel_path) or not self._should_include(rel_path):
            return
        if self._is_inside_build_output(rel_path):
            return  # build output files are synced via build_and_sync
        self._enqueue_task(SyncTask("delete", rel_path=rel_path))

    def schedule_move(self, old_absolute_path: str, new_absolute_path: str) -> None:
        new_rel_path = self._relative_path_from_absolute(new_absolute_path)
        old_rel_path = self._relative_path_from_absolute(old_absolute_path)

        if new_rel_path and self._is_inside_build_output(new_rel_path):
            return  # build output files are synced via build_and_sync
        if new_rel_path and not self._should_ignore(new_rel_path) and self._should_include(new_rel_path):
            self._enqueue_task(SyncTask("move", rel_path=new_rel_path, old_rel_path=old_rel_path))
        elif old_rel_path and self.project.delete_remote and not self._should_ignore(old_rel_path) and self._should_include(old_rel_path):
            self._enqueue_task(SyncTask("delete", rel_path=old_rel_path))

    def set_claude_paused(self, paused: bool) -> None:
        self._claude_paused = paused

    def _enqueue_task(self, task: SyncTask) -> None:
        if self._claude_paused and task.action not in {"full_sync", "build_and_sync"}:
            return
        with self._pending_lock:
            if task in self._pending:
                return
            self._pending.add(task)
        self._task_queue.put(task)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            task = self._task_queue.get()
            if task is None:
                break

            try:
                self._process_task(task)
            except Exception as exc:  # noqa: BLE001
                self._close_client()
                self._set_status("error")
                self.log_bus.emit_log(self.project.id, "error", f"{task.action} failed: {exc}")
            finally:
                with self._pending_lock:
                    self._pending.discard(task)

    def _process_task(self, task: SyncTask) -> None:
        if task.action == "build_and_sync":
            self._set_status("building")
            if not self._run_build():
                self._set_status("error")
                return
            self._set_status("syncing")
            self._perform_full_sync(self._build_root())
            self._set_status("running")
            return

        if task.action == "full_sync":
            self._set_status("syncing")
            self._perform_full_sync()
            self._set_status("running")
            return

        if task.action == "delete":
            assert task.rel_path is not None
            remote_path = self._remote_path_for(task.rel_path)
            client = self._ensure_client()
            client.delete_file(remote_path)
            self.log_bus.emit_log(self.project.id, "info", f"Deleted remote file: {task.rel_path}")
            return

        if task.action == "move" and task.old_rel_path and self.project.delete_remote:
            remote_old = self._remote_path_for(task.old_rel_path)
            client = self._ensure_client()
            client.delete_file(remote_old)

        if task.action in {"upload", "move"}:
            assert task.rel_path is not None
            local_file = self.local_root / task.rel_path
            if not local_file.exists() or not local_file.is_file():
                return
            remote_path = self._remote_path_for(task.rel_path)
            client = self._ensure_client()
            client.upload_file(local_file, remote_path)
            self.log_bus.emit_log(self.project.id, "info", f"Uploaded: {task.rel_path}")

    def _perform_full_sync(self, root: Path | None = None) -> None:
        sync_root = root or self.local_root
        is_build_output = root is not None
        client = self._ensure_client()
        uploaded = 0
        for local_file in sync_root.rglob("*"):
            if not local_file.is_file():
                continue
            rel_path = local_file.relative_to(sync_root).as_posix()
            if self._should_ignore(rel_path):
                continue
            if not is_build_output and not self._should_include(rel_path):
                continue
            remote_path = self._remote_path_for(rel_path)
            client.upload_file(local_file, remote_path)
            uploaded += 1
        self.log_bus.emit_log(self.project.id, "info", f"Full sync complete: {uploaded} files uploaded.")

    def _build_root(self) -> Path | None:
        output = self.project.build_output_path.strip()
        return (self.local_root / output) if output else None

    def _run_build(self) -> bool:
        cmd = self.project.build_command.strip()
        self.log_bus.emit_log(self.project.id, "info", f"Running build: {cmd}")
        try:
            process = subprocess.Popen(
                cmd,
                shell=True,
                cwd=str(self.local_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in process.stdout:
                self.log_bus.emit_log(self.project.id, "info", line.rstrip())
            process.wait()
            if process.returncode == 0:
                self.log_bus.emit_log(self.project.id, "info", "Build succeeded.")
                return True
            self.log_bus.emit_log(self.project.id, "error", f"Build failed (exit {process.returncode}).")
            return False
        except Exception as exc:  # noqa: BLE001
            self.log_bus.emit_log(self.project.id, "error", f"Build error: {exc}")
            return False

    def _test_connection_worker(self) -> None:
        try:
            client = build_remote_client(self.project, self.password)
            client.connect()
            client.close()
            self.log_bus.emit_log(self.project.id, "info", "Connection test succeeded.")
        except Exception as exc:  # noqa: BLE001
            self.log_bus.emit_log(self.project.id, "error", f"Connection test failed: {exc}")

    def _ensure_client(self):
        if self._client is None:
            self._client = build_remote_client(self.project, self.password)
            self._client.connect()
            self.log_bus.emit_log(self.project.id, "info", "Connected to remote host.")
        return self._client

    def _close_client(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        finally:
            self._client = None

    def _set_status(self, status: str) -> None:
        self.status_changed.emit(self.project.id, status)

    def _relative_path_from_absolute(self, absolute_path: str) -> str | None:
        try:
            path = Path(absolute_path).resolve()
            rel = path.relative_to(self.local_root.resolve()).as_posix()
        except ValueError:
            return None
        return rel if rel != "." else None

    def _is_inside_build_output(self, rel_path: str) -> bool:
        build_output = self.project.build_output_path.strip()
        if not build_output:
            return False
        prefix = build_output.replace("\\", "/").strip("/") + "/"
        return rel_path.replace("\\", "/").startswith(prefix)

    def _remote_path_for(self, rel_path: str) -> str:
        remote_root = normalize_remote_path(self.project.remote_path or "/")
        combined = posixpath.join(remote_root, rel_path.replace(os.sep, "/"))
        return normalize_remote_path(combined)

    def _should_ignore(self, rel_path: str) -> bool:
        normalized = rel_path.replace("\\", "/")
        basename = Path(normalized).name
        for pattern in self.project.ignore_patterns:
            candidate = pattern.strip()
            if not candidate:
                continue
            if fnmatch(normalized, candidate) or fnmatch(basename, candidate):
                return True
        return False

    def _should_include(self, rel_path: str) -> bool:
        if not self.project.watch_paths:
            return True  # no filter configured — include everything

        normalized = rel_path.replace("\\", "/")
        is_root_file = "/" not in normalized

        watch_folders: list[str] = []
        explicit_root_files: list[str] = []

        for raw in self.project.watch_paths:
            entry = raw.strip().replace("\\", "/").strip("/")
            if not entry:
                continue
            if (self.local_root / entry).is_dir():
                watch_folders.append(entry)
            else:
                explicit_root_files.append(entry)

        for folder in watch_folders:
            if normalized == folder or normalized.startswith(folder + "/"):
                return True

        if is_root_file:
            if not explicit_root_files:
                return True  # folders selected but no root files specified — include all root files
            return normalized in explicit_root_files

        return False


class ProjectEventHandler(FileSystemEventHandler):
    def __init__(self, runner: ProjectRunner) -> None:
        self.runner = runner

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self.runner.schedule_upload(event.src_path)

    def on_modified(self, event: FileModifiedEvent) -> None:
        if not event.is_directory:
            self.runner.schedule_upload(event.src_path)

    def on_deleted(self, event: FileDeletedEvent) -> None:
        if not event.is_directory:
            self.runner.schedule_delete(event.src_path)

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self.runner.schedule_move(event.src_path, event.dest_path)


class SyncManager(QObject):
    project_status_changed = Signal(str, str)

    def __init__(self, log_bus: LogBus) -> None:
        super().__init__()
        self.log_bus = log_bus
        self.runners: dict[str, ProjectRunner] = {}

    def start_project(self, project: ProjectConfig, password: str) -> None:
        self.stop_project(project.id)
        runner = ProjectRunner(project, password, self.log_bus)
        runner.status_changed.connect(self.project_status_changed.emit)
        self.runners[project.id] = runner
        runner.start()

    def stop_project(self, project_id: str) -> None:
        runner = self.runners.pop(project_id, None)
        if runner:
            runner.stop()

    def restart_project(self, project: ProjectConfig, password: str) -> None:
        self.start_project(project, password)

    def schedule_full_sync(self, project_id: str) -> None:
        runner = self.runners.get(project_id)
        if runner:
            runner.schedule_full_sync()

    def test_connection(self, project: ProjectConfig, password: str) -> None:
        runner = self.runners.get(project.id)
        if runner:
            runner.run_connection_test()
            return
        temp_runner = ProjectRunner(project, password, self.log_bus)
        temp_runner.run_connection_test()

    def handle_claude_working(self, resolved_path: str) -> None:
        runner = self._runner_for_path(resolved_path)
        if runner and runner.project.claude_sync:
            runner.set_claude_paused(True)

    def handle_claude_done(self, resolved_path: str) -> None:
        runner = self._runner_for_path(resolved_path)
        if runner and runner.project.claude_sync:
            runner.set_claude_paused(False)
            runner.schedule_full_sync()

    def _runner_for_path(self, resolved_path: str) -> ProjectRunner | None:
        target = Path(resolved_path)
        for runner in self.runners.values():
            try:
                if runner.local_root.resolve() == target:
                    return runner
            except OSError:
                pass
        return None

    def stop_all(self) -> None:
        for project_id in list(self.runners):
            self.stop_project(project_id)
