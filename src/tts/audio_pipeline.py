import logging
import subprocess
import numpy as np
import librosa
from pathlib import Path
from typing import Dict, List, Optional
from tqdm import tqdm
from pydub import AudioSegment

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
) -> AudioSegment:
    """
    Stitch individual audio segments from manifest into a final composite audio segment.
    """
    if not manifest:
        raise ValueError("Manifest is empty")

    final_length = max(item["end_ms"] for item in manifest) + 100
    final_audio = AudioSegment.silent(
        duration=final_length,
        frame_rate=sample_rate,
    ).set_channels(1)

    for entry in tqdm(manifest, desc="Stitching segments", unit="seg"):
        try:
            segment_audio = AudioSegment.from_file(entry["wav"]).set_channels(1).set_frame_rate(sample_rate)
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

def mux_audio_video(video_path: Path, audio_path: Path, output_path: Path) -> None:
    """
    Mux audio track into video using ffmpeg.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path),
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0", "-map", "0:s?",
        "-map_metadata", "0",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logging.error(f"ffmpeg failed to mux audio: {result.stderr.strip()}")
        raise RuntimeError("ffmpeg muxing failed")
