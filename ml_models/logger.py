"""
Simple JSONL logger for step performance analysis.

Logs each env.step execution with duration, feedback, and configuration
for later analysis of which settings lead to faster, more successful steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


LOG_DIR = Path("ml_models/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "steps.jsonl"


def log_step(record: Dict[str, Any]) -> None:
    """
    Append a step execution record to the JSONL log file.

    Args:
        record: Dictionary containing step metadata (agent, task_id, duration, feedback, etc.)
    """
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as e:
        print(f"[ML_LOG] Failed to write log: {e}")

