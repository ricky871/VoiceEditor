#!/usr/bin/env python3
"""
VoiceEditor: Unified Video Dubbing & Voice Synthesis CLI

Usage:
  python main.py setup                      # Initial environment & model setup
  python main.py <url>                      # Short form for processing a video
  python main.py run <url> [options]         # Full form for processing a video
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

from src.config import setup_environment, get_logging_config
from src.resource_manager import ResourceManager

def setup_logger(verbose=False):
    logging.basicConfig(**get_logging_config(verbose))

def cmd_setup(args):
    setup_environment()
    from src.setup_env import setup_all
    logging.info("Starting Environment Setup...")
    if setup_all(cn_mirror=args.cn, skip_download=args.skip_download):
        logging.info("Setup Success.")
    else:
        logging.error("Setup Failed.")
        sys.exit(1)

def cmd_run(args):
    setup_environment()
    from src.video_handler import run_video_pipeline
    from src.tts_generator import run_tts_generation
    
    # 0. Initialize Resources
    res_manager = ResourceManager(work_dir=args.work_dir, out_dir=os.path.join(args.work_dir, "out_segs"))
    res_manager.ensure_dirs()

    # 1. Video Processing
    video_data = run_video_pipeline(args.url, str(res_manager.work_dir), args.whisper_model, args.lang, verbose=args.verbose)
    if not video_data:
        logging.error("视频处理失败。")
        sys.exit(1)
    
    # 1.5. Manual SRT Editing
    srt_path = video_data["srt"]
    try:
        if platform.system() == "Windows":
            os.startfile(srt_path)
        elif platform.system() == "Darwin":  # macOS
            subprocess.run(["open", srt_path])
        else:  # Linux
            subprocess.run(["xdg-open", srt_path])
        
        print("\n" + "="*60)
        print(f"请编辑字幕文件: {srt_path}")
        print("你可以修正转录错误、调整时间轴或修改文本。")
        print("保存并关闭编辑器后，按回车键（ENTER）继续...")
        print("="*60 + "\n")
        input()
        logging.info(">> 已确认编辑，正在进入 TTS 生成阶段...")
    except Exception as e:
        print(f"\n无法自动打开编辑器。请手动编辑字幕文件: {srt_path}")
        input("编辑完成后按回车键（ENTER）继续...")
        logging.info(">> 已确认编辑，正在进入 TTS 生成阶段...")

    # 2. TTS Generation
    # logging.info("Step 2: Generating TTS from Subtitles (This may take several minutes)")
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
        diffusion_steps=args.diffusion_steps,
        video=video_data["video"],
        output_video=args.output,
        verbose=args.verbose
    )
    
    status = run_tts_generation(tts_args)
    if status != 0:
        logging.error(f"TTS 语音合成任务失败，状态码: {status}")
        sys.exit(status)

def main():
    # Pre-parse to handle the "simple" case: `python main.py <url>`
    if len(sys.argv) > 1 and sys.argv[1].startswith(("http://", "https://", "BV")):
        # Synthesize a "run --url <url>" args list
        url = sys.argv[1]
        # Keep any other arguments
        remaining = sys.argv[2:]
        sys.argv = [sys.argv[0], "run", "--url", url] + remaining

    parser = argparse.ArgumentParser(description="VoiceEditor Unified Entry Point")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Setup command
    setup_parser = subparsers.add_parser("setup", help="Setup environment and models")
    setup_parser.add_argument("--cn", action="store_true", default=True, help="Force Chinese mirrors")
    setup_parser.add_argument("--skip-download", action="store_true")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run full dubbing pipeline")
    # Also allow URL as positional if --url is not provided
    run_parser.add_argument("pos_url", nargs="?", help="Video URL (positional)")
    run_parser.add_argument("--url", help="Video URL (named option)")
    run_parser.add_argument("--work-dir", default="work")
    run_parser.add_argument("--output", help="Final video output path")
    run_parser.add_argument("--cn", action="store_true", default=True, help="Use Chinese mirrors")
    run_parser.add_argument("--stitch", action="store_true", default=True, help="Stitch segments into a single file")
    run_parser.add_argument("--whisper-model", default="small")
    run_parser.add_argument("--lang", default="zh")
    run_parser.add_argument("--emo-text", default="", help="Emotion prompt for TTS")
    run_parser.add_argument("--diffusion-steps", type=int, default=25, help="Diffusion steps for TTS")

    args = parser.parse_args()
    
    if args.command == "run":
        # Resolve URL from either positional or named argument
        if not args.url and args.pos_url:
            args.url = args.pos_url
        if not args.url:
            run_parser.error("The '--url' argument or a positional URL is required.")

    setup_logger(args.verbose)

    # Global progress bar suppression if not verbose
    if not args.verbose:
        from src.config import patch_tqdm
        patch_tqdm(True)

    if args.command == "setup":
        cmd_setup(args)
    elif args.command == "run":
        cmd_run(args)

if __name__ == "__main__":
    main()
