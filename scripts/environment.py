"""Read-only preflight and explicitly confirmed, allowlisted installation."""

import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]
BACKENDS = frozenset(("archify", "drawio", "gif", "topology-gif", "icons", "all"))
DEPENDENCIES = frozenset(("node", "python", "drawio", "pillow", "font", "browser"))
PROBE_TIMEOUT = 15
INSTALL_TIMEOUT = 600

_HINTS = {
    "python": "安装 Python >=3.9 后重新运行；可先查看 install_plan('python')。",
    "node": "安装 Node.js >=18；可先查看 install_plan('node')。Archify 运行不需要 npm install。",
    "drawio": "安装 draw.io Desktop，或将 DRAWIO_BIN 指向其可执行文件（不是带参数的命令）。",
    "pillow": "查看 install_plan('pillow')；仅在本技能 .venv 中安装 Pillow>=10，不改系统 Python。",
    "font": "安装可用中文字体（例如 Noto Sans CJK SC），或用 CJK_FONT 指向本地字体文件。",
    "browser": "使用本机浏览器人工打开产物，检查布局、中文、交互和导出；不自动启动浏览器。",
}

_PYTHON_PROBE = """import io, json, pathlib, sys, xml.etree.ElementTree
print(json.dumps({'version': '.'.join(map(str, sys.version_info[:3]))}))
"""
_NODE_PROBE = """for (const name of ['node:fs', 'node:path', 'node:url', 'node:zlib']) require(name);
process.stdout.write(JSON.stringify({version: process.versions.node}));
"""
_PILLOW_PROBE = """import io, json
result = {'version': None, 'ok': False}
try:
    import PIL
    result['version'] = PIL.__version__
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
    first = Image.new('RGB', (8, 8), 'white')
    ImageDraw.Draw(first).rectangle((0, 0, 3, 3), fill='black')
    first = first.filter(ImageFilter.GaussianBlur(0.1))
    second = ImageChops.invert(first).resize((8, 8), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    first.save(stream, format='GIF', save_all=True, append_images=[second], duration=50, loop=0)
    stream.seek(0)
    with Image.open(stream) as image:
        if image.n_frames != 2:
            raise RuntimeError('animated GIF codec failed')
        image.seek(1)
        image.load()
    stream = io.BytesIO()
    first.save(stream, format='PNG')
    stream.seek(0)
    with Image.open(stream) as image:
        image.load()
    result['ok'] = True
except Exception as exc:
    result['error'] = str(exc)
print(json.dumps(result))
"""
_FONT_PROBE = """import json, sys
from PIL import Image, ImageDraw, ImageFont
result = {'ok': False, 'path': None, 'error': 'No font can render the Chinese sample without missing glyphs'}
for path in json.load(sys.stdin):
    try:
        font = ImageFont.truetype(path, 24)
        def signature(char):
            mask = font.getmask(char)
            return mask.size, bytes(mask)
        missing = signature(chr(0x10ffff))
        glyphs = [signature(char) for char in '中文架构数据流']
        if any(g == missing or not any(g[1]) for g in glyphs) or len(set(glyphs)) != len(glyphs):
            continue
        image = Image.new('RGB', (240, 48), 'white')
        ImageDraw.Draw(image).text((0, 0), '中文架构数据流', font=font, fill='black')
        result = {'ok': True, 'path': path}
        break
    except (OSError, ValueError, RuntimeError):
        continue
print(json.dumps(result))
"""
_ISOLATION_PROBE = """import json, site, sys, sysconfig
print(json.dumps({'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
                  'user_site': site.ENABLE_USER_SITE,
                  'install_paths': {key: sysconfig.get_path(key) for key in ('purelib', 'platlib', 'scripts', 'data')}}))
"""


def _validate(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError("Unsupported {}: {!r}; choose {}".format(label, value, ", ".join(sorted(choices))))


def _version_ok(version, minimum):
    match = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(version or "").strip())
    return bool(match and tuple(int(part or 0) for part in match.groups()) >= minimum)


def _executable(path):
    return bool(path and Path(path).is_file() and os.access(str(path), os.X_OK))


def _which(name):
    found = shutil.which(name)
    return os.path.abspath(found) if found and _executable(found) else None


def _child_env(installing=False):
    # Preflight and renderers must ignore inherited preloads and icon registries alike.
    excluded = {"NODE_OPTIONS", "NODE_PATH", "ICON_PERSONAL_ROOT", "ARCH_ICONS_ROOT",
                "ARCHITECTURE_DIAGRAM_ICONS", "ARCHITECTURE_DIAGRAM_THEME"}
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "PIP_")) and key not in excluded}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if installing:
        env.update(PIP_CONFIG_FILE=os.devnull, HOMEBREW_NO_AUTO_UPDATE="1",
                   HOMEBREW_NO_ANALYTICS="1", HOMEBREW_NO_ENV_HINTS="1")
    return env


def _run(command, installing=False, input_text=None):
    try:
        completed = subprocess.run(
            command, shell=False, check=False, capture_output=True, text=True,
            input=input_text, cwd=str(SKILL_ROOT), env=_child_env(installing),
            timeout=INSTALL_TIMEOUT if installing else PROBE_TIMEOUT,
        )
        return {"command": list(command), "returncode": completed.returncode,
                "stdout": completed.stdout.strip(), "stderr": completed.stderr.strip()}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": list(command), "returncode": None, "stdout": "", "stderr": str(exc)}


def _json_probe(command, input_text=None):
    result = _run(command, input_text=input_text)
    if result["returncode"] == 0:
        try:
            data = json.loads(result["stdout"])
            if isinstance(data, dict):
                return data, None
        except (ValueError, TypeError):
            pass
    return {}, (result["stderr"] or result["stdout"] or "probe failed")[:2000]


def _check(dependency, ready, path=None, version=None, purpose="", detail="", required=True):
    return {"id": dependency, "required": required, "status": "ready" if ready else "blocked",
            "version": version, "path": str(path) if path else None, "purpose": purpose,
            "install_hint": _HINTS[dependency], "detail": detail}


def _venv_python():
    return SKILL_ROOT / ".venv" / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def _selected_python(gif=False):
    candidate = _venv_python()
    if gif and _executable(candidate):
        return str(candidate)
    return os.path.abspath(sys.executable) if sys.executable else None


def _check_python(executable):
    purpose = "Python >=3.9；标准库入口及所选后端的 Python 运行时"
    if not _executable(executable):
        return _check("python", False, executable, purpose=purpose, detail="Python executable is unavailable")
    data, error = _json_probe([executable, "-I", "-B", "-S", "-c", _PYTHON_PROBE])
    ready = sys.version_info[:2] >= (3, 9) and _version_ok(data.get("version"), (3, 9, 0))
    return _check("python", ready, executable, data.get("version"), purpose,
                  error or ("stdlib probe passed" if ready else "Both the entry and selected Python must be >=3.9"))


def _check_node():
    path = _which("node")
    purpose = "Node.js >=18；Archify 渲染与 drawio XML 校验；无第三方 npm 运行依赖"
    if not path:
        return _check("node", False, purpose=purpose, detail="node not found on PATH")
    data, error = _json_probe([path, "-e", _NODE_PROBE])
    ready = _version_ok(data.get("version"), (18, 0, 0))
    return _check("node", ready, path, data.get("version"), purpose,
                  error or ("Node built-in module probe passed" if ready else "Node.js >=18 required"))


def _drawio_path():
    override = os.environ.get("DRAWIO_BIN")
    if override:
        # Treat as one executable name/path, never split or interpret a shell.
        # An explicit but invalid override is not silently ignored.
        expanded = os.path.expanduser(override)
        if _executable(expanded):
            return os.path.abspath(expanded)
        return _which(expanded) if not os.path.isabs(expanded) else None
    if platform.system() == "Darwin":
        for root in (Path("/Applications"), Path.home() / "Applications"):
            candidate = root / "draw.io.app/Contents/MacOS/draw.io"
            if _executable(candidate):
                return str(candidate)
    return _which("drawio") or _which("draw.io")


def _check_drawio(required=True):
    path = _drawio_path()
    version = None
    # Never run Electron, even with --version: it can open a GUI/write a profile.
    if path:
        info = Path(path).parent.parent / "Info.plist"
        try:
            data = plistlib.loads(info.read_bytes())
            version = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion")
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    return _check("drawio", bool(path), path, version,
                  "drawio 预览导出的桌面 CLI 入口（仅本地可执行文件静态检查）",
                  "Executable found; actual export is NOT tested" if path else "No executable drawio CLI found; check DRAWIO_BIN",
                  required=required)


def _check_pillow(executable, python_ready=True):
    purpose = "Pillow >=10；在内存中验证 PNG 与双帧 GIF 编解码"
    if not python_ready:
        return _check("pillow", False, executable, purpose=purpose, detail="Selected Python is blocked")
    data, error = _json_probe([executable, "-I", "-B", "-c", _PILLOW_PROBE])
    ready = data.get("ok") is True and _version_ok(data.get("version"), (10, 0, 0))
    return _check("pillow", ready, executable, data.get("version"), purpose,
                  error or data.get("error") or ("In-memory codecs passed" if ready else "Pillow >=10 required"))


def _font_candidates():
    override = os.environ.get("ARCHITECTURE_DIAGRAM_FONT") or os.environ.get("CJK_FONT")
    if override:
        return [os.path.abspath(os.path.expanduser(override))]
    candidates = [
        Path("/System/Library/Fonts/STHeiti Light.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/System/Library/Fonts/Hiragino Sans GB.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/simsun.ttc",
    ]
    for directory in (SKILL_ROOT / "assets/fonts", Path.home() / "Library/Fonts",
                      Path.home() / ".local/share/fonts", Path.home() / ".fonts"):
        for pattern in ("*.ttf", "*.ttc", "*.otf"):
            candidates.extend(sorted(directory.glob(pattern)))
    return list(dict.fromkeys(str(path.absolute()) for path in candidates if path.is_file()))[:64]


def _check_font(executable, pillow_ready):
    purpose = "Pillow/FreeType 实际加载字体并绘制中文样本；拒绝缺字方框，不保证所有汉字覆盖"
    if not pillow_ready:
        return _check("font", False, purpose=purpose, detail="Cannot test font without usable Pillow >=10")
    candidates = _font_candidates()
    if not candidates:
        return _check("font", False, purpose=purpose, detail="No local CJK font candidates found")
    data, error = _json_probe([executable, "-I", "-B", "-c", _FONT_PROBE], json.dumps(candidates))
    ready = data.get("ok") is True and data.get("path") in candidates
    return _check("font", ready, data.get("path") if ready else None, purpose=purpose,
                  detail=error or data.get("error") or "Chinese sample rendered in memory")


def _browser_manual(required=True):
    return {"id": "browser", "required": required, "status": "manual", "version": None,
            "path": None, "purpose": "浏览器中的产物视觉与交互验收，尚未执行",
            "install_hint": _HINTS["browser"]}


def doctor(backend: str, require_preview: bool = True) -> dict:
    """Check prerequisites; keep actual rendering acceptance separate."""
    _validate(backend, BACKENDS, "backend")
    if not isinstance(require_preview, bool):
        raise ValueError("require_preview must be a bool")
    gif = backend in ("gif", "topology-gif", "all")
    executable = _selected_python(gif)
    python = _check_python(executable)
    checks = [python]
    executables = {"node": None, "python": executable, "drawio": None}
    manual = []
    if backend in ("archify", "drawio", "topology-gif", "all"):
        node = _check_node()
        checks.append(node)
        executables["node"] = node["path"]
    if backend in ("topology-gif", "all"):
        data, error = _json_probe([executables['node'], str(SKILL_ROOT / 'scripts/topology_snapshot.mjs'), '--doctor']) if node['status'] == 'ready' else ({}, 'Node is unavailable')
        checks.append(_check('browser', data.get('ok') is True, data.get('path'), data.get('version'),
                             'Topology GIF requires existing Chrome/Chromium; executable discovery only',
                             error or 'Actual offline snapshot is checked during rendering'))
    if backend in ("drawio", "all"):
        drawio = _check_drawio(required=require_preview)
        checks.append(drawio)
        executables["drawio"] = drawio["path"]
        if require_preview:
            manual.append({"id": "drawio_export", "required": True, "status": "manual",
                           "purpose": "实际 CLI 导出未执行；无显示服务器、沙箱或 Electron 故障需在导出时确认"})
    if gif:
        pillow = _check_pillow(executable, python["status"] == "ready")
        font = _check_font(executable, pillow["status"] == "ready")
        checks.extend((pillow, font))
        executables["font"] = font["path"]
    if backend != "icons":
        manual.append(_browser_manual(require_preview))
    blocked = any(check["required"] and check["status"] == "blocked" for check in checks)
    return {"backend": backend, "require_preview": require_preview,
            "status": "blocked" if blocked else "ready", "checks": checks,
            "executables": executables, "manual": manual}


def _venv_problem():
    venv = SKILL_ROOT / ".venv"
    if venv.is_symlink():
        return "Refusing a symlinked skill .venv; create a real isolated venv manually."
    if venv.exists():
        try:
            cfg = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "Existing .venv has no readable pyvenv.cfg; it will not be overwritten."
        settings = [line.partition("=")[2].strip().lower() for line in cfg.splitlines()
                    if line.partition("=")[0].strip().lower() == "include-system-site-packages"]
        if settings != ["false"]:
            return "Existing .venv is not isolated (include-system-site-packages must be false, exactly once)."
        if not _executable(_venv_python()):
            return "Existing .venv Python is unavailable; repair it manually."
    return None


def install_plan(dependency: str) -> dict:
    """Describe an allowlisted installation without executing it."""
    _validate(dependency, DEPENDENCIES, "dependency")
    plan = {"dependency": dependency, "supported": False, "commands": [],
            "source": None, "impact_paths": [], "requires_confirmation": True,
            "manual_instructions": _HINTS[dependency], "reason": ""}
    if dependency == "pillow":
        problem = _venv_problem()
        if problem or sys.version_info[:2] < (3, 9) or not _executable(sys.executable):
            plan["reason"] = problem or "A working Python >=3.9 with venv/ensurepip is required."
            return plan
        venv = SKILL_ROOT / ".venv"
        if not venv.exists():
            plan["commands"].append([sys.executable, "-I", "-B", "-m", "venv", str(venv)])
        plan["commands"].append([
            str(_venv_python()), "-I", "-B", "-m", "pip", "--isolated", "install",
            "--disable-pip-version-check", "--no-input", "--no-cache-dir",
            "--only-binary=:all:", "--index-url", "https://pypi.org/simple", "--upgrade", "Pillow>=10",
        ])
        plan.update(supported=True, source="Python stdlib venv/ensurepip; PyPI official Pillow wheels only",
                    impact_paths=[str(venv), "OS temporary directory (pip temporary files)"],
                    reason="Isolated skill venv only; no system/user-site pip install, no source builds.")
        return plan

    system = platform.system()
    brew = _which("brew") if system == "Darwin" else None
    if brew:
        formulae = {"node": "node", "python": "python"}
        casks = {"drawio": "drawio", "font": "font-noto-sans-cjk-sc", "browser": "firefox"}
        command = [brew, "install"]
        if dependency in formulae:
            command.append("homebrew/core/" + formulae[dependency])
            source = "Homebrew official core formula: " + formulae[dependency]
        else:
            command.extend(["--cask", "--appdir=" + str(Path.home() / "Applications"),
                            "homebrew/cask/" + casks[dependency]])
            source = "Homebrew official cask: " + casks[dependency]
        plan.update(supported=True, commands=[command], source=source,
                    impact_paths=[str(Path(brew).parent.parent), str(Path.home() / "Applications"),
                                  str(Path.home() / "Library/Fonts"), str(Path.home() / "Library/Caches/Homebrew")],
                    reason="Uses the existing Homebrew; may install package dependencies. No sudo or automatic brew update.")
        return plan

    apt = _which("apt-get") if system == "Linux" else None
    packages = {"node": ["nodejs"], "python": ["python3", "python3-venv"], "font": ["fonts-noto-cjk"]}
    if apt and dependency in packages and hasattr(os, "geteuid") and os.geteuid() == 0:
        plan.update(supported=True, commands=[[apt, "install", "--yes", "--no-install-recommends"] + packages[dependency]],
                    source="Already configured OS APT repositories (no repositories added)",
                    impact_paths=["/usr", "/etc", "/var/lib/dpkg", "/var/cache/apt", "/var/log/apt"],
                    reason="Already-root APT only, no sudo/update. Repository versions may be too old; postcheck decides readiness.")
        return plan
    plan["reason"] = ("No supported existing package manager/privilege for this dependency. "
                      "Install manually using the vendor or OS package manager; this API never installs a package manager or elevates privileges.")
    return plan


def _check_browser_installation():
    # Only executable discovery, not a claim about browser rendering/interaction.
    path = None
    if platform.system() == "Darwin":
        for root in (Path.home() / "Applications", Path("/Applications"), Path("/System/Applications")):
            for entry in ("Firefox.app/Contents/MacOS/firefox", "Google Chrome.app/Contents/MacOS/Google Chrome",
                          "Safari.app/Contents/MacOS/Safari"):
                candidate = root / entry
                if _executable(candidate):
                    path = str(candidate)
                    break
            if path:
                break
    if not path:
        for name in ("firefox", "chromium", "chromium-browser", "google-chrome", "msedge"):
            path = _which(name)
            if path:
                break
    return _check("browser", bool(path), path, purpose="浏览器可执行文件的静态发现；不启动 GUI",
                  detail="Executable found; visual acceptance remains manual" if path else "No browser executable found after installation")


def _postcheck(dependency):
    if dependency == "node":
        return _check_node()
    if dependency == "python":
        return _check_python(_selected_python())
    if dependency == "drawio":
        return _check_drawio()
    if dependency == "browser":
        return _check_browser_installation()
    executable = _selected_python(gif=True)
    python = _check_python(executable)
    pillow = _check_pillow(executable, python["status"] == "ready")
    return pillow if dependency == "pillow" else _check_font(executable, pillow["status"] == "ready")


def _isolation_error():
    problem = _venv_problem()
    if problem:
        return problem
    data, error = _json_probe([str(_venv_python()), "-I", "-B", "-c", _ISOLATION_PROBE])
    expected = str(SKILL_ROOT / ".venv")
    if (error or data.get("prefix") != expected or data.get("prefix") == data.get("base_prefix")
            or data.get("user_site") is not False):
        return error or "Refusing pip: interpreter is not the isolated skill .venv."
    paths = data.get("install_paths")
    if not isinstance(paths, dict) or set(paths) != {"purelib", "platlib", "scripts", "data"}:
        return "Refusing pip: cannot verify its installation paths."
    try:
        for path in paths.values():
            Path(path).resolve().relative_to(Path(expected).resolve())
    except (OSError, RuntimeError, TypeError, ValueError):
        return "Refusing pip: installation paths escape the skill .venv (possibly a symlink)."
    return None


def install(dependency: str, confirmed: bool = False) -> dict:
    """Require literal confirmation, execute the allowlist, then independently recheck."""
    plan = install_plan(dependency)
    result = {"dependency": dependency, "status": "refused", "plan": plan,
              "executed": [], "checks": [], "manual": [],
              "reason": "Explicit confirmed=True is required; nothing was executed."}
    if confirmed is not True:
        return result
    if not plan["supported"]:
        result.update(status="unsupported", reason=plan["reason"])
        return result
    failure = None
    for command in plan["commands"]:
        if dependency == "pillow" and "pip" in command:
            failure = _isolation_error()
            if failure:
                break
        execution = _run(command, installing=True)
        # Limit returned installer logs; retain the actual exit code and argv.
        execution["stdout"] = execution["stdout"][-4000:]
        execution["stderr"] = execution["stderr"][-4000:]
        result["executed"].append(execution)
        if execution["returncode"] != 0:
            failure = execution["stderr"] or "Installer exited unsuccessfully"
            break
    check = _postcheck(dependency)
    result["checks"] = [check]
    if dependency == "browser":
        result["manual"] = [_browser_manual()]
    if failure:
        result.update(status="failed", reason=failure)
    elif dependency == "browser" and check["status"] == "ready":
        result.update(status="manual", reason="Browser executable found; actual visual acceptance has not been performed.")
    else:
        result.update(status=check["status"], reason="Installer completed; status is the independent postcheck, not an installation promise.")
    return result
