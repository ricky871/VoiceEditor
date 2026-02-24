"""
Audio Segment Hub: Merge WAV segments listed in a manifest into a single WAV file.

This module provides utilities to consolidate individual TTS segments into a continuous
audio track, optionally matching timestamps and muxing into video via ffmpeg.
"""
import argparse
import json
import os
import wave
import shutil
import subprocess
from pathlib import Path


def resolve_path(path, base):
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base, path))


def read_manifest(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data


def merge_segments(manifest, out_path, pad_gaps=False, workspace_root=None):
    if workspace_root is None:
        workspace_root = os.getcwd()
    
    # ensure ordering by start_ms then id
    manifest_sorted = sorted(manifest, key=lambda x: (x.get('start_ms', 0), x.get('id', 0)))

    params = None
    frames_list = []
    current_time_ms = 0

    for entry in manifest_sorted:
        wav_rel = entry.get('wav')
        if not wav_rel:
            continue
        wav_path = resolve_path(wav_rel, workspace_root)
        if not os.path.exists(wav_path):
            print(f'Warning: Missing file {wav_path}')
            continue

        with wave.open(wav_path, 'rb') as wf:
            nchannels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            nframes = wf.getnframes()
            this_params = (nchannels, sampwidth, framerate)
            raw = wf.readframes(nframes)

        if params is None:
            params = this_params
        elif params != this_params:
            raise RuntimeError(f'Incompatible WAV params in {wav_path}')

        if pad_gaps:
            start_ms = entry.get('start_ms', None)
            if start_ms is not None and start_ms > current_time_ms:
                gap_ms = start_ms - current_time_ms
                n_silence_frames = int((gap_ms / 1000.0) * params[2])
                silence = (b'\x00' * (n_silence_frames * params[0] * params[1]))
                frames_list.append(silence)
                current_time_ms += int(n_silence_frames * 1000.0 / params[2])

        frames_list.append(raw)
        current_time_ms += int(len(raw) / (params[0] * params[1]) * 1000.0 / params[2])

    if params is None:
        raise RuntimeError('No valid segments found to merge')

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, 'wb') as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        for chunk in frames_list:
            out.writeframes(chunk)

    print(f'Merged {len(frames_list)} chunks -> {out_path}')


def ffmpeg_available():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return True
    except FileNotFoundError:
        return False


def merge_video_with_audio_and_subs(video_in, audio_in, subs_path, output_video, burn_subs=False, audio_bitrate='192k'):
    """
    Replace original video audio with merged WAV and attach/burn subtitles.
    """
    if not ffmpeg_available():
        raise RuntimeError('ffmpeg not found in PATH.')

    if not os.path.exists(video_in):
        raise FileNotFoundError(f'Input video not found: {video_in}')
    if not os.path.exists(audio_in):
        raise FileNotFoundError(f'Input audio not found: {audio_in}')
    
    os.makedirs(os.path.dirname(output_video), exist_ok=True)
    ext = os.path.splitext(output_video)[1].lower()
    is_mp4 = ext == '.mp4'

    cmd = []
    if burn_subs:
        if not os.path.exists(subs_path):
            raise FileNotFoundError(f'Subtitles file not found: {subs_path}')
        # Cross-platform: ffmpeg subtitles filter requires forward slashes even on Windows
        path_for_ffmpeg = str(Path(subs_path).as_posix()).replace(":", "\\:") 
        cmd = [
            'ffmpeg', '-y',
            '-i', video_in,
            '-i', audio_in,
            '-filter_complex', f"subtitles='{path_for_ffmpeg}'",
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
            '-c:a', 'aac', '-b:a', audio_bitrate,
            '-shortest',
            output_video
        ]
    else:
        if subs_path and os.path.exists(subs_path):
            cmd = [
                'ffmpeg', '-y',
                '-i', video_in,
                '-i', audio_in,
                '-i', subs_path,
                '-map', '0:v:0', '-map', '1:a:0', '-map', '2:0',
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', audio_bitrate,
                '-c:s', 'mov_text' if is_mp4 else 'copy',
                '-metadata:s:s:0', 'language=chi',
                '-shortest',
                output_video
            ]
        else:
            cmd = [
                'ffmpeg', '-y',
                '-i', video_in,
                '-i', audio_in,
                '-map', '0:v:0', '-map', '1:a:0',
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', audio_bitrate,
                '-shortest',
                output_video
            ]

    print('Running ffmpeg to produce final video...')
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore')
    if proc.returncode != 0:
        raise RuntimeError(f'ffmpeg failed: {proc.stderr}')
    print(f'Final video written -> {output_video}')


def find_video_candidate(subs_path=None, workspace_root=None):
    video_exts = ['.mp4', '.mkv', '.webm', '.mov', '.avi']
    candidates = []

    def add_candidates_from(dir_path, match_basename=None):
        if not dir_path or not os.path.isdir(dir_path):
            return
        try:
            for name in os.listdir(dir_path):
                lower = name.lower()
                for ext in video_exts:
                    if lower.endswith(ext):
                        full = os.path.join(dir_path, name)
                        if match_basename is None or os.path.splitext(name)[0] == match_basename:
                            candidates.append(full)
                        break
        except Exception:
            pass
    
    if subs_path:
        subs_p = Path(subs_path)
        add_candidates_from(str(subs_p.parent), subs_p.stem)
    
    if workspace_root:
        add_candidates_from(workspace_root)
        add_candidates_from(os.path.join(workspace_root, 'work'))
    
    return candidates[0] if candidates else None


def run_audio_merger(args):
    manifest_data = read_manifest(args.manifest)
    workspace = args.workspace or os.getcwd()
    
    merge_segments(
        manifest_data, 
        args.out, 
        pad_gaps=args.pad_gaps, 
        workspace_root=workspace
    )
    
    if args.video:
        video_in = args.video
        if video_in == 'auto':
            video_in = find_video_candidate(args.subs, workspace)
        
        if video_in:
            merge_video_with_audio_and_subs(
                video_in, 
                args.out, 
                args.subs, 
                args.output_video, 
                burn_subs=args.burn_subs
            )


def main():
    parser = argparse.ArgumentParser(description="Merge WAV segments and optionally mux to video")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--pad-gaps", action="store_true")
    parser.add_argument("--workspace", help="Root workspace for resolving relative paths")
    parser.add_argument("--video", help="Video path or 'auto'")
    parser.add_argument("--subs", help="SRT subtitle path")
    parser.add_argument("--output-video", help="Final output video path")
    parser.add_argument("--burn-subs", action="store_true")
    args = parser.parse_args()
    run_audio_merger(args)


if __name__ == "__main__":
    main()
