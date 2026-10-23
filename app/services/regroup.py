"""Runs "Regroup all" on a background thread so the Settings page can poll it."""

import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from app.services.grouping import Grouper

log = logging.getLogger(__name__)


@dataclass
class RegroupState:
    status: str = "idle"  # idle | running | done | failed
    groups: int | None = None
    error: str | None = None
    finished_at: datetime | None = None


class Regrouper:
    def __init__(self, factory: sessionmaker[Session], grouper: Grouper) -> None:
        self.factory = factory
        self.grouper = grouper
        self.state = RegroupState()
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """False if a regroup is already running."""
        if self.state.status == "running":
            return False
        self.state = RegroupState(status="running")
        self._thread = threading.Thread(target=self._run, name="regroup", daemon=True)
        self._thread.start()
        return True

    def wait(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self) -> None:
        try:
            self.state.groups = self.grouper.regroup(self.factory)
            self.state.status = "done"
        except Exception as e:
            log.exception("regroup failed")
            self.state.status, self.state.error = "failed", str(e)
        finally:
            self.state.finished_at = datetime.now(UTC).replace(tzinfo=None)
