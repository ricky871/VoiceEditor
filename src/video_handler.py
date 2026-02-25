"""
Video Processing Engine for VoiceEditor

Handles:
- Downloading video (yt-dlp)
- Audio extraction (ffmpeg)
- Precise transcription (Faster-Whisper)
- Selective voice reference extraction (Energy-based minimum noise search)
"""
import os
import sys
import subprocess
import json
import logging
import math
from pathlib import Path
from typing import Optional, Dict
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class VideoEngine:
    def __init__(self, work_dir: str = "work"):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        
    def _run_cmd(self, cmd, desc="Command"):
        logging.info(f"Running {desc}: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if result.returncode != 0:
            logging.error(f"{desc} failed: {result.stderr}")
            return False, result.stderr
        return True, result.stdout

    def download_video(self, url: str) -> Optional[Path]:
        logging.info(f"Downloading video from {url}...")
        try:
            import yt_dlp
        except ImportError:
            logging.error("yt_dlp not found. Run 'uv pip install yt-dlp'")
            return None
            
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            'outtmpl': str(self.work_dir / '%(title)s.%(ext)s'),
            'restrictfilenames': True,
            'nocheckcertificate': True,
            'retries': 10,
            'fragment_retries': 10,
            'retry_sleep_functions': {'http': lambda n: 5 * (n + 1)},
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            video_path = Path(ydl.prepare_filename(info))
            logging.info(f"Video downloaded to {video_path}")
            return video_path

    def extract_audio(self, video_path: Path) -> Optional[Path]:
        audio_path = video_path.with_suffix(".wav")
        logging.info(f"Extracting audio to {audio_path}...")
        
        cmd = [
            "ffmpeg", "-y", "-i", str(video_path),
            "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "1",
            str(audio_path)
        ]
        success, _ = self._run_cmd(cmd, "ffmpeg extraction")
        return audio_path if success else None

    def _format_timestamp(self, seconds: float) -> str:
        ms = int(round((seconds % 1) * 1000))
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def transcribe(self, audio_path: Path, model_size: str = "small", lang: str = "zh") -> Optional[Path]:
        logging.info(f"Transcribing audio using Faster-Whisper ({model_size})...")
        try:
            from faster_whisper import WhisperModel
            from opencc import OpenCC
        except ImportError:
            logging.error("Missing libraries: faster-whisper or opencc. Run 'uv sync'")
            return None

        # GPU logic
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        # float16 is standard for GPU, int8/int8_float16 for lower VRAM
        compute_type = "float16" if device == "cuda" else "int8"
        
        logging.info(f"Using device: {device} with compute_type: {compute_type}")
        cc = OpenCC('t2s')
        
        try:
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            # info contains basic info about the audio and language
            # initial_prompt helps Nudge Whisper to use Simplified Chinese
            segments, info = model.transcribe(
                str(audio_path), 
                language=lang, 
                beam_size=5,
                initial_prompt="以下是普通话的句子，请使用简体中文。"
            )
            
            srt_path = audio_path.with_suffix(".srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                for idx, segment in enumerate(segments, start=1):
                    start_str = self._format_timestamp(segment.start)
                    end_str = self._format_timestamp(segment.end)
                    # Convert to Simplified Chinese
                    simplified_text = cc.convert(segment.text.strip())
                    f.write(f"{idx}\n{start_str} --> {end_str}\n{simplified_text}\n\n")
            
            logging.info(f"Transcription saved to {srt_path}")
            return srt_path
        except Exception as e:
            logging.error(f"Transcription failed: {e}")
            return None

    def extract_voice_ref(self, audio_path: Path, duration_sec: int = 10, srt_path: Optional[Path] = None) -> Optional[Path]:
        logging.info(f"Extracting {duration_sec}s voice reference...")
        try:
            import librosa
            import soundfile as sf
            import numpy as np
        except ImportError:
            logging.error("Missing audio analysis libs. Run 'uv pip install librosa soundfile'")
            return None
            
        y, sr = librosa.load(str(audio_path), sr=None)
        
        # Split into windows of duration_sec
        samples_per_window = duration_sec * sr
        if len(y) < samples_per_window:
            samples_per_window = len(y)
            
        best_start = 0
        
        # Strategy 1: Use SRT to find a segment with confirmed speech
        if srt_path and srt_path.exists():
            try:
                import pysrt
                subs = pysrt.open(str(srt_path))
                if len(subs) > 0:
                    # Pick a subtitle somewhere early-to-mid to ensure good voice clarity
                    idx = min(len(subs) // 4, 10) 
                    if idx >= len(subs): idx = 0
                    
                    best_start = int((subs[idx].start.ordinal / 1000.0) * sr)
                    logging.info(f"Using SRT to find voice reference at sample {best_start} (sub index {idx})")
            except Exception as e:
                logging.warning(f"Failed to use SRT for voice ref extraction: {e}")
        
        # Strategy 2: Fallback to Peak Energy Search if SRT failed or wasn't provided
        if best_start == 0:
            logging.info("Searching for highest energy segment for voice reference...")
            max_rms = -1.0
            step = 5 * sr  # Step through in 5s increments
            
            search_range = range(0, len(y) - samples_per_window, step)
            for start in tqdm(search_range, desc="Analyzing audio for voice ref", unit="pos"):
                window = y[start : start + samples_per_window]
                rms = np.sqrt(np.mean(window**2))
                # We want typical speech energy, usually higher than background but not clipping
                if rms > max_rms and rms < 0.5: 
                    max_rms = rms
                    best_start = start
                
        ref_path = audio_path.parent / "voice_ref.wav"
        y_ref = y[best_start : best_start + samples_per_window]
        sf.write(str(ref_path), y_ref, sr)
        
        logging.info(f"Voice reference saved to {ref_path}")
        return ref_path

def run_video_pipeline(url: str, work_dir: str = "work", model: str = "small", lang: str = "zh"):
    engine = VideoEngine(work_dir)
    video = engine.download_video(url)
    if not video: return None
    
    audio = engine.extract_audio(video)
    if not audio: return None
    
    srt = engine.transcribe(audio, model_size=model, lang=lang)
    ref = engine.extract_voice_ref(audio, srt_path=srt)
    
    return {
        "video": str(video),
        "audio": str(audio),
        "srt": str(srt),
        "voice_ref": str(ref)
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="VoiceEditor Video Handler")
    parser.add_argument("--url", required=True)
    parser.add_argument("--work-dir", default="work")
    parser.add_argument("--model", default="small")
    parser.add_argument("--lang", default="zh")
    args = parser.parse_args()
    
    res = run_video_pipeline(args.url, args.work_dir, args.model, args.lang)
    if res:
        print(json.dumps(res, indent=2))
        sys.exit(0)
    else:
        sys.exit(1)
