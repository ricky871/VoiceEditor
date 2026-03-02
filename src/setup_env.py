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
    logging.error("Alternately, try: pip install uv")
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
        try:
            from huggingface_hub import snapshot_download
        except ImportError:
            logging.error("huggingface_hub library not found. Run 'uv pip install huggingface-hub' first.")
            return False

        # List of mirrors to try
        mirrors = [
            "https://hf-mirror.com",
            "https://hf-cdn.sufy.com",
            "http://aifasthub.com"
        ]
        
        # Check if user already set one
        if "HF_ENDPOINT" in os.environ:
             current_endpoint = os.environ["HF_ENDPOINT"]
             if current_endpoint not in mirrors:
                 mirrors.insert(0, current_endpoint)

        success = False
        for mirror in mirrors:
            try:
                logging.info(f"Trying HuggingFace mirror: {mirror}...")
                os.environ["HF_ENDPOINT"] = mirror
                snapshot_download(repo_id="IndexTeam/IndexTTS-2", local_dir=str(local_path))
                logging.info(f"Models downloaded from {mirror} successfully.")
                success = True
                break
            except Exception as e:
                logging.warning(f"Download failed from {mirror}: {e}")
        
        if not success:
            logging.error("All HuggingFace mirrors failed.")
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
            
        # Download Whisper models in advance
        try:
            from faster_whisper import WhisperModel
            import torch
            logging.info("Pre-downloading Whisper models...")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            # Default models to pre-download
            for model_size in ["small", "medium"]:
                logging.info(f"Downloading Whisper model: {model_size}...")
                WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as e:
            logging.warning(f"Failed to pre-download Whisper models: {e}. They will be downloaded during first run.")

        # Download secondary Hub models (MaskGCT, SeamlessM4T, CAMPPlus, BigVGAN)
        try:
            logging.info("Pre-downloading auxiliary models from HuggingFace/ModelScope...")
            from huggingface_hub import hf_hub_download
            from transformers import SeamlessM4TFeatureExtractor
            
            # 1. SeamlessM4T
            SeamlessM4TFeatureExtractor.from_pretrained("facebook/w2v-bert-2.0")
            
            # 2. MaskGCT semantic codec
            hf_hub_download("amphion/MaskGCT", filename="semantic_codec/model.safetensors")
            
            # 3. CAMPPlus
            hf_hub_download("funasr/campplus", filename="campplus_cn_common.bin")
            
            # 4. BigVGAN (Common default)
            try:
                # Add index-tts to path if not exists
                src_dir = Path(__file__).parent
                if (src_dir.parent / "index-tts").exists():
                    sys.path.append(str(src_dir.parent / "index-tts"))
                from indextts.s2mel.modules.bigvgan import bigvgan
                bigvgan.BigVGAN.from_pretrained("nvidia/bigvgan_v2_24khz_80band_256x")
            except:
                pass

        except Exception as e:
            logging.warning(f"Failed to pre-download some auxiliary models: {e}")

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
