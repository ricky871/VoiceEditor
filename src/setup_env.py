"""
Environment Setup & Model Downloader for VoiceEditor

Handles:
- Dependency synchronization via uv.
- GPU detection.
- Model checkpoint downloads from ModelScope (preferred for China) or HuggingFace.
- Environment variable injection (mirrors).
"""
import os
import sys
import subprocess
import shutil
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Global Settings
MODELSCOPE_ID = "IndexTeam/IndexTTS-2"
HF_ID = "amphion/MaskGCT" # IndexTTS based on MaskGCT weights if needed, but IndexTeam has its own repo

def is_gpu_available():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        # Fallback to nvidia-smi check
        try:
            subprocess.run(["nvidia-smi"], capture_output=True, check=True)
            return True
        except:
            return False

def check_uv():
    if shutil.which("uv"):
        return True
    logging.error("uv not found. Please install it first: https://github.com/astral-sh/uv")
    return False

def sync_dependencies(cn_mirror=True):
    logging.info("Syncing dependencies using uv...")
    env = os.environ.copy()
    if cn_mirror:
        env["UV_INDEX_URL"] = "https://pypi.tuna.tsinghua.edu.cn/simple"
    
    cmd = ["uv", "sync"]
    try:
        subprocess.run(cmd, env=env, check=True)
        logging.info("Dependencies synced successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to sync dependencies: {e}")
        return False
    return True

def download_checkpoints(source="modelscope", local_dir="checkpoints"):
    logging.info(f"Downloading checkpoints from {source} to {local_dir}...")
    local_path = Path(local_dir)
    local_path.mkdir(parents=True, exist_ok=True)
    
    if source == "modelscope":
        try:
            from modelscope import snapshot_download
            logging.info("Using ModelScope snapshot_download...")
            snapshot_download(MODELSCOPE_ID, local_dir=str(local_path))
            logging.info("Models downloaded from ModelScope successfully.")
        except ImportError:
            logging.error("modelscope library not found. Run 'uv pip install modelscope' first.")
            return False
        except Exception as e:
            logging.error(f"ModelScope download failed: {e}")
            return False
    else:
        # HuggingFace mode
        env = os.environ.copy()
        env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        try:
            from huggingface_hub import snapshot_download
            logging.info("Using HuggingFace snapshot_download (HF-Mirror)...")
            snapshot_download(repo_id="IndexTeam/IndexTTS-2", local_dir=str(local_path))
            logging.info("Models downloaded from HuggingFace successfully.")
        except ImportError:
            logging.error("huggingface_hub library not found. Run 'uv pip install huggingface-hub' first.")
            return False
        except Exception as e:
            logging.error(f"HuggingFace download failed: {e}")
            return False
    return True

def setup_all(cn_mirror=True, skip_download=False):
    if not check_uv():
        return False
    
    if not sync_dependencies(cn_mirror):
        return False
    
    if not skip_download:
        source = "modelscope" if cn_mirror else "huggingface"
        # Ensure modelscope is installed if needed
        if cn_mirror:
            subprocess.run(["uv", "pip", "install", "modelscope"], check=True)
        if not download_checkpoints(source=source):
            return False
            
    logging.info("Setup process completed successfully.")
    return True

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="VoiceEditor Setup Tool")
    parser.add_argument("--cn", action="store_true", default=True, help="Use Chinese mirrors")
    parser.add_argument("--skip-download", action="store_true", help="Skip model downloads")
    args = parser.parse_args()
    
    if setup_all(cn_mirror=args.cn, skip_download=args.skip_download):
        sys.exit(0)
    else:
        sys.exit(1)
