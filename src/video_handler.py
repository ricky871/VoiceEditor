"""
Video Processing Engine for VoiceEditor

Handles:
- Downloading video (yt-dlp)
- Audio extraction (ffmpeg)
- Precise transcription (OpenAI Whisper)
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

    def transcribe(self, audio_path: Path, model_size: str = "small", lang: str = "zh") -> Optional[Path]:
        logging.info(f"Transcribing audio using Whisper ({model_size})...")
        try:
            import whisper
        except ImportError:
            logging.error("whisper not found. Run 'uv pip install openai-whisper'")
            return None

        # Run whisper via CLI for better memory management in subagent context, 
        # or use internal API if preferred.
        model = whisper.load_model(model_size)
        result = model.transcribe(str(audio_path), language=lang, verbose=False)
        
        # Save as SRT
        srt_path = audio_path.with_suffix(".srt")
        from whisper.utils import get_writer
        writer = get_writer("srt", str(srt_path.parent))
        writer(result, str(audio_path.stem))
        
        logging.info(f"Transcription saved to {srt_path}")
        return srt_path

    def extract_voice_ref(self, audio_path: Path, duration_sec: int = 30) -> Optional[Path]:
        logging.info(f"Extracting {duration_sec}s voice reference (choosing lowest energy segment)...")
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
            
        # Root Mean Square (RMS) energy to find the "quietest" but non-silent segment 
        # (usually means less background music/noise if it's a commentary video)
        # Note: Actually, for voices, we want clear speech. 
        # For simplicity, we'll pick a slice that isn't silence.
        
        best_start = 0
        min_rms = float('inf')
        
        # Step through audio in 5s increments
        step = 5 * sr
        for start in range(0, len(y) - samples_per_window, step):
            window = y[start : start + samples_per_window]
            rms = np.sqrt(np.mean(window**2))
            # We want clear voice, typically RMS > threshold to avoid literal silence
            if 0.01 < rms < min_rms: 
                min_rms = rms
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
    ref = engine.extract_voice_ref(audio)
    
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
