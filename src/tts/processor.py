import json
import math
import logging
import time
import hashlib
import traceback
import sys
import re
from pathlib import Path
from glob import glob
from typing import Dict, List, Optional, Any
from tqdm import tqdm
from pydub import AudioSegment
import pysrt

from .audio_pipeline import retime_segment_to_target

class SRTProcessor:
    """
    Handles subtitle parsing and resolving related paths.
    """
    @staticmethod
    def parse(path: Path) -> List[Dict]:
        """
        Parse SRT into structured entries with graceful error handling.
        
        Returns list of entries with format {'id', 'text', 'start_ms', 'end_ms', 'dur_ms'}.
        Gracefully skips malformed entries and logs warnings instead of failing.
        """
        try:
            from opencc import OpenCC
            cc = OpenCC('t2s')
        except ImportError:
            logging.warning("opencc could not be imported. Proceeding without T2S conversion.")
            cc = None

        entries = []
        skipped_count = 0
        
        try:
            subs = pysrt.open(str(path), encoding="utf-8")
        except Exception as e:
            # Try fallback: manual line-by-line parsing for common format issues
            logging.warning(f"pysrt parsing failed: {e}. Attempting fallback line-by-line parsing.")
            return SRTProcessor._parse_fallback(path, cc)
        
        for index, item in enumerate(subs, start=1):
            try:
                text = item.text.replace("\r", " ").strip()
                if not text: 
                    skipped_count += 1
                    continue
                
                # Format text and convert to simplified if CC is available
                clean_text = " ".join(text.splitlines())
                if cc:
                    clean_text = cc.convert(clean_text)

                # Validate timing
                start = max(0, item.start.ordinal)
                end = max(start, item.end.ordinal)
                
                # Skip entries with invalid duration
                if end - start <= 0:
                    logging.warning(f"Skipping entry {index}: invalid duration ({start}ms -> {end}ms)")
                    skipped_count += 1
                    continue
                
                entries.append({
                    "id": len(entries) + 1,  # Re-index after skipping
                    "text": clean_text, 
                    "start_ms": start, 
                    "end_ms": end, 
                    "dur_ms": end - start
                })
            except Exception as e:
                logging.warning(f"Failed to parse entry {index}: {e}. Skipping.")
                skipped_count += 1
                continue
        
        if skipped_count > 0:
            logging.info(f"SRT parsing complete: {len(entries)} entries parsed, {skipped_count} skipped")
        
        if not entries:
            logging.error(f"No valid entries found in SRT file: {path}")
        
        return entries

    @staticmethod
    def _parse_fallback(path: Path, cc=None) -> List[Dict]:
        """
        Fallback SRT parser for malformed files.
        Attempts to parse line-by-line and extract timecodes and text.
        """
        entries = []
        current_entry = {}
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        # Blank line signals end of entry
                        if current_entry.get('text') and current_entry.get('start_ms') is not None:
                            entries.append(current_entry)
                            current_entry = {}
                        continue
                    
                    # Try to match timecode line (e.g., "00:00:01,000 --> 00:00:05,000")
                    import re
                    tc_match = re.match(r'(\d{2}):(\d{2}):(\d{2})[,:](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,:](\d{3})', line)
                    if tc_match:
                        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, tc_match.groups())
                        start_ms = h1 * 3600000 + m1 * 60000 + s1 * 1000 + ms1
                        end_ms = h2 * 3600000 + m2 * 60000 + s2 * 1000 + ms2
                        current_entry = {
                            'id': len(entries) + 1,
                            'start_ms': start_ms,
                            'end_ms': end_ms,
                            'dur_ms': end_ms - start_ms,
                            'text': ''
                        }
                    elif current_entry and line and not line.isdigit():
                        # This is text content
                        text = line.replace("\r", " ").strip()
                        if text:
                            if cc:
                                text = cc.convert(text)
                            if current_entry.get('text'):
                                current_entry['text'] += " " + text
                            else:
                                current_entry['text'] = text
        except Exception as e:
            logging.error(f"Fallback SRT parsing failed: {e}")
            return []
        
        # Add final entry if exists
        if current_entry.get('text') and current_entry.get('start_ms') is not None:
            entries.append(current_entry)
        
        logging.info(f"Fallback parsing recovered {len(entries)} entries from {path}")
        return entries


    @staticmethod
    def resolve_path(pattern: str) -> Path:
        """Resolve wildcard pattern to single SRT file."""
        candidate = Path(pattern)
        if candidate.exists(): return candidate
        matches = sorted(glob(pattern, recursive=True))
        if not matches: raise FileNotFoundError(pattern)
        return Path(matches[0])

    @staticmethod
    def guess_video(srt_path: Path) -> Optional[Path]:
        """Guess video file from SRT location."""
        video_exts = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".m4v"}
        stem_match = srt_path.with_suffix(".mp4")
        if stem_match.exists(): return stem_match
        matches = [c for c in srt_path.parent.glob(f"{srt_path.stem}.*") if c.suffix.lower() in video_exts]
        return sorted(matches)[0] if matches else None


def _get_gpu_diagnostics() -> Dict[str, Any]:
    """Collect GPU diagnostics if available."""
    diag = {"gpu_available": False}
    try:
        import torch
        diag["gpu_available"] = torch.cuda.is_available()
        if diag["gpu_available"]:
            diag["cuda_version"] = torch.version.cuda
            diag["device_count"] = torch.cuda.device_count()
            diag["current_device"] = torch.cuda.current_device()
            # Try to get memory usage
            try:
                allocated = torch.cuda.memory_allocated() / 1e9  # GB
                reserved = torch.cuda.memory_reserved() / 1e9
                diag["gpu_memory_allocated_gb"] = round(allocated, 2)
                diag["gpu_memory_reserved_gb"] = round(reserved, 2)
            except:
                pass
    except:
        pass
    return diag


def _format_segment_diagnostic(entry: Dict, exc: Exception, error_type: str) -> str:
    """Format comprehensive error diagnostics for a segment."""
    gpu_info = _get_gpu_diagnostics()
    
    lines = [
        f"[Segment Diagnostic] ID={entry.get('id', '?')} Error={error_type}",
        f"  Text: {entry.get('text', '')[:100]}",
        f"  Duration: {entry.get('dur_ms')}ms",
        f"  Error: {str(exc)[:150]}",
    ]
    
    # Add GPU info if available
    if gpu_info.get("gpu_available"):
        lines.append(f"  GPU: available | CUDA {gpu_info.get('cuda_version', 'unknown')}")
        if "gpu_memory_allocated_gb" in gpu_info:
            lines.append(f"  Memory: {gpu_info['gpu_memory_allocated_gb']}GB allocated, "
                        f"{gpu_info['gpu_memory_reserved_gb']}GB reserved")
    else:
        lines.append(f"  GPU: not available (CPU mode)")
    
    # Add recovery suggestions
    if "cuda out of memory" in str(exc).lower():
        lines.append("  💡 Suggestion: Reduce diffusion_steps, use smaller batch size, or switch to CPU")
    elif "permission" in str(exc).lower() or "permission denied" in str(exc).lower():
        lines.append("  💡 Suggestion: Check write permissions in output directory")
    elif "no space left" in str(exc).lower():
        lines.append("  💡 Suggestion: Free up disk space in work directory")
    
    return "\n".join(lines)


def _clear_gpu_cache():
    """Clear GPU cache and collect garbage after OOM. Graceful if GPU unavailable."""
    try:
        import torch
        import gc
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            gc.collect()
            logging.info(">> GPU 缓存已清理，准备继续处理下一批分段")
    except:
        pass


from src.config import Config, FILENAME_MANIFEST

class TTSSynthesizer:
    """
    Orchestrates the synthesis process from entries to audio segments.
    Includes memory-efficient batch processing for long videos.
    """
    BATCH_SIZE_DEFAULT = 10  # Process 10 segments before memory cleanup
    
    def __init__(self, tts, config: Any):
        self.tts = tts
        self.config = config
        # Estimate if this is a long video (>50 segments = ~15+ minutes)
        self.is_long_video = False
        self.batch_size = self.BATCH_SIZE_DEFAULT

    def build_duration_candidates(self, target_ms: float) -> List[Dict]:
        """Build candidates for inference duration parameters."""
        tokens = max(1, math.ceil((target_ms / 1000.0) * self.config.tokens_per_sec))
        return [{"max_mel_tokens": tokens}, {"max_generate_length": tokens}]

    def _perform_batch_cleanup(self, batch_num: int):
        """
        Perform memory cleanup after processing a batch of segments.
        Helps prevent OOM on long videos.
        """
        try:
            import gc
            # Force garbage collection
            gc.collect()
            
            # Clear GPU cache if available
            _clear_gpu_cache()
            
            logging.debug(f"Batch {batch_num} cleanup completed")
        except Exception as e:
            logging.debug(f"Batch cleanup warning: {e}")

    def safe_infer(self, base_kwargs: Dict, candidates: List[Dict], seq: int):
        """Safely run TTS with fallback parameters."""
        last_exc = None
        max_retries = max(1, int(getattr(self.config, "max_retries", 3)))
        for attempt in candidates:
            kwargs = {**base_kwargs, **attempt}
            param_name = next(iter(attempt))
            try:
                for retry_index in range(max_retries):
                    try:
                        self.tts.infer(**kwargs)
                        return
                    except TypeError as exc:
                        if param_name in str(exc):
                            last_exc = exc
                            break
                        raise
                    except Exception as exc:
                        last_exc = exc
                        if retry_index + 1 >= max_retries:
                            break
                        wait_seconds = min(2 ** retry_index, 4)
                        logging.warning(
                            "Segment %s inference failed on attempt %s/%s, retrying in %ss: %s | text=%s | params=%s",
                            seq,
                            retry_index + 1,
                            max_retries,
                            wait_seconds,
                            exc,
                            base_kwargs.get("text", "")[:80],
                            {k: v for k, v in kwargs.items() if k != "text"},
                        )
                        time.sleep(wait_seconds)
                if last_exc and not isinstance(last_exc, TypeError):
                    continue
            except TypeError as exc:
                if param_name in str(exc):
                    last_exc = exc
                    continue
                raise
        if last_exc: raise last_exc

    def synthesize(self, entries: List[Dict]) -> tuple[List[Dict], int]:
        """
        Synthesize audio segments for all entries with memory-efficient batch processing.
        
        For long videos (>50 segments), automatically enables batch cleanup to prevent OOM.
        """
        # 1. Explicitly check ref_voice existence with detailed error
        if not self.config.ref_voice or not self.config.ref_voice.exists():
             logging.error(f"CRITICAL: Reference voice file missing at: {self.config.ref_voice}")
             logging.error(f"Absolute path: {self.config.ref_voice.resolve() if self.config.ref_voice else 'None'}")
             raise FileNotFoundError(f"Reference voice file not found: {self.config.ref_voice}")

        # 2. Verify output directory
        if not self.config.out_dir.exists():
            logging.info(f"Creating output directory: {self.config.out_dir}")
            self.config.out_dir.mkdir(parents=True, exist_ok=True)
        elif not self.config.out_dir.is_dir():
             raise NotADirectoryError(f"Output path is not a directory: {self.config.out_dir}")

        # Detect long video and adjust batch processing
        self.is_long_video = len(entries) > 50
        if self.is_long_video:
            logging.info(f"Long video detected ({len(entries)} segments). Enabling batch memory cleanup every {self.batch_size} segments.")

        manifest_path = self.config.out_dir / FILENAME_MANIFEST
        legacy_manifest = self.config.out_dir / "manifest.json"
        
        existing = {}
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    entries_j = json.load(f)
                existing = {idx: entry for idx, entry in enumerate(entries_j, start=1)}
            except Exception: pass
        elif legacy_manifest.exists():
            try:
                logging.info(f"Mapping legacy manifest: {legacy_manifest}")
                with open(legacy_manifest, "r", encoding="utf-8") as f:
                    entries_j = json.load(f)
                existing = {idx: entry for idx, entry in enumerate(entries_j, start=1)}
            except Exception: pass

        manifest = []
        failed = []
        canceled = False
        cancel_event = getattr(self.config, "cancel_event", None)
        force_regen = getattr(self.config, "force_regen", False)
        
        pbar = tqdm(entries, desc="正在生成语音", unit="句", disable=not self.config.verbose)
        for seq, entry in enumerate(pbar, start=1):
            if cancel_event is not None and cancel_event.is_set():
                logging.warning(">> 合成已取消，正在停止（已完成片段将保留）")
                canceled = True
                break
            
            # 1. Calculate Fingerprint for current entry
            # Includes text, duration, and key synthesis parameters
            fingerprint_data = {
                "text": entry["text"],
                "dur_ms": entry["dur_ms"],
                "ref_voice": str(self.config.ref_voice.resolve()) if self.config.ref_voice else "",
                "emo_text": self.config.emo_text,
                "emo_audio": str(Path(self.config.emo_audio).resolve()) if self.config.emo_audio else "",
                "emo_alpha": self.config.emo_alpha,
                "diffusion_steps": self.config.diffusion_steps,
                "lang": self.config.lang
            }
            content_hash = hashlib.md5(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
            
            seg_path = self.config.out_dir / f"seg_{seq:04d}.wav"
            
            # 2. Check for cache hit using fingerprint
            if not force_regen and seq in existing:
                cached_entry = existing[seq]
                # If hash matches and file exists, we can reuse it
                if cached_entry.get("content_hash") == content_hash and seg_path.exists():
                    logging.info(f"Segment {seq} Cache Hit (Fingerprint match).")
                    cached_entry = dict(cached_entry)
                    cached_entry["wav"] = seg_path.name
                    manifest.append(cached_entry)
                    continue
                # Fallback: Check if file exists and text matches (legacy support)
                elif cached_entry.get("text", "").strip() == entry["text"].strip() and \
                     cached_entry.get("dur_target_ms") == entry["dur_ms"] and \
                     seg_path.exists():
                    logging.info(f"Segment {seq} Cache Hit (Metadata match).")
                    cached_entry = dict(cached_entry)
                    cached_entry["content_hash"] = content_hash
                    cached_entry["wav"] = seg_path.name
                    manifest.append(cached_entry)
                    continue
            
            candidates = self.build_duration_candidates(entry["dur_ms"])
            base_kwargs = {
                "spk_audio_prompt": str(self.config.ref_voice),
                "text": entry["text"], "output_path": str(seg_path),
                "emo_alpha": self.config.emo_alpha, "verbose": self.config.verbose,
                "diffusion_steps": self.config.diffusion_steps
            }
            if self.config.emo_audio: base_kwargs["emo_audio_prompt"] = str(self.config.emo_audio)
            if self.config.emo_text: base_kwargs.update({"use_emo_text": True, "emo_text": self.config.emo_text})

            # always log info for every segment so user sees progress
            logging.info(f"Processing Segment {seq}/{len(entries)} | Dur: {entry['dur_ms']}ms | Text: {entry['text'][:30]}...")
            
            # 3. Debug print for first item to confirm correct inputs
            if seq == 1:
                logging.debug(f"Snippet Arguments: {base_kwargs}")

            try:
                self.safe_infer(base_kwargs, candidates, seq)
                if not seg_path.exists():
                     raise FileNotFoundError(f"TTS completed but segment file not found: {seg_path}")
                
                generated = AudioSegment.from_file(seg_path)
                retimed, actual_ms, speed = retime_segment_to_target(generated, entry["dur_ms"], self.config.sample_rate)
                retimed.export(seg_path, format="wav")
                
                # Check file size to ensure it's not a dummy mock or corrupted
                if seg_path.stat().st_size < 100:
                     raise RuntimeError(f"Synthesized segment {seq} is too small, likely corrupted.")
                
                new_entry = {
                    "id": entry["id"], "text": entry["text"], "start_ms": entry["start_ms"], 
                    "end_ms": entry["end_ms"], "wav": seg_path.name, "dur_target_ms": entry["dur_ms"], 
                    "dur_actual_ms": actual_ms, "diff_ms": actual_ms - entry["dur_ms"], "speed_factor": round(speed, 3),
                    "content_hash": content_hash
                }
                manifest.append(new_entry)
                
                # Batch cleanup for long videos: clean memory every batch_size segments
                if self.is_long_video and seq % self.batch_size == 0:
                    batch_num = seq // self.batch_size
                    self._perform_batch_cleanup(batch_num)
                
                # Incremental save every 5 items or if it's the last one
                if seq % 5 == 0 or seq == len(entries):
                    self.save_manifest(manifest, self.config.out_dir, silent=True)
            except Exception as e:
                # 4. Catch critical errors and log full traceback
                err_str = str(e).lower()
                is_oom = "cuda out of memory" in err_str or "cualloc" in err_str or "cufft" in err_str
                is_env = "not found" in err_str or "permission" in err_str or "no space left" in err_str
                segment_context = {
                    "id": entry["id"],
                    "start_ms": entry["start_ms"],
                    "end_ms": entry["end_ms"],
                    "dur_ms": entry["dur_ms"],
                    "text": entry["text"][:120],
                }
                
                if is_oom:
                     # Output comprehensive diagnostic info for OOM
                     diag = _format_segment_diagnostic(entry, e, "CUDA Out of Memory")
                     logging.warning(diag)
                     logging.warning(">> 保存当前进度后继续处理其他片段")
                     
                     # Still add to manifest as failed entry so we don't lose track
                     failed_entry = {
                        "id": entry["id"], "text": entry["text"], "start_ms": entry["start_ms"], 
                        "end_ms": entry["end_ms"], "dur_target_ms": entry["dur_ms"],
                        "failed": True, "error_reason": "CUDA Out of Memory"
                     }
                     manifest.append(failed_entry)
                     failed.append(seq)
                     
                     # Save manifest checkpoint before OOM
                     self.save_manifest(manifest, self.config.out_dir, silent=True)
                     
                     # Clear GPU cache to recover memory for remaining segments
                     _clear_gpu_cache()
                     
                     # Log recovery instruction to manifest
                     recovery_note = {
                        "type": "oom_recovery_checkpoint",
                        "timestamp": time.time(),
                        "failed_count": len(failed),
                        "completed_count": len(manifest) - len(failed),
                        "note": "After OOM, GPU cache cleared. Attempting to continue with remaining segments.",
                        "suggestion": "If OOM persists, try: (1) reduce diffusion_steps in config, (2) switch to CPU mode, (3) process fewer segments per batch"
                     }
                     logging.info(f"Recovery checkpoint: {recovery_note['completed_count']} completed, {recovery_note['failed_count']} failed, retrying...")
                     
                     # Continue trying next segments with reduced load if possible
                     continue
                
                if is_env:
                     diag = _format_segment_diagnostic(entry, e, "System/Environment Error")
                     logging.error(diag)
                     # Mark as failed and continue
                     failed_entry = {
                        "id": entry["id"], "text": entry["text"], "start_ms": entry["start_ms"], 
                        "end_ms": entry["end_ms"], "dur_target_ms": entry["dur_ms"],
                        "failed": True, "error_reason": str(e)[:100]
                     }
                     manifest.append(failed_entry)
                     failed.append(seq)
                     continue
                
                # Logic/Model errors: mark as failed and continue
                diag = _format_segment_diagnostic(entry, e, "TTS Synthesis Error")
                logging.error(diag)
                # Also log the full traceback for debugging
                logging.debug(f"Full traceback:\n{traceback.format_exc()}")
                failed_entry = {
                    "id": entry["id"], "text": entry["text"], "start_ms": entry["start_ms"], 
                    "end_ms": entry["end_ms"], "dur_target_ms": entry["dur_ms"],
                    "failed": True, "error_reason": "Synthesis error: " + str(e)[:80]
                }
                manifest.append(failed_entry)
                failed.append(seq)
        
        # Final save to ensure everything is captured
        self.save_manifest(manifest, self.config.out_dir, silent=True)
        
        # Determine success and warning status
        if canceled:
            return manifest, 130
        
        # Critical Check: if we have NO manifest at all but had entries, it's a failure
        if entries and not manifest:
            logging.error("TTSSynthesizer: No segments were generated at all.")
            return manifest, 1

        # Partial failure check: report but allow continuation
        if failed:
            successful_count = len([m for m in manifest if not m.get("failed", False)])
            logging.warning(f"TTSSynthesizer: Partial failure detected. "
                          f"Successfully generated: {successful_count}/{len(entries)} segments. "
                          f"Failed segment IDs: {failed}")
            # Return with warning code if some succeeded
            if successful_count > 0:
                logging.info(f">> Continuing with {successful_count} successful segments (will skip {len(failed)} failed).")
                return manifest, 0  # Return success code to allow continuation, but manifest marks failures
            else:
                return manifest, 1  # All failed

        return manifest, 0

    @staticmethod
    def save_manifest(manifest: List[Dict], out_dir: Path, silent: bool = False):
        """Save manifest and report stats. Normalizes paths to be relative for cross-platform portability."""
        path = out_dir / "manifest.json"
        
        # Normalize wav paths to be relative to manifest directory for portability
        normalized = []
        for entry in manifest:
            normalized_entry = dict(entry)
            if "wav" in normalized_entry:
                wav_val = normalized_entry["wav"]
                # If it's an absolute path, convert to relative to out_dir
                if wav_val:
                    wav_path = Path(wav_val)
                    try:
                        # If already just a filename or relative path, keep it
                        if not wav_path.is_absolute():
                            normalized_entry["wav"] = wav_val
                        else:
                            # Convert absolute to relative to out_dir
                            relative = wav_path.relative_to(out_dir.parent)
                            normalized_entry["wav"] = relative.as_posix()  # Use forward slashes for portability
                    except ValueError:
                        # If relative_to fails, keep original
                        pass
            normalized.append(normalized_entry)
        
        # Write to temp file first for atomic save
        tmp_path = path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        
        if manifest and not silent:
            t_tar = sum(m["dur_target_ms"] for m in manifest) / 1000.0
            t_act = sum(m["dur_actual_ms"] for m in manifest) / 1000.0
            logging.info(f">> 生成清单已保存。目标时长: {t_tar:.2f}秒, 实际时长: {t_act:.2f}秒。")
