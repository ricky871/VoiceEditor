from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import shutil
import signal
import socket
import sys
import atexit
from pathlib import Path

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

import pysrt
from nicegui import app, context, run, ui
from nicegui.timer import Timer as BackgroundTimer

from src.config import get_device, get_logging_config, patch_tqdm, setup_environment, DEFAULT_WORK_DIR, DIRNAME_SEGMENTS
from src.tts.processor import SRTProcessor
from src.tts_generator import run_tts_generation
from src.video_handler import run_video_pipeline
from ui.state import AppState, UILogHandler
from ui.theme import apply_theme


# Global state for cleanup
_active_processes = []
_cleanup_done = False

def _register_process(proc):
    """Register a process for cleanup."""
    if proc:
        _active_processes.append(proc)

def _cleanup_processes():
    """Clean up all active processes gracefully."""
    import subprocess
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    
    for proc in _active_processes:
        try:
            if proc and proc.poll() is None:  # Process still running
                logging.info(f"Terminating GUI process {proc.pid}...")
                proc.terminate()  # Send SIGTERM
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logging.warning(f"GUI process {proc.pid} did not terminate, killing...")
                    proc.kill()  # Forceful SIGKILL
                    proc.wait()
        except Exception as e:
            logging.warning(f"Error cleaning up GUI process: {e}")
    
    _active_processes.clear()

def _signal_handler(signum, frame):
    """Handle Ctrl+C and SIGTERM in GUI gracefully."""
    logging.warning(f"\n>> GUI 收到信号 {signum}，正在优雅关闭...")
    _cleanup_processes()
    logging.info("GUI 已安全退出。")
    sys.exit(0)

# Register signal handlers for Ctrl+C (SIGINT) and termination (SIGTERM)
signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)

# Register cleanup on normal exit
atexit.register(_cleanup_processes)

state = AppState()
setup_environment()
patch_tqdm(True)

AUDIO_PLAYER_ID = "segment-player"
ALLOWED_SOCKET_IO_TRANSPORTS = {"websocket", "polling"}
DEFAULT_SOCKET_IO_TRANSPORTS = ["websocket", "polling"]

root_logger = logging.getLogger()
if not root_logger.handlers:
    logging.basicConfig(**get_logging_config(verbose=False))
else:
    root_logger.setLevel(logging.INFO)

_ui_handler = UILogHandler(state)
_ui_handler.setFormatter(logging.Formatter("%(message)s"))
root_logger.addHandler(_ui_handler)


def ms_to_srt_time(milliseconds: int) -> pysrt.SubRipTime:
    return pysrt.SubRipTime(milliseconds=max(0, int(milliseconds)))


def format_srt_timestamp(milliseconds: int) -> str:
    ts = ms_to_srt_time(milliseconds)
    return f"{ts.hours:02d}:{ts.minutes:02d}:{ts.seconds:02d},{ts.milliseconds:03d}"


def get_segment_path(segment_id: int) -> Path:
    return (Path(state.work_dir).resolve() / "out_segs" / f"seg_{segment_id:04d}.wav")


def resolve_work_base_dir() -> Path:
    work_dir = (state.work_dir or "work").strip() or "work"
    return Path(work_dir).resolve()


def is_path_within_base(base_dir: Path, target_path: Path) -> bool:
    try:
        target_path.relative_to(base_dir)
        return True
    except ValueError:
        return False


def get_work_url(target_path: Path) -> str | None:
    # Use the current state's work_dir as the base for URL resolution
    base_dir = resolve_work_base_dir()
    try:
        rel_path = target_path.resolve().relative_to(base_dir)
    except ValueError:
        return None
    return f"/work/{rel_path.as_posix()}"


def play_audio_url(audio_url: str) -> None:
    safe_url = audio_url.replace("'", "\\'")
    ui.run_javascript(
        "const player = document.getElementById('" + AUDIO_PLAYER_ID + "');"
        "if (player) {"
        "  player.src = '" + safe_url + "';"
        "  player.load();"
        "  player.play();"
        "}"
    )


def preview_segment(segment_id: int) -> None:
    seg_path = get_segment_path(segment_id)
    if not seg_path.exists():
        ui.notify(f"该句音频尚未生成: {seg_path.name}", type="warning")
        return
    try:
        audio_url = get_work_url(seg_path)
        if not audio_url:
            ui.notify("无法为工作目录生成浏览器播放地址", type="warning")
            return
        play_audio_url(audio_url)
    except Exception as exc:
        ui.notify(f"试听失败: {exc}", type="negative")


def stop_current_task() -> None:
    if not state.processing and not state.synthesizing:
        ui.notify("当前没有运行中的任务", type="warning")
        return
    state.cancel_event.set()
    if state.current_process:
        try:
            import psutil
            parent = psutil.Process(state.current_process.pid)
            children = parent.children(recursive=True)
            for child in children:
                child.terminate()
            state.current_process.terminate()
            gone, alive = psutil.wait_procs(children + [parent], timeout=3)
            for p in alive:
                p.kill()
            state.append_log(">> 已终止子进程及其所有派生进程 (含 FFmpeg)。")
        except Exception as e:
            # Fallback if psutil fails or process already gone
            try:
                state.current_process.terminate()
            except:
                pass
            logging.debug(f"Process termination detail: {e}")
    state.append_log(">> 用户请求停止当前任务")
    ui.notify("已请求停止任务，子进程已清理", type="warning")


def save_session_state() -> None:
    """Save state to disk for future session recovery."""
    try:
        work_path = resolve_work_base_dir()
        work_path.mkdir(parents=True, exist_ok=True)
        state_file = work_path / "session_state.json"
        
        # Avoid saving if task is in progress (don't save intermediate volatile state)
        if state.processing or state.synthesizing:
            return

        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state.to_dict(), f, ensure_ascii=False, indent=2)
        logging.info(f"Session state saved to {state_file}")
    except Exception as e:
        logging.error(f"Failed to save session state: {e}")


def load_session_state() -> None:
    """Load session state from the current work directory if available."""
    work_path = resolve_work_base_dir()
    state_file = work_path / "session_state.json"
    legacy_state = work_path / "state.json"
    
    if not state_file.exists() and not legacy_state.exists():
        return

    target_file = state_file if state_file.exists() else legacy_state
    try:
        with open(target_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        state.from_dict(data)
        cleared_fields = state.clear_invalid_paths()
        
        # Notify user if any files were cleared
        if cleared_fields:
            logging.warning(f"恢复会话时发现以下文件已删除，已重置相关设置: {', '.join(cleared_fields)}")
        
        if state.srt_path and state.srt_path.exists():
            state.srt_entries = SRTProcessor.parse(state.srt_path)
        else:
            state.srt_entries = []
        logging.info(f"Loaded previous session from {target_file}")
    except Exception as e:
        logging.error(f"Failed to auto-load session: {e}")


def save_entries_to_srt(entries: list[dict], output_path: Path) -> None:
    subs = pysrt.SubRipFile()
    for idx, entry in enumerate(entries, start=1):
        start = ms_to_srt_time(entry.get("start_ms", 0))
        end = ms_to_srt_time(entry.get("end_ms", 0))
        text = str(entry.get("text", "")).strip()
        subs.append(pysrt.SubRipItem(index=idx, start=start, end=end, text=text))
    subs.save(str(output_path), encoding="utf-8")


def resolve_final_video_path(work_dir: str, output_video: str, input_video: str) -> Path:
    """Unified video path resolution: Default to work_dir if not absolute."""
    input_v = Path(input_video)
    default_name = f"{input_v.stem}_dubbed{input_v.suffix}"
    
    if not output_video.strip():
        # Case 1: No output path provided, use work_dir/default_name
        return (Path(work_dir).resolve() / default_name).resolve()
        
    out_p = Path(output_video)
    if out_p.is_absolute():
        # Case 2: User provided an absolute path, respect it
        return out_p.resolve()
    else:
        # Case 3: User provided a filename or relative path, join with work_dir
        return (Path(work_dir).resolve() / output_video).resolve()


@ui.refreshable
def subtitle_editor(filter_str: str = "") -> None:
    if not state.srt_entries:
        ui.label("等待转写结果...").classes("text-gray-500")
        return
        
    entries_to_show = state.srt_entries
    if filter_str:
        entries_to_show = [e for e in state.srt_entries if filter_str in e["text"]]
        if not entries_to_show:
            ui.label(f"未找到包含 '{filter_str}' 的字幕").classes("text-gray-500")
            return

    with ui.column().classes("w-full gap-2"):
        for entry in entries_to_show:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"#{entry['id']}").classes("w-12 text-gray-500")
                ui.label(
                    f"{format_srt_timestamp(entry['start_ms'])} → {format_srt_timestamp(entry['end_ms'])}"
                ).classes("w-64 text-gray-500")
                text_input = ui.input(value=entry["text"]).classes("flex-1")
                
                def on_text_input_focus(item=entry):
                    # Save history before the user starts typing/editing
                    state.push_srt_history()

                text_input.on("focus", on_text_input_focus)
                text_input.on(
                    "change",
                    lambda _event, item=entry, widget=text_input: (
                        item.__setitem__("text", widget.value),
                        save_session_state()
                    ),
                )
                ui.button("播放合成", on_click=lambda _e, sid=entry["id"]: preview_segment(sid)).props("flat")


async def start_processing(
    url_input: ui.input,
    work_dir_input: ui.input,
    whisper_model_select: ui.select,
    lang_select: ui.select,
    progress_bar: ui.linear_progress,
    status_label: ui.label,
) -> None:
    if state.processing or state.synthesizing:
        ui.notify("已有任务在运行，请稍候", type="warning")
        return

    url_or_path = (url_input.value or "").strip()
    if not url_or_path:
        ui.notify("请输入视频 URL 或本地路径", type="negative")
        return

    state.clear_logs()
    state.cancel_event.clear()
    state.processing = True
    state.step = 1
    state.progress = 0.05
    state.url_or_path = url_or_path
    state.work_dir = (work_dir_input.value or "work").strip() or "work"

    work_path = resolve_work_base_dir()
    work_path.mkdir(parents=True, exist_ok=True)

    state.whisper_model = whisper_model_select.value or "small"
    state.lang = lang_select.value or "zh"
    state.video_data = None
    state.srt_entries = []
    state.srt_path = None
    state.final_video_path = None

    progress_bar.value = state.progress
    status_label.text = "处理中：下载/转写/提取参考音..."

    try:
        result = await run.io_bound(
            run_video_pipeline,
            state.url_or_path,
            state.work_dir,
            state.whisper_model,
            state.lang,
            False,
        )
        if state.cancel_event.is_set():
            ui.notify("任务已取消", type="warning")
            return
        if not result:
            ui.notify("视频处理失败，请查看日志", type="negative")
            return

        state.video_data = result
        state.srt_path = Path(result["srt"]).resolve()
        state.srt_entries = SRTProcessor.parse(state.srt_path)
        state.step = 2
        state.progress = 0.6
        
        save_session_state()

        subtitle_editor.refresh()
        ui.notify(f"转写完成，共 {len(state.srt_entries)} 条字幕", type="positive")
    except Exception as exc:
        logging.exception("处理流程异常")
        ui.notify(f"处理异常: {exc}", type="negative")
    finally:
        state.processing = False
        save_session_state()
        progress_bar.value = state.progress
        status_label.text = "等待用户编辑字幕并开始合成"


async def start_synthesis(
    emo_text_input: ui.input,
    diffusion_steps_input: ui.number,
    burn_subs_checkbox: ui.checkbox,
    force_regen_checkbox: ui.checkbox,
    output_video_input: ui.input,
    progress_bar: ui.linear_progress,
    status_label: ui.label,
    output_label: ui.label,
) -> None:
    if state.processing or state.synthesizing:
        ui.notify("已有任务在运行，请稍候", type="warning")
        return
    if not state.video_data or not state.srt_path:
        ui.notify("请先完成视频处理", type="warning")
        return
    if not state.srt_entries:
        ui.notify("没有可合成的字幕", type="warning")
        return

    state.synthesizing = True
    state.cancel_event.clear()
    state.step = 3
    state.progress = 0.7
    state.burn_subs = burn_subs_checkbox.value
    state.force_regen = force_regen_checkbox.value
    state.emo_text = (emo_text_input.value or "").strip()
    state.diffusion_steps = int(diffusion_steps_input.value or 25)
    state.output_video = (output_video_input.value or "").strip()

    progress_bar.value = state.progress
    status_label.text = "合成中：正在生成语音并混流..."

    try:
        save_entries_to_srt(state.srt_entries, state.srt_path)
        
        # Also update session state if any synthesis-specific settings changed
        save_session_state()

        # Build command for subprocess
        cmd = [
            sys.executable,
            "src/tts_generator.py",
            "--cfg_path", "checkpoints/config.yaml",
            "--model_dir", "checkpoints",
            "--ref_voice", str(state.video_data["voice_ref"]),
            "--srt", str(state.srt_path),
            "--out_dir", str(Path(state.work_dir) / "out_segs"),
            "--duration_mode", "seconds",
            "--lang", state.lang,
            "--diffusion_steps", str(state.diffusion_steps),
            "--speed", str(state.speed),
            "--sample_rate", str(state.sample_rate),
            "--gain_db", str(state.gain_db),
            "--tokens_per_sec", str(state.tokens_per_sec),
            "--emo_alpha", str(state.emo_alpha),
            "--max-retries", str(state.max_retries),
            "--video", str(state.video_data["video"]),
            "--stitch", # Enable stitching by default as per previous logic
        ]
        
        if state.burn_subs:
            cmd.append("--burn-subs")
            
        if state.force_regen:
            cmd.append("--force-regen")
            
        if state.emo_text:
            cmd.extend(["--emo_text", state.emo_text])
            
        if state.output_video:
            cmd.extend(["--output_video", state.output_video])
        elif state.output_video is not None: 
            # If user provided empty string (explicitly cleared), we pass nothing?
            # Or if output_video_input was empty string
            pass

        # Prepare environment
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        # Add project root to PYTHONPATH so src.config can be imported
        project_root = os.getcwd()
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
        
        logging.info(f"Starting synthesis process: {' '.join(cmd)}")

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
            cwd=os.getcwd()
        )
        state.current_process = process

        # Read output line by line
        while True:
            if state.cancel_event.is_set():
                 process.terminate()
                 break
                 
            line = await process.stdout.readline()
            if not line:
                break
            
            text = line.decode('utf-8', errors='replace').strip()
            if text:
                logging.info(text) # Log to UI via handler

        await process.wait()
        status = process.returncode
        state.current_process = None

        if status == 130 or state.cancel_event.is_set():
            ui.notify("任务已取消", type="warning")
            return
        if status != 0:
            ui.notify(f"合成失败，子进程退出码: {status}", type="negative")
            return

        state.final_video_path = resolve_final_video_path(
            state.work_dir,
            state.output_video,
            state.video_data["video"],
        )
        state.step = 4
        state.progress = 1.0
        output_label.text = f"输出视频: {state.final_video_path}"
        ui.notify("合成完成", type="positive")
    except Exception as exc:
        logging.exception("合成流程异常")
        ui.notify(f"合成异常: {exc}", type="negative")
    finally:
        state.synthesizing = False
        save_session_state()
        progress_bar.value = state.progress
        status_label.text = compute_status_text()


def create_start_processing_handler(
    url_input: ui.input,
    work_dir_input: ui.input,
    whisper_model_select: ui.select,
    lang_select: ui.select,
    progress_bar: ui.linear_progress,
    status_label: ui.label,
):
    async def handle_start_processing() -> None:
        await start_processing(
            url_input,
            work_dir_input,
            whisper_model_select,
            lang_select,
            progress_bar,
            status_label,
        )

    return handle_start_processing


def create_start_synthesis_handler(
    emo_text_input: ui.input,
    diffusion_steps_input: ui.number,
    burn_subs_checkbox: ui.checkbox,
    force_regen_checkbox: ui.checkbox,
    output_video_input: ui.input,
    progress_bar: ui.linear_progress,
    status_label: ui.label,
    output_label: ui.label,
):
    async def handle_start_synthesis() -> None:
        await start_synthesis(
            emo_text_input,
            diffusion_steps_input,
            burn_subs_checkbox,
            force_regen_checkbox,
            output_video_input,
            progress_bar,
            status_label,
            output_label,
        )

    return handle_start_synthesis


def open_work_folder(work_dir_input: ui.input) -> None:
    target = resolve_work_dir_target(work_dir_input)
    target.mkdir(parents=True, exist_ok=True)
    ui.run_javascript("window.open('/work-browser', '_blank')")


def clear_work_dir(target_dir: Path, dialog: ui.dialog) -> None:
    errors: list[str] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for child in target_dir.iterdir():
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink(missing_ok=True)
            except Exception as exc:
                errors.append(f"{child.name}: {exc}")

        if errors:
            ui.notify(f"清空未完全成功，首个错误: {errors[0]}", type="warning")
            return

        state.video_data = None
        state.srt_entries = []
        state.srt_path = None
        state.final_video_path = None
        state.segment_current = 0
        state.segment_total = 0
        state.step = 1
        state.progress = 0.0
        subtitle_editor.refresh()
        ui.notify("工作目录已清空", type="positive")
    except Exception as exc:
        ui.notify(f"清空失败: {exc}", type="negative")
    finally:
        dialog.close()


def resolve_work_dir_target(work_dir_input: ui.input | None = None) -> Path:
    if work_dir_input is not None:
        typed_work_dir = (work_dir_input.value or "").strip()
        if typed_work_dir:
            state.work_dir = typed_work_dir

    state.work_dir = (state.work_dir or "work").strip() or "work"
    return Path(state.work_dir).resolve()


def confirm_clear_work_dir(work_dir_input: ui.input | None = None) -> None:
    if state.processing or state.synthesizing:
        ui.notify("任务运行中，无法清空工作目录", type="warning")
        return

    target_dir = resolve_work_dir_target(work_dir_input)
    project_root = Path.cwd().resolve()
    if target_dir.exists() and not target_dir.is_dir():
        ui.notify("工作目录不可用", type="warning")
        return
    if target_dir == project_root:
        ui.notify("禁止清空项目根目录", type="negative")
        return
    if project_root not in target_dir.parents and target_dir != project_root:
        ui.notify("工作目录不在项目目录内，已阻止清空", type="negative")
        return

    with ui.dialog() as dialog, ui.card():
        ui.label("确认清空工作目录？").classes("text-lg font-medium")
        ui.label(str(target_dir)).classes("text-xs text-gray-500")
        ui.separator()
        with ui.row().classes("w-full gap-2"):
            ui.button("取消", on_click=dialog.close).classes("flex-1")
            ui.button("清空", on_click=lambda: clear_work_dir(target_dir, dialog)).classes("flex-1")
    dialog.open()


def compute_status_text() -> str:
    if state.processing:
        return "处理中：下载/转写/提取参考音..."

    if state.synthesizing:
        if state.segment_total > 0:
            status = f"合成中：第 {state.segment_current}/{state.segment_total} 句"
            # Only show warning if running on CPU
            if get_device() == "cpu":
                status += " (CPU 模式会较慢)"
            return status
        return "合成中：正在生成语音并混流..."

    if state.step == 2 and state.srt_entries:
        return "等待用户编辑字幕并开始合成"

    if state.step == 4 and state.progress >= 1.0:
        return "已完成，可打开工作目录"

    return "等待开始"


def parse_socket_io_transports(value: str | list[str] | None) -> list[str]:
    if value is None:
        return DEFAULT_SOCKET_IO_TRANSPORTS.copy()

    items = value if isinstance(value, list) else value.split(",")
    transports: list[str] = []
    for item in items:
        transport = str(item).strip().lower()
        if not transport:
            continue
        if transport not in ALLOWED_SOCKET_IO_TRANSPORTS:
            allowed = ", ".join(sorted(ALLOWED_SOCKET_IO_TRANSPORTS))
            raise ValueError(f"Unsupported Socket.IO transport '{transport}'. Allowed values: {allowed}")
        if transport not in transports:
            transports.append(transport)

    if not transports:
        raise ValueError("At least one Socket.IO transport must be configured.")

    return transports


def resolve_public_base_url(runtime_args: argparse.Namespace, bound_port: int) -> str:
    public_host = (getattr(runtime_args, "public_host", "") or "").strip()
    if not public_host:
        bind_host = (runtime_args.host or "").strip()
        public_host = "127.0.0.1" if bind_host in {"", "0.0.0.0", "::"} else bind_host

    public_port = getattr(runtime_args, "public_port", None) or bound_port
    return f"http://{public_host}:{public_port}"


def register_disconnect_cleanup(client, *timers) -> None:
    def stop_timers() -> None:
        for timer in timers:
            timer.cancel()

    client.on_disconnect(stop_timers)


def render_work_browser(subpath: str) -> None:
    base_dir = resolve_work_base_dir()
    rel_path = Path(subpath) if subpath else Path()
    target_dir = (base_dir / rel_path).resolve()

    with ui.column().classes("w-full gap-3 p-4"):
        ui.label("工作目录浏览").classes("text-xl font-medium")
        ui.link("返回主界面", "/").classes("text-blue-600")
        ui.label(f"根目录: {base_dir}").classes("text-sm text-gray-500")
        ui.separator()

        if not is_path_within_base(base_dir, target_dir):
            ui.label("路径超出工作目录，已阻止访问").classes("text-red-600")
            return
        if not target_dir.exists():
            ui.label("目录不存在").classes("text-gray-500")
            return

        if target_dir != base_dir:
            parent_rel = target_dir.parent.relative_to(base_dir)
            parent_href = (
                f"/work-browser/{parent_rel.as_posix()}" if str(parent_rel) != "." else "/work-browser"
            )
            ui.link("返回上一级", parent_href).classes("text-blue-600")

        ui.label(f"当前目录: {target_dir}").classes("text-sm text-gray-500")

        entries = sorted(target_dir.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        if not entries:
            ui.label("目录为空").classes("text-gray-500")
            return

        with ui.column().classes("w-full gap-1"):
            for entry in entries:
                rel_entry = entry.relative_to(base_dir)
                if entry.is_dir():
                    ui.link(f"[目录] {entry.name}", f"/work-browser/{rel_entry.as_posix()}").classes(
                        "text-blue-600"
                    )
                else:
                    file_url = get_work_url(entry)
                    if file_url:
                        link = ui.link(entry.name, file_url).classes("text-blue-600")
                        link.props("target=_blank")
                    else:
                        ui.label(f"{entry.name} (不可访问)").classes("text-gray-500")


@ui.page("/work-browser")
def work_browser_root() -> None:
    render_work_browser("")


@ui.page("/work-browser/{subpath:path}")
def work_browser_subpath(subpath: str) -> None:
    render_work_browser(subpath)


@app.get("/work")
def work_root() -> Response:
    return RedirectResponse(url="/work-browser")


@app.get("/work/{path:path}")
def work_files(path: str) -> Response:
    base_dir = resolve_work_base_dir()
    target_path = (base_dir / path).resolve()

    if not is_path_within_base(base_dir, target_path):
        raise HTTPException(status_code=404, detail="Invalid path")
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target_path.is_dir():
        return RedirectResponse(url=f"/work-browser/{path}")

    return FileResponse(target_path)


@ui.page("/")
def index_page() -> None:
    apply_theme()
    load_session_state()

    with ui.column().classes("app-shell w-full gap-4 p-4"):
        ui.label("VoiceEditor NiceGUI").classes("text-2xl font-bold")

        with ui.row().classes("w-full gap-4 items-start"):
            with ui.card().classes("w-[420px] gap-3"):
                ui.label("输入与参数").classes("text-lg font-medium")
                url_input = ui.input("视频 URL 或本地文件路径", value=state.url_or_path or "").bind_value(state, "url_or_path").classes("w-full")
                work_dir_input = ui.input("工作目录", value=str(state.work_dir) or "work").bind_value(state, "work_dir").classes("w-full")
                whisper_model_select = ui.select(
                    ["tiny", "base", "small", "medium", "large-v3"],
                    value=state.whisper_model,
                    label="Whisper 模型",
                ).bind_value(state, "whisper_model").classes("w-full")
                lang_select = ui.select(["zh", "en", "ja", "auto"], value=state.lang, label="输出语言").bind_value(state, "lang").classes("w-full")
                emo_text_input = ui.input("情绪提示（可选，如：whispering）", value=state.emo_text).bind_value(state, "emo_text").classes("w-full")
                diffusion_steps_input = ui.number(label="迭代步数 (Diffusion Steps)", value=state.diffusion_steps, min=5, max=80, step=1).bind_value(state, "diffusion_steps").classes("w-full")
                with ui.row().classes("w-full gap-2"):
                    burn_subs_checkbox = ui.checkbox("烧录字幕", value=state.burn_subs).bind_value(state, "burn_subs").classes("flex-1")
                    force_regen_checkbox = ui.checkbox("全量合成", value=state.force_regen).bind_value(state, "force_regen").classes("flex-1")
                    burn_subs_checkbox.tooltip("将修改后的字幕添加到输出视频上方")
                    force_regen_checkbox.tooltip("忽略缓存并重新生成所有音频片段")
                output_video_input = ui.input("输出视频路径（可选，默认为工作目录下）", value=str(state.output_video or "")).bind_value(state, "output_video").classes("w-full")

                # Advanced Parameters Section
                with ui.expansion("⚙️ 高级参数 (Advanced Parameters)", icon="tune").classes("w-full"):
                    speed_input = ui.number(
                        label="语速 (Speed)",
                        value=state.speed,
                        min=0.5, max=2.0, step=0.1
                    ).bind_value(state, "speed").classes("w-full")
                    speed_input.tooltip("调整音频播放速度 (0.5-2.0，1.0=正常)")
                    
                    sample_rate_input = ui.number(
                        label="采样率 (Sample Rate)",
                        value=state.sample_rate,
                        min=8000, max=48000, step=4000
                    ).bind_value(state, "sample_rate").classes("w-full")
                    sample_rate_input.tooltip("音频采样率 (8000-48000 Hz，常见值：44100, 48000)")
                    
                    gain_db_input = ui.number(
                        label="音量增益 (Gain dB)",
                        value=state.gain_db,
                        min=-20, max=20, step=0.5
                    ).bind_value(state, "gain_db").classes("w-full")
                    gain_db_input.tooltip("音量调整范围 (-20 到 +20 分贝)")
                    
                    tokens_per_sec_input = ui.number(
                        label="每秒 Token 数 (Tokens/sec)",
                        value=state.tokens_per_sec,
                        min=50, max=300, step=10
                    ).bind_value(state, "tokens_per_sec").classes("w-full")
                    tokens_per_sec_input.tooltip("控制语音合成速率，影响字幕对齐精度")
                    
                    emo_alpha_input = ui.number(
                        label="情绪强度 (Emotion Alpha)",
                        value=state.emo_alpha,
                        min=0.0, max=1.0, step=0.1
                    ).bind_value(state, "emo_alpha").classes("w-full")
                    emo_alpha_input.tooltip("情绪提示的影响力 (0.0=无，1.0=最大)")
                    
                    max_retries_input = ui.number(
                        label="最大重试次数 (Max Retries)",
                        value=state.max_retries,
                        min=1, max=10, step=1
                    ).bind_value(state, "max_retries").classes("w-full")
                    max_retries_input.tooltip("合成失败时的重试次数")

                progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                progress_label = ui.label("进度: 0%").classes("text-sm text-gray-600")
                status_label = ui.label("等待开始")
                output_label = ui.label(f"输出视频: {state.output_video or '-'}")

                with ui.row().classes("w-full gap-2"):
                    ui.button(
                        "1) 开始处理",
                        on_click=create_start_processing_handler(
                            url_input,
                            work_dir_input,
                            whisper_model_select,
                            lang_select,
                            progress_bar,
                            status_label,
                        ),
                    ).classes("flex-1")
                    ui.button(
                        "2) 开始合成",
                        on_click=create_start_synthesis_handler(
                            emo_text_input,
                            diffusion_steps_input,
                            burn_subs_checkbox,
                            force_regen_checkbox,
                            output_video_input,
                            progress_bar,
                            status_label,
                            output_label,
                        ),
                    ).classes("flex-1")

                ui.button("停止当前任务", on_click=stop_current_task).classes("w-full")

                ui.button("清空工作目录", on_click=lambda: confirm_clear_work_dir(work_dir_input)).classes("w-full")

                ui.button("打开工作目录", on_click=lambda: open_work_folder(work_dir_input)).classes("w-full")

            with ui.card().classes("flex-1 gap-3"):
                ui.label("字幕编辑").classes("text-lg font-medium")

                with ui.row().classes("w-full gap-2 items-center"):
                    find_input = ui.input("查找内容").classes("flex-1").props("dense outlined")
                    replace_input = ui.input("替换为").classes("flex-1").props("dense outlined")

                    def run_search() -> None:
                        subtitle_editor.refresh(find_input.value)

                    def clear_search() -> None:
                        find_input.value = ""
                        subtitle_editor.refresh("")

                    def run_replace_all() -> None:
                        f_text = find_input.value
                        r_text = replace_input.value
                        if not f_text:
                            ui.notify("查找内容为空", type="warning")
                            return
                        
                        # Save current state for undo
                        state.push_srt_history()
                        
                        count = 0
                        for entry in state.srt_entries:
                            if f_text in entry["text"]:
                                entry["text"] = entry["text"].replace(f_text, r_text)
                                count += 1
                        if count > 0:
                            ui.notify(f"已替换 {count} 处文本", type="positive")
                            save_session_state()
                            subtitle_editor.refresh(f_text if f_text else "")  # Refresh view
                        else:
                            ui.notify("没有找到匹配内容", type="info")

                    def run_undo() -> None:
                        if state.undo_srt_change():
                            ui.notify("已撤销上一步操作", type="positive")
                            save_session_state()
                            subtitle_editor.refresh(find_input.value)
                        else:
                            ui.notify("没有可撤销的操作", type="info")

                    ui.button(on_click=run_search, icon="search").props("dense flat").tooltip("查找/筛选")
                    ui.button(on_click=run_replace_all, icon="find_replace").props("dense flat").tooltip("全部替换")
                    ui.button(on_click=run_undo, icon="undo").props("dense flat").tooltip("撤销 (Undo)")
                    ui.button(on_click=clear_search, icon="clear").props("dense flat").tooltip("清除搜索")

                with ui.scroll_area().classes("w-full h-[380px] border rounded p-2"):
                    subtitle_editor()

                ui.label("试听播放器").classes("text-sm text-gray-500")
                ui.element("audio").props(f"id={AUDIO_PLAYER_ID} controls preload=none").classes("w-full")

                ui.separator()
                ui.label("运行日志").classes("text-lg font-medium")
                log_view = ui.textarea().props("readonly").classes("w-full h-[220px] mono-textarea")

                def refresh_logs() -> None:
                    # Defensive check: if the component is deleted, don't update
                    if log_view.is_deleted:
                        return
                    # Only update if content changed to minimize flicker
                    new_text = state.get_log_text()
                    if log_view.value != new_text:
                        log_view.value = new_text

                log_timer = BackgroundTimer(0.5, refresh_logs)

                def refresh_runtime_status() -> None:
                    if progress_bar.is_deleted:
                        return
                    progress_bar.value = state.progress
                    progress_label.text = f"进度: {round(state.progress * 100)}%"
                    status_label.text = compute_status_text()

                status_timer = BackgroundTimer(0.4, refresh_runtime_status)
                register_disconnect_cleanup(context.client, log_timer, status_timer)


def parse_runtime_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceEditor NiceGUI")
    parser.add_argument(
        "--host",
        default=os.environ.get("VOICEEDITOR_GUI_HOST", os.environ.get("NICEGUI_HOST", "0.0.0.0")),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("VOICEEDITOR_GUI_PORT", os.environ.get("NICEGUI_PORT", "8196"))),
    )
    parser.add_argument("--public-host", default=os.environ.get("VOICEEDITOR_GUI_PUBLIC_HOST", ""))
    parser.add_argument(
        "--public-port",
        type=int,
        default=int(os.environ["VOICEEDITOR_GUI_PUBLIC_PORT"]) if os.environ.get("VOICEEDITOR_GUI_PUBLIC_PORT") else None,
    )
    parser.add_argument(
        "--socket-io-transports",
        default=os.environ.get("VOICEEDITOR_GUI_SOCKET_IO_TRANSPORTS", ",".join(DEFAULT_SOCKET_IO_TRANSPORTS)),
    )
    parser.add_argument(
        "--reconnect-timeout",
        type=float,
        default=float(os.environ.get("VOICEEDITOR_GUI_RECONNECT_TIMEOUT", "30.0")),
    )
    parser.add_argument(
        "--binding-refresh-interval",
        type=float,
        default=float(os.environ.get("VOICEEDITOR_GUI_BINDING_REFRESH_INTERVAL", "0.5")),
    )
    runtime_args = parser.parse_args(argv)
    try:
        runtime_args.socket_io_transports = parse_socket_io_transports(runtime_args.socket_io_transports)
    except ValueError as exc:
        parser.error(str(exc))
    return runtime_args


def find_free_port(start_port: int, max_port: int = 65535) -> int:
    """Find a free port starting from start_port."""
    port = start_port
    while port <= max_port:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("0.0.0.0", port))
                return port
        except OSError:
            port += 1
    raise RuntimeError("No free ports found.")


if __name__ in {"__main__", "__mp_main__"}:
    runtime_args = parse_runtime_args()

    # Find a free port if the specified one is busy
    final_port = find_free_port(runtime_args.port)
    if final_port != runtime_args.port:
        print(f"Port {runtime_args.port} is busy. Using available port {final_port} instead.")

    app.config.socket_io_js_transports = runtime_args.socket_io_transports
    public_url = resolve_public_base_url(runtime_args, final_port)

    print(f"Starting VoiceEditor GUI on {public_url} (bind: http://{runtime_args.host}:{final_port})")
    print(f"Socket.IO transports: {', '.join(runtime_args.socket_io_transports)}")

    ui.run(
        title="VoiceEditor GUI",
        reload=False,
        host=runtime_args.host,
        port=final_port,
        reconnect_timeout=runtime_args.reconnect_timeout,
        binding_refresh_interval=runtime_args.binding_refresh_interval,
    )