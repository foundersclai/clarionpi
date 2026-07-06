"""Per-matter run logs: valid JSON lines, accumulation, dir creation, dual-emit to root logger."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

import pytest

from app.core.matter_logs import MatterRunLogger


def test_lines_are_valid_json_with_ts_event_and_fields(tmp_path: Path) -> None:
    matter_id = uuid.uuid4()
    logger = MatterRunLogger(matter_id, "phase0", logs_dir=tmp_path)
    logger.log("classified", document_id="doc-1", doc_type="bill")

    lines = logger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "classified"
    assert record["document_id"] == "doc-1"
    assert record["doc_type"] == "bill"
    assert "ts" in record and isinstance(record["ts"], str)


def test_appends_accumulate(tmp_path: Path) -> None:
    logger = MatterRunLogger(uuid.uuid4(), "phase0", logs_dir=tmp_path)
    logger.log("a")
    logger.log("b")
    logger.log("c")
    events = [json.loads(line)["event"] for line in logger.path.read_text().splitlines()]
    assert events == ["a", "b", "c"]


def test_matter_subdir_is_auto_created(tmp_path: Path) -> None:
    matter_id = uuid.uuid4()
    logs_dir = tmp_path / "does" / "not" / "exist" / "yet"
    logger = MatterRunLogger(matter_id, "analysis", logs_dir=logs_dir)
    assert logger.path.parent == logs_dir / str(matter_id)
    assert logger.path.parent.is_dir()
    logger.log("started")
    assert logger.path.is_file()


def test_dual_emit_reaches_root_logger(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    logger = MatterRunLogger(uuid.uuid4(), "phase0", logs_dir=tmp_path)
    with caplog.at_level(logging.INFO, logger="clarionpi.matter"):
        logger.log("mirrored_event", note="hi")
    messages = [r.getMessage() for r in caplog.records if r.name == "clarionpi.matter"]
    assert any("mirrored_event" in m for m in messages)
