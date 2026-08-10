"""环境检测工具。"""

import os
import platform
import shutil
import subprocess


def get_platform() -> str:
    """返回当前平台的标识符：'linux'、'darwin' 或 'win32'。"""
    system = platform.system().lower()
    if system == "linux":
        return "linux"
    elif system == "darwin":
        return "darwin"
    elif system == "windows":
        return "win32"
    return system


def get_shell() -> str:
    """返回当前 shell 的名称：'bash'、'zsh'、'fish' 或 'unknown'。"""
    shell = os.environ.get("SHELL", "")
    if shell:
        basename = os.path.basename(shell)
        if basename in ("bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "csh"):
            return basename
    # 回退：检查常见 shell 是否可用
    for candidate in ("bash", "zsh"):
        if shutil.which(candidate):
            return candidate
    return "unknown"


def get_os_version() -> str:
    """返回人类可读的操作系统版本字符串，例如 'Linux 6.6.4' 或 'Darwin 23.1.0'。"""
    system = platform.system()
    release = platform.release()
    return f"{system} {release}"


def is_git_repo(cwd: str | None = None) -> bool:
    """检查给定目录（或当前目录）是否位于 git 仓库内部。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd or os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_cwd() -> str:
    """返回当前工作目录。"""
    return os.getcwd()
