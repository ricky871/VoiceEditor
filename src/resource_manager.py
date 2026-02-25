import shutil
import logging
from pathlib import Path
from typing import List, Union

class ResourceManager:
    """
    Centralized resource manager for handling work directories, temporary files, and cleanup.
    """
    def __init__(self, work_dir: Union[str, Path] = "work", out_dir: Union[str, Path] = "work/out_segs"):
        self.work_dir = Path(work_dir).resolve()
        self.out_dir = Path(out_dir).resolve()
        self._temp_files: List[Path] = []

    def ensure_dirs(self):
        """Create necessary directories."""
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self

    def track_temp_file(self, file_path: Union[str, Path]) -> Path:
        """Register a file to be tracked for potential cleanup."""
        path = Path(file_path).resolve()
        self._temp_files.append(path)
        return path

    def cleanup(self, only_tracked: bool = True):
        """Clean up resources."""
        if only_tracked:
            for file in self._temp_files:
                try:
                    if file.exists():
                        file.unlink()
                        logging.debug(f"Cleaned up tracked file: {file}")
                except Exception as e:
                    logging.warning(f"Failed to cleanup {file}: {e}")
            self._temp_files.clear()
        else:
            # Dangerous: deletes entire folders
            logging.warning(f"Cleaning all outputs in {self.out_dir}")
            if self.out_dir.exists():
                shutil.rmtree(self.out_dir)
            self.out_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_dir_of_file(file_path: Union[str, Path]) -> Path:
        """Ensure the parent directory of a file exists."""
        path = Path(file_path).parent
        path.mkdir(parents=True, exist_ok=True)
        return path

    def get_output_path(self, filename: str) -> Path:
        """Get a path within the work directory."""
        return self.work_dir / filename
