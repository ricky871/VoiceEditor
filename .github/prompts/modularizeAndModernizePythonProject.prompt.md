---
name: modularizeAndModernizePythonProject
description: Refactor fragmented scripts into a modern, modular Python project with a unified CLI.
argument-hint: The project directory or list of scripts to refactor.
---
Analyze the provided collection of scripts and project structure. Refactor them into a modern, modular Python architecture following these steps:

1.  **Restructure**: Define a clear directory hierarchy, moving core logic into a `src/` directory with standardized, descriptive filenames.
2.  **Unified Entry Point**: Create a `main.py` at the root that serves as the single interface for the project, utilizing a subcommand-based CLI (e.g., `setup`, `run`, `process`).
3.  **Cross-Platform Porting**: Port any OS-specific logic (e.g., .sh or .ps1 scripts) into cross-platform Python code using libraries like `pathlib`, `subprocess`, and `shutil`.
4.  **Environment Management**: Implement modern environment setup using tools like `uv`. Include logic for handling dependencies, mirror sources, and GPU/environment checks.
5.  **Modularization**: Refactor standalone scripts into reusable Python modules and functions that can be imported and orchestrated by the main entry point.
6.  **Configuration & Mirrors**: Add support for configuration via environment variables or CLI flags, specifically focusing on regional mirror support (e.g., China mirrors) for package and data downloads.
7.  **Documentation**: Update or create documentation (e.g., README.md, DEVELOPMENT.md) that explains the new architecture, dependency management, and usage instructions.

Ensure that all file path references are updated correctly to reflect the new structure and maintain project functionality.
