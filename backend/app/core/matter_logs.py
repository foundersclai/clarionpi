"""Per-matter run logs — invariant 14: debugging starts at the logs, not the code.

Every ingest/analysis/draft phase writes a per-matter run log, so a silent corpus problem
(wrong output, no exception) has one canonical file to read first. :class:`MatterRunLogger`
appends JSON-lines to ``<logs_dir>/<matter_id>/<phase>.log`` **and** mirrors each line to the
root logger (dual-emit discipline), so log aggregation and on-disk forensics agree.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import get_settings

_ROOT_LOGGER = logging.getLogger("clarionpi.matter")


class MatterRunLogger:
    """Appends JSON-lines to a per-matter, per-phase run log and mirrors each to the root logger.

    One logger instance targets one ``(matter_id, phase)`` file. ``logs_dir`` defaults to
    ``settings.matter_logs_dir``; the ``<matter_id>`` subdirectory is created on construction.
    """

    def __init__(
        self,
        matter_id: uuid.UUID,
        phase: str,
        logs_dir: str | Path | None = None,
    ) -> None:
        root = Path(logs_dir) if logs_dir is not None else Path(get_settings().matter_logs_dir)
        self._matter_id = matter_id
        self._phase = phase
        self._dir = root / str(matter_id)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._path = self._dir / f"{phase}.log"

    @property
    def path(self) -> Path:
        """The on-disk path of this matter/phase run log."""
        return self._path

    def log(self, event: str, **fields: object) -> None:
        """Append one JSON line ``{"ts", "event", **fields}`` and mirror it to the root logger.

        The line is flushed immediately so a crash mid-phase still leaves the trail on disk.
        """
        record: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **fields,
        }
        line = json.dumps(record, default=str)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
        # Dual-emit: the same line reaches the root logger so aggregation sees it too.
        _ROOT_LOGGER.info("matter=%s phase=%s %s", self._matter_id, self._phase, line)
