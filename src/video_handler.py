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
# logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class VideoEngine:
    def __init__(self, work_dir: str = "work", verbose: bool = False):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        
    def _run_cmd(self, cmd, desc="Command"):
        logging.info(f"Running {desc}: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        if result.returncode != 0:
            logging.error(f"{desc} failed: {result.stderr}")
            return False, result.stderr
        return True, result.stdout

    def download_video(self, url: str) -> Optional[tuple[Path, float]]:
        logging.info(f">> 开始处理视频: {url}")
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
            'quiet': True,
            'no_warnings': True,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # First, extract info without downloading to check filename
            info = ydl.extract_info(url, download=False)
            logging.info(f">> 视频信息: {info.get('title', 'Unknown')}")
            video_path = Path(ydl.prepare_filename(info))
            duration = float(info.get('duration', 0))

            if video_path.exists() and video_path.stat().st_size > 0:
                return video_path, duration
            
            # If not exists, download
            ydl.process_info(info)
            
        # Re-run for actual download if we didn't return above
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
             info = ydl.extract_info(url, download=True)
             video_path = Path(ydl.prepare_filename(info))
             duration = float(info.get('duration', 0))
             return video_path, duration

    def extract_audio(self, video_path: Path) -> Optional[Path]:
        audio_path = video_path.with_suffix(".wav")
        if audio_path.exists() and audio_path.stat().st_size > 0:
            return audio_path

        # logging.info(f"Extracting audio...")
        
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
        srt_path = audio_path.with_suffix(".srt")
        if srt_path.exists() and srt_path.stat().st_size > 0:
             logging.info(f">> 缓存命中: {srt_path.name}")
             return srt_path

        logging.info(f">> 开始转写生成字幕 ({model_size})...")
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
        
        # logging.info(f"Using device: {device} with compute_type: {compute_type}")
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
            
            final_srt_path = audio_path.with_suffix(".srt")
            tmp_srt_path = audio_path.with_suffix(".srt.tmp") 
            
            with open(tmp_srt_path, "w", encoding="utf-8") as f:
                for idx, segment in enumerate(segments, start=1):
                    start_str = self._format_timestamp(segment.start)
                    end_str = self._format_timestamp(segment.end)
                    # Convert to Simplified Chinese
                    simplified_text = cc.convert(segment.text.strip())
                    f.write(f"{idx}\n{start_str} --> {end_str}\n{simplified_text}\n\n")
            
            # Atomic rename
            if tmp_srt_path.exists():
                tmp_srt_path.replace(final_srt_path)

            logging.info(f"Transcription saved to {final_srt_path}")
            return final_srt_path
        except Exception as e:
            logging.error(f"Transcription failed: {e}")
            if 'tmp_srt_path' in locals() and tmp_srt_path.exists():
                tmp_srt_path.unlink()
            return None

    def extract_voice_ref(self, audio_path: Path, duration_sec: int = 10, srt_path: Optional[Path] = None) -> Optional[Path]:
        out_path = self.work_dir / f"{srt_path.stem}_voice.wav" if srt_path else self.work_dir / "voice_ref.wav"
        if out_path.exists() and out_path.stat().st_size > 0:
             logging.info(f">> 缓存命中: {out_path}")
             return out_path

        # logging.info(f"Extracting voice reference...")
        try:
            import librosa
            import soundfile as sf
            import numpy as np
        except ImportError:
            logging.error("Missing audio analysis libs. Run 'uv pip install librosa soundfile'")
            return None
        
        y = None
        sr = 44100 
        
        def load_chunk(start_sec, dur_sec):
            with sf.SoundFile(str(audio_path)) as f:
                f.seek(max(0, int(start_sec * f.samplerate)))
                frames_to_read = int(dur_sec * f.samplerate)
                if frames_to_read > (f.frames - f.tell()):
                    frames_to_read = f.frames - f.tell()
                data = f.read(frames_to_read)
                if len(data.shape) > 1:
                    data = data.mean(axis=1)
                return data, f.samplerate

        # Strategy 1: Use SRT to find a segment with confirmed speech
        if srt_path and srt_path.exists():
            try:
                import pysrt
                subs = pysrt.open(str(srt_path))
                if len(subs) > 0:
                    idx = min(len(subs) // 4, 10) 
                    start_time = subs[idx].start.ordinal / 1000.0
                    # logging.info(f"Loading partial audio from {start_time}s based on SRT...")
                    try:
                        # Load 30s to have some buffer to find the best window
                        y, sr = load_chunk(start_time, duration_sec * 3)
                    except Exception as e:
                        logging.warning(f"Failed to load chunk: {e}")
            except Exception as e:
                logging.warning(f"Error using SRT: {e}")

        # Strategy 2: If no data yet, load start of file
        if y is None or len(y) == 0:
             logging.info("Loading first 2 minutes of audio for voice ref search...")
             try:
                 y, sr = load_chunk(0, 120)
             except Exception:
                 # Fallback to full load if sf fails (e.g. format issues)
                 y, sr = librosa.load(str(audio_path), sr=None)

        if y is None or len(y) == 0:
             logging.error("Failed to load any audio.")
             return None

        # Extract best window from loaded audio 'y'
        samples_per_window = int(duration_sec * sr)
        if len(y) < samples_per_window:
            samples_per_window = len(y)
            
        best_start = 0
        max_rms = -1.0
        step = int(1.0 * sr) # Step 1s
        
        search_range = range(0, len(y) - samples_per_window, step)
        for start in tqdm(search_range, desc="正在分析最佳音色切片", unit="win", disable=not self.verbose):
            window = y[start : start + samples_per_window]
            rms = np.sqrt(np.mean(window**2))
            # Prefer loud but not clipping segments
            if rms > max_rms and rms < 0.5: 
                max_rms = rms
                best_start = start
                
        # Save the best segment
        best_segment = y[best_start : best_start + samples_per_window]
        out_path = self.work_dir / f"{srt_path.stem}_voice.wav"
        sf.write(str(out_path), best_segment, sr)
        
        logging.info(f">> 音色提取成功")
        return out_path



def run_video_pipeline(url: str, work_dir: str = "work", model: str = "small", lang: str = "zh", verbose: bool = False):
    engine = VideoEngine(work_dir, verbose=verbose)
    result = engine.download_video(url)
    if not result: return None
    video, duration = result
    
    # 打印预估时间
    if duration > 0:
        import torch
        # 预估倍数：GPU约 1.2倍 (whisper+maskgct)，CPU约 15倍 (较慢)
        is_cuda = torch.cuda.is_available()
        rtf = 1.2 if is_cuda else 15.0
        est_seconds = max(20, int(duration * rtf))
        
        if est_seconds < 60:
            est_str = f"{est_seconds}秒"
        else:
            est_str = f"{est_seconds // 60}分{est_seconds % 60}秒"
            
        mode_str = "GPU加速" if is_cuda else "CPU (较慢)"
        logging.info(f">> 预计处理耗时: 约 {est_str} (模式: {mode_str})")

    audio = engine.extract_audio(video)
    if not audio: return None
    
    srt = engine.transcribe(audio, model_size=model, lang=lang)
    if not srt:
        logging.error("转写失败，无法继续。")
        return None

    ref = engine.extract_voice_ref(audio, srt_path=srt)
    if not ref:
        logging.error("参考音频提取失败，无法继续。")
        return None
    
    return {
        "video": str(video),
        "audio": str(audio),
        "srt": str(srt),
        "voice_ref": str(ref),
        "duration": duration
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
