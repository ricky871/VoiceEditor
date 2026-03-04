from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import socket
import sys
from pathlib import Path

from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

import pysrt
from nicegui import app, run, ui

from src.config import get_logging_config, patch_tqdm, setup_environment
from src.tts.processor import SRTProcessor
from src.tts_generator import run_tts_generation
from src.video_handler import run_video_pipeline
from ui.state import AppState, UILogHandler
from ui.theme import apply_theme


state = AppState()
setup_environment()
patch_tqdm(True)

AUDIO_PLAYER_ID = "segment-player"

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
            state.current_process.terminate()
            state.append_log(">> 正在终止子进程...")
        except Exception:
            pass
    state.append_log(">> 用户请求停止当前任务")
    ui.notify("已请求停止任务，将在当前步骤结束后生效", type="warning")


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
def subtitle_editor() -> None:
    if not state.srt_entries:
        ui.label("等待转写结果...").classes("text-gray-500")
        return

    with ui.column().classes("w-full gap-2"):
        for entry in state.srt_entries:
            with ui.row().classes("w-full items-center gap-2"):
                ui.label(f"#{entry['id']}").classes("w-12 text-gray-500")
                ui.label(
                    f"{format_srt_timestamp(entry['start_ms'])} → {format_srt_timestamp(entry['end_ms'])}"
                ).classes("w-64 text-gray-500")
                text_input = ui.input(value=entry["text"]).classes("flex-1")
                text_input.on(
                    "change",
                    lambda _event, item=entry, widget=text_input: item.__setitem__("text", widget.value),
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
        subtitle_editor.refresh()
        ui.notify(f"转写完成，共 {len(state.srt_entries)} 条字幕", type="positive")
    except Exception as exc:
        logging.exception("处理流程异常")
        ui.notify(f"处理异常: {exc}", type="negative")
    finally:
        state.processing = False
        progress_bar.value = state.progress
        status_label.text = "等待用户编辑字幕并开始合成"


async def start_synthesis(
    emo_text_input: ui.input,
    diffusion_steps_input: ui.number,
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
    state.emo_text = (emo_text_input.value or "").strip()
    state.diffusion_steps = int(diffusion_steps_input.value or 25)
    state.output_video = (output_video_input.value or "").strip()

    progress_bar.value = state.progress
    status_label.text = "合成中：正在生成语音并混流..."

    try:
        save_entries_to_srt(state.srt_entries, state.srt_path)
        
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
            "--tokens_per_sec", "150.0",
            "--lang", state.lang,
            "--diffusion_steps", str(state.diffusion_steps),
            "--video", str(state.video_data["video"]),
            "--stitch", # Enable stitching by default as per previous logic
        ]
        
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
        progress_bar.value = state.progress
        status_label.text = compute_status_text()


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
            return f"合成中：第 {state.segment_current}/{state.segment_total} 句（CPU 模式会较慢）"
        return "合成中：正在生成语音并混流..."

    if state.step == 2 and state.srt_entries:
        return "等待用户编辑字幕并开始合成"

    if state.step == 4 and state.progress >= 1.0:
        return "已完成，可打开工作目录"

    return "等待开始"


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
    with ui.column().classes("app-shell w-full gap-4 p-4"):
        ui.label("VoiceEditor NiceGUI").classes("text-2xl font-bold")

        with ui.row().classes("w-full gap-4 items-start"):
            with ui.card().classes("w-[420px] gap-3"):
                ui.label("输入与参数").classes("text-lg font-medium")
                url_input = ui.input("视频 URL 或本地文件路径").classes("w-full")
                work_dir_input = ui.input("工作目录", value="work").classes("w-full")
                whisper_model_select = ui.select(
                    ["tiny", "base", "small", "medium", "large-v3"],
                    value="small",
                    label="Whisper 模型",
                ).classes("w-full")
                lang_select = ui.select(["zh", "en", "ja", "auto"], value="zh", label="输出语言").classes("w-full")
                emo_text_input = ui.input("情绪提示（可选，如：whispering）").classes("w-full")
                diffusion_steps_input = ui.number(label="迭代步数 (Diffusion Steps)", value=25, min=5, max=80, step=1).classes("w-full")
                output_video_input = ui.input("输出视频路径（可选，默认为工作目录下）").classes("w-full")

                progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full")
                progress_label = ui.label("进度: 0%").classes("text-sm text-gray-600")
                status_label = ui.label("等待开始")
                output_label = ui.label("输出视频: -").classes("text-gray-600")

                with ui.row().classes("w-full gap-2"):
                    ui.button(
                        "1) 开始处理",
                        on_click=lambda: start_processing(
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
                        on_click=lambda: start_synthesis(
                            emo_text_input,
                            diffusion_steps_input,
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
                with ui.scroll_area().classes("w-full h-[380px] border rounded p-2"):
                    subtitle_editor()

                ui.label("试听播放器").classes("text-sm text-gray-500")
                ui.element("audio").props(f"id={AUDIO_PLAYER_ID} controls preload=none").classes("w-full")

                ui.separator()
                ui.label("运行日志").classes("text-lg font-medium")
                log_view = ui.textarea().props("readonly").classes("w-full h-[220px] mono-textarea")

                def refresh_logs() -> None:
                    # Only update if content changed to minimize flicker
                    new_text = state.get_log_text()
                    if log_view.value != new_text:
                        log_view.value = new_text

                ui.timer(0.5, refresh_logs)

                def refresh_runtime_status() -> None:
                    progress_bar.value = state.progress
                    progress_label.text = f"进度: {round(state.progress * 100)}%"
                    status_label.text = compute_status_text()

                ui.timer(0.4, refresh_runtime_status)


def parse_runtime_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VoiceEditor NiceGUI")
    parser.add_argument("--host", default=os.environ.get("VOICEEDITOR_GUI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VOICEEDITOR_GUI_PORT", "8196")))
    return parser.parse_args()


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

    print(f"Starting VoiceEditor GUI on http://{runtime_args.host}:{final_port}")

    ui.run(
        title="VoiceEditor GUI",
        reload=False,
        host=runtime_args.host,
        port=final_port,
        reconnect_timeout=30.0,
        binding_refresh_interval=0.5,
    )