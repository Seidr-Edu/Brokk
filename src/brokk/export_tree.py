from __future__ import annotations

import os
import shutil
from pathlib import Path


def export_clean_tree(source_dir: Path, destination_dir: Path) -> None:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(source_dir, topdown=True, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(source_dir)
        target_root = (
            destination_dir / relative_root if relative_root != Path(".") else destination_dir
        )
        target_root.mkdir(parents=True, exist_ok=True)

        kept_dirs: list[str] = []
        for directory in dirs:
            if directory == ".git":
                continue
            source_path = root_path / directory
            target_path = target_root / directory
            if source_path.is_symlink():
                _copy_symlink(source_path, target_path)
                continue
            target_path.mkdir(parents=True, exist_ok=True)
            shutil.copystat(source_path, target_path, follow_symlinks=False)
            kept_dirs.append(directory)
        dirs[:] = kept_dirs

        for filename in files:
            if filename == ".git":
                continue
            source_path = root_path / filename
            target_path = target_root / filename
            if source_path.is_symlink():
                _copy_symlink(source_path, target_path)
            else:
                shutil.copy2(source_path, target_path, follow_symlinks=False)


def _copy_symlink(source_path: Path, target_path: Path) -> None:
    if target_path.exists() or target_path.is_symlink():
        target_path.unlink()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.readlink(source_path), target_path)
