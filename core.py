# -*- coding: utf-8 -*-
"""
自动化更新器 —— 核心引擎（无界面依赖，可被 GUI 或命令行复用）

能力：
  * 多根目录扫描：可添加任意多个「检查文件夹」，每个可单独排除其内部子文件夹
  * git 仓库：fetch + 比较落后提交数，收集提交日志作为更新日志，ff-only 合并
  * Release 程序：查 GitHub 最新发布、读取 release notes，下载解压后「净化覆盖」
  * 净化覆盖：保留用户数据目录（config / User / data ...），其余旧文件全部删除再解压新版
  * 网络自适应：本地代理探测 → 直连 → GitHub 镜像加速站竞速
  * 任何覆盖前自动备份，可回滚；绝不改动你原有的文件夹名称
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import glob
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field

APP_NAME = "自动化更新器"          # 显示名称（窗口标题 / 托盘 / 关于）
APP_ID = "AutomateUpdater"        # 内部标识：配置回退目录、单实例名、AppUserModelID
APP_VERSION = "1.6.0"

# 子进程（git / tar / tasklist / powershell）都是控制台程序，必须禁止弹窗
CREATE_NO_WINDOW = 0x08000000
SILENT = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}

# 常见用户数据目录（默认保留名单，可在设置里改；匹配时不区分大小写，无需重复写变体）
DEFAULT_PRESERVE = ["User", "config", "configs", "data", "saves", "save", "logs",
                    "profiles", "3rdparty"]

# 设置页里做成「一键开关」的常见保留项（默认全部开启）
COMMON_PRESERVE = [
    ("User", "User"),
    ("config", "config"),
    ("data", "data"),
    ("saves", "saves"),
    ("logs", "logs"),
    ("profiles", "profiles"),
    ("3rdparty", "3rdparty"),
]

DEFAULT_MIRRORS = [
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/",
    "https://github.moeyy.xyz/",
    "https://ghproxy.cc/",
    "https://gh.llkk.cc/",
    "https://gh-proxy.net/",
    "https://hub.whtrys.space/",
    "https://gitproxy.click/",
    "https://gh.api.99988866.xyz/",
]
DEFAULT_PROXY_PORTS = [7890, 7891, 1080, 10809, 10808, 8080, 2080, 33210, 7897, 10801]

# 程序 → GitHub 仓库的预置映射（按文件夹名匹配，跨根目录通用）
BUILTIN_PROGRAMS = [
    {"name": "M9A", "match": "M9A", "repo": "MAA1999/M9A",
     "asset_regex": r"M9A-win-x86_64-.*\.zip$", "anchor": "interface.json",
     "version": {"type": "json", "file": "interface.json", "key": "version"}},
    {"name": "MaaEnd", "match": "MaaEnd", "repo": "MaaEnd/MaaEnd",
     "asset_regex": r"MaaEnd-win-x86_64-.*\.zip$", "anchor": "interface.json",
     "version": {"type": "json", "file": "interface.json", "key": "version"}},
    {"name": "Maa_bbb", "match": "Maa_bbb", "repo": "miaojiuqing/Maa_bbb",
     "asset_regex": r"Maa_bbb-win-x86_64-.*\.zip$", "anchor": "interface.json",
     "version": {"type": "json", "file": "interface.json", "key": "version"}},
    {"name": "MAA", "match": "MAA", "repo": "MaaAssistantArknights/MaaAssistantArknights",
     "asset_regex": r"MAA-v.*-win-x64\.zip$", "anchor": "MAA.exe",
     "version": {"type": "exe", "file": "MAA.exe"}},
    {"name": "BetterGI", "match": "BetterGI", "repo": "babalae/better-genshin-impact",
     "asset_regex": r"BetterGI_v.*\.(7z|zip)$", "anchor": "BetterGI.exe",
     "version": {"type": "exe", "file": "BetterGI.exe"}},
    {"name": "March7thAssistant", "match": "March7thAssistant", "repo": "moesnow/March7thAssistant",
     "asset_regex": r"March7thAssistant_full\.7z$", "anchor": "March7th Assistant.exe",
     "version": {"type": "regex", "file": "assets/config/version.txt", "regex": r"[0-9]+(?:\.[0-9]+)+"}},
    {"name": "OneDragon ScriptChainer", "match": "OneDragon ScriptChainer",
     "repo": "OneDragon-Anything/OneDragon-ScriptChainer",
     "asset_regex": r"OneDragon-ScriptChainer-v.*\.zip$", "anchor": "OneDragon ScriptChainer.exe",
     "version": {"type": "record"}},
]

# 嗅探仓库时应当忽略的常见无关链接
_REPO_NOISE = re.compile(r"(MaaFramework|MaaAssistantArknights/MaaFramework|shields\.io|/tree/|/blob/|"
                         r"github\.com/features|github\.com/about|nodejs|python)", re.I)


# ==================================================================== 配置

def config_dir() -> str:
    """配置/缓存目录：程序所在目录可写就用旁边，否则用 %APPDATA%。"""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    # onefile 打包时 sys.executable 是 exe 本身，取它的目录
    for cand in (base, os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "AutomateUpdater")):
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".write_test")
            with open(probe, "w") as f:
                f.write("1")
            os.remove(probe)
            return cand
        except Exception:
            continue
    return os.path.expanduser("~")


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


def default_config() -> dict:
    return {
        "version": 3,
        "roots": [{"path": "D:\\Automate", "exclude": ["My-maawork", "Cache", "_updater", "_backup", ".git"],
                   "depth": 4, "enabled": True}],
        "preserve": list(DEFAULT_PRESERVE),
        "preserve_map": {},                    # 程序名(小写) → {"custom": True, "list": [...]}
        "proxy_ports": list(DEFAULT_PROXY_PORTS),
        "proxy": {"mode": "auto", "url": ""},   # auto 自动探测 | manual 手动指定 | off 不用
        "manual_versions": {},                  # 程序名(小写) → 手动标记的本地版本
        "mirrors": {
            "enabled": True,                 # 默认开启镜像加速
            "list": list(DEFAULT_MIRRORS),
            "seeded": len(DEFAULT_MIRRORS),  # 已注入的内置镜像数量（用于版本升级时自动补充新增项）
            "daily_check": True,             # 每天自动测速一次
            "last_check": "",
            "chosen": "",
        },
        "update": {"timeout_min": 10, "install_mode": "clean"},   # clean 保留指定文件夹 | overwrite 直接覆盖
        "background": {
            "close_to_tray": True,           # 关闭窗口时最小化到托盘
            "run_at_startup": False,         # 开机自启（后台静默）
            "check_enabled": True,           # 定时检查
            "interval_min": 60,              # 检查间隔（分钟）
            "auto_mode": "off",              # off 只通知 | silent 静默更新 | notify 静默更新后提示
            "notify_methods": ["tray"],      # tray 系统通知 | dingtalk 钉钉 | window 弹出主界面
        },
        "backup": {"enabled": True, "keep": 2},
        "dingtalk": {"enabled": False, "webhook": "", "secret": ""},
        "ui": {"theme": "auto", "preset": "acrylic", "blur": True,
               "sort": "name", "prior": "update"},   # sort=基础排序, prior=特殊优先规则
        "programs": [],
        "last_scan": "",
    }


def _normalize(cfg: dict) -> dict:
    """兼容旧版配置：mirrors 曾是纯数组，update 段可能缺失。"""
    m = cfg.get("mirrors")
    base = default_config()["mirrors"]
    if isinstance(m, list):
        base.update({"list": [x for x in m if isinstance(x, str) and x], "seeded": 0})
        cfg["mirrors"] = base
    elif isinstance(m, dict):
        base.update({k: v for k, v in m.items() if k in base})
        if not isinstance(base.get("list"), list):
            base["list"] = list(DEFAULT_MIRRORS)
        cfg["mirrors"] = base
    else:
        cfg["mirrors"] = base
    # 版本升级后自动补充新增的内置镜像（删掉的旧镜像不会被加回来）
    mm = cfg["mirrors"]
    try:
        seeded = int(mm.get("seeded") or 0)
    except Exception:
        seeded = 0
    if seeded < len(DEFAULT_MIRRORS):
        have = {x.rstrip("/") for x in mm["list"] if isinstance(x, str)}
        for url in DEFAULT_MIRRORS[seeded:]:
            if url.rstrip("/") not in have:
                mm["list"].append(url)
        mm["seeded"] = len(DEFAULT_MIRRORS)
    if not isinstance(cfg.get("update"), dict):
        cfg["update"] = {"timeout_min": 10, "install_mode": "clean"}
    if cfg["update"].get("install_mode") not in ("clean", "overwrite"):
        cfg["update"]["install_mode"] = "clean"
    try:
        cfg["update"]["timeout_min"] = max(1, int(cfg["update"].get("timeout_min") or 10))
    except Exception:
        cfg["update"]["timeout_min"] = 10
    if not isinstance(cfg.get("preserve"), list):
        cfg["preserve"] = list(DEFAULT_PRESERVE)
    seen, cleaned = set(), []
    for name in cfg["preserve"]:
        name = str(name).strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            cleaned.append(name)
    cfg["preserve"] = cleaned
    # 单个程序的独立保留列表
    pm = cfg.get("preserve_map")
    clean_pm = {}
    if isinstance(pm, dict):
        for k, v in pm.items():
            key = str(k or "").lower()
            if not key:
                continue
            if isinstance(v, dict):
                lst = [str(x) for x in (v.get("list") or []) if str(x).strip()]
                clean_pm[key] = {"custom": bool(v.get("custom", True)), "list": lst}
            elif isinstance(v, list):
                clean_pm[key] = {"custom": True, "list": [str(x) for x in v if str(x).strip()]}
    cfg["preserve_map"] = clean_pm
    pc = cfg.get("proxy")
    base_pc = default_config()["proxy"]
    if isinstance(pc, dict):
        base_pc.update({k: v for k, v in pc.items() if k in base_pc})
    cfg["proxy"] = base_pc
    if base_pc.get("mode") not in ("auto", "manual", "off"):
        base_pc["mode"] = "auto"
    base_pc["url"] = str(base_pc.get("url") or "").strip()
    if not isinstance(cfg.get("manual_versions"), dict):
        cfg["manual_versions"] = {}
    else:
        cfg["manual_versions"] = {str(k).lower(): str(v) for k, v in cfg["manual_versions"].items()
                                  if str(v).strip()}
    if not isinstance(cfg.get("background"), dict):
        cfg["background"] = default_config()["background"]
    else:
        bg = default_config()["background"]
        bg.update({k: v for k, v in cfg["background"].items() if k in bg})
        cfg["background"] = bg
    try:
        cfg["background"]["interval_min"] = max(5, int(cfg["background"].get("interval_min") or 60))
    except Exception:
        cfg["background"]["interval_min"] = 60
    if cfg["background"].get("auto_mode") not in ("off", "silent", "notify"):
        cfg["background"]["auto_mode"] = "off"
    if not isinstance(cfg["background"].get("notify_methods"), list):
        cfg["background"]["notify_methods"] = ["tray"]

    # 界面设置：补齐字段 + 二级排序的旧值迁移
    base_ui = default_config()["ui"]
    if not isinstance(cfg.get("ui"), dict):
        cfg["ui"] = base_ui
    else:
        merged = dict(base_ui)
        merged.update({k: v for k, v in cfg["ui"].items()
                       if k in base_ui or k in ("geometry", "settings_tab")})
        cfg["ui"] = merged
    # 旧版 ui.sort 是「单一排序」（update/git/release 其实是优先规则）
    s = cfg["ui"].get("sort")
    if s in ("update", "git", "release"):
        cfg["ui"]["prior"] = s
        cfg["ui"]["sort"] = "name"
    elif s not in ("name", "name_desc", "size_desc", "size_asc", "recent", "old",
                   "updated_desc", "updated_asc"):
        cfg["ui"]["sort"] = "name"
    if cfg["ui"].get("prior") not in ("update", "none", "git", "release"):
        cfg["ui"]["prior"] = "update"
    # 规范化检查目录路径（避免 D://Automate 这类写法导致显示与去重异常）
    for r in cfg.get("roots") or []:
        if isinstance(r, dict) and r.get("path"):
            try:
                r["path"] = os.path.normpath(str(r["path"]))
            except Exception:
                pass
    return cfg


def mirror_list(cfg: dict) -> list:
    """返回当前生效的镜像列表（尊重总开关）。"""
    m = cfg.get("mirrors") or {}
    if isinstance(m, list):
        return [x for x in m if x]
    if not m.get("enabled", True):
        return []
    out = [x for x in (m.get("list") or []) if isinstance(x, str) and x.strip()]
    return out


def normalize_mirror(url: str) -> str:
    """把用户输入的代理地址整理成「https://主机/路径/」；不像网址就返回空串。

    下载时按「镜像 + 原始地址」拼接，所以允许带路径前缀（如 https://x.com/gh/）。
    """
    u = str(url or "").strip().replace(" ", "")
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u
    m = re.match(r"^(https?)://([^/]+)(/.*)?$", u, re.I)
    if not m:
        return ""
    scheme, host, path = m.group(1).lower(), m.group(2), m.group(3) or "/"
    if "." not in host and ":" not in host:
        return ""
    if re.fullmatch(r"(www\.)?github\.com", host, re.I):
        return ""                       # 官方地址不是镜像，加了只会绕圈
    if not path.endswith("/"):
        path += "/"
    return f"{scheme}://{host}{path}"


def add_mirror(cfg: dict, url: str) -> str:
    """把一个代理地址加进候选列表；已存在就返回原样，非法输入返回空串。"""
    u = normalize_mirror(url)
    if not u:
        return ""
    m = cfg.setdefault("mirrors", {})
    lst = [x for x in (m.get("list") or []) if isinstance(x, str)]
    if u.rstrip("/") not in {x.rstrip("/") for x in lst}:
        lst.append(u)
    m["list"] = lst
    return u


def remove_mirror(cfg: dict, url: str) -> bool:
    """从候选列表删掉一个地址（内置的也可以删，下次升级不会自动补回）。"""
    m = cfg.setdefault("mirrors", {})
    lst = [x for x in (m.get("list") or []) if isinstance(x, str)]
    key = (normalize_mirror(url) or str(url or "").strip()).rstrip("/")
    left = [x for x in lst if x.rstrip("/") != key]
    if len(left) == len(lst):
        return False
    m["list"] = left
    # 删掉的地址不希望被「升级补充内置镜像」的逻辑加回来
    m["seeded"] = len(DEFAULT_MIRRORS)
    if str(m.get("chosen") or "").rstrip("/") == key:
        m["chosen"] = ""
        m["last_check"] = ""
    return True


def set_mirrors(cfg: dict, urls) -> list:
    """整体替换候选列表（设置页对话框保存时用），返回清洗后的列表。"""
    out, seen = [], set()
    for u in urls or []:
        n = normalize_mirror(u)
        if n and n.rstrip("/") not in seen:
            seen.add(n.rstrip("/"))
            out.append(n)
    m = cfg.setdefault("mirrors", {})
    m["list"] = out
    m["seeded"] = len(DEFAULT_MIRRORS)
    if (m.get("chosen") or "").rstrip("/") not in {x.rstrip("/") for x in out}:
        m["chosen"] = ""
        m["last_check"] = ""
    return out


def reset_mirrors(cfg: dict) -> list:
    """恢复成内置镜像列表。"""
    m = cfg.setdefault("mirrors", {})
    m["list"] = list(DEFAULT_MIRRORS)
    m["seeded"] = len(DEFAULT_MIRRORS)
    m["chosen"] = ""
    m["last_check"] = ""
    return m["list"]


def load_config() -> dict:
    cfg = default_config()
    p = config_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                disk = json.load(f)
            for k, v in disk.items():
                if k in ("backup", "dingtalk", "ui", "mirrors", "update") and isinstance(v, dict):
                    cfg.setdefault(k, {})
                    if isinstance(cfg[k], dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
                elif k in ("roots", "preserve", "proxy_ports", "programs") and isinstance(v, list):
                    cfg[k] = v
                else:
                    cfg[k] = v
        except Exception:
            pass
    # 首次运行迁移旧版 CLI 配置
    if not os.path.exists(p):
        legacy = r"D:\Automate\_updater\config.json"
        if os.path.exists(legacy):
            try:
                with open(legacy, encoding="utf-8") as f:
                    old = json.load(f)
                if old.get("dingtalk"):
                    cfg["dingtalk"].update({"webhook": old["dingtalk"].get("webhook", ""),
                                            "secret": old["dingtalk"].get("secret", "")})
                if old.get("root"):
                    cfg["roots"] = [{"path": old["root"].replace("/", "\\"),
                                     "exclude": ["My-maawork", "Cache", "_updater", "_backup", ".git"],
                                     "depth": int(old.get("git_scan_max_depth", 4)), "enabled": True}]
            except Exception:
                pass
    return _normalize(cfg)


def save_config(cfg: dict) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    tmp = config_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config_path())


# ==================================================================== 网络

class UpdateTimeout(Exception):
    """单个程序更新超过设定时限。"""


def detect_proxy(ports) -> str | None:
    for p in ports or []:
        # 1080/1081 是常见的 SOCKS 端口，ProxyHandler 根本用不了，别误判成 HTTP 代理
        if int(p) in (1080, 1081):
            continue
        s = socket.socket()
        s.settimeout(0.2)
        try:
            if s.connect_ex(("127.0.0.1", int(p))) == 0:
                return f"http://127.0.0.1:{p}"
        except Exception:
            pass
        finally:
            s.close()
    return None


def mask_url(url: str | None) -> str:
    """隐藏 URL 里的凭证（user:pass@），只保留协议+主机+端口，供日志/界面显示。

    代理地址可能是 ``http://user:pass@host:port`` 形式，原样展示会把密码暴露在
    Toast、侧栏和 crash.log 里；这里统一脱敏成 ``http://***@host:port``。
    """
    if not url:
        return ""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return url
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    cred = "***@" if (p.username or p.password) else ""
    return f"{p.scheme}://{cred}{host}" if p.scheme else f"{cred}{host}"


def http_get(url: str, proxy: str | None = None, timeout: int = 25, headers: dict | None = None):
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AutomateUpdater"}
    if headers:
        hdr.update(headers)
    return opener.open(urllib.request.Request(url, headers=hdr), timeout=timeout)


def _probe(session_url: str) -> float | None:
    t0 = time.time()
    try:
        with http_get(session_url, timeout=5) as r:
            # 读 64KB 才是「测速」；只读 128 字节测的其实是首字节延迟
            r.read(65536)
        return time.time() - t0
    except Exception:
        return None


def probe_mirrors(mirrors, wait: float = 12) -> list:
    """并行测速所有镜像，返回 [(毫秒, 镜像)]，按速度升序；失败的镜像被丢弃。"""
    mirrors = [m for m in (mirrors or []) if m]
    if not mirrors:
        return []
    tail = "https://raw.githubusercontent.com/git/git/master/README.md"
    out = []
    with ThreadPoolExecutor(max_workers=min(10, len(mirrors))) as ex:
        futs = {ex.submit(_probe, m.rstrip("/") + "/" + tail): m for m in mirrors}
        try:
            for fut in as_completed(futs, timeout=wait):
                try:
                    dt = fut.result()
                except Exception:
                    continue
                if dt is not None:
                    out.append((int(dt * 1000), futs[fut]))
        except Exception:
            pass
    out.sort(key=lambda x: x[0])
    return out




def http_json(url: str, proxy: str | None, mirrors=None, retries: int = 1,
              deadline: float = 0):
    """请求 JSON：代理 → 最快镜像 → 直连 → 其余镜像，带总时限。

    以前是「直连 + 12 个镜像 × 20s × 2 次重试」串行回退，最坏要 8 分钟，
    而且完全没有用上测速结果。现在 mirrors 已由 Engine.mirrors() 把 chosen
    排到首位，并对整条回退链加一个总 deadline。
    """
    ms = [m for m in (mirrors or []) if m]
    preferred = ms[0] if ms else None
    # (proxy, url, 是否官方直连)
    cands = []
    if proxy:
        cands.append((proxy, url, False))
    if preferred:
        cands.append((None, preferred.rstrip("/") + "/" + url, False))
    cands.append((None, url, True))          # 官方直连
    for m in (ms[1:] if preferred else []):
        cands.append((None, m.rstrip("/") + "/" + url, False))
    for px, u, official in cands:
        if deadline and time.time() > deadline:
            return None
        for _ in range(max(1, retries)):
            try:
                with http_get(u, proxy=px, timeout=10,
                              headers={"Accept": "application/vnd.github+json"}) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                # 只有官方源的 404 才算「资源不存在」；镜像/代理 404 说明它不支持该路径，
                # 应继续回退到下一个候选，否则一个坏镜像会挡住官方直连
                if e.code == 404 and official:
                    return None
            except Exception:
                pass
    return None


def download(url: str, dest: str, proxy, mirrors, progress_cb=None, cancel=None,
             deadline: float = 0, expect_size: int = 0, sha256: str = "") -> bool:
    """下载到 dest，支持断点续传，支持 sha256 内容校验。

    实际写入 dest.part：下载中断时保留已传字节，下一次（或换下一个候选源）
    带 Range 头从断点继续；服务器不支持 Range（返回非 206）时自动从头重下。
    只有所有候选源都失败才返回 False，.part 仍保留待续传。

    内容可信性：expect_size 只能证明「大小完整」，不能证明「内容可信」。有 sha256
    时下载后必须校验哈希；校验失败会删除 .part 并失败（防止恶意镜像用同尺寸包替换）。
    """
    part = dest + ".part"
    # 续传缓存必须绑定 URL：同资产名跨版本/跨源会复用同一 .part，换源后内容可能 A/B 拼接。
    # 用 URL 哈希做缓存键，源变了就从头下，避免拼接出错误文件。
    url_key = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:16]
    part = dest + "." + url_key + ".part"
    # 只允许 https 的镜像（明文 http 的镜像/代理会暴露内容，且无法信任）
    cands = []
    if proxy:
        cands.append((proxy, url))
    cands.append((None, url))
    for m in mirrors or []:
        if str(m).lower().startswith("https://"):
            cands.append((None, m.rstrip("/") + "/" + url))
    for px, u in cands:
        if deadline and time.time() > deadline:
            return False
        try:
            have = os.path.getsize(part) if os.path.exists(part) else 0
            # 已完成的 .part：完整大小与预期一致时直接校验收尾，别再发 Range 触发 416
            if have > 0 and expect_size and have == expect_size:
                if _verify_digest(part, sha256):
                    shutil.move(part, dest)
                    return True
                os.remove(part)
                have = 0
            headers = {"Range": f"bytes={have}-"} if have > 0 else None
            with http_get(u, proxy=px, timeout=180, headers=headers) as r:
                code = int(getattr(r, "status", 0) or 200)
                if have > 0 and code == 416:
                    # 服务器认为断点越界：可能文件已完成。若大小/哈希匹配则收尾，否则重下。
                    os.remove(part)
                    have = 0
                    continue
                if have > 0 and code != 206:
                    # 服务器不支持续传：丢弃旧数据从头下
                    have = 0
                # 校验 Content-Range 起点：换源后若服务器从非 have 处返回，会拼出错误文件
                cr = r.headers.get("Content-Range") or ""
                if have > 0 and code == 206 and cr:
                    m = re.match(r"bytes\s+(\d+)-", cr)
                    if m and int(m.group(1)) != have:
                        os.remove(part)
                        have = 0
                        continue
                cl = int(r.headers.get("Content-Length") or 0)
                total = have + cl if code == 206 else cl
                done = have
                mode = "ab" if have > 0 else "wb"
                with open(part, mode) as f:
                    while True:
                        if cancel is not None and cancel.is_set():
                            return False          # 保留 .part，下次继续
                        if deadline and time.time() > deadline:
                            raise UpdateTimeout("下载超时")
                        chunk = r.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total:
                            progress_cb(min(1.0, done / total))
                if total and done < total:
                    # 连接提前中断：抛错换下一个候选源（.part 保留，下次续传）
                    raise IOError(f"连接中断于 {done}/{total}")
            got = os.path.getsize(part) if os.path.exists(part) else 0
            if expect_size and got != expect_size:
                # GitHub API 给了 asset.size，用来挡住「无 Content-Length 时断连也当成功」
                os.remove(part)
                raise IOError(f"文件大小不符：{got}/{expect_size}")
            if not _verify_digest(part, sha256):
                os.remove(part)
                raise IOError("文件 sha256 校验失败（内容可能被镜像篡改）")
            # 小文件（如 <1KB 的合法 zip）也应能成功：以哈希/大小为准，不再要求 >1024
            shutil.move(part, dest)
            return True
        except UpdateTimeout:
            return False
        except Exception:
            continue
    return False


def _verify_digest(path: str, sha256: str) -> bool:
    """校验文件 sha256；未提供摘要时视为通过（大小校验由调用方负责）。"""
    if not sha256:
        return True
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(256 * 1024), b""):
                h.update(chunk)
        return h.hexdigest().lower() == sha256.lower()
    except Exception:
        return False


# ==================================================================== 版本工具

def _ver_parts(s: str):
    """解析版本为 (数字元组, 预发布标识或 None)。解析失败返回 (None, None)。"""
    s = (s or "").strip().lstrip("vV")
    m = re.match(r"^(\d+(?:\.\d+)*)(?:[-_.]?([A-Za-z0-9][A-Za-z0-9_.-]*))?$", s)
    if not m:
        return None, None
    try:
        nums = tuple(int(x) for x in m.group(1).split("."))
    except Exception:
        return None, None
    pre = m.group(2) or None
    return nums, pre


def ver_gt(a: str, b: str) -> bool:
    """比较 a 是否比 b 新。完整 SemVer 语义，解析失败返回 False（不把未知格式当最新）。"""
    na, pa = _ver_parts(a)
    nb, pb = _ver_parts(b)
    if na is None and nb is None:
        return False
    if na is None:        # a 不可解析（如 nightly）→ 不算更新
        return False
    if nb is None:        # 本地不可解析，远端可解析 → 视为有更新
        return True
    # 数字段逐段比较（不截断）
    n = max(len(na), len(nb))
    ta = na + (0,) * (n - len(na))
    tb = nb + (0,) * (n - len(nb))
    if ta != tb:
        return ta > tb
    # 数字段相同：预发布版本低于正式版；同为预发布时按字典序比较
    if pa is None and pb is None:
        return False
    if pa is None and pb is not None:
        return True          # a 是正式版，b 是预发布 → a 更新
    if pa is not None and pb is None:
        return False
    return pa > pb




def short(s: str, n: int = 160) -> str:
    s = (s or "").replace("\r", "").strip()
    s = re.sub(r"\s*\n\s*", " ", s)
    return s if len(s) <= n else s[: n - 1] + "…"


# ==================================================================== 数据模型

def dir_size(path: str, max_files: int = 30000) -> int:
    """统计程序目录体积（跳过 .git，避免把仓库历史算进去）。"""
    total = 0
    n = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__")]
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except Exception:
                    pass
                n += 1
                if n >= max_files:
                    return total
    except Exception:
        pass
    return total




@dataclass
class Item:
    key: str
    name: str
    kind: str                     # git | release
    path: str
    repo: str = ""
    status: str = "unknown"       # update | latest | error | skip | unbound | running
    local: str = ""
    remote: str = ""
    detail: str = ""
    notes: str = ""                # 更新日志正文（release notes 或 commit 列表）
    notes_url: str = ""
    published: str = ""
    local_size: int = 0            # 本地目录体积（排序用）
    local_manual: bool = False           # 版本号是否由用户手动标记
    asset: str = ""
    asset_size: int = 0
    asset_digest: str = ""          # GitHub 提供的 sha256（若有），用于下载后校验内容可信性
    asset_regex: str = ""
    download_url: str = ""
    anchor: str = ""
    preserve: list = field(default_factory=list)
    version_spec: dict = field(default_factory=dict)
    state: str = "idle"            # idle | queued | running | done | failed
    scanned: bool = False          # 是否已完成本次检查
    updated_at: str = ""           # 上次通过本程序更新成功的时间（排序用）
    progress: float = 0.0
    message: str = ""
    error: str = ""
    archived: str = ""            # 备份路径

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ==================================================================== 扫描

class Engine:
    """扫描 / 更新引擎。所有耗时操作都可取消，并通过回调上报进度。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.proxy = self.resolve_proxy()
        self.mirror = None
        self._mirror_lock = threading.Lock()
        self.cancel = threading.Event()
        self._target_locks: dict[str, threading.Lock] = {}
        self._target_locks_guard = threading.Lock()
        self.records = self._load_records()

    def _lock_for(self, path: str) -> threading.Lock:
        """按规范化目标路径取锁，更新与撤销对同一目录互斥，防止并发删除/复制出混合版本。"""
        key = os.path.normcase(os.path.realpath(path))
        with self._target_locks_guard:
            return self._target_locks.setdefault(key, threading.Lock())

    def _record_key(self, it) -> str:
        """记录键：显示名 + 路径哈希，避免不同根目录下的同名程序互相污染 records。"""
        ph = hashlib.sha256(os.path.normcase(os.path.realpath(it.path)).encode("utf-8")).hexdigest()[:10]
        return f"{it.name}@{ph}"

    # ---------- 代理 ----------
    def resolve_proxy(self) -> str | None:
        """按设置返回代理地址：自动探测 / 手动填写 / 不使用。"""
        pc = self.cfg.get("proxy") or {}
        if not isinstance(pc, dict):
            pc = {}
        mode = pc.get("mode", "auto")
        if mode == "off":
            return None
        if mode == "manual":
            url = (pc.get("url") or "").strip()
            if not url:
                return None
            if "://" not in url:
                url = "http://" + url
            return url
        return detect_proxy(self.cfg.get("proxy_ports", []))

    def refresh_proxy(self) -> str | None:
        """设置页改了代理配置后重新计算。"""
        self.proxy = self.resolve_proxy()
        return self.proxy

    # ---------- 手动标记的本地版本 ----------
    def manual_version(self, name: str) -> str:
        mv = self.cfg.get("manual_versions") or {}
        return str(mv.get((name or "").lower(), "") or "")

    def set_manual_version(self, name: str, ver: str) -> None:
        mv = self.cfg.setdefault("manual_versions", {})
        key = (name or "").lower()
        if not key:
            return
        if str(ver or "").strip():
            mv[key] = str(ver).strip()
        else:
            mv.pop(key, None)
        try:
            save_config(self.cfg)
        except Exception:
            pass

    # ---------- 记录 ----------
    @property
    def records_path(self) -> str:
        return os.path.join(config_dir(), "records.json")

    def _load_records(self) -> dict:
        out = {"versions": {}, "update_times": {}, "last_scan": ""}
        try:
            with open(self.records_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out.update(data)
        except Exception:
            # 解析失败可能是上次非原子写留下半截文件；保留副本便于排查，不再静默当空
            try:
                if os.path.exists(self.records_path):
                    shutil.copyfile(self.records_path, self.records_path + ".corrupt")
            except Exception:
                pass
        # 旧文件可能缺键，后面都是直接 self.records["versions"][name] 索引
        for k, v in (("versions", {}), ("update_times", {}), ("last_scan", "")):
            if not isinstance(out.get(k), type(v)):
                out[k] = v
        return out

    def save_records(self, update_last_scan: bool = True) -> None:
        # 原子写：先写临时文件再替换，避免崩溃/磁盘满留下空文件或混合 JSON
        if update_last_scan:
            self.records["last_scan"] = time.strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs(config_dir(), exist_ok=True)
        tmp = self.records_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.records, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.records_path)

    # ---------- 镜像 ----------
    def mirrors(self) -> list:
        """当前生效的镜像列表；把测速选出的最快镜像排到首位，让下载/查询真用上。"""
        out = mirror_list(self.cfg)
        chosen = str((self.cfg.get("mirrors") or {}).get("chosen") or "").strip()
        if chosen and chosen in out:
            out = [chosen] + [m for m in out if m != chosen]
        return out

    def ensure_mirror(self, force: bool = False) -> str | None:
        """返回可用镜像：优先用上次测速记录的最快项，并按需每天重新测速一次。"""
        with self._mirror_lock:
            if self.mirror is not None and not force:
                # 缓存值要连日期一起看：长期驻留进程跨天后也要重新测速，不能永远复用旧结果
                mcfg = self.cfg.setdefault("mirrors", {})
                today = time.strftime("%Y-%m-%d")
                last = str(mcfg.get("last_check") or "")[:10]
                if mcfg.get("daily_check", True) and last != today:
                    self.mirror = None
                else:
                    return self.mirror or None
            cands = self.mirrors()
            if not cands:
                self.mirror = ""
                return None
            mcfg = self.cfg.setdefault("mirrors", {})
            chosen = (mcfg.get("chosen") or "").strip()
            today = time.strftime("%Y-%m-%d")
            fresh = (mcfg.get("last_check") or "")[:10] == today
            if chosen and chosen in cands and fresh and not force:
                self.mirror = chosen
                return chosen
            if not mcfg.get("daily_check", True) and chosen in cands and not force:
                self.mirror = chosen
                return chosen
            ranked = probe_mirrors(cands)
            best = ranked[0][1] if ranked else (chosen if chosen in cands else "")
            if best and ranked:
                mcfg["chosen"] = best
                mcfg["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    save_config(self.cfg)
                except Exception:
                    pass
            self.mirror = best or ""
            return self.mirror or None

    def measure_mirrors(self) -> list:
        """测速全部镜像（设置页「立即测速」用），返回 [(毫秒, 镜像)] 并落盘。"""
        ranked = probe_mirrors(self.mirrors())
        mcfg = self.cfg.setdefault("mirrors", {})
        if ranked:
            mcfg["chosen"] = ranked[0][1]
            self.mirror = ranked[0][1]
        else:
            self.mirror = ""
        mcfg["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            save_config(self.cfg)
        except Exception:
            pass
        return ranked

    # ---------- 程序映射 ----------
    def program_map(self) -> dict:
        """文件夹名(小写) → 程序定义。预置 + 用户在设置里自定义/绑定的。"""
        m = {}
        for p in BUILTIN_PROGRAMS:
            m[p["match"].lower()] = dict(p)
        for p in self.cfg.get("programs", []):
            key = (p.get("match") or p.get("name") or "").lower()
            if not key:
                continue
            base = dict(m.get(key, {}))
            base.update(p)
            base.setdefault("preserve", [])
            m[key] = base
        return m

    # ---------- 收集待扫描对象 ----------
    def collect(self, progress=None) -> list[Item]:
        items: list[Item] = []
        prog = self.program_map()
        for root in self.cfg.get("roots", []):
            if not root.get("enabled", True):
                continue
            base = root.get("path", "")
            if not base or not os.path.isdir(base):
                continue
            excl = {e.lower() for e in root.get("exclude", [])}
            depth = int(root.get("depth", 4))
            # 1) git 仓库
            for repo in find_git_repos(base, excl, depth):
                rel = os.path.relpath(repo, base)
                items.append(Item(key="git:" + repo, name=os.path.basename(repo), kind="git",
                                  path=repo, detail=f"{os.path.basename(base)}\\{rel}"))
            # 2) Release 候选（根目录下一层的程序文件夹）
            for cand in find_program_dirs(base, excl):
                if os.path.isdir(os.path.join(cand, ".git")):
                    continue
                name = os.path.basename(cand)
                spec = prog.get(name.lower())
                if not spec:
                    spec = self.sniff_program(cand)
                it = Item(key="release:" + cand, name=name, kind="release", path=cand,
                          repo=(spec or {}).get("repo") or "",
                          anchor=(spec or {}).get("anchor") or guess_anchor(cand),
                          preserve=self.preserve_for(name, spec or {}),
                          version_spec=(spec or {}).get("version") or guess_version_spec(cand),
                          asset_regex=(spec or {}).get("asset_regex") or "")
                it.repo = (spec or {}).get("repo") or ""
                if not it.repo:
                    it.repo = sniff_repo(cand)
                if not it.repo:
                    it.status = "unbound"
                    it.detail = "未识别到 GitHub 仓库，可在「程序」里手动绑定"
                items.append(it)
        return items

    def preserve_for(self, name: str, spec: dict) -> list:
        """某程序更新时要保留的文件夹名：单程序自定义优先，否则用全局列表。"""
        custom = (self.cfg.get("preserve_map") or {}).get((name or "").lower())
        if custom and custom.get("custom"):
            base = list(custom.get("list") or [])
        else:
            base = list(self.cfg.get("preserve", DEFAULT_PRESERVE))
        for extra in (spec.get("preserve") or []):
            if extra not in base:
                base.append(extra)
        return base

    def set_preserve_map(self, name: str, names: list | None) -> None:
        """写入单程序的保留列表；names 为 None 表示改回跟随全局。"""
        pm = self.cfg.setdefault("preserve_map", {})
        key = (name or "").lower()
        if not key:
            return
        if names is None:
            pm.pop(key, None)
        else:
            pm[key] = {"custom": True, "list": [str(x) for x in names if str(x).strip()]}
        try:
            save_config(self.cfg)
        except Exception:
            pass

    # ---------- 排除文件夹 ----------
    def owner_root(self, path: str) -> dict | None:
        """找出某个路径属于哪个检查根目录（最长前缀匹配）。"""
        best, best_len = None, -1
        target = os.path.normpath(path).lower()
        for root in self.cfg.get("roots", []):
            rp = os.path.normpath(root.get("path", "") or "").lower()
            if rp and (target == rp or target.startswith(rp.rstrip("\\") + "\\")) and len(rp) > best_len:
                best, best_len = root, len(rp)
        return best

    def exclude_entry(self, path: str) -> str:
        """把路径转成排除项：根目录下的直接子文件夹用名字，更深的用相对路径。"""
        root = self.owner_root(path)
        base = (root or {}).get("path", "")
        if base:
            try:
                rel = os.path.relpath(path, base)
                if rel and not rel.startswith(".."):
                    return rel.replace("/", "\\")
            except Exception:
                pass
        return os.path.basename(path.rstrip("\\/"))

    def exclude_path(self, path: str) -> tuple[bool, str]:
        """把一个文件夹加入所属根目录的排除列表并保存配置。"""
        root = self.owner_root(path)
        if not root:
            return False, "该文件夹不在任何检查目录内"
        entry = self.exclude_entry(path)
        excl = root.setdefault("exclude", [])
        low = {e.lower() for e in excl}
        if entry.lower() in low or os.path.basename(entry).lower() in low:
            return False, f"「{entry}」已经在排除列表里了"
        excl.append(entry)
        try:
            save_config(self.cfg)
        except Exception:
            pass
        return True, entry

    def include_path(self, path: str) -> bool:
        """把文件夹从排除列表移除。"""
        root = self.owner_root(path)
        if not root:
            return False
        entry = self.exclude_entry(path).lower()
        name = os.path.basename(entry)
        before = len(root.get("exclude", []))
        root["exclude"] = [e for e in root.get("exclude", [])
                           if e.lower() not in (entry, name)]
        if len(root["exclude"]) != before:
            try:
                save_config(self.cfg)
            except Exception:
                pass
            return True
        return False

    def sniff_program(self, path: str) -> dict:
        repo = sniff_repo(path)
        if not repo:
            return {}
        return {"name": os.path.basename(path), "match": os.path.basename(path), "repo": repo,
                "anchor": guess_anchor(path), "version": guess_version_spec(path)}

    # ---------- 扫描单个对象 ----------
    def scan_item(self, it: Item) -> Item:
        rk = self._record_key(it)
        it.updated_at = (self.records.get("update_times") or {}).get(rk, "")
        if not it.local_size:
            it.local_size = dir_size(it.path)
        try:
            res = self._scan_git(it) if it.kind == "git" else self._scan_release(it)
        except Exception as e:
            it.status = "error"
            it.error = f"{type(e).__name__}: {e}"
            it.detail = short(str(e), 120)
            res = it
        res.scanned = True
        return res

    def _scan_git(self, it: Item) -> Item:
        repo = it.path
        ok, how, err = self._git_fetch(repo)
        if not ok:
            it.status = "error"
            it.error = err
            it.detail = "网络/代理不可用：" + short(err, 90)
            return it
        branch = (git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout or "").strip() or "HEAD"
        up = git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
        if up.returncode == 0:
            uref = "@{u}"
        else:
            uref = f"origin/{branch}"
            if git(repo, ["rev-parse", "--verify", uref]).returncode != 0:
                it.status = "error"
                it.detail = f"分支 {branch} 没有可用的上游"
                return it
        # 关键命令必须检查返回码：rev-list 失败时 stdout 为空，int("" or 0)=0 会误判成 latest
        rl = git_checked(repo, ["rev-list", "--count", f"HEAD..{uref}"])
        if rl is None:
            it.status = "error"
            it.detail = "统计落后提交失败"
            return it
        behind = int((rl.stdout or "").strip() or 0)
        ra = git_checked(repo, ["rev-list", "--count", f"{uref}..HEAD"])
        ahead = int((ra.stdout or "").strip() or 0) if ra is not None else 0
        it.local = (git(repo, ["rev-parse", "--short", "HEAD"]).stdout or "").strip()
        it.remote = (git(repo, ["rev-parse", "--short", uref]).stdout or "").strip()
        it.detail = f"{branch} · {how}" + (f" · 本地领先 {ahead}" if ahead else "")
        it.repo = origin_slug(repo)
        if behind == 0:
            it.status = "latest"
            return it
        st = git_checked(repo, ["status", "--porcelain"])
        dirty = (st.stdout or "").strip() if st is not None else ""
        it.status = "update"
        it.detail = f"落后 {behind} 个提交 · {branch}"
        it.message = f"落后 {behind} 个提交"
        it.notes = self._git_changelog(repo, uref, limit=20)
        it.published = (git(repo, ["log", "-1", "--format=%ad", "--date=short", uref]).stdout or "").strip()
        if dirty:
            it.status = "skip"
            it.detail = f"落后 {behind} 个提交，但本地有未提交改动"
        return it

    def _git_changelog(self, repo: str, uref: str, limit: int = 20) -> str:
        r = git(repo, ["log", f"-{limit}", "--date=short",
                       "--pretty=format:%ad  %s", f"HEAD..{uref}"])
        lines = [l.strip() for l in (r.stdout or "").splitlines() if l.strip()]
        return "\n".join(lines)

    def _scan_release(self, it: Item) -> Item:
        if it.status == "unbound" or not it.repo:
            it.status = "unbound"
            return it
        prox = self.proxy
        latest = http_json(f"https://api.github.com/repos/{it.repo}/releases/latest", prox,
                           self.mirrors(), deadline=time.time() + 45)
        if not latest:
            it.status = "error"
            it.detail = f"查询 {it.repo} 失败（网络或限流）"
            return it
        tag = latest.get("tag_name", "") or ""
        assets = latest.get("assets") or []
        asset = pick_asset(assets, it.asset_regex or (it.version_spec or {}).get("asset_regex", ""))
        it.remote = tag
        it.published = (latest.get("published_at") or "")[:10]
        it.notes_url = latest.get("html_url", "")
        it.notes = (latest.get("body") or "").strip()
        if asset:
            it.asset = asset.get("name", "")
            it.asset_size = int(asset.get("size") or 0)
            it.asset_digest = str(asset.get("digest") or "").strip() or ""
            it.download_url = asset.get("browser_download_url", "")
        it.local = read_local_version(it.path, it.version_spec, it.name, self.records,
                                      record_key=self._record_key(it))
        it.local_manual = False
        if not it.local:
            mv = self.manual_version(it.name)
            if mv:
                it.local, it.local_manual = mv, True
        if not it.local:
            it.local = self.records["versions"].get(self._record_key(it), "")
        mark = "　· 手动标记" if it.local_manual else ""
        if not tag:
            it.status = "error"
            it.detail = "该仓库没有发布版本"
            return it
        if not asset:
            it.status = "error"
            it.detail = f"最新版 {tag} 没有可下载的压缩包"
            return it
        if not it.local:
            it.status = "unknown"
            it.detail = f"本地版本未知，最新 {tag}（可直接更新）"
            return it
        if ver_gt(tag, it.local):
            it.status = "update"
            it.detail = f"{it.local} → {tag}{mark}"
        else:
            it.status = "latest"
            it.detail = f"{it.local}（最新 {tag}）{mark}"
        return it

    def _git_fetch(self, repo: str, deadline: float = 0):
        base_cfg = ["-c", "http.lowSpeedLimit=1000", "-c", "http.lowSpeedTime=60"]
        attempts = []
        if self.proxy:
            attempts.append((f"代理", base_cfg + ["-c", f"http.proxy={self.proxy}",
                                                 "-c", f"https.proxy={self.proxy}"]))
        attempts.append(("直连", base_cfg))
        mirror = self.ensure_mirror()
        if mirror and is_https_github(repo):
            attempts.append(("镜像", base_cfg + ["-c", f"url.{mirror}https://github.com/.insteadOf=https://github.com/"]))
        last = ""
        for label, cfg in attempts:
            if self.cancel.is_set():
                return False, label, "已取消"
            if self.expired(deadline):
                return False, label, "更新超时"
            try:
                r = git(repo, ["fetch", "--prune", "--quiet"],
                        timeout=int(max(15, self.remaining(deadline, 180))), cfg=cfg)
                if r.returncode == 0:
                    return True, label, ""
                last = ((r.stderr or r.stdout) or "").strip()[:140]
            except Exception as e:
                last = f"{type(e).__name__}: {e}"
        return False, "全部失败", last or "未知错误"

    # ---------- 并发扫描 ----------
    def scan_all(self, items: list[Item], on_item=None, on_progress=None, workers: int = 6) -> list[Item]:
        # cancel 由 GUI 在启动 worker 前清理；这里再 clear 会吞掉刚点下的「停止」
        done = 0
        total = len(items)
        results: list[Item] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(self.scan_item, it): it for it in items}
            for fut in as_completed(futs):
                it = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    it.status = "error"
                    it.error = str(e)
                    res = it
                results.append(res)
                done += 1
                if on_item:
                    on_item(res)
                if on_progress:
                    on_progress(done, total, res)
                if self.cancel.is_set():
                    break
        self.save_records()
        return results

    # ---------- 更新 ----------
    def timeout_sec(self) -> int:
        try:
            return max(1, int(self.cfg.get("update", {}).get("timeout_min") or 10)) * 60
        except Exception:
            return 600

    def expired(self, deadline: float = 0) -> bool:
        return bool(deadline) and time.time() > deadline

    def remaining(self, deadline: float = 0, cap: float = 1e9) -> float:
        if not deadline:
            return cap
        return max(0.0, min(cap, deadline - time.time()))

    def update_item(self, it: Item, on_progress=None) -> Item:
        limit = self.timeout_sec()
        deadline = time.time() + limit
        with self._lock_for(it.path):
            return self._update_item_locked(it, deadline, on_progress)

    def _update_item_locked(self, it: Item, deadline: float = 0, on_progress=None) -> Item:
        try:
            it.state = "running"
            if it.kind == "git":
                return self._update_git(it, deadline, on_progress)
            return self._update_release(it, deadline, on_progress)
        except Exception as e:
            it.state = "failed"
            it.error = f"{type(e).__name__}: {e}"
            it.detail = short(str(e), 120)
            return it

    def _stamp_update(self, it: Item) -> None:
        """记录「上次更新时间」，供排序使用。"""
        rk = self._record_key(it)
        self.records.setdefault("update_times", {})[rk] = time.strftime("%Y-%m-%d %H:%M:%S")
        it.updated_at = self.records["update_times"][rk]

    def _update_git(self, it: Item, deadline: float = 0, on_progress=None) -> Item:
        repo = it.path
        ok, how, err = self._git_fetch(repo, deadline)
        if not ok:
            it.state, it.status = "failed", "error"
            it.detail = "拉取失败：" + short(err, 90)
            return it
        branch = git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
        uref = "@{u}"
        if (git(repo, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]).returncode != 0):
            uref = f"origin/{branch}"
        st = git_checked(repo, ["status", "--porcelain"])
        if st is not None and (st.stdout or "").strip():
            it.state, it.status = "failed", "skip"
            it.detail = "本地有未提交改动，已跳过"
            return it
        r = git(repo, ["merge", "--ff-only", uref], timeout=int(max(30, self.remaining(deadline, 180))))
        if r.returncode == 0:
            it.state, it.status = "done", "latest"
            it.local = (git(repo, ["rev-parse", "--short", "HEAD"]).stdout or "").strip()
            it.detail = f"已更新到 {it.local}"
            it.remote = it.local
            self._stamp_update(it)
            return it
        it.state, it.status = "failed", "error"
        it.detail = short((r.stderr or r.stdout), 120)
        return it

    def _update_release(self, it: Item, deadline: float = 0, on_progress=None) -> Item:
        if not it.download_url:
            it.state, it.status = "failed", "error"
            it.detail = "缺少下载地址"
            return it
        exe_name = os.path.basename(it.anchor or "")
        if exe_name.lower().endswith(".exe") and is_process_running(exe_name):
            it.state, it.status = "failed", "skip"
            it.detail = f"{exe_name} 正在运行，请先关闭程序"
            return it

        def pr(p, msg=""):
            it.progress = p
            if msg:
                it.message = msg
            if on_progress:
                on_progress(it, p, msg)

        mins = self.timeout_sec() // 60

        tmp = tempfile.mkdtemp(prefix="autoupd_")
        try:
            # 下载放进持久缓存：中断后下次从断点继续，而不是从头再来
            cache_dir = os.path.join(config_dir(), "dlcache")
            try:
                os.makedirs(cache_dir, exist_ok=True)
            except Exception:
                cache_dir = tmp
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{it.name}_{it.asset or 'package.zip'}")
            archive = os.path.join(cache_dir, safe)
            pr(0.02, "下载中（支持断点续传）")
            ok = download(it.download_url, archive, self.proxy, self.mirrors(),
                          progress_cb=lambda p: pr(0.02 + p * 0.6, "下载中"),
                          cancel=self.cancel, deadline=deadline,
                          expect_size=int(it.asset_size or 0),
                          sha256=it.asset_digest)
            if self.cancel.is_set():
                it.state, it.status = "failed", "error"
                it.detail = "已取消"
                return it
            if not ok:
                it.state, it.status = "failed", "error"
                if self.expired(deadline):
                    it.detail = f"更新超时（超过 {mins} 分钟），已中止"
                else:
                    it.detail = "下载失败（网络/镜像均不可用或文件不完整）"
                return it
            if self.expired(deadline):
                it.state, it.status = "failed", "error"
                it.detail = f"更新超时（超过 {mins} 分钟），已中止"
                return it

            pr(0.65, "解压中")
            ex = os.path.join(tmp, "extract")
            if not extract_archive(archive, ex):
                it.state, it.status = "failed", "error"
                it.detail = "解压失败（7z 需要 py7zr 或已安装 7-Zip）"
                return it
            src_root = locate_root(ex, it.anchor)
            # 锚点必验：找不到预期的锚点（exe/interface.json）就中止，绝不清空原程序再装错误内容
            if it.anchor and not os.path.exists(os.path.join(src_root, it.anchor)):
                it.state, it.status = "failed", "error"
                it.detail = f"解压包内未找到预期锚点「{it.anchor}」，已中止（资产可能选错）"
                return it

            pr(0.72, "备份中")
            if self.cfg.get("backup", {}).get("enabled", True):
                # 备份失败必须终止：磁盘满/权限不足时若继续替换，旧文件删了、新文件
                # 没装上、备份也没有，完全不可恢复。绝不吞异常继续。
                try:
                    it.archived = backup_dir(it.path, it.name,
                                             int(self.cfg.get("backup", {}).get("keep", 2)))
                    # 备份里记下旧版本号，供「撤销上次更新」还原
                    with open(os.path.join(it.archived, "_updater_undo.json"), "w",
                              encoding="utf-8") as f:
                        json.dump({"version": it.local, "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                                  f, ensure_ascii=False)
                except Exception as e:
                    it.state, it.status = "failed", "error"
                    it.detail = f"备份失败，已中止更新：{short(str(e), 90)}"
                    return it

            pr(0.8, "替换文件")
            try:
                if self.cfg.get("update", {}).get("install_mode", "clean") == "overwrite":
                    # 直接覆盖：旧文件全部保留，只用新版同名内容替换
                    copied = copy_over(src_root, it.path, [])
                    it.detail = f"已覆盖安装 {it.remote}（写入 {copied} 项）"
                else:
                    removed, copied = clean_replace(src_root, it.path, it.preserve)
                    it.detail = f"已更新到 {it.remote}（清理旧文件 {removed} 项，写入 {copied} 项）"
            except Exception as e:
                # 绝对不能标 latest / 写 records，否则用户目录残了却再也不会提示更新
                rolled = ""
                if it.archived and os.path.isdir(it.archived):
                    try:
                        clean_replace(it.archived, it.path, it.preserve)
                        rolled = "，已自动回滚到更新前备份"
                    except Exception:
                        rolled = "，回滚也失败，请手动点「撤销上次更新」"
                else:
                    rolled = "（未开启备份，无法自动回滚）"
                it.state, it.status = "failed", "error"
                it.detail = f"更新失败：{short(str(e), 90)}{rolled}"
                return it
            it.state, it.status = "done", "latest"
            it.local = it.remote or it.local
            self.records["versions"][self._record_key(it)] = it.remote
            self._stamp_update(it)
            # 成功立即原子写盘：只改内存的话，record 类型程序下次启动会把已装新版
            # 误判为「待更新」。写盘失败单独报告，不能静默丢失，也不能把已装好的文件
            # 误标成安装失败。
            try:
                self.save_records()
            except Exception:
                it.detail += "（注意：记录保存失败）"
            # 更新成功：只清理当前这次下载的缓存文件，别把其它程序的断点/并发下载一起删掉
            try:
                if os.path.exists(archive):
                    os.remove(archive)
            except Exception:
                pass
            pr(1.0, "完成")
            return it
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def undo_update(self, it: Item, on_progress=None) -> Item:
        """撤销上次更新：把最近一次备份还原回程序目录。"""
        with self._lock_for(it.path):
            return self._undo_update_locked(it, on_progress)

    def _undo_update_locked(self, it: Item, on_progress=None) -> Item:
        def pr(p, msg=""):
            it.progress = p
            if msg:
                it.message = msg
            if on_progress:
                on_progress(it, p, msg)

        it.state = "running"
        pr(0.1, "查找备份")
        target_norm = os.path.normcase(os.path.realpath(it.path))
        cands = []
        if it.archived and os.path.isdir(it.archived):
            cands.append(it.archived)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", it.name)
        cache = os.path.join(os.path.dirname(it.path.rstrip(os.sep)), "Cache", "_backup")
        cands += sorted(glob.glob(os.path.join(cache, safe + "_*")),
                        key=os.path.getmtime, reverse=True)
        # 按 manifest 核对备份归属：跳过不属于本程序（同名/同前缀碰撞）的备份
        bk = ""
        for c in cands:
            if not os.path.isdir(c):
                continue
            mf = os.path.join(c, "_updater_manifest.json")
            if os.path.exists(mf):
                try:
                    with open(mf, encoding="utf-8") as f:
                        owner = (json.load(f).get("target") or "")
                    if owner and os.path.normcase(owner) != target_norm:
                        continue
                except Exception:
                    continue
            bk = c
            break
        if not bk:
            it.state, it.status = "failed", "error"
            it.detail = "没有可用的备份，无法撤销（备份关闭或从未更新过）"
            return it
        ver = ""
        try:
            with open(os.path.join(bk, "_updater_undo.json"), encoding="utf-8") as f:
                ver = (json.load(f).get("version") or "").strip()
        except Exception:
            pass
        pr(0.5, "还原备份")
        try:
            removed, copied = clean_replace(bk, it.path, it.preserve)
        except Exception as e:
            it.state, it.status = "failed", "error"
            it.detail = f"还原失败：{short(str(e), 90)}"
            return it
        # 还原后重新读本地版本：元数据缺失时绝不能把已还原的旧版标成「最新」
        new_local = ver or read_local_version(it.path, it.version_spec, it.name, self.records)
        if ver:
            self.records["versions"][self._record_key(it)] = ver
            self.save_records(update_last_scan=False)
        it.local = new_local or it.local
        it.state = "idle"          # 撤销完成归 idle，让行按钮恢复「更新」；不要用 done（会被显示成「已最新」禁用）
        if it.remote and new_local:
            it.status = "update" if ver_gt(it.remote, new_local) else "latest"
        else:
            it.status = "latest"
        it.detail = f"已撤销更新，恢复到 {new_local or '备份版本'}（清理 {removed} 项，还原 {copied} 项）"
        pr(1.0, "完成")
        return it

    # ---------- 通知 ----------
    def send_dingtalk(self, text: str, title: str = APP_NAME) -> bool:
        """直接推送钉钉（不再检查 enabled，供「通知方式」里勾选钉钉时使用）。"""
        return send_dingtalk_raw(self.cfg.get("dingtalk", {}), text, title)



def send_dingtalk_raw(ding_cfg: dict, text: str, title: str = APP_NAME) -> bool:
    """按 webhook + 加签密钥推送一条 markdown 消息。返回是否真正发送成功（errcode==0）。"""
    webhook = (ding_cfg.get("webhook") or "").strip()
    secret = (ding_cfg.get("secret") or "").strip()
    if not webhook:
        return False
    # 只允许 https：http 会明文发送 access_token 与消息内容
    if not webhook.lower().startswith("https://"):
        return False
    url = webhook
    if secret:
        ts = str(round(time.time() * 1000))
        sign = urllib.parse.quote_plus(base64.b64encode(hmac.new(
            secret.encode(), f"{ts}\n{secret}".encode(), hashlib.sha256).digest()))
        # 用 urlparse 正确合并 query，先移除旧 timestamp/sign 再追加，避免重复参数或 fragment 错位
        parts = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        q = [(k, v) for k, v in q if k not in ("timestamp", "sign")]
        q += [("timestamp", ts), ("sign", sign)]
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), ""))
    payload = json.dumps({"msgtype": "markdown",
                          "markdown": {"title": title, "text": text}}).encode()
    try:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read(64 * 1024)
        # 业务失败（如加签错 310000）也当失败，别把 HTTP 200 当成功
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return True
        return int(data.get("errcode", 0) or 0) == 0
    except Exception:
        return False


# ==================================================================== 目录/文件工具

def _excluded(root: str, path: str, name: str, excl: set) -> bool:
    """排除项既可以是文件夹名，也可以是相对根目录的路径（右键排除时写入）。"""
    if not excl:
        return False
    if name.lower() in excl:
        return True
    try:
        rel = os.path.relpath(path, root).replace("/", "\\").lower()
    except Exception:
        return False
    return rel in excl


def find_git_repos(root: str, exclude: set, max_depth: int) -> list[str]:
    out = []
    base = os.path.abspath(root).rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, _ in os.walk(root):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base
        dirnames[:] = [d for d in dirnames if not d.startswith(".")
                       and not _excluded(root, os.path.join(dirpath, d), d, exclude)]
        if os.path.isdir(os.path.join(dirpath, ".git")):
            out.append(dirpath)
            dirnames[:] = []          # 仓库内部不再下钻
            continue
        if depth >= max_depth:
            dirnames[:] = []
    return out


def find_program_dirs(root: str, exclude: set) -> list[str]:
    """只认根目录下一层的「独立程序」文件夹。

    判定：顶层有 .exe / interface.json / 启动脚本，且本身不是 git 仓库。
    这样不会钻进 MAA\\python、March7th\\3rdparty 这类内部目录。
    """
    out = []
    try:
        entries = sorted(os.listdir(root))
    except Exception:
        return out
    for name in entries:
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        if name.startswith(".") or _excluded(root, path, name, exclude):
            continue
        if os.path.isdir(os.path.join(path, ".git")):
            continue
        if looks_like_app(path):
            out.append(path)
    return out


def looks_like_app(path: str) -> bool:
    try:
        names = [n.lower() for n in os.listdir(path)]
    except Exception:
        return False
    if any(n.endswith(".exe") for n in names):
        return True
    if "interface.json" in names:
        return True
    if any(n.endswith((".cmd", ".bat", ".lnk")) for n in names):
        return True
    return False


def guess_anchor(path: str) -> str:
    if os.path.exists(os.path.join(path, "interface.json")):
        return "interface.json"
    exes = sorted(glob.glob(os.path.join(path, "*.exe")),
                  key=lambda p: os.path.getsize(p), reverse=True)
    exes = [e for e in exes if not re.search(r"(update|uninst|setup)", os.path.basename(e), re.I)]
    return os.path.basename(exes[0]) if exes else ""


def guess_version_spec(path: str) -> dict:
    if os.path.exists(os.path.join(path, "interface.json")):
        return {"type": "json", "file": "interface.json", "key": "version"}
    for fn, rx in (("version.txt", r"[0-9]+(?:\.[0-9]+)+"),
                   ("version.json", None),
                   ("updateInfo.txt", r"version\s*=\s*['\"]?([0-9.]+)")):
        if os.path.exists(os.path.join(path, fn)):
            if fn.endswith(".json"):
                return {"type": "json", "file": fn, "key": "version"}
            return {"type": "regex", "file": fn, "regex": rx}
    anchor = guess_anchor(path)
    if anchor.lower().endswith(".exe"):
        return {"type": "exe", "file": anchor}
    return {"type": "record"}




def read_local_version(target: str, spec: dict, name: str, records: dict, record_key: str = "") -> str:
    spec = spec or {}
    t = spec.get("type")
    try:
        if t == "json":
            with open(os.path.join(target, spec["file"]), encoding="utf-8-sig") as f:
                data = json.load(f)
            return str(data.get(spec.get("key", "version")) or "")
        if t == "regex":
            with open(os.path.join(target, spec["file"]), encoding="utf-8", errors="ignore") as f:
                m = re.search(spec["regex"], f.read())
            if not m:
                return ""
            return (m.group(1) if m.groups() else m.group(0)).strip()
        if t == "exe":
            p = os.path.join(target, spec.get("file", ""))
            if not os.path.exists(p):
                return ""
            # 路径经环境变量传给 PowerShell，绝不用双引号拼进 -Command：
            # 路径里含 $(...) 会被当成子表达式执行（命令注入）
            env = dict(os.environ)
            env["_AU_EXE"] = p
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-Item -LiteralPath $env:_AU_EXE).VersionInfo.FileVersion"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=20, env=env, **SILENT)
            v = (r.stdout or "").strip()
            return v if re.search(r"\d", v) else ""
        if t == "record":
            key = record_key or name
            return str(records.get("versions", {}).get(key, "") or "")
    except Exception:
        return ""
    return ""


def pick_asset(assets, pattern: str = ""):
    """挑出 Windows 压缩包。显式正则（asset_regex）是「严格匹配」：零匹配/非法正则
    都返回 None 让调用方报错，绝不悄悄退回通用 ZIP/7z 选一个（可能选到 Linux 包）。"""
    if not assets:
        return None
    if pattern:
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            return None
        hit = [a for a in assets if rx.search(a.get("name", ""))]
        if not hit:
            return None
        return max(hit, key=lambda a: int(a.get("size") or 0))
    arcs = [a for a in assets
            if re.search(r"\.(zip|7z)$", a.get("name", ""), re.I)
            and not re.search(r"(source|sha256|checksum|symbol)", a.get("name", ""), re.I)]
    if not arcs:
        return None
    win = [a for a in arcs if re.search(r"(win|x64|x86_64|windows)", a.get("name", ""), re.I)]
    pool = win or arcs
    return max(pool, key=lambda a: int(a.get("size") or 0))


def _safe_join(dest: str, member: str) -> str | None:
    """把归档成员名解析到 dest 内的绝对路径；越界（.. / 绝对 / 盘符 / 链接）返回 None。"""
    name = (member or "").replace("\\", "/")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    target = os.path.abspath(os.path.join(dest, *parts))
    dest_abs = os.path.abspath(dest)
    if target != dest_abs and not target.startswith(dest_abs + os.sep):
        return None
    return target


def extract_archive(archive: str, dest: str) -> bool:
    """安全解压 zip / 7z。zip 用内置并做路径穿越防护；7z 依次尝试 py7zr、bsdtar、7-Zip。

    每个后端都用一个全新的空目录，成功后只接受该次产物；避免某个后端部分解压失败后，
    残留文件混入下一个后端继续处理的目录。
    """
    low = archive.lower()
    if low.endswith(".zip"):
        return _extract_zip(archive, dest)
    # 7z 用外部工具，先解到独立临时目录再校验成员，再移入 dest
    tmp = tempfile.mkdtemp(prefix="autoupd_x_")
    try:
        ok = False
        try:
            import py7zr
            with py7zr.SevenZipFile(archive, mode="r") as z:
                z.extractall(tmp)
            ok = any(os.scandir(tmp))
        except Exception:
            ok = False
        if not ok:
            for cmd in (["C:\\Windows\\System32\\tar.exe", "-xf", archive, "-C", tmp],
                        ["C:\\Program Files\\7-Zip\\7z.exe", "x", archive, f"-o{tmp}", "-y"],
                        ["C:\\Program Files (x86)\\7-Zip\\7z.exe", "x", archive, f"-o{tmp}", "-y"]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=1800, **SILENT)
                    if r.returncode == 0 and any(os.scandir(tmp)):
                        ok = True
                        break
                except Exception:
                    continue
        if not ok:
            return False
        # 校验解压产物没有越界/链接后，再搬进 dest
        for dirpath, dirnames, filenames in os.walk(tmp):
            dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
            for fn in filenames:
                p = os.path.join(dirpath, fn)
                if os.path.islink(p):
                    return False
            for d in dirnames:
                if os.path.islink(os.path.join(dirpath, d)):
                    return False
        os.makedirs(dest, exist_ok=True)
        for entry in os.listdir(tmp):
            shutil.move(os.path.join(tmp, entry), os.path.join(dest, entry))
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _extract_zip(archive: str, dest: str) -> bool:
    """内置 zip 解压，逐成员做路径穿越防护。"""
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                # 目录项跳过；链接/设备项拒绝
                if info.is_dir():
                    continue
                fmode = (info.external_attr >> 16) & 0o170000
                if fmode == 0o120000:   # symlink
                    return False
                target = _safe_join(dest, info.filename)
                if target is None:
                    return False
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out, 256 * 1024)
            return True
    except Exception:
        return False


def locate_root(extract_dir: str, anchor: str) -> str:
    """剥掉压缩包多余层级：优先最浅的含锚点目录，否则逐层唯一子目录下钻。"""
    if anchor:
        matches = [d for d, _, _ in os.walk(extract_dir) if os.path.exists(os.path.join(d, anchor))]
        if matches:
            return sorted(matches, key=lambda p: (p.count(os.sep), len(p)))[0]
    cur = extract_dir
    for _ in range(10):
        entries = [e for e in os.listdir(cur) if e not in ("__MACOSX", ".DS_Store")]
        if len(entries) == 1 and os.path.isdir(os.path.join(cur, entries[0])):
            cur = os.path.join(cur, entries[0])
        else:
            break
    return cur


def _force_remove(func, path, _):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def copy_over(src_root: str, dst: str, preserve) -> int:
    """写入失败必须抛错：旧版这里静默吞异常，调用方却照样报「更新成功」。"""
    keep = {p.lower() for p in (preserve or [])}
    failures = []
    n = 0
    for name in os.listdir(src_root):
        # _updater_undo.json 是备份目录里的内部元数据，不能随撤销拷回程序目录
        if name.lower() in keep or name in ("__MACOSX", ".DS_Store", "_updater_undo.json"):
            continue
        s, d = os.path.join(src_root, name), os.path.join(dst, name)
        try:
            if os.path.isdir(s):
                shutil.copytree(s, d, dirs_exist_ok=True)
            else:
                shutil.copy2(s, d)
            n += 1
        except Exception as e:
            failures.append(f"{name}: {e}")
    if failures:
        raise RuntimeError("写入失败 " + "；".join(failures[:3]))
    return n


def clean_replace(src_root: str, dst: str, preserve) -> tuple[int, int]:
    """净化覆盖：保留名单之外的全部删除，再写入新版内容。绝不改动目标目录名。"""
    keep = {p.lower() for p in (preserve or [])}
    removed = 0
    failed = []
    for name in list(os.listdir(dst)):
        if name.lower() in keep:
            continue
        p = os.path.join(dst, name)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, onerror=_force_remove)
            else:
                os.remove(p)
            removed += 1
        except Exception as e:
            failed.append(f"{name}: {e}")
    copied = copy_over(src_root, dst, preserve)
    if failed:
        raise RuntimeError("旧文件清理失败 " + "；".join(failed[:3]))
    return removed, copied


def backup_dir(target: str, app_name: str, keep: int) -> str:
    """备份程序目录，返回备份路径。

    目录名用「清洗后的程序名 + 8 位随机 + 时间」保证唯一，避免中文名/空格名
    （如 `Foo Bar` 与 `Foo_Bar`）清洗后前缀碰撞，撤销时把另一个程序的备份还原进来。
    备份内写入 _updater_manifest.json 记录目标绝对路径，撤销前必须核对。
    同秒并发时 dirs_exist_ok=True 会把两份备份合并，这里改成独占目录：已存在就重试新名字。
    """
    cache = os.path.join(os.path.dirname(target.rstrip(os.sep)), "Cache", "_backup")
    try:
        os.makedirs(cache, exist_ok=True)
    except Exception:
        cache = os.path.join(config_dir(), "backups")
        os.makedirs(cache, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", app_name)
    target_norm = os.path.normcase(os.path.realpath(target))
    out = ""
    for _ in range(20):
        cand = os.path.join(cache, f"{safe}_{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(4).hex()}")
        if not os.path.exists(cand):
            out = cand
            break
    if not out:
        raise RuntimeError("无法创建唯一备份目录")
    shutil.copytree(target, out, dirs_exist_ok=False)
    with open(os.path.join(out, "_updater_manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"target": target_norm, "app_name": app_name,
                   "time": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    # 清理超量旧备份：只按「同一程序前缀」收集，避免误删其它程序的备份
    olds = sorted(glob.glob(os.path.join(cache, safe + "_*")), key=os.path.getmtime, reverse=True)
    for old in olds[max(1, keep):]:
        shutil.rmtree(old, ignore_errors=True)
    return out


def open_url(url: str) -> None:
    """用默认浏览器打开链接（失败时静默忽略）。"""
    try:
        import webbrowser

        webbrowser.open(url)
    except Exception:
        pass


def is_process_running(exe_name: str) -> bool:
    if not exe_name or not exe_name.lower().endswith(".exe"):
        return False
    try:
        r = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {exe_name}"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=15, **SILENT)
        return exe_name.lower() in (r.stdout or "").lower()
    except Exception:
        return False


# ==================================================================== git 辅助

def git(repo: str, args, timeout=120, cfg=None):
    cmd = ["git", "-C", repo]
    if cfg:
        cmd += cfg
    cmd += args
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, **SILENT)


def git_checked(repo: str, args, timeout=120, cfg=None):
    """执行 git 命令并检查返回码；失败时返回 None（调用方必须处理），避免把空 stdout
    当成「没有落后提交 / 工作区干净」这类错误结论。"""
    r = git(repo, args, timeout=timeout, cfg=cfg)
    if r.returncode != 0:
        return None
    return r


def is_https_github(repo: str) -> bool:
    try:
        return git(repo, ["remote", "get-url", "origin"], timeout=10).stdout.strip().startswith(
            "https://github.com/")
    except Exception:
        return False


def origin_slug(repo: str) -> str:
    try:
        url = git(repo, ["remote", "get-url", "origin"], timeout=10).stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
        return f"{m.group(1)}/{m.group(2)}" if m else ""
    except Exception:
        return ""


def sniff_repo(path: str) -> str:
    """从程序目录的元数据里嗅探 GitHub 仓库 owner/repo。"""
    cands = []
    iface = os.path.join(path, "interface.json")
    if os.path.exists(iface):
        try:
            with open(iface, encoding="utf-8-sig") as f:
                data = json.load(f)
            for k in ("github", "repo", "url", "homepage", "mirror"):
                v = data.get(k)
                if isinstance(v, str) and "github.com" in v:
                    m = re.search(r"github\.com/([^/\s#?]+)/([^/\s#?]+)", v)
                    if m:
                        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
        except Exception:
            pass
    for pat in ("*.json", "*.txt", "*.md", "*.yml", "*.yaml"):
        for f in glob.glob(os.path.join(path, pat))[:20]:
            try:
                if os.path.getsize(f) > 400_000:
                    continue
                with open(f, encoding="utf-8", errors="ignore") as fh:
                    text = fh.read()
            except Exception:
                continue
            for m in re.finditer(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", text):
                full = m.group(0)
                if _REPO_NOISE.search(full):
                    continue
                cands.append(f"{m.group(1)}/{m.group(2).removesuffix('.git')}")
    if not cands:
        return ""
    freq = {}
    for c in cands:
        freq[c] = freq.get(c, 0) + 1
    return max(freq.items(), key=lambda kv: kv[1])[0]


def open_in_explorer(path: str) -> None:
    try:
        os.startfile(path)  # type: ignore[attr-defined]
    except Exception:
        subprocess.Popen(["explorer", path])
