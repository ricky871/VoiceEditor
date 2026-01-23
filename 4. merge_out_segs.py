"""
Merge WAV segments listed in a manifest into a single WAV file.
Usage:
  python merge_out_segs.py \
    --manifest work/out_segs/manifest.json \
    --out work/merged_output.wav \
    [--pad-gaps]

Default behavior: concatenates segments in manifest order. Use --pad-gaps to insert silence
between segments matching their `start_ms` timestamps in the manifest.
"""
import argparse
import json
import os
import wave
import shutil
import subprocess


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
            print('Skipping entry without wav:', entry)
            continue
        wav_path = resolve_path(wav_rel, workspace_root)
        if not os.path.exists(wav_path):
            print('Missing file, skipping:', wav_path)
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
            raise RuntimeError(f'Incompatible WAV params: {wav_path} has {this_params}, expected {params}')

        if pad_gaps:
            start_ms = entry.get('start_ms', None)
            if start_ms is not None and start_ms > current_time_ms:
                gap_ms = start_ms - current_time_ms
                n_silence_frames = int((gap_ms / 1000.0) * params[2])
                silence = (b'\x00' * (n_silence_frames * params[0] * params[1]))
                frames_list.append(silence)
                current_time_ms += int(n_silence_frames * 1000.0 / params[2])

        frames_list.append(raw)
        # advance current_time_ms by the actual frames duration
        current_time_ms += int(len(raw) / (params[0] * params[1]) * 1000.0 / params[2])

    if params is None:
        raise RuntimeError('No valid segments found to merge')

    # write output
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with wave.open(out_path, 'wb') as out:
        out.setnchannels(params[0])
        out.setsampwidth(params[1])
        out.setframerate(params[2])
        for chunk in frames_list:
            out.writeframes(chunk)

    print('Merged', len(frames_list), 'chunks ->', out_path)


def ffmpeg_available():
    try:
        subprocess.run(['ffmpeg', '-version'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return True
    except FileNotFoundError:
        return False


def merge_video_with_audio_and_subs(video_in, audio_in, subs_path, output_video, burn_subs=False, audio_bitrate='192k'):
    """
    Replace original video audio with merged WAV and attach/burn subtitles.
    - If burn_subs=False: embed soft subtitles (mp4 uses mov_text).
    - If burn_subs=True: burn subtitles into video frames (re-encodes video).
    """
    if not ffmpeg_available():
        raise RuntimeError('ffmpeg not found in PATH. Please install ffmpeg and ensure it is available.')

    if not os.path.exists(video_in):
        raise FileNotFoundError(f'Input video not found: {video_in}')
    if not os.path.exists(audio_in):
        raise FileNotFoundError(f'Input audio not found: {audio_in}')
    if not os.path.exists(os.path.dirname(output_video)):
        os.makedirs(os.path.dirname(output_video), exist_ok=True)

    # Determine container/codec choices
    ext = os.path.splitext(output_video)[1].lower()
    is_mp4 = ext == '.mp4'

    cmd = []
    if burn_subs:
        # Burn subtitles requires re-encode video. We use libx264 + aac.
        # Use filter_complex for subtitles to support Unicode paths via quoted argument.
        if not os.path.exists(subs_path):
            raise FileNotFoundError(f'Subtitles file not found: {subs_path}')
        path_for_ffmpeg = subs_path.replace('\\', '/')
        cmd = [
            'ffmpeg', '-y',
            '-i', video_in,
            '-i', audio_in,
            '-filter_complex', "subtitles='" + path_for_ffmpeg + "'",
            '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
            '-c:a', 'aac', '-b:a', audio_bitrate,
            '-shortest',
            output_video
        ]
    else:
        # Soft subtitles: for mp4 use mov_text, for mkv we can copy srt.
        if subs_path and os.path.exists(subs_path):
            cmd = [
                'ffmpeg', '-y',
                '-i', video_in,
                '-i', audio_in,
                '-i', subs_path,
                # keep video, replace audio, attach subs
                '-map', '0:v:0', '-map', '1:a:0', '-map', '2:0',
                '-c:v', 'copy',
                '-c:a', 'aac', '-b:a', audio_bitrate,
                '-c:s', 'mov_text' if is_mp4 else 'copy',
                '-metadata:s:s:0', 'language=chi',
                '-shortest',
                output_video
            ]
        else:
            # No subtitles provided: just replace audio
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
        raise RuntimeError(f'ffmpeg failed (code {proc.returncode})\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}')
    print('Final video written ->', output_video)


def find_video_candidate(subs_path=None, workspace_root=None):
    """Try to locate a suitable input video.
    Priority:
      1) If subs_path provided, look for a video with the same basename in its folder.
      2) Search common video files in workspace_root and workspace_root/work.
    Returns absolute path or None.
    """
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

    if subs_path and os.path.exists(subs_path):
        subs_dir = os.path.dirname(subs_path)
        subs_base = os.path.splitext(os.path.basename(subs_path))[0]
        add_candidates_from(subs_dir, subs_base)
        if candidates:
            return candidates[0]

    if workspace_root is None:
        workspace_root = os.getcwd()

    add_candidates_from(workspace_root)
    work_dir = os.path.join(workspace_root, 'work')
    add_candidates_from(work_dir)

    return candidates[0] if candidates else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', '-m', default='work/out_segs/manifest.json')
    p.add_argument('--out', '-o', default='work/merged_output.wav')
    p.add_argument('--pad-gaps', action='store_true', help='Insert silence for gaps between segments based on start_ms')
    p.add_argument('--workspace-root', default=None, help='Base path to resolve relative wav paths (defaults to cwd)')
    # Final video merge options
    p.add_argument('--video-in', default=None, help='Original video to replace audio and add subtitles (auto-detects if missing)')
    p.add_argument('--subs', default=None, help='Subtitle file (.srt); if omitted, audio is replaced without subtitles')
    p.add_argument('--out-video', default=None, help='Output merged video path (e.g., work/final_output.mp4)')
    p.add_argument('--burn-subs', action='store_true', help='Burn subtitles into video frames (re-encode). Default: embed soft subtitles')
    p.add_argument('--audio-bitrate', default='192k', help='Bitrate for output AAC audio when muxing (default: 192k)')
    args = p.parse_args()

    manifest_path = args.manifest
    workspace_root = args.workspace_root or os.getcwd()

    manifest = read_manifest(manifest_path)
    merge_segments(manifest, args.out, pad_gaps=args.pad_gaps, workspace_root=workspace_root)

    # Optional: produce final video if requested
    if args.video_in or args.out_video or args.subs:
        video_in = args.video_in
        if not video_in or not os.path.exists(video_in):
            auto_video = find_video_candidate(args.subs, workspace_root)
            if auto_video:
                print('Auto-detected input video ->', auto_video)
                video_in = auto_video
            else:
                raise SystemExit(f'Missing or invalid --video-in. Could not auto-detect a video in {workspace_root} or work/.')
        out_video = args.out_video or os.path.join(os.path.dirname(args.out), 'final_output.mp4')
        subs_path = args.subs
        try:
            merge_video_with_audio_and_subs(
                video_in=video_in,
                audio_in=args.out,
                subs_path=subs_path,
                output_video=out_video,
                burn_subs=args.burn_subs,
                audio_bitrate=args.audio_bitrate,
            )
        except Exception as e:
            raise SystemExit(f'Final video merge failed: {e}')


if __name__ == '__main__':
    main()
