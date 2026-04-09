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


from src.config import DEFAULT_WORK_DIR

@dataclass
class AppState:
    state_version: int = 2
    processing: bool = False
    synthesizing: bool = False
    step: int = 1
    progress: float = 0.0
    logs: list[str] = field(default_factory=list)
    log_tail: int = 300

    url_or_path: str = ""
    work_dir: str = DEFAULT_WORK_DIR
    whisper_model: str = "small"
    lang: str = "zh"
    emo_text: str = ""
    diffusion_steps: int = 25
    burn_subs: bool = False
    force_regen: bool = False
    output_video: str = ""
    segment_current: int = 0
    segment_total: int = 0
    cancel_event: Event = field(default_factory=Event, repr=False)
    
    # Advanced parameters for TTS synthesis
    speed: float = 1.0
    sample_rate: int = 44100
    gain_db: float = -1.5
    tokens_per_sec: float = 150.0
    emo_alpha: float = 0.8
    max_retries: int = 3

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
            self.segment_total = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert current application state to a serializable dictionary."""
        with self._lock:
            return {
                "version": self.state_version,
                "url_or_path": self.url_or_path,
                "work_dir": self.work_dir,
                "whisper_model": self.whisper_model,
                "lang": self.lang,
                "emo_text": self.emo_text,
                "diffusion_steps": self.diffusion_steps,
                "burn_subs": self.burn_subs,
                "force_regen": self.force_regen,
                "output_video": self.output_video,
                "segment_current": self.segment_current,
                "segment_total": self.segment_total,
                "video_data": self.video_data,
                "srt_path": str(self.srt_path) if self.srt_path else None,
                "logs": self.logs,
            }

    def from_dict(self, data: dict[str, Any]) -> None:
        """Update state from a dictionary. Handles version migration."""
        with self._lock:
            # Check version and apply migrations if needed
            saved_version = int(data.get("version", 1))
            if saved_version < self.state_version:
                # Apply migration logic for older versions
                data = self._migrate_state(data, saved_version, self.state_version)
            
            self.state_version = int(data.get("version", self.state_version))
            self.url_or_path = data.get("url_or_path", "")
            self.work_dir = data.get("work_dir", "work")
            self.whisper_model = data.get("whisper_model", "small")
            self.lang = data.get("lang", "zh")
            self.emo_text = data.get("emo_text", "")
            self.diffusion_steps = data.get("diffusion_steps", 25)
            self.burn_subs = data.get("burn_subs", False)
            self.force_regen = data.get("force_regen", False)
            self.output_video = data.get("output_video", "")
            self.segment_current = data.get("segment_current", 0)
            self.segment_total = data.get("segment_total", 0)
            self.video_data = data.get("video_data")
            srt_path_str = data.get("srt_path")
            self.srt_path = Path(srt_path_str) if srt_path_str else None
            # If we restored a valid SRT path, we are at step 2
            if self.srt_path and self.srt_path.exists():
                self.step = 2
                self.progress = 0.6
            
            # Restore logs if present
            self.logs = data.get("logs", [])
            if self.logs:
                # Re-infer progress from the last log line to be safe
                self._infer_progress_from_log(self.logs[-1])

    @staticmethod
    def _migrate_state(data: dict[str, Any], from_version: int, to_version: int) -> dict[str, Any]:
        """Migrate state from older versions to current version."""
        # Version 1 to 2: Added burn_subs, force_regen, output_video fields
        if from_version < 2:
            if "burn_subs" not in data:
                data["burn_subs"] = False
            if "force_regen" not in data:
                data["force_regen"] = False
            if "output_video" not in data:
                data["output_video"] = ""
        
        data["version"] = to_version
        return data

    def clear_invalid_paths(self) -> list[str]:
        """Clear restored file references that no longer exist on disk. Returns list of cleared fields."""
        cleared = []
        with self._lock:
            # Check SRT path
            if self.srt_path and not self.srt_path.exists():
                logging.warning(f"SRT file no longer exists: {self.srt_path}")
                self.srt_path = None
                self.srt_entries = []
                self.video_data = None
                self.step = 1
                self.progress = 0.0
                cleared.append("srt_path")
                return cleared  # Early return to reset state

            # Check video_data paths
            if isinstance(self.video_data, dict):
                for key in ("video", "audio", "srt", "voice_ref"):
                    value = self.video_data.get(key)
                    if value and not Path(str(value)).exists():
                        logging.warning(f"Video artifact '{key}' no longer exists: {value}")
                        # Clear all video data if any critical file is missing
                        self.video_data = None
                        self.srt_entries = []
                        self.step = 1
                        self.progress = 0.0
                        cleared.append(f"video_data.{key}")
                        break
            
            # Check output_video path if set
            if self.output_video:
                output_path = Path(str(self.output_video))
                if not output_path.parent.exists():
                    logging.warning(f"Output directory no longer exists: {output_path.parent}")
                    self.output_video = ""
                    cleared.append("output_video")
            
            # Check work_dir exists
            if self.work_dir:
                work_path = Path(self.work_dir)
                if not work_path.exists():
                    logging.warning(f"Work directory no longer exists: {work_path}")
                    # Don't clear work_dir, but reset progress
                    self.step = 1
                    self.progress = 0.0
                    self.srt_entries = []
                    self.video_data = None
                    cleared.append("work_dir (reset progress)")
        
        return cleared

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