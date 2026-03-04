import json
import math
import logging
from pathlib import Path
from glob import glob
from typing import Dict, List, Optional, Any
from tqdm import tqdm
from pydub import AudioSegment
import pysrt

from .audio_pipeline import retime_segment_to_target

class SRTProcessor:
    """
    Handles subtitle parsing and resolving related paths.
    """
    @staticmethod
    def parse(path: Path) -> List[Dict]:
        """Parse SRT into structured entries and ensure Simplified Chinese."""
        try:
            from opencc import OpenCC
            cc = OpenCC('t2s')
        except ImportError:
            logging.warning("opencc could not be imported. Proceeding without T2S conversion.")
            cc = None

        subs = pysrt.open(str(path), encoding="utf-8")
        entries = []
        for index, item in enumerate(subs, start=1):
            text = item.text.replace("\r", " ").strip()
            if not text: continue
            
            # Format text and convert to simplified if CC is available
            clean_text = " ".join(text.splitlines())
            if cc:
                clean_text = cc.convert(clean_text)

            start = max(0, item.start.ordinal)
            end = max(start, item.end.ordinal)
            entries.append({
                "id": index, "text": clean_text, 
                "start_ms": start, "end_ms": end, "dur_ms": end - start
            })
        return entries

    @staticmethod
    def resolve_path(pattern: str) -> Path:
        """Resolve wildcard pattern to single SRT file."""
        candidate = Path(pattern)
        if candidate.exists(): return candidate
        matches = sorted(glob(pattern, recursive=True))
        if not matches: raise FileNotFoundError(pattern)
        return Path(matches[0])

    @staticmethod
    def guess_video(srt_path: Path) -> Optional[Path]:
        """Guess video file from SRT location."""
        video_exts = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".m4v"}
        stem_match = srt_path.with_suffix(".mp4")
        if stem_match.exists(): return stem_match
        matches = [c for c in srt_path.parent.glob(f"{srt_path.stem}.*") if c.suffix.lower() in video_exts]
        return sorted(matches)[0] if matches else None

class TTSSynthesizer:
    """
    Orchestrates the synthesis process from entries to audio segments.
    """
    def __init__(self, tts, config: Any):
        self.tts = tts
        self.config = config

    def build_duration_candidates(self, target_ms: float) -> List[Dict]:
        """Build candidates for inference duration parameters."""
        tokens = max(1, math.ceil((target_ms / 1000.0) * self.config.tokens_per_sec))
        return [{"max_mel_tokens": tokens}, {"max_generate_length": tokens}]

    def safe_infer(self, base_kwargs: Dict, candidates: List[Dict], seq: int):
        """Safely run TTS with fallback parameters."""
        last_exc = None
        for attempt in candidates:
            kwargs = {**base_kwargs, **attempt}
            try:
                self.tts.infer(**kwargs)
                return
            except TypeError as exc:
                if next(iter(attempt)) in str(exc):
                    last_exc = exc; continue
                raise
        if last_exc: raise last_exc

    def synthesize(self, entries: List[Dict]) -> tuple[List[Dict], int]:
        """Synthesize audio segments for all entries."""
        # 1. Explicitly check ref_voice existence with detailed error
        if not self.config.ref_voice or not self.config.ref_voice.exists():
             logging.error(f"CRITICAL: Reference voice file missing at: {self.config.ref_voice}")
             logging.error(f"Absolute path: {self.config.ref_voice.resolve() if self.config.ref_voice else 'None'}")
             raise FileNotFoundError(f"Reference voice file not found: {self.config.ref_voice}")

        # 2. Verify output directory
        if not self.config.out_dir.exists():
            logging.info(f"Creating output directory: {self.config.out_dir}")
            self.config.out_dir.mkdir(parents=True, exist_ok=True)
        elif not self.config.out_dir.is_dir():
             raise NotADirectoryError(f"Output path is not a directory: {self.config.out_dir}")

        manifest_path = self.config.out_dir / "manifest.json"
        existing = {}
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    entries_j = json.load(f)
                existing = {idx: entry for idx, entry in enumerate(entries_j, start=1)}
            except Exception: pass

        manifest = []
        failed = []
        canceled = False
        cancel_event = getattr(self.config, "cancel_event", None)
        pbar = tqdm(entries, desc="正在生成语音", unit="句", disable=not self.config.verbose)
        for seq, entry in enumerate(pbar, start=1):
            if cancel_event is not None and cancel_event.is_set():
                logging.warning(">> 合成已取消，正在停止（已完成片段将保留）")
                canceled = True
                break
            seg_path = self.config.out_dir / f"seg_{seq:04d}.wav"
            
            # Check for cache hit
            if seq in existing and seg_path.exists():
                cached_entry = existing[seq]
                # Verify text consistency to avoid using stale cache if SRT changed
                if cached_entry.get("text", "").strip() == entry["text"].strip():
                    manifest.append(cached_entry)
                    continue
            
            candidates = self.build_duration_candidates(entry["dur_ms"])
            base_kwargs = {
                "spk_audio_prompt": str(self.config.ref_voice),
                "text": entry["text"], "output_path": str(seg_path),
                "emo_alpha": self.config.emo_alpha, "verbose": self.config.verbose,
                "diffusion_steps": self.config.diffusion_steps
            }
            if self.config.emo_audio: base_kwargs["emo_audio_prompt"] = str(self.config.emo_audio)
            if self.config.emo_text: base_kwargs.update({"use_emo_text": True, "emo_text": self.config.emo_text})

            # always log info for every segment so user sees progress
            logging.info(f"Processing Segment {seq}/{len(entries)} | Dur: {entry['dur_ms']}ms | Text: {entry['text'][:30]}...")
            
            # 3. Debug print for first item to confirm correct inputs
            if seq == 1:
                logging.debug(f"Snippet Arguments: {base_kwargs}")

            try:
                self.safe_infer(base_kwargs, candidates, seq)
                generated = AudioSegment.from_file(seg_path)
                retimed, actual_ms, speed = retime_segment_to_target(generated, entry["dur_ms"], self.config.sample_rate)
                retimed.export(seg_path, format="wav")
                
                new_entry = {
                    "id": entry["id"], "text": entry["text"], "start_ms": entry["start_ms"], 
                    "end_ms": entry["end_ms"], "wav": str(seg_path), "dur_target_ms": entry["dur_ms"], 
                    "dur_actual_ms": actual_ms, "diff_ms": actual_ms - entry["dur_ms"], "speed_factor": round(speed, 3)
                }
                manifest.append(new_entry)
                
                # Incremental save every 5 items or if it's the last one
                if seq % 5 == 0 or seq == len(entries):
                    self.save_manifest(manifest, self.config.out_dir, silent=True)
            except Exception as e:
                # 4. Catch critical errors and log full traceback
                logging.error(f"Segment {seq} failed: {e}", exc_info=True)
                if isinstance(e, RuntimeError) and "CUDA out of memory" in str(e):
                     logging.critical("CUDA OOM detected. Stopping.")
                     raise e
                if isinstance(e, FileNotFoundError):
                    logging.critical(f"File missing during synthesis: {e}")
                    raise e
                failed.append(seq)
        
        # Final save to ensure everything is captured
        self.save_manifest(manifest, self.config.out_dir, silent=True)
        if canceled:
            return manifest, 130
        return manifest, (1 if failed and not manifest else 0)

    @staticmethod
    def save_manifest(manifest: List[Dict], out_dir: Path, silent: bool = False):
        """Save manifest and report stats."""
        path = out_dir / "manifest.json"
        
        # Write to temp file first for atomic save
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        
        if manifest and not silent:
            t_tar = sum(m["dur_target_ms"] for m in manifest) / 1000.0
            t_act = sum(m["dur_actual_ms"] for m in manifest) / 1000.0
            logging.info(f">> 生成清单已保存。目标时长: {t_tar:.2f}秒, 实际时长: {t_act:.2f}秒。")
