import logging
import subprocess
import platform
import numpy as np
import librosa
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from tqdm import tqdm
from pydub import AudioSegment


def ensure_safe_srt_for_ffmpeg(srt_path: Path, work_dir: str = "work") -> str:
    """
    Ensure SRT path is safe for FFmpeg filter usage.
    If path contains special characters (spaces, quotes, etc), copy to a safe location.
    Returns the path formatted for use inside an FFmpeg filter string:
    - Forward slashes (backslashes converted via as_posix)
    - Windows drive letter colon escaped as \\: (e.g. C\\:/path/file.srt)
    """
    srt_p = Path(srt_path).resolve()

    # Check for characters that are problematic in FFmpeg filter strings.
    # Backslashes are NOT listed here — they are handled later via as_posix().
    problematic_chars = ["'", '"', ' ', '&', '|', '<', '>', '(', ')', '[', ']', '{', '}', ';', '`', '$']
    has_problematic = any(char in str(srt_p) for char in problematic_chars)

    if has_problematic:
        logging.info("FFmpeg: Detected special characters in subtitle path. Using safe copy in work directory.")
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        safe_srt = Path(work_dir) / "ffmpeg_safe_subtitles.srt"
        shutil.copy(str(srt_p), str(safe_srt))
        srt_p = safe_srt.resolve()

    # Convert to POSIX forward slashes and escape the Windows drive-letter colon.
    # FFmpeg filter syntax requires:  C\:/path/to/file.srt
    return srt_p.as_posix().replace(":", "\\:")

def retime_segment_to_target(
    segment: AudioSegment,
    target_ms: float,
    sample_rate: int,
    tolerance_ms: int = 1,
) -> tuple[AudioSegment, int, float]:
    """
    Retime a segment so its length strictly matches the subtitle window.
    Uses librosa.effects.time_stretch for high-quality in-memory stretching.
    """
    target = max(1, int(round(target_ms)))
    # Ensure consistent format
    seg = segment.set_frame_rate(sample_rate).set_channels(1).set_sample_width(2)
    current = len(seg)
    speed_factor = 1.0

    if abs(current - target) <= tolerance_ms:
        return seg, current, speed_factor

    speed_factor = current / target

    # 1. OPTIMIZATION: If generated speech is faster than target (shorter duration),
    # do NOT slow it down. Instead, pad with silence to fill the target duration.
    # This keeps natural speech rate and avoids "slow motion" effect.
    if current < target:
        padding = AudioSegment.silent(duration=target - current, frame_rate=seg.frame_rate)
        seg = seg + padding
        return seg, len(seg), 1.0

    # Handle small differences (over-generated) with simple truncation
    if 1.0 <= speed_factor <= 1.02 or (current - target) < 50:
        seg = seg[:target]
        return seg, len(seg), 1.0

    # For larger differences (over-generated significantly), use librosa to speed up
    try:
        # 1. Convert AudioSegment to numpy array
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32)
        # Normalize if necessary (pydub uses int16 by default for 2 bytes)
        max_val = float(1 << (8 * seg.sample_width - 1))
        samples /= max_val

        # 2. Apply time stretch
        # librosa expects speed_factor relative to original (1.5 is faster, 0.5 is slower)
        # our speed_factor is current/target, which matches librosa's 'rate'
        # e.g., if current=1000ms, target=500ms, rate=2.0 (speed up)
        y_stretched = librosa.effects.time_stretch(samples, rate=speed_factor)

        # 3. Convert back to AudioSegment
        # Denormalize
        y_out = (y_stretched * (max_val - 1)).astype(np.int16)
        seg = AudioSegment(
            y_out.tobytes(),
            frame_rate=sample_rate,
            sample_width=2,
            channels=1
        )
        
        # Final fine-tuning (rounding errors)
        if len(seg) > target:
            seg = seg[:target]
        elif len(seg) < target:
            seg = seg + AudioSegment.silent(duration=target - len(seg), frame_rate=sample_rate)
            
    except Exception as e:
        logging.warning(f"Librosa speedup failed: {e}. Falling back to simple truncation/padding.")
        if current > target:
            seg = seg[:target]
        else:
            padding = AudioSegment.silent(duration=target - current, frame_rate=seg.frame_rate)
            seg = seg + padding
            
    return seg, len(seg), speed_factor

def stitch_segments_from_manifest(
    manifest: List[Dict],
    sample_rate: int,
    gain_db: float,
    manifest_dir: Path | None = None,  # Directory containing manifest.json for relative path resolution
) -> AudioSegment:
    """
    Stitch individual audio segments from manifest into a final composite audio segment.
    Automatically skips segments marked as failed.
    
    Args:
        manifest: List of segment entries from manifest.json
        sample_rate: Target sample rate in Hz
        gain_db: Gain in dB to apply
        manifest_dir: Directory containing manifest.json (for relative path resolution)
    """
    if not manifest:
        raise ValueError("Manifest is empty")

    # Default manifest_dir to current working directory if not specified
    if manifest_dir is None:
        manifest_dir = Path.cwd()
    else:
        manifest_dir = Path(manifest_dir).resolve()

    # Filter out failed segments for stitching
    valid_entries = [item for item in manifest if not item.get("failed", False)]
    failed_count = len(manifest) - len(valid_entries)
    
    if failed_count > 0:
        logging.warning(f"Stitching: Skipping {failed_count} failed segments. Using {len(valid_entries)}/{len(manifest)} valid segments.")
    
    if not valid_entries:
        raise ValueError("All segments failed. Cannot create final audio.")

    final_length = max(item["end_ms"] for item in valid_entries) + 100
    final_audio = AudioSegment.silent(
        duration=final_length,
        frame_rate=sample_rate,
    ).set_channels(1)

    for entry in tqdm(valid_entries, desc="Stitching segments", unit="seg"):
        # Skip failed entries
        if entry.get("failed", False):
            logging.debug(f"Skipping failed segment {entry.get('id', '?')}: {entry.get('error_reason', 'unknown')}")
            continue
            
        raw_wav = entry.get("wav")
        if not raw_wav:
            logging.warning(f"Segment {entry.get('id', '?')} has no wav path. Skipping.")
            continue
            
        # Resolve path - support both absolute and relative paths (with forward slashes for portability)
        wav_path = Path(raw_wav)
        
        # If relative, resolve relative to manifest_dir
        if not wav_path.is_absolute():
            wav_path = manifest_dir / wav_path
        
        wav_path = wav_path.resolve()

        if not wav_path.exists():
            # In some multi-platform or docker scenarios, relative paths from manifest may fail
            # Let's try to resolve it relative to manifest location or CWD if absolute fails
            logging.warning(f"Segment file not found at {wav_path}. Attempting fallback resolution.")
            # manifest is often in work/out_segs, and segments are there too
            local_name = Path(raw_wav).name
            # common places to look
            candidates = [
                manifest_dir / local_name,
                Path("work/out_segs") / local_name,
                Path("out_segs") / local_name,
                Path(".") / local_name,
                Path(__file__).parent.parent.parent / "work" / "out_segs" / local_name
            ]
            found = False
            for cand in candidates:
                if cand.exists():
                    wav_path = cand
                    found = True
                    logging.info(f"Resolved segment to: {wav_path}")
                    break
            
            if not found:
                logging.error(f"Failed to find segment {entry.get('id', 'unknown')} at {raw_wav} or common fallbacks.")
                continue

        try:
            segment_audio = AudioSegment.from_file(str(wav_path)).set_channels(1).set_frame_rate(sample_rate)
            segment_audio, _, _ = retime_segment_to_target(
                segment_audio,
                entry["dur_target_ms"],
                sample_rate,
            )
            final_audio = final_audio.overlay(segment_audio, position=entry["start_ms"])
        except Exception as e:
            logging.error(f"Failed to process segment {entry.get('id', 'unknown')}: {e}")
            continue

    final_audio += gain_db
    return final_audio.set_frame_rate(sample_rate)

def mux_audio_video(video_path: Path, audio_path: Path, output_path: Path, srt_path: Path | None = None) -> None:
    """
    Mux audio track into video using ffmpeg. Optionally burn subtitles at the top.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
    ]

    if srt_path and srt_path.exists():
        # Burn subtitles at the top (Alignment=6)
        # Use safe path handling for FFmpeg
        safe_srt_path = ensure_safe_srt_for_ffmpeg(Path(srt_path), work_dir=str(output_path.parent))
        
        # Select a font that supports Chinese characters for Windows/Linux
        # Alignment=6 is Top-Center. Alignment=2 is Bottom-Center (default).
        font_style = "Alignment=6"
        if platform.system() == "Windows":
            # Microsoft YaHei is standard on Windows. 
            # Note: FFmpeg subtitles filter on Windows often needs the font name, not path.
            font_style += ",Fontname=Microsoft YaHei"
        else:
            # On Linux, try common fonts like Noto Sans CJK or WenQuanYi
            # This is a fallback string; FFmpeg will try to find a match.
            font_style += ",Fontname=Noto Sans CJK SC,Fontname=WenQuanYi Micro Hei"

        filter_str = f"subtitles='{safe_srt_path}':force_style='{font_style}'"
        
        cmd.extend([
            "-filter_complex", filter_str,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        ])
    else:
        cmd.extend([
            "-c:v", "copy",
        ])

    cmd.extend([
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "0:s?",
        "-map_metadata", "0",
        str(output_path),
    ])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error(f"ffmpeg failed to mux audio: {result.stderr.strip()}")
        raise RuntimeError("ffmpeg muxing failed")
