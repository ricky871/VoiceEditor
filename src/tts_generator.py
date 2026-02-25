#!/usr/bin/env python3
# Example: uv run -p .\.venv\Scripts\python.exe src/tts_generator.py --ref_voice .\work\voice_ref.wav --srt .\work\*.srt --stitch
"""
IndexTTS2 API-B Subtitle-Driven Zero-Shot TTS with Fine-Grained Duration Control

This script uses Chain-of-Thought (COT) methodology to organize processing into
discrete, single-responsibility functions that form a coherent pipeline.

Pipeline Stages (COT Chain):
  Stage 1: Parse arguments & create configuration
  Stage 2: Resolve & validate configuration paths (fallback to bundled models)
  Stage 3: Setup logging and system checks
  Stage 4: Load TTS model
  Stage 5: Parse SRT subtitles
  Stage 6: Synthesize audio segments (per subtitle)
  Stage 7: Save manifest with metadata
  Stage 8: Stitch segments if requested
  Stage 9: Mux audio into video if requested
  Stage 10: Report metrics and cleanup
"""

import argparse
import json
import math
import os
import sys
import logging
import time
import subprocess
import signal
import tempfile
from glob import glob
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import contextmanager
from tqdm import tqdm

try:
    import pysrt
except ImportError:
    print(
        "Missing dependency pysrt. Run 'uv pip install pysrt pydub'",
        file=sys.stderr,
    )
    sys.exit(2)

try:
    from pydub import AudioSegment
    from pydub.effects import speedup
except ImportError:
    print(
        "Missing dependency pydub. Run 'uv pip install pysrt pydub'",
        file=sys.stderr,
    )
    sys.exit(2)

import torch
import numpy as np

# Workaround for numpy 2.0+ compatibility with older packages that use np.bool8
if not hasattr(np, 'bool8'):
    np.bool8 = np.bool_
if not hasattr(np, 'int8'):
    np.int8 = np.int_
if not hasattr(np, 'float32'):
    np.float32 = np.float_

# Use Chinese mirror and cache in project root
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# When running from main.py or root, .cache is in root. 
# If running from src/, we want to point to root/.cache.
_project_root = Path(__file__).parent.parent
hf_cache_dir = _project_root / ".cache" / "hf"
hf_cache_dir.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("HF_HOME", str(hf_cache_dir))
os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hf_cache_dir))

# ============================================================================
# SECTION 1: CONFIGURATION MANAGEMENT
# ============================================================================


class Config:
    """
    Centralized configuration object for the TTS pipeline.
    """

    def __init__(
        self,
        cfg_path: str = "checkpoints/config.yaml",
        model_dir: str = "checkpoints",
        ref_voice: str = "work/voice_ref.wav",
        srt_pattern: str = "work/*.srt",
        out_dir: str = "work/out_segs",
        duration_mode: str = "seconds",
        tokens_per_sec: float = 150.0,
        emo_text: str = "",
        emo_audio: str = "",
        emo_alpha: float = 0.8,
        lang: str = "zh",
        speed: float = 1.0,
        stitch: bool = False,
        sample_rate: int = 44100,
        gain_db: float = -1.5,
        video: str = "",
        output_video: str = "",
        verbose: bool = False,
    ):
        self.cfg_path = Path(cfg_path)
        self.model_dir = Path(model_dir)
        self.ref_voice = Path(ref_voice)
        self.srt_pattern = srt_pattern
        self.out_dir = Path(out_dir)
        self.duration_mode = duration_mode
        self.tokens_per_sec = tokens_per_sec
        self.emo_text = emo_text
        self.emo_audio = emo_audio
        self.emo_alpha = emo_alpha
        self.lang = lang
        self.speed = speed
        self.stitch = stitch
        self.sample_rate = sample_rate
        self.gain_db = gain_db
        self.video = Path(video) if video else None
        self.output_video = Path(output_video) if output_video else None
        self.verbose = verbose
        self.default_model_dir = Path("checkpoints")

    @classmethod
    def from_args(cls, args):
        """Factory method to create Config from argparse.Namespace."""
        return cls(
            cfg_path=args.cfg_path,
            model_dir=args.model_dir,
            ref_voice=args.ref_voice,
            srt_pattern=args.srt,
            out_dir=args.out_dir,
            duration_mode=args.duration_mode,
            tokens_per_sec=args.tokens_per_sec,
            emo_text=args.emo_text,
            emo_audio=args.emo_audio,
            emo_alpha=args.emo_alpha,
            lang=args.lang,
            speed=args.speed,
            stitch=args.stitch,
            sample_rate=args.sample_rate,
            gain_db=args.gain_db,
            video=args.video,
            output_video=args.output_video,
            verbose=args.verbose,
        )


# ============================================================================
# SECTION 2: UTILITY & CONVERSION FUNCTIONS
# ============================================================================


def ensure_dir(path: Path) -> Path:
    """Create directory and all parent directories."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def ms_to_seconds(ms: float) -> float:
    """Convert milliseconds to seconds."""
    return ms / 1000.0


def round_3sec(value: float) -> float:
    """Round to 3 decimal places."""
    return round(value, 3)


# ============================================================================
# SECTION 3: FILE & PATH OPERATIONS
# ============================================================================


def resolve_srt_path(pattern: str) -> Path:
    """
    Resolve SRT file path from pattern.
    """
    candidate = Path(pattern)
    if candidate.exists():
        return candidate
    matches = sorted(glob(pattern, recursive=True))
    if not matches:
        raise FileNotFoundError(pattern)
    resolved = Path(matches[0])
    logging.info("Resolved subtitle wildcard %s -> %s", pattern, resolved)
    return resolved


def guess_video_from_srt(srt_path: Path) -> Optional[Path]:
    """
    Guess video file path from SRT location.
    """
    video_exts = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".m4v"}
    stem_match = srt_path.with_suffix(".mp4")
    if stem_match.exists():
        return stem_match
    matches = [
        candidate
        for candidate in srt_path.parent.glob(f"{srt_path.stem}.*")
        if candidate.suffix.lower() in video_exts
    ]
    return sorted(matches)[0] if matches else None


def resolve_config_paths(config: Config) -> None:
    """
    Resolve and validate configuration paths.
    """
    # Use absolute project root to resolve bundled paths
    project_root = Path(__file__).parent.parent
    
    if not config.cfg_path.exists():
        alt_cfg = project_root / "index-tts" / config.cfg_path
        if alt_cfg.exists():
            logging.info("Config %s not found, using bundled %s", config.cfg_path, alt_cfg)
            config.cfg_path = alt_cfg
            if config.model_dir == config.default_model_dir:
                alt_model_dir = alt_cfg.parent
                logging.info("Redirecting model_dir to match config parent %s", alt_model_dir)
                config.model_dir = alt_model_dir

    if not config.model_dir.exists():
        alt_model_dir = project_root / "index-tts" / config.model_dir
        if alt_model_dir.exists():
            logging.info("Model dir %s not found, using bundled %s", config.model_dir, alt_model_dir)
            config.model_dir = alt_model_dir

    config.cfg_path = config.cfg_path.resolve()
    config.model_dir = config.model_dir.resolve()


# ============================================================================
# SECTION 4: SUBTITLE & METADATA PARSING
# ============================================================================


def parse_srt(path: Path) -> List[Dict]:
    """
    Parse SRT subtitle file into structured entries.
    """
    subs = pysrt.open(str(path), encoding="utf-8")
    entries: List[Dict] = []
    for index, item in enumerate(subs, start=1):
        clean_text = " ".join(item.text.replace("\r", " ").splitlines()).strip()
        if not clean_text:
            continue
        start = item.start.ordinal
        end = item.end.ordinal
        start = max(0, start)
        end = max(start, end)
        entries.append(
            {
                "id": index,
                "text": clean_text,
                "start_ms": start,
                "end_ms": end,
                "dur_ms": end - start,
            }
        )
    return entries


# ============================================================================
# SECTION 5: AUDIO PROCESSING
# ============================================================================


def time_stretch_or_pad(segment: AudioSegment, target_ms: float) -> AudioSegment:
    """
    Stretch or pad audio segment to target duration.
    """
    target = max(1, int(round(target_ms)))
    current = len(segment)
    delta = target - current
    if abs(delta) <= 15:
        return segment
    if delta > 0:
        padding = AudioSegment.silent(duration=delta, frame_rate=segment.frame_rate)
        return segment + padding
    return segment[:target]


def retime_segment_to_target(
    segment: AudioSegment,
    target_ms: float,
    sample_rate: int,
    tolerance_ms: int = 1,
) -> tuple[AudioSegment, int, float]:
    """
    Retime a segment so its length strictly matches the subtitle window.
    Optimized for consistent speech rate using atempo when possible to 
    avoid the "jerky" or "fluctuating" sound of simple silence-stripping.
    """
    target = max(1, int(round(target_ms)))
    seg = segment.set_frame_rate(sample_rate).set_channels(1)
    current = len(seg)
    speed_factor = 1.0

    if abs(current - target) <= tolerance_ms:
        return seg, current, speed_factor

    speed_factor = current / target

    # Only apply speed adjustment if difference is more than 2% or 50ms
    # Small differences are better handled by slight padding or truncation
    if 0.98 <= speed_factor <= 1.02 or abs(current - target) < 50:
        if current > target:
            seg = seg[:target]
        else:
            padding = AudioSegment.silent(duration=target - current, frame_rate=seg.frame_rate)
            seg = seg + padding
        return seg, len(seg), 1.0

    # For larger differences, use a high-quality speedup method
    try:
        # Use ffmpeg for higher quality smooth speed change (atempo filter)
        # This keeps the speech rate uniform and avoids word truncation artifacts
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_in:
            seg.export(tmp_in.name, format="wav")
            tmp_in_path = Path(tmp_in.name)
        
        tmp_out_path = tmp_in_path.with_suffix(".out.wav")
        
        # atempo range is [0.5, 2.0]. Chain filters for extreme factors if necessary.
        filters = []
        temp_s = speed_factor
        while temp_s > 2.0:
            filters.append("atempo=2.0")
            temp_s /= 2.0
        while temp_s < 0.5:
            filters.append("atempo=0.5")
            temp_s /= 0.5
        filters.append(f"atempo={temp_s}")

        cmd = [
            "ffmpeg", "-y", "-i", str(tmp_in_path),
            "-filter:a", ",".join(filters),
            str(tmp_out_path)
        ]
        result = subprocess.run(cmd, capture_output=True, check=False)
        
        if result.returncode == 0 and tmp_out_path.exists():
            seg = AudioSegment.from_file(str(tmp_out_path)).set_frame_rate(sample_rate).set_channels(1)
        else:
            raise RuntimeError(f"FFmpeg failed: {result.stderr.decode(errors='ignore')}")

        if tmp_in_path.exists(): tmp_in_path.unlink()
        if tmp_out_path.exists(): tmp_out_path.unlink()
        
        # Final fine-tuning to ensure exact length
        if len(seg) > target:
            seg = seg[:target]
        elif len(seg) < target:
            seg = seg + AudioSegment.silent(duration=target - len(seg), frame_rate=sample_rate)
            
    except Exception as e:
        # Fallback to pydub speedup but with larger chunk size for better stability
        logging.warning("Smooth speedup failed, falling back to pydub: %s", e)
        if current > target:
            # chunk_size 60 and crossfade 10 are slightly better for speech than 50/5
            seg = speedup(seg, playback_speed=speed_factor, chunk_size=60, crossfade=10)
            if len(seg) > target:
                seg = seg[:target]
        else:
            padding = AudioSegment.silent(duration=target - current, frame_rate=seg.frame_rate)
            seg = seg + padding
            
    return seg, len(seg), speed_factor


def build_duration_candidates(mode: str, target_ms: float, tokens_per_sec: float) -> List[Dict]:
    """
    Build list of duration parameter candidates for TTS inference.
    """
    tokens = max(1, math.ceil(ms_to_seconds(target_ms) * tokens_per_sec))
    return [
        {"max_mel_tokens": tokens},
        {"max_generate_length": tokens},
    ]


def safe_infer(
    tts,
    base_kwargs: Dict,
    duration_candidates: List[Dict],
    *,
    verbose: bool,
    segment_index: int,
) -> None:
    """
    Safely run TTS inference with fallback duration parameters.
    """
    last_exc = None
    for attempt in duration_candidates:
        kwargs = {**base_kwargs, **attempt}
        try:
            result = tts.infer(**kwargs)
            if verbose:
                logging.debug("Segment %d inference kwargs: %s", segment_index, attempt)
            return
        except TypeError as exc:
            message = str(exc)
            key = next(iter(attempt))
            if key in message:
                last_exc = exc
                logging.debug("Duration key %s unsupported, trying fallback.", key)
                continue
            raise
    if last_exc:
        raise last_exc


def stitch_segments(
    manifest: List[Dict],
    sample_rate: int,
    gain_db: float,
) -> AudioSegment:
    """
    Stitch individual audio segments into final composite audio.
    """
    if not manifest:
        raise ValueError("Manifest is empty")

    final_length = max(item["end_ms"] for item in manifest) + 100
    final_audio = AudioSegment.silent(
        duration=final_length,
        frame_rate=sample_rate,
    ).set_channels(1)

    for entry in tqdm(manifest, desc="Stitching segments", unit="seg"):
        segment_audio = AudioSegment.from_file(entry["wav"]).set_channels(1).set_frame_rate(sample_rate)
        segment_audio, _, _ = retime_segment_to_target(
            segment_audio,
            entry["dur_target_ms"],
            sample_rate,
        )
        final_audio = final_audio.overlay(segment_audio, position=entry["start_ms"])

    final_audio += gain_db
    return final_audio.set_frame_rate(sample_rate)


# ============================================================================
# SECTION 6: VIDEO & MULTIMEDIA OPERATIONS
# ============================================================================


def mux_audio_into_video(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """
    Mux audio track into video using ffmpeg.
    """
    ensure_dir(output_path.parent)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map",
        "0:s?",
        "-map_metadata",
        "0",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error("ffmpeg failed to mux audio: %s", result.stderr.strip())
        raise RuntimeError("ffmpeg muxing failed")


# ============================================================================
# SECTION 7: MODEL INITIALIZATION & DEPENDENCY SETUP
# ============================================================================


def setup_python_path() -> None:
    """Ensure index-tts package is on sys.path for imports. (COT Stage 4)"""
    # From src/tts_generator.py, go up to root
    src_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(src_dir)
    index_tts_path = os.path.join(project_root, "index-tts")
    if index_tts_path not in sys.path:
        sys.path.append(index_tts_path)


def load_tts_model(config: Config):
    """
    Load IndexTTS2 model with error handling.
    """
    try:
        from indextts.infer_v2 import IndexTTS2
    except (ImportError, AttributeError) as exc:
        logging.error(
            "Failed to import IndexTTS2: %s. Please run 'python main.py setup' first.",
            exc,
        )
        raise

    tts = IndexTTS2(
        cfg_path=str(config.cfg_path),
        model_dir=str(config.model_dir),
        use_fp16=True,
        use_cuda_kernel=False,
        use_deepspeed=False,
    )
    return tts


# ============================================================================
# SECTION 8: VALIDATION & CHECKS
# ============================================================================


def validate_config_paths(config: Config) -> int:
    """
    Validate that all required paths exist after resolution.
    """
    if not config.cfg_path.exists():
        logging.error("Config file %s not found.", config.cfg_path)
        return 1
    if not config.model_dir.exists():
        logging.error("Model directory %s not found.", config.model_dir)
        return 1
    if not config.ref_voice.exists():
        logging.error("Reference voice %s not found.", config.ref_voice)
        return 1
    return 0


def validate_srt_and_resolve(config: Config) -> Optional[Path]:
    """
    Resolve and validate SRT path.
    """
    try:
        srt_path = resolve_srt_path(config.srt_pattern)
    except FileNotFoundError:
        logging.error("Subtitle pattern %s matched nothing.", config.srt_pattern)
        return None

    if not srt_path.exists():
        logging.error("Subtitle file %s not found after resolving wildcard.", srt_path)
        return None

    return srt_path


# ============================================================================
# SECTION 9: SEGMENT SYNTHESIS (Core Processing)
# ============================================================================


def load_existing_manifest(out_dir: Path) -> Dict[int, Dict]:
    """
    Load existing manifest to skip already-generated segments.
    """
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        return {}
    
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        existing = {idx: entry for idx, entry in enumerate(entries, start=1)}
        logging.info("Loaded existing manifest with %d entries", len(existing))
        return existing
    except Exception as exc:
        logging.warning("Failed to load existing manifest: %s", exc)
        return {}


def synthesize_segments(
    tts,
    entries: List[Dict],
    config: Config,
) -> tuple[List[Dict], int]:
    """
    Synthesize audio for each subtitle entry.
    """
    existing_manifest = load_existing_manifest(config.out_dir)
    manifest: List[Dict] = []
    failed_segments = []

    pbar = tqdm(entries, desc="Generating TTS", unit="seg")
    for seq, entry in enumerate(pbar, start=1):
        seg_name = f"seg_{seq:04d}.wav"
        seg_path = config.out_dir / seg_name

        if seq in existing_manifest and seg_path.exists():
            pbar.set_postfix_str(f"Skipping {seq}")
            manifest.append(existing_manifest[seq])
            continue

        pbar.set_description(f"TTS {seq}/{len(entries)}: {entry['text'][:20]}...")

        duration_candidates = build_duration_candidates(
            config.duration_mode,
            entry["dur_ms"],
            config.tokens_per_sec,
        )

        base_tts_kwargs = {
            "spk_audio_prompt": str(config.ref_voice),
            "text": entry["text"],
            "output_path": str(seg_path),
            "emo_alpha": config.emo_alpha,
            "verbose": config.verbose,
        }
        if config.emo_audio:
            base_tts_kwargs["emo_audio_prompt"] = str(config.emo_audio)
        if config.emo_text:
            base_tts_kwargs["use_emo_text"] = True
            base_tts_kwargs["emo_text"] = config.emo_text

        try:
            safe_infer(
                tts,
                base_tts_kwargs,
                duration_candidates,
                verbose=config.verbose,
                segment_index=seq,
            )
        except Exception as exc:
            logging.error(
                "Segment %d synthesis failed: %s",
                seq,
                str(exc)[:500],
            )
            failed_segments.append({"seq": seq, "text": entry["text"], "error": str(exc)})
            continue

        if not seg_path.exists():
            logging.error("Segment %d: Failed to generate output file.", seq)
            continue

        try:
            generated = AudioSegment.from_file(seg_path)
            retimed, actual_ms, speed_factor = retime_segment_to_target(
                generated,
                entry["dur_ms"],
                config.sample_rate,
            )
            retimed.export(seg_path, format="wav")
            diff_ms = actual_ms - entry["dur_ms"]

            manifest.append(
                {
                    "id": entry["id"],
                    "text": entry["text"],
                    "start_ms": entry["start_ms"],
                    "end_ms": entry["end_ms"],
                    "wav": str(seg_path),
                    "dur_target_ms": entry["dur_ms"],
                    "dur_actual_ms": actual_ms,
                    "diff_ms": diff_ms,
                    "speed_factor": round_3sec(speed_factor),
                }
            )

            # Use pbar.write to avoid breaking the progress bar
            pbar.write(
                f"Segment {seq} | target {ms_to_seconds(entry['dur_ms']):.2f}s | "
                f"actual {ms_to_seconds(actual_ms):.2f}s | diff {ms_to_seconds(diff_ms):.2f}s | "
                f"speed x{round_3sec(speed_factor):.3f}"
            )
        except Exception as exc:
            logging.error("Segment %d: Failed to process audio: %s", seq, str(exc))
            continue

    if failed_segments:
        return manifest, 1 if not manifest else 0
    return manifest, 0


# ============================================================================
# SECTION 10: MANIFEST & METRICS
# ============================================================================


def save_manifest(manifest: List[Dict], out_dir: Path) -> int:
    """
    Save manifest with per-segment metadata.
    """
    manifest_path = out_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, ensure_ascii=False, indent=2)

    total_target = sum(item["dur_target_ms"] for item in manifest)
    total_actual = sum(item["dur_actual_ms"] for item in manifest)
    total_diff = sum(abs(item["diff_ms"]) for item in manifest)
    avg_diff = total_diff / len(manifest) if manifest else 0

    logging.info(
        "Manifest saved to %s | entries %d | target %.2f s | actual %.2f s | avg diff %.3f s",
        manifest_path,
        len(manifest),
        ms_to_seconds(total_target),
        ms_to_seconds(total_actual),
        ms_to_seconds(avg_diff),
    )
    return 0


# ============================================================================
# SECTION 11: VIDEO MUXING & STITCHING
# ============================================================================


def process_final_audio(
    manifest: List[Dict],
    srt_path: Path,
    config: Config,
) -> Optional[Path]:
    """
    Process final audio: stitch segments and optionally mux into video.
    """
    need_final_audio = bool(config.stitch or config.video or config.output_video)
    if not (need_final_audio and manifest):
        return None

    final_audio = stitch_segments(manifest, config.sample_rate, config.gain_db)
    
    work_dir = config.out_dir.parent
    final_audio_path = work_dir / "merged_audio.wav"
    ensure_dir(final_audio_path.parent)
    final_audio.export(str(final_audio_path), format="wav")
    logging.info("Merged audio saved to %s", final_audio_path)

    video_source = config.video if config.video else guess_video_from_srt(srt_path)
    if video_source and video_source.exists():
        default_video_out = work_dir / f"{video_source.stem}_dubbed{video_source.suffix}"
        output_video = config.output_video if config.output_video else default_video_out
        try:
            mux_audio_into_video(video_source, final_audio_path, output_video)
            logging.info("Dubbed video created and saved to %s", output_video)
        except RuntimeError:
            return None
    return final_audio_path


# ============================================================================
# SECTION 12: MAIN ORCHESTRATION
# ============================================================================


def run_tts_generation(args):
    """Entry point for modular usage."""
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    logging.info("Torch CUDA available: %s", torch.cuda.is_available())
    config = Config.from_args(args)
    resolve_config_paths(config)

    if validate_config_paths(config) != 0:
        return 1

    srt_path = validate_srt_and_resolve(config)
    if not srt_path:
        return 1

    entries = parse_srt(srt_path)
    if not entries:
        logging.warning("No valid subtitles parsed.")
        return 0

    logging.info("Parsed %d subtitle entries.", len(entries))
    total_dur_s = sum(ms_to_seconds(e["dur_ms"]) for e in entries)
    logging.info("Total audio duration to synthesize: %.2f seconds", total_dur_s)
    
    ensure_dir(config.out_dir)
    setup_python_path()
    
    try:
        logging.info("Loading TTS model...")
        tts = load_tts_model(config)
        logging.info("TTS model loaded successfully")
    except Exception as exc:
        logging.error("Failed to load TTS model: %s", exc)
        return 2

    start_time = time.time()
    manifest, err = synthesize_segments(tts, entries, config)

    if manifest:
        save_manifest(manifest, config.out_dir)
        process_final_audio(manifest, srt_path, config)

    elapsed = time.time() - start_time
    logging.info("Synthesis completed in %.2f seconds", elapsed)
    return 0 if manifest else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="IndexTTS2 Subtitle-Driven TTS")
    parser.add_argument("--cfg_path", default="checkpoints/config.yaml")
    parser.add_argument("--model_dir", default="checkpoints")
    parser.add_argument("--ref_voice", default="work/voice_ref.wav")
    parser.add_argument("--srt", default="work/*.srt")
    parser.add_argument("--out_dir", default="work/out_segs")
    parser.add_argument("--duration_mode", choices=["seconds", "tokens"], default="seconds")
    parser.add_argument("--tokens_per_sec", type=float, default=150.0)
    parser.add_argument("--emo_text", default="")
    parser.add_argument("--emo_audio", default="")
    parser.add_argument("--emo_alpha", type=float, default=0.8)
    parser.add_argument("--lang", default="zh")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--stitch", action="store_true")
    parser.add_argument("--sample_rate", type=int, default=44100)
    parser.add_argument("--gain_db", type=float, default=-1.5)
    parser.add_argument("--video", default="")
    parser.add_argument("--output_video", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return run_tts_generation(args)


if __name__ == "__main__":
    sys.exit(main())
