from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any


class UILogHandler(logging.Handler):
    def __init__(self, state: "AppState"):
        super().__init__()
        self.state = state

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            self.state.append_log(message)
        except Exception:
            pass


@dataclass
class AppState:
    processing: bool = False
    synthesizing: bool = False
    step: int = 1
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    log_tail: int = 300

    url_or_path: str = ""
    work_dir: str = "work"
    whisper_model: str = "small"
    lang: str = "zh"
    emo_text: str = ""
    diffusion_steps: int = 25
    burn_subs: bool = False
    output_video: str = ""
    segment_current: int = 0
    segment_total: int = 0
    cancel_event: Event = field(default_factory=Event, repr=False)

    video_data: dict[str, Any] | None = None
    srt_entries: list[dict[str, Any]] = field(default_factory=list)
    srt_path: Path | None = None
    srt_history: list[list[dict[str, Any]]] = field(default_factory=list)
    final_video_path: Path | None = None
    current_process: Any = None  # Store current subprocess if any

    _lock: Lock = field(default_factory=Lock, repr=False)
    _segment_re: re.Pattern[str] = field(
        default_factory=lambda: re.compile(r"Processing Segment\s+(\d+)/(\d+)") ,
        repr=False,
    )

    def append_log(self, text: str) -> None:
        with self._lock:
            self.logs.append(text)
            if len(self.logs) > self.log_tail:
                self.logs = self.logs[-self.log_tail :]
            self._infer_progress_from_log(text)

    def _infer_progress_from_log(self, text: str) -> None:
        if ">> 开始处理视频" in text:
            self.step = 1
            self.progress = max(self.progress, 0.05)
            return

        if ">> 缓存命中:" in text and text.strip().endswith(".srt"):
            self.step = 1
            self.progress = max(self.progress, 0.45)
            return

        if ">> 音色提取成功" in text or (">> 缓存命中:" in text and text.strip().endswith("_voice.wav")):
            self.step = 2
            self.progress = max(self.progress, 0.60)
            return

        match = self._segment_re.search(text)
        if match:
            current = int(match.group(1))
            total = max(1, int(match.group(2)))
            self.segment_current = current
            self.segment_total = total
            self.step = 3
            segment_ratio = min(1.0, current / total)
            self.progress = max(self.progress, 0.70 + 0.28 * segment_ratio)
            return

        if ">> TTS 生成成功" in text or ">> 成功合并音频到视频" in text:
            self.step = 4
            self.progress = 1.0

    def get_log_text(self) -> str:
        with self._lock:
            return "\n".join(self.logs)

    def clear_logs(self) -> None:
        with self._lock:
            self.logs.clear()
            self.segment_current = 0

    def push_srt_history(self) -> None:
        """Push a deep copy of current srt_entries to history stack."""
        import copy
        with self._lock:
            # Only keep last 20 steps of history to avoid memory bloat
            self.srt_history.append(copy.deepcopy(self.srt_entries))
            if len(self.srt_history) > 20:
                self.srt_history.pop(0)

    def undo_srt_change(self) -> bool:
        """Pop the last state from history and restore to srt_entries. Returns True if successful."""
        with self._lock:
            if not self.srt_history:
                return False
            self.srt_entries = self.srt_history.pop()
            return True
            self.segment_total = 0