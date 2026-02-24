#!/usr/bin/env python3
"""
VoiceEditor: Unified Video Dubbing & Voice Synthesis CLI

Usage:
  python main.py setup [--cn]
  python main.py run --url <video_url> [--cn] [--stitch]
"""
import argparse
import sys
import os
import json
import logging
import platform
import subprocess
from pathlib import Path

# Add src to sys.path for direct imports
sys.path.append(os.path.join(os.path.dirname(__file__), "src"))

def setup_logger(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

def cmd_setup(args):
    from src.setup_env import setup_all
    logging.info("Starting Environment Setup...")
    if setup_all(cn_mirror=args.cn, skip_download=args.skip_download):
        logging.info("Setup Success.")
    else:
        logging.error("Setup Failed.")
        sys.exit(1)

def cmd_run(args):
    from src.video_handler import run_video_pipeline
    from src.tts_generator import run_tts_generation
    
    # 1. Video Processing
    logging.info(f"Step 1: Processing Video from {args.url}")
    video_data = run_video_pipeline(args.url, args.work_dir, args.whisper_model, args.lang)
    if not video_data:
        logging.error("Video processing failed.")
        sys.exit(1)
    
    # 1.5. Manual SRT Editing
    srt_path = video_data["srt"]
    logging.info(f"Subtitles generated at: {srt_path}")
    logging.info("Opening subtitles for manual editing...")
    
    try:
        if platform.system() == "Windows":
            os.startfile(srt_path)
        elif platform.system() == "Darwin":  # macOS
            subprocess.run(["open", srt_path])
        else:  # Linux
            subprocess.run(["xdg-open", srt_path])
        
        print("\n" + "="*60)
        print(f"PLEASE EDIT THE SUBTITLE FILE: {srt_path}")
        print("You can correct transcription errors, adjust timing, or change text.")
        print("Once you have saved and closed the editor, press ENTER to continue...")
        print("="*60 + "\n")
        input()
    except Exception as e:
        logging.warning(f"Could not open editor automatically: {e}")
        print(f"\nPlease manually edit: {srt_path}")
        input("Press ENTER after you have finished editing...")

    # 2. TTS Generation
    logging.info("Step 2: Generating TTS from Subtitles")
    # Mapping args to tts_generator expected format
    # We can either pass args directly if structured correctly, or manually map.
    tts_args = argparse.Namespace(
        cfg_path="checkpoints/config.yaml",
        model_dir="checkpoints",
        ref_voice=video_data["voice_ref"],
        srt=video_data["srt"],
        out_dir=os.path.join(args.work_dir, "out_segs"),
        duration_mode="seconds",
        tokens_per_sec=150.0,
        emo_text=args.emo_text,
        emo_audio="",
        emo_alpha=0.8,
        lang=args.lang,
        speed=1.0,
        stitch=args.stitch,
        sample_rate=44100,
        gain_db=-1.5,
        video=video_data["video"],
        output_video=args.output,
        verbose=args.verbose
    )
    
    status = run_tts_generation(tts_args)
    if status == 0:
        logging.info("Run successfully completed.")
    else:
        logging.error(f"TTS pipeline failed with status {status}")
        sys.exit(status)

def main():
    parser = argparse.ArgumentParser(description="VoiceEditor Unified Entry Point")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Setup environment and models")
    setup_parser.add_argument("--cn", action="store_true", default=True, help="Force Chinese mirrors")
    setup_parser.add_argument("--skip-download", action="store_true")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run full dubbing pipeline")
    run_parser.add_argument("--url", required=True, help="Video URL")
    run_parser.add_argument("--work-dir", default="work")
    run_parser.add_argument("--output", help="Final video output path")
    run_parser.add_argument("--cn", action="store_true", default=True, help="Use Chinese mirrors")
    run_parser.add_argument("--stitch", action="store_true", help="Stitch segments into a single file")
    run_parser.add_argument("--whisper-model", default="small")
    run_parser.add_argument("--lang", default="zh")
    run_parser.add_argument("--emo-text", default="", help="Emotion prompt for TTS")

    args = parser.parse_args()
    setup_logger(args.verbose)

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "run":
        cmd_run(args)

if __name__ == "__main__":
    main()
