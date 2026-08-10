"""原子化文件写入。

每个写入 JSON 状态（会话、设置、转录记录）的地方都必须能在崩溃或
并发写入时保持文件不被损坏。其模式为：在同一目录中写入一个临时文件，
fsync，然后用 ``os.replace`` 覆盖到最终路径。``os.replace`` 在 POSIX
和 Windows 上都是原子的。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: str | Path, text: str) -> None:
    """将 *text* 原子化地写入 *path*。

    在同一目录中创建临时文件（这样 ``os.replace`` 始终是一次
    重命名，而非跨文件系统的拷贝），写入后 fsync，然后替换。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # 临时文件必须位于同一目录，os.replace 才能是原子的。
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # 任何失败——清理临时文件，而不是留下残留文件。
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | Path, data: Any, *, indent: int | None = 2) -> None:
    """将 *data* 以 JSON 形式原子化地写入 *path*。"""
    if indent is None:
        text = json.dumps(data, default=str, separators=(",", ":"))
    else:
        text = json.dumps(data, default=str, indent=indent)
        text += "\n"
    atomic_write_text(path, text)
