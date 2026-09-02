# -*- coding: utf-8 -*-
"""
voicehist - 本地语音输入 + 完整历史留底（含浮动状态指示灯）

指示灯状态：
  灰 ●  待机（程式活着，随时可用）
  红 ●  录音中（跟著音量跳动 + 秒数）
  黄 ●  转写中
  绿 ●  完成（显示前几个字，2 秒后回待机）

转写完成后依序：先写 history.jsonl -> 复制剪贴板 -> 才尝试贴上。
贴上失败或焦点跑掉，纪录都还在。
"""
import sys, os, json, time, queue, threading, ctypes, winsound

if sys.stdout is None:
    # pythonw 静默启动（开机自动执行）时没有主控台，把输出转到 log 档
    try:
        _r = os.path.join(os.path.expanduser("~"), ".voicehist")
        os.makedirs(_r, exist_ok=True)
        _log = open(os.path.join(_r, "voicehist.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = _log
        _log.write("\n===== %s 启动 =====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
    except Exception:
        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        sys.stdout = sys.stderr = _Null()
elif hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def ensure_single_instance():
    """同一时间只跑一份。

    但重复点图示不该是「什么都不做」—— 那样使用者会以为没反应。
    既然已经在待命了，就把历史视窗叫出来，让这个图示同时是两个入口：
      没在跑 -> 启动待命     已经在跑 -> 看历史
    """
    k = ctypes.windll.kernel32
    k.CreateMutexW(None, False, "voicehist_singleton_mutex_v1")
    if k.GetLastError() != 183:      # ERROR_ALREADY_EXISTS
        return True
    try:
        import subprocess
        subprocess.Popen([sys.executable, os.path.join(ROOT, "history_gui.py")],
                         cwd=ROOT)
    except Exception:
        msg = ("voicehist 已经在待命了。" + chr(10) * 2 +
               "右下角的指示灯就是它，直接按 Ctrl+空白 开始讲话。" + chr(10) +
               "历史与设定都在系统匣图示右键。")
        ctypes.windll.user32.MessageBoxW(0, msg, "voicehist", 0x40)
    return False

import numpy as np
import sounddevice as sd
import pyperclip
import keyboard
import tkinter as tk


def _add_cuda_dlls():
    """Windows 上 ctranslate2 要 cuBLAS/cuDNN 的 DLL。
    单靠 add_dll_directory 不够（ctranslate2 走自己的 LoadLibrary），
    所以 PATH + add_dll_directory + 直接预载 三管齐下。"""
    import ctypes as _c, glob
    try:
        import nvidia
    except ImportError:
        return
    base = list(nvidia.__path__)[0]
    bins = [os.path.join(base, s, "bin") for s in ("cublas", "cudnn", "cuda_nvrtc")]
    bins = [b for b in bins if os.path.isdir(b)]
    if not bins:
        return
    os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")
    for b in bins:
        try:
            os.add_dll_directory(b)
        except Exception:
            pass
        for dll in sorted(glob.glob(os.path.join(b, "*.dll"))):
            try:
                _c.WinDLL(dll)
            except OSError:
                pass


_add_cuda_dlls()

ROOT = os.path.join(os.path.expanduser("~"), ".voicehist")
HIST = os.path.join(ROOT, "history.jsonl")
CFG = os.path.join(ROOT, "config.json")

DEFAULT_CFG = {
    "hotkey": "ctrl+space",
    "model": "medium",
    "beam_size": 1,         # 1 最快；调大只会变慢，实测文字一模一样
    "language": None,
    # --- 断句 / 标点 ---
    "pause_space": 0.35,    # 停顿超过这秒数 -> 插入空格
    "pause_newline": 1.0,   # 停顿超过这秒数 -> 换行
    "fix_punctuation": True,  # 修掉 Whisper 滥用的全形冒号
    "initial_prompt": "以下是繁體中文的句子，會夾雜英文技術名詞。例如：幫我重構那個 middleware，然後跑一次測試，順便看一下 log。",
    "auto_paste": True,
    # 剪贴簿策略：
    #   restore = 贴上後把剪贴簿还原成你原本复制的东西（预设，不干扰你）
    #   keep    = 转写结果留在剪贴簿里（旧行为）
    #   none    = 完全不碰剪贴簿，也不自动贴上，只写历史
    "clipboard": "restore",
    "restore_delay": 0.35,   # 还原前等多久，确保 Ctrl+V 已经贴完
    "beep": True,
    "sample_rate": 16000,
    "idle_unload_minutes": 5,   # 闲置这么久就释放显存；0 = 一直常驻
    # auto  = 平常隐藏，录音／转写时才浮出来（预设）
    # always= 一直显示
    # off   = 完全不显示，只靠 tray 图示
    "indicator_mode": "auto",
    "indicator": True,        # 保留给旧设定档；False 等同 indicator_mode=off
    "indicator_pos": "br",
    "tray_status_color": True,   # tray 图示依状态变色
    "wake_gap_seconds": 5.0,     # 两次 tick 相差超过这秒数 = 系统睡过，自动重挂热键
    "ui_scale": 2.1,        # 高度／字体倍率（原本 3.0 的 70%）
    "ui_width_scale": 1.5,  # 宽度倍率（原本 3.0 的一半）
    "stop_key": "esc",        # 录音中按这个停止并转写（单键，不用按组合键）
    "cancel_key": "delete",   # 录音／转写中按这个直接丢弃，不转写也不留纪录
    # --- GPU 精度与显存（2026-09-02 加，起因：DaVinci Resolve 开著时转写从 3 秒变 90 秒）---
    # int8_float16：权重压成 int8、计算仍用 float16，显存约省一半，文字几乎一样。
    # 4GB 显卡要跟 Resolve／Chrome／dwm 共存，这是关键。float16 是旧预设。
    "compute_type": "int8_float16",
    # 转写完把 GPU 工作缓冲区还给显卡。ctranslate2 旧预设 cub_caching 会一直抓著不放，
    # 闲置时多占好几百 MB。
    "cuda_allocator": "cuda_malloc_async",
    # 载入时显存剩不到这么多 MB 就改跑 CPU。硬塞进被挤满的显卡，模型会被换到主记忆体，
    # 实测慢 10～60 倍；CPU 反而快得多。0 = 永远硬上 GPU
    "min_free_vram_mb": 1500,
    "cpu_threads": 0,         # CPU 模式用几条线程；0 = 实体核心数
}


def load_cfg():
    cfg = dict(DEFAULT_CFG)
    if os.path.exists(CFG):
        try:
            cfg.update(json.load(open(CFG, encoding="utf-8")))
        except Exception as e:
            print("[警告] config.json 读取失败，改用预设值：%s" % e)
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return cfg


C = load_cfg()

# 要在 import ctranslate2 之前设好（faster_whisper 会一并载入 ctranslate2）
os.environ.setdefault("CT2_CUDA_ALLOCATOR", str(C.get("cuda_allocator") or "cuda_malloc_async"))
from faster_whisper import WhisperModel


def beep(f, ms):
    if C["beep"]:
        try:
            winsound.Beep(f, ms)
        except Exception:
            pass


def fg_title():
    try:
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        b = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, b, n + 1)
        return b.value
    except Exception:
        return ""


def _run_hidden(args, timeout=8):
    """跑外部指令但不弹黑窗（pythonw 下 subprocess 预设会闪一个 console）。"""
    import subprocess
    return subprocess.check_output(args, text=True, timeout=timeout,
                                   encoding="utf-8", errors="replace",
                                   creationflags=0x08000000)   # CREATE_NO_WINDOW


def gpu_mem_status():
    """回传 (已用 MB, 总量 MB)。没有 nvidia-smi（无 N 卡）就回 None。"""
    try:
        out = _run_hidden(["nvidia-smi", "--query-gpu=memory.used,memory.total",
                           "--format=csv,noheader,nounits"], 6)
        used, total = [float(x) for x in out.strip().split(",")[:2]]
        return int(used), int(total)
    except Exception:
        return None


def _pid_name(pid):
    if pid == os.getpid():
        return "voicehist"
    try:
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "pid%d" % pid
        try:
            n = ctypes.c_ulong(512)
            b = ctypes.create_unicode_buffer(512)
            if k.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(n)):
                return os.path.splitext(os.path.basename(b.value))[0]
        finally:
            k.CloseHandle(h)
    except Exception:
        pass
    return "pid%d" % pid


def gpu_mem_hogs(top=5):
    """谁在吃显存，由大到小回传 [(程式名, MB), ...]。

    WDDM 下 nvidia-smi 看不到各行程的用量（全部显示 N/A），
    要问 Windows 效能计数器 GPU Process Memory。typeperf 抓一个样本约 1 秒。
    """
    import csv, re
    try:
        out = _run_hidden(["typeperf", r"\GPU Process Memory(*)\Local Usage", "-sc", "1"], 10)
    except Exception:
        return []
    lines = [l for l in out.splitlines() if l.startswith('"')]
    if len(lines) < 2:
        return []
    hdr = next(csv.reader([lines[0]]))
    vals = next(csv.reader([lines[-1]]))
    by_pid = {}
    for h, v in zip(hdr[1:], vals[1:]):
        m = re.search(r"pid_(\d+)_", h)
        if not m:
            continue
        try:
            by_pid[int(m.group(1))] = by_pid.get(int(m.group(1)), 0.0) + float(v)
        except ValueError:
            pass
    rows = sorted(by_pid.items(), key=lambda kv: -kv[1])[:top]
    return [(_pid_name(p), int(b / 1048576)) for p, b in rows if b > 20 * 1048576]


class S:
    """共享状态：UI 只轮询这些值，不跨线程直接碰 tkinter"""
    mode = "idle"
    level = 0.0
    t0 = 0.0
    msg = ""
    model_loaded = False
    quit = False
    cancelled = False      # 使用者按了取消键，这轮录音整个作废
    force_show_until = 0.0 # tray 手动唤醒後，指示灯强制显示到这个时间点


class Indicator:
    COLORS = {"idle": "#5a5f66", "rec": "#e5484d", "trans": "#f5a524",
              "done": "#30a46c", "cancel": "#8b929e"}

    def __init__(self, root, app=None):
        self.root = root
        self.app = app
        self.tray = None
        self._last_tick = time.time()
        self._visible = True
        self._tray_mode = None
        sh = float(C.get("ui_scale", 1.6))          # 高度／字体
        sw = float(C.get("ui_width_scale", 1.2))    # 宽度
        self.s = sh
        pxh = lambda v: max(1, int(round(v * sh)))
        pxw = lambda v: max(1, int(round(v * sw)))
        self.barw, self.barh = pxw(54), pxh(6)

        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.93)
        root.configure(bg="#16181d")
        self.f = tk.Frame(root, bg="#16181d", padx=pxw(11), pady=pxh(7))
        self.f.pack()

        d = pxh(14)
        self.dot = tk.Canvas(self.f, width=d, height=d, bg="#16181d", highlightthickness=0)
        self.dot.pack(side="left")
        pad = max(1, int(round(2 * sh)))
        self.oval = self.dot.create_oval(pad, pad, d - pad, d - pad,
                                         fill=self.COLORS["idle"], outline="")
        # 录音中只显示秒数、不显示「录音中」三个字，所以字元数可以很少
        chars = max(5, int(round(8 * sw / sh)))
        self.txt = tk.Label(self.f, text="载入中…", fg="#c9ced6", bg="#16181d",
                            font=("Microsoft JhengHei UI", max(9, int(round(9 * sh)))),
                            width=chars, anchor="w")
        self.txt.pack(side="left", padx=(pxw(8), 0))
        self.bar = tk.Canvas(self.f, width=self.barw, height=self.barh,
                             bg="#2a2e35", highlightthickness=0)
        self.bar.pack(side="left", padx=(pxw(6), 0))
        self.fill = self.bar.create_rectangle(0, 0, 0, self.barh, fill="#e5484d", outline="")

        for w in (root, self.f, self.txt, self.dot, self.bar):
            w.bind("<Button-1>", self._down)
            w.bind("<B1-Motion>", self._drag)
        self._place()
        # auto 模式平常是隐藏的，第一个 tick 会依状态决定要不要显示
        if C.get("indicator_mode", "auto") != "always":
            self.root.withdraw()
            self._visible = False
        self.tick()

    def _place(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        pos = C.get("indicator_pos", "br")
        x = sw - w - 24 if pos.endswith("r") else 24
        y = sh - h - 70 if pos.startswith("b") else 24
        self.root.geometry("+%d+%d" % (x, y))

    def _down(self, e):
        self._ox = e.x_root - self.root.winfo_x()
        self._oy = e.y_root - self.root.winfo_y()

    def _drag(self, e):
        self.root.geometry("+%d+%d" % (e.x_root - self._ox, e.y_root - self._oy))

    def tick(self):
        if S.quit:
            self.root.destroy()
            return
        now = time.time()

        # --- 零成本的睡眠侦测 ---
        # tick 本该每 0.12 秒跑一次。两次差了好几秒，只有一种可能：系统刚睡过。
        # Windows 休眠时会静默移除低阶键盘钩子，程式还活着但热键收不到，
        # 所以这里顺手重挂一次。成本是一次减法。
        gap = now - self._last_tick
        self._last_tick = now
        if gap > float(C.get("wake_gap_seconds", 5.0)) and self.app is not None:
            print("[唤醒] tick 间隔 %.0f 秒，判定系统刚从休眠恢复，重挂热键" % gap)
            self.app.rehook()

        m = S.mode

        # --- 显示／隐藏 ---
        mode_cfg = C.get("indicator_mode", "auto")
        if not C.get("indicator", True):
            mode_cfg = "off"
        forced = now < S.force_show_until
        want = (mode_cfg == "always") or (mode_cfg == "auto" and (m != "idle" or forced))
        if want != self._visible:
            self._visible = want
            if want:
                self.root.deiconify()
                self.root.attributes("-topmost", True)
                self.root.lift()
            else:
                self.root.withdraw()
        elif want and forced:
            # 手动唤醒期间，每个 tick 都往上顶，确保盖在别的视窗上面
            self.root.attributes("-topmost", True)
            self.root.lift()

        # --- 同步 tray 图示颜色与提示文字 ---
        if self.tray is not None and C.get("tray_status_color", True) and m != self._tray_mode:
            self._tray_mode = m
            try:
                self.tray.icon = make_tray_image(m)
                self.tray.title = "voicehist - " + {
                    "idle": "待机中" if S.model_loaded else "载入中",
                    "rec": "录音中", "trans": "转写中",
                    "done": "完成", "cancel": "已取消"}.get(m, "待机中")
            except Exception:
                pass

        if m == "rec":
            self.dot.itemconfig(self.oval, fill=self.COLORS["rec"])
            self.txt.config(text="%.1fs" % (now - S.t0))
            self.bar.coords(self.fill, 0, 0,
                            min(self.barw, S.level * 120 * self.s), self.barh)
        else:
            self.dot.itemconfig(self.oval, fill=self.COLORS[m])
            if m == "idle":
                self.txt.config(text="待机" if S.model_loaded else "载入中")
            elif m == "trans":
                self.txt.config(text="转写中")
            elif m == "cancel":
                self.txt.config(text="已取消")
            else:
                self.txt.config(text=(S.msg[:8] or "完成"))
            self.bar.coords(self.fill, 0, 0, 0, self.barh)
        self.root.after(120, self.tick)


class Rec:
    def __init__(self, sr):
        self.sr = sr
        self.q = queue.Queue()
        self.st = None

    def _cb(self, indata, frames, t, status):
        self.q.put(indata.copy())
        try:
            S.level = float(np.sqrt(np.mean(indata ** 2)))
        except Exception:
            pass

    def start(self):
        while not self.q.empty():
            self.q.get_nowait()
        self.st = sd.InputStream(samplerate=self.sr, channels=1,
                                 dtype="float32", callback=self._cb)
        self.st.start()

    def stop(self):
        if self.st is None:
            return np.zeros(0, dtype=np.float32)
        self.st.stop()
        self.st.close()
        self.st = None
        S.level = 0.0
        ch = []
        while not self.q.empty():
            ch.append(self.q.get_nowait())
        if not ch:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(ch, axis=0).flatten()


def fix_punctuation(text):
    """Whisper 会模仿 initial_prompt 里出现过的标点。
    实测踩过两次：范例里放三个问号 -> 问号泛滥；出现一个「例如：」-> 整段冒号泛滥
    （一句 25 秒的话被塞了 7 个冒号，零个句号）。

    中文口语几乎用不到全形冒号，所以一律换掉：句尾的换句号，句中的换逗号。
    半形冒号保留不动 —— 时间 10:30、网址、key: value 都需要它。
    """
    if not C.get("fix_punctuation", True):
        return text
    out = []
    for i, ch in enumerate(text):
        if ch == "：":                     # 全形冒号
            rest = text[i + 1:].strip()
            out.append("。" if not rest else "，")   # 句号 / 逗号
        else:
            out.append(ch)
    return "".join(out)


def join_segments(segs):
    """Whisper 回传的 segment 本来就是照停顿切的。
    照实际停顿长度还原成空格／换行，而不是全部黏成一坨。"""
    sp = float(C.get("pause_space", 0.35))
    nl = float(C.get("pause_newline", 1.0))
    out, prev_end = [], None
    for s in segs:
        t = s.text.strip()
        if not t:
            continue
        if prev_end is not None:
            gap = s.start - prev_end
            if gap >= nl:
                out.append("\n")
            elif gap >= sp:
                out.append(" ")
        out.append(t)
        prev_end = s.end
    text = fix_punctuation("".join(out).strip())
    # Whisper 常在结尾多留一个逗号／顿号，清掉
    while text and text[-1] in "，,、 ":
        text = text[:-1].rstrip()
    return text


def save_history(text, lang, dur, title, pasted, extra=None):
    rec = {"ts": int(time.time()), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "text": text, "lang": lang, "seconds": round(dur, 1),
           "window": title, "pasted": pasted}
    if extra:
        rec.update(extra)      # trans_s / load_s / device：转写花了多久、在哪跑，方便事後查慢的原因
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class App:
    def __init__(self):
        self.rec = Rec(C["sample_rate"])
        self.on = False
        self.lock = threading.Lock()
        self.mlock = threading.Lock()   # 载入／卸载模型时互斥
        self.model = None
        self.device = None              # "cuda" / "cpu"，目前模型跑在哪
        self.last_use = time.time()

    def _cpu_threads(self):
        n = int(C.get("cpu_threads") or 0)
        return n or max(4, (os.cpu_count() or 8) // 2)

    def _pick_device(self):
        """显存够就 GPU，不够就 CPU。

        2026-09-02 实测：DaVinci Resolve 开著时 4GB 显卡剩不到 1.2GB，
        模型硬塞进去会被 Windows 换到主记忆体，37 秒的话转写 254 秒（原本 3 秒）。
        这种时候 CPU 反而快得多，而且不会拖慢 Resolve。
        """
        need = int(C.get("min_free_vram_mb") or 0)
        st = gpu_mem_status()
        if st is None:
            return "cuda"        # 没 nvidia-smi 就照旧硬试 GPU，失败会自己退 CPU
        used, total = st
        free = total - used
        print("[显存] 载入前其它程式已用 %d / %d MB，剩 %d MB" % (used, total, free))
        if need and free < need:
            hogs = "、".join("%s %dMB" % h for h in gpu_mem_hogs()) or "（查不到）"
            print("[显存] 剩不到 %d MB，硬塞会被换到主记忆体、慢 10 倍以上，这次改用 CPU（%d 线程）。"
                  "最占显存：%s。关掉它们之後下次载入会自动回 GPU。"
                  % (need, self._cpu_threads(), hogs))
            return "cpu"
        return "cuda"

    def _load(self, dev):
        """依序尝试：GPU 指定精度 → GPU float16 → CPU int8。"""
        ct = str(C.get("compute_type") or "int8_float16")
        tries = []
        if dev == "cuda":
            tries.append(("cuda", ct))
            if ct != "float16":
                tries.append(("cuda", "float16"))
        tries.append(("cpu", "int8"))
        last = None
        for d, c in tries:
            t = time.time()
            try:
                m = WhisperModel(C["model"], device=d, compute_type=c,
                                 cpu_threads=self._cpu_threads())
                print("[载入] %s @ %s/%s  %.1fs" % (C["model"], "GPU" if d == "cuda" else "CPU",
                                                   c, time.time() - t))
                self.device = d
                return m
            except Exception as e:
                last = e
                print("[载入] %s/%s 失败（%s：%s）" % (d, c, type(e).__name__, str(e)[:120]))
        raise last

    def get_model(self):
        with self.mlock:
            if self.model is None:
                print("[载入] %s 模型…" % C["model"])
                self.model = self._load(self._pick_device())
                S.model_loaded = True
            self.last_use = time.time()
            return self.model

    def unload(self, why):
        with self.mlock:
            if self.model is None:
                return
            print("[卸载] %s" % why)
            self.model = None
            self.device = None
            S.model_loaded = False
        import gc
        gc.collect()

    def idle_watch(self):
        mins = C.get("idle_unload_minutes", 0)
        if not mins:
            return
        while not S.quit:
            time.sleep(20)
            if (self.model is not None and S.mode == "idle"
                    and time.time() - self.last_use > mins * 60):
                self.unload("闲置超过 %s 分钟，释放显存（下次按热键自动载回）" % mins)

    def toggle(self):
        with self.lock:
            if not self.on:
                self.on = True
                S.cancelled = False
                S.t0 = time.time()
                S.mode = "rec"
                self.rec.start()
                beep(880, 90)
                print("● 录音中… %s" % time.strftime("%H:%M:%S"))
            else:
                self.on = False
                audio = self.rec.stop()
                dur = time.time() - S.t0
                S.mode = "trans"
                beep(560, 90)
                title = fg_title()
                print("■ 停止 %.1fs，转写中…" % dur)
                threading.Thread(target=self.work, args=(audio, dur, title),
                                 daemon=True).start()

    def rehook(self):
        """重挂全域热键。

        系统休眠／合盖再打开後，Windows 会静默移除低阶键盘钩子（WH_KEYBOARD_LL），
        程式本身没死 —— tray 在、指示灯在 —— 但再也收不到按键。这就是
        「合盖回来 Ctrl+空白 没反应」的根因。unhook 再 hook 一次就好。
        """
        try:
            keyboard.unhook_all()
        except Exception:
            pass
        keyboard.add_hotkey(C["hotkey"], self.toggle, suppress=False)
        sk = C.get("stop_key")
        if sk:
            keyboard.on_press_key(sk, self.stop_if_recording, suppress=False)
        ck = C.get("cancel_key")
        if ck:
            keyboard.on_press_key(ck, self.cancel, suppress=False)
        print("[热键] 已挂载  开始/停止=%s  停止=%s  取消=%s" % (C["hotkey"], sk, ck))

    def cancel(self, _event=None):
        """取消键：把这轮整个丢掉。

        误触热键录了几分钟，本来还得等它转写完、再把不要的文字从输入框删掉。
        现在录音中或转写中按下取消，音讯直接丢弃，不转写、不进剪贴板、
        也不写进历史 —— 当作没发生过。
        """
        with self.lock:
            if self.on:
                self.on = False
                self.rec.stop()
                dur = time.time() - S.t0
                S.cancelled = True
                S.mode = "cancel"
                beep(400, 140)
                print("✕ 已取消（录了 %.1fs，不转写）" % dur)
                self._back()
            elif S.mode == "trans":
                # 已经在转写了，设旗标让结果被丢弃
                S.cancelled = True
                beep(400, 140)
                print("✕ 转写中止，结果将丢弃")

    def stop_if_recording(self, _event=None):
        """停止键（预设 ESC）：只在录音中才动作，
        平时完全不拦截，ESC 在别的程式照常用。"""
        if self.on:
            self.toggle()

    def work(self, audio, dur, title):
        if S.cancelled:
            return
        if audio.size < C["sample_rate"] * 0.3:
            S.msg = "太短"
            S.mode = "done"
            self._back()
            print("  → 太短，忽略\n")
            return
        try:
            t_load = time.time()
            m = self.get_model()
            t_load = time.time() - t_load
            t_tr = time.time()
            segs, info = m.transcribe(audio, language=C["language"],
                                      beam_size=int(C.get("beam_size", 1)),
                                      initial_prompt=C.get("initial_prompt") or None,
                                      vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 500})
            text = join_segments(segs)      # segments 是 lazy generator，这里才真的算完
            t_tr = time.time() - t_tr
            lang = info.language
        except Exception as e:
            S.msg = "转写失败"
            S.mode = "done"
            self._back()
            print("  → 转写失败：%s\n" % e)
            return

        if S.cancelled:
            print("  → 已取消，丢弃转写结果" + chr(10))
            S.mode = "cancel"
            self._back()
            return

        if not text:
            S.msg = "没听到"
            S.mode = "done"
            self._back()
            print("  → 没听到内容\n")
            return

        pasted = False
        mode = str(C.get("clipboard", "restore")).lower()

        if mode == "none":
            # 完全不碰剪贴板。历史已经留底，要用就去历史视窗手动复制。
            print("  [剪贴板] 未使用（clipboard=none）")
        else:
            backup = None
            if mode == "restore":
                try:
                    backup = pyperclip.paste()
                except Exception:
                    backup = None
            try:
                pyperclip.copy(text)
            except Exception as e:
                print("  [注意] 复制剪贴板失败：%s" % e)

            if C["auto_paste"]:
                try:
                    time.sleep(0.15)
                    keyboard.send("ctrl+v")
                    pasted = True
                except Exception as e:
                    print("  [注意] 自动贴上失败（历史已留底）：%s" % e)

            # 只在原本确实有文字时才还原。否则会把剪贴板清空
            # （图片、档案等非文字内容 pyperclip 读不到，会回传空字串）
            if mode == "restore" and backup:
                try:
                    time.sleep(float(C.get("restore_delay", 0.35)))
                    pyperclip.copy(backup)
                    print("  [剪贴板] 已还原成你原本的内容")
                except Exception as e:
                    print("  [注意] 剪贴板还原失败：%s" % e)

        save_history(text, lang, dur, title, pasted,
                     {"trans_s": round(t_tr, 1), "load_s": round(t_load, 1), "device": self.device})
        self.last_use = time.time()
        S.msg = text
        S.mode = "done"
        self._back()
        ratio = t_tr / dur if dur else 0.0
        print("  → [%s] %s" % (lang, text))
        timing = "⏱ 转写 %.1fs（音讯的 %.2f 倍，%s）" % (
            t_tr, ratio, "GPU" if self.device == "cuda" else "CPU")
        if t_load > 0.5:
            timing += "，载入 %.1fs" % t_load
        print("     %s ｜ %s ｜ %s\n" % ("已贴上" if pasted else "未贴上（已存历史+剪贴板）",
                                         timing, title[:40]))
        if t_tr > 8 and ratio > 0.5:
            threading.Thread(target=self._explain_slow, args=(t_tr, ratio), daemon=True).start()

    def _explain_slow(self, t_tr, ratio):
        """转写慢得不正常时，把原因写进 log：谁占了显存、要不要改跑 CPU。
        正常是音讯长度的 0.05～0.2 倍；超过 0.5 倍几乎都是显存被挤爆。"""
        st = gpu_mem_status()
        msg = "  [慢] 这次转写 %.0f 秒，是音讯长度的 %.1f 倍（正常 0.1～0.2 倍）。" % (t_tr, ratio)
        if self.device == "cpu":
            msg += "目前在 CPU 上跑（载入时显存不够）。"
        elif st:
            used, total = st
            hogs = gpu_mem_hogs()
            msg += "显存 %d / %d MB" % (used, total)
            if hogs:
                msg += "，最占的是 " + "、".join("%s %dMB" % h for h in hogs)
            msg += "。4GB 卡被挤满时模型会被换到主记忆体，速度掉 10 倍以上。"
            need = int(C.get("min_free_vram_mb") or 0)
            if need and total - used < need:
                msg += " 已先卸载模型，下次载入会重新判断改用 CPU。"
                print(msg)
                self.unload("显存吃紧，下次载入重新选 GPU/CPU")
                return
        print(msg)

    def _back(self):
        def r():
            time.sleep(2.0)
            if S.mode == "done":
                S.mode = "idle"
        threading.Thread(target=r, daemon=True).start()


def make_tray_image(mode):
    """依状态画 tray 图示。16x16 的小图看不清形状，所以用整块颜色。"""
    from PIL import Image, ImageDraw
    col = {"idle": (140, 146, 158), "rec": (229, 72, 77),
           "trans": (245, 165, 36), "done": (48, 163, 108),
           "cancel": (110, 116, 128)}.get(mode, (140, 146, 158))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([6, 6, 58, 58], fill=col + (255,))
    # 中间挖一个麦克风形状（留白），待机时对比低、录音时最醒目
    w = (255, 255, 255, 235)
    d.rounded_rectangle([26, 16, 38, 38], radius=6, fill=w)
    d.arc([19, 26, 45, 48], start=0, end=180, fill=w, width=4)
    d.rectangle([30, 44, 34, 50], fill=w)
    return img


def setup_tray(app):
    """系统匣图示。

    左键双击 = 强制唤醒：重挂热键 + 指示灯浮上来 3 秒。
    这是「合盖回来热键没反应」的一键解法，不用再结束程式跑去桌面重开。
    右键选单：强制唤醒 / 开启语音历史 / 设定 / 状态 / 结束。
    图示颜色由 Indicator.tick 依状态即时更新（灰待机、红录音、黄转写、绿完成）。
    """
    try:
        import pystray
        import subprocess
    except ImportError as e:
        print("[匣图示] 略过（缺套件：%s）" % e)
        return None

    def _spawn(script):
        try:
            subprocess.Popen([sys.executable, os.path.join(ROOT, script)], cwd=ROOT)
        except Exception as e:
            print("[匣图示] 开启 %s 失败：%s" % (script, e))

    def wake(icon=None, item=None):
        print("[唤醒] 由 tray 手动触发")
        app.rehook()
        S.force_show_until = time.time() + 3.0

    def open_hist(icon=None, item=None):
        _spawn("history_gui.py")

    def open_settings(icon=None, item=None):
        _spawn("settings_gui.py")

    def quit_app(icon=None, item=None):
        print("[结束] 由系统匣结束")
        S.quit = True
        try:
            icon.stop()
        except Exception:
            pass

    menu = pystray.Menu(
        pystray.MenuItem("强制唤醒（重挂热键）", wake, default=True),
        pystray.MenuItem("开启语音历史", open_hist),
        pystray.MenuItem("设定", open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda i: "状态：%s" % {
            "idle": "待机中" if S.model_loaded else "载入中",
            "rec": "录音中", "trans": "转写中", "done": "刚完成",
            "cancel": "已取消"}.get(S.mode, "待机中"),
                         None, enabled=False),
        pystray.MenuItem(lambda i: "热键：%s" % C["hotkey"], None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("结束", quit_app),
    )
    icon = pystray.Icon("voicehist", make_tray_image("idle"), "voicehist - 载入中", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    print("[匣图示] 已建立（双击=强制唤醒，右键=选单）")
    return icon


def main():
    if not ensure_single_instance():
        return
    app = App()
    hk = C["hotkey"]
    print("=" * 58)
    print("  voicehist   热键：%s" % hk)
    print("  历史档：%s" % HIST)
    print("  模型：%s   语言：%s" % (C["model"], C["language"] or "自动侦测"))
    if C.get("idle_unload_minutes"):
        print("  闲置 %s 分钟自动释放显存" % C["idle_unload_minutes"])
    ck0 = C.get("cancel_key")
    if ck0:
        print("  录音中按 %s 可直接取消，不转写" % ck0.upper())
    sk = C.get("stop_key")
    if sk:
        print("  按热键开始录音；再按一次热键 或 按 %s 停止" % sk.upper())
    else:
        print("  按热键开始录音，再按一次停止")
    print("  关闭：关掉这个视窗，或按 Ctrl+C")
    print("=" * 58 + "\n")

    threading.Thread(target=app.get_model, daemon=True).start()
    threading.Thread(target=app.idle_watch, daemon=True).start()
    app.rehook()

    # tk 主回圈永远要跑：就算指示灯设成 off，睡眠侦测跟 tray 图示变色都靠 tick
    root = tk.Tk()
    ind = Indicator(root, app)
    tray = setup_tray(app)
    ind.tray = tray
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    S.quit = True
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        S.quit = True
        print("\n结束。")
