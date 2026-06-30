"""Windows 下 Unix 命令兼容垫片，供 Bash 工具使用。

本目录下每个 *.cmd 启动器都调用 ``python unix_shim.py <cmd> <args>``，
让 Bash 工具在 Windows（原生没有 ls/cat/head/find 等）上也能跑常见 Unix 风格命令。

刻意做得宽松：不影响语义的 flag 直接忽略，输出近似 Unix 工具，
够模型日常使用即可。
"""

import os
import re
import shutil
import subprocess
import sys


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def cmd_ls(args):
    detailed = any(a in ("-l", "-la", "-al", "-lA") for a in args)
    paths = [_strip_quotes(a) for a in args if not a.startswith("-")]
    target = paths[0] if paths else "."
    if detailed:
        subprocess.run(["cmd", "/c", "dir", target], check=False)
        return
    try:
        for name in sorted(os.listdir(target)):
            print(name)
    except Exception as e:  # noqa: BLE001
        print(str(e), file=sys.stderr)
        sys.exit(1)


def cmd_cat(args):
    for p in args:
        p = _strip_quotes(p)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                sys.stdout.write(f.read())
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_pwd(_args):
    print(os.getcwd())


def cmd_cp(args):
    dest = _strip_quotes(args[-1])
    for src in args[:-1]:
        src = _strip_quotes(src)
        try:
            shutil.copy2(src, dest)
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_mv(args):
    dest = _strip_quotes(args[-1])
    for src in args[:-1]:
        src = _strip_quotes(src)
        try:
            shutil.move(src, dest)
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_mkdir(args):
    for p in args:
        p = _strip_quotes(p)
        if p in ("-p",):
            continue
        try:
            os.makedirs(p, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_rm(args):
    recursive = any(a in ("-r", "-rf", "-fr", "-rF", "-R") for a in args)
    targets = [_strip_quotes(a) for a in args if not a.startswith("-")]
    for t in targets:
        try:
            if os.path.isdir(t):
                if recursive:
                    shutil.rmtree(t)
                else:
                    os.rmdir(t)
            else:
                os.remove(t)
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_touch(args):
    for p in args:
        p = _strip_quotes(p)
        try:
            with open(p, "a"):
                pass
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_which(args):
    for a in args:
        a = _strip_quotes(a)
        loc = shutil.which(a)
        if loc:
            print(loc)
        else:
            sys.exit(1)


def cmd_head(args):
    n = 10
    if "-n" in args:
        i = args.index("-n")
        try:
            n = int(args[i + 1])
        except (IndexError, ValueError):
            pass
    paths = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "-n":
            skip = True
            continue
        if a.startswith("-"):
            continue
        paths.append(_strip_quotes(a))
    if not paths:
        lines = sys.stdin.read().splitlines()
        print("\n".join(lines[:n]))
        return
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                print("".join(f.readlines()[:n]), end="")
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_tail(args):
    n = 10
    if "-n" in args:
        i = args.index("-n")
        try:
            n = int(args[i + 1])
        except (IndexError, ValueError):
            pass
    paths = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a == "-n":
            skip = True
            continue
        if a.startswith("-"):
            continue
        paths.append(_strip_quotes(a))
    if not paths:
        lines = sys.stdin.read().splitlines()
        print("\n".join(lines[-n:]))
        return
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                print("".join(f.readlines()[-n]), end="")
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)


def cmd_wc(args):
    lines = words = chars = False
    if "-l" in args:
        lines = True
    if "-w" in args:
        words = True
    if "-c" in args:
        chars = True
    if not (lines or words or chars):
        lines = words = chars = True
    paths = [_strip_quotes(a) for a in args if not a.startswith("-")]
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception as e:  # noqa: BLE001
            print(str(e), file=sys.stderr)
            sys.exit(1)
        out = []
        if lines:
            out.append(str(text.count("\n") + (1 if text and not text.endswith("\n") else 0)))
        if words:
            out.append(str(len(text.split())))
        if chars:
            out.append(str(len(text)))
        print(" ".join(out) + f" {p}")


def cmd_grep(args):
    recursive = "-r" in args or "-R" in args
    ignore_case = "-i" in args
    show_line = "-n" in args
    rest = [a for a in args if not a.startswith("-")]
    if not rest:
        return
    pattern = _strip_quotes(rest[0])
    paths = [_strip_quotes(a) for a in rest[1:]] or ["."]
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        rx = re.compile(re.escape(pattern), flags)
    for base in paths:
        if recursive and os.path.isdir(base):
            for root, _dirs, files in os.walk(base):
                for fn in files:
                    _grep_file(os.path.join(root, fn), rx, show_line)
        else:
            _grep_file(base, rx, show_line)


def _grep_file(path, rx, show_line):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, 1):
                if rx.search(line):
                    line = line.rstrip("\n")
                    if show_line:
                        print(f"{path}:{i}:{line}")
                    else:
                        print(f"{path}:{line}")
    except Exception:  # noqa: BLE001
        pass


def cmd_find(args):
    # find <path> -type f -name <pattern>（按路径、类型、名称模式查找）
    rest = [_strip_quotes(a) for a in args]
    if not rest:
        return
    base = rest[0]
    ftype = "f" if "-type" in rest and "f" in rest[rest.index("-type") + 1 : rest.index("-type") + 2] else None
    name_pat = None
    if "-name" in rest:
        name_pat = rest[rest.index("-name") + 1]
    glob_pat = name_pat.replace("*", ".*").replace("?", ".") if name_pat else None
    if glob_pat:
        glob_pat = re.compile(glob_pat, re.IGNORECASE)
    for root, dirs, files in os.walk(base):
        entries = files if ftype != "d" else []
        if ftype == "d":
            entries = dirs
        for name in entries:
            if glob_pat and not glob_pat.search(name):
                continue
            print(os.path.join(root, name))


CMDS = {
    "ls": cmd_ls,
    "cat": cmd_cat,
    "pwd": cmd_pwd,
    "cp": cmd_cp,
    "mv": cmd_mv,
    "mkdir": cmd_mkdir,
    "rm": cmd_rm,
    "touch": cmd_touch,
    "which": cmd_which,
    "head": cmd_head,
    "tail": cmd_tail,
    "wc": cmd_wc,
    "grep": cmd_grep,
    "find": cmd_find,
}


def main():
    if len(sys.argv) < 2:
        print("usage: unix_shim.py <cmd> <args>", file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    fn = CMDS.get(cmd)
    if fn is None:
        print(f"shim: unsupported command '{cmd}'", file=sys.stderr)
        sys.exit(1)
    fn(sys.argv[2:])


if __name__ == "__main__":
    main()
