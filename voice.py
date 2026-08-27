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
    """同一时间只跑一份。重复开启时直接跳提示并结束，不会开出第二个视窗。"""
    k = ctypes.windll.kernel32
    k.CreateMutexW(None, False, "voicehist_singleton_mutex_v1")
    if k.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
        ctypes.windll.user32.MessageBoxW(
            0,
            "voicehist 已经在执行中了。\n\n"
            "萤幕右下角那个小指示灯就是它，直接按 Ctrl+空白 就能用。\n\n"
            "要重开的话，先关掉原本那个再启动。",
            "voicehist", 0x40)
        return False
    return True

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
from faster_whisper import WhisperModel

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
    "indicator": True,
    "indicator_pos": "br",
    "ui_scale": 2.1,        # 高度／字体倍率（原本 3.0 的 70%）
    "ui_width_scale": 1.5,  # 宽度倍率（原本 3.0 的一半）
    "stop_key": "esc",      # 录音中额外的停止键；设成 null 可关掉
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


class S:
    """共享状态：UI 只轮询这些值，不跨线程直接碰 tkinter"""
    mode = "idle"
    level = 0.0
    t0 = 0.0
    msg = ""
    model_loaded = False
    quit = False


class Indicator:
    COLORS = {"idle": "#5a5f66", "rec": "#e5484d", "trans": "#f5a524", "done": "#30a46c"}

    def __init__(self, root):
        self.root = root
        sh = float(C.get("ui_scale", 2.1))          # 高度／字体
        sw = float(C.get("ui_width_scale", 1.5))    # 宽度
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
        # 字元数随宽高比一起缩，避免字体放大后把视窗撑宽
        chars = max(6, int(round(13 * sw / sh)))
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
        m = S.mode
        if m == "rec":
            # 常亮，不闪烁
            self.dot.itemconfig(self.oval, fill=self.COLORS["rec"])
            self.txt.config(text="录音中  %.1fs" % (time.time() - S.t0))
            self.bar.coords(self.fill, 0, 0,
                            min(self.barw, S.level * 120 * self.s), self.barh)
        else:
            self.dot.itemconfig(self.oval, fill=self.COLORS[m])
            if m == "idle":
                self.txt.config(text="待机" if S.model_loaded else "载入中…")
            elif m == "trans":
                self.txt.config(text="转写中…")
            else:
                self.txt.config(text=(S.msg[:12] or "完成"))
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
    text = "".join(out).strip()
    # Whisper 常在结尾多留一个逗号／顿号，清掉
    while text and text[-1] in "，,、 ":
        text = text[:-1].rstrip()
    return text


def save_history(text, lang, dur, title, pasted):
    rec = {"ts": int(time.time()), "time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "text": text, "lang": lang, "seconds": round(dur, 1),
           "window": title, "pasted": pasted}
    with open(HIST, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class App:
    def __init__(self):
        self.rec = Rec(C["sample_rate"])
        self.on = False
        self.lock = threading.Lock()
        self.model = None
        self.last_use = time.time()

    def get_model(self):
        if self.model is None:
            print("[载入] %s 模型…" % C["model"])
            try:
                self.model = WhisperModel(C["model"], device="cuda", compute_type="float16")
                print("[载入] %s @ GPU" % C["model"])
            except Exception as e:
                print("[载入] GPU 不可用（%s），改 CPU：%s" % (type(e).__name__, str(e)[:100]))
                self.model = WhisperModel(C["model"], device="cpu", compute_type="int8")
                print("[载入] %s @ CPU" % C["model"])
            S.model_loaded = True
        self.last_use = time.time()
        return self.model

    def idle_watch(self):
        mins = C.get("idle_unload_minutes", 0)
        if not mins:
            return
        while not S.quit:
            time.sleep(20)
            if (self.model is not None and S.mode == "idle"
                    and time.time() - self.last_use > mins * 60):
                print("[卸载] 闲置超过 %s 分钟，释放显存（下次按热键自动载回）" % mins)
                self.model = None
                S.model_loaded = False
                import gc
                gc.collect()

    def toggle(self):
        with self.lock:
            if not self.on:
                self.on = True
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

    def stop_if_recording(self, _event=None):
        """停止键（预设 ESC）：只在录音中才动作，
        平时完全不拦截，ESC 在别的程式照常用。"""
        if self.on:
            self.toggle()

    def work(self, audio, dur, title):
        if audio.size < C["sample_rate"] * 0.3:
            S.msg = "太短"
            S.mode = "done"
            self._back()
            print("  → 太短，忽略\n")
            return
        try:
            m = self.get_model()
            segs, info = m.transcribe(audio, language=C["language"],
                                      beam_size=int(C.get("beam_size", 1)),
                                      initial_prompt=C.get("initial_prompt") or None,
                                      vad_filter=True,
                                      vad_parameters={"min_silence_duration_ms": 500})
            text = join_segments(segs)
            lang = info.language
        except Exception as e:
            S.msg = "转写失败"
            S.mode = "done"
            self._back()
            print("  → 转写失败：%s\n" % e)
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

        save_history(text, lang, dur, title, pasted)
        self.last_use = time.time()
        S.msg = text
        S.mode = "done"
        self._back()
        print("  → [%s] %s" % (lang, text))
        print("     %s ｜ %s\n" % ("已贴上" if pasted else "未贴上（已存历史+剪贴板）", title[:40]))

    def _back(self):
        def r():
            time.sleep(2.0)
            if S.mode == "done":
                S.mode = "idle"
        threading.Thread(target=r, daemon=True).start()


def setup_tray():
    """系统匣图示：右键可开历史、结束程式。左键双击直接开历史。"""
    try:
        import pystray
        from PIL import Image
        import subprocess
    except ImportError as e:
        print("[匣图示] 略过（缺套件：%s）" % e)
        return None

    ico = os.path.join(ROOT, "voiceinput.ico")
    img = None
    if os.path.exists(ico):
        try:
            img = Image.open(ico)
        except Exception:
            pass
    if img is None:
        from PIL import Image as _I, ImageDraw as _D
        img = _I.new("RGBA", (64, 64), (0, 0, 0, 0))
        _D.Draw(img).ellipse([8, 8, 56, 56], fill=(92, 106, 232, 255))

    def _spawn(script):
        try:
            subprocess.Popen([sys.executable, os.path.join(ROOT, script)], cwd=ROOT)
        except Exception as e:
            print("[匣图示] 开启 %s 失败：%s" % (script, e))

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
        pystray.MenuItem("开启语音历史", open_hist, default=True),
        pystray.MenuItem("设定", open_settings),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda i: "状态：%s" % {
            "idle": "待机中" if S.model_loaded else "载入中",
            "rec": "录音中", "trans": "转写中", "done": "刚完成"}.get(S.mode, "待机中"),
                         None, enabled=False),
        pystray.MenuItem(lambda i: "热键：%s" % C["hotkey"], None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("结束", quit_app),
    )
    icon = pystray.Icon("voicehist", img, "voicehist 语音输入", menu)
    threading.Thread(target=icon.run, daemon=True).start()
    print("[匣图示] 已建立（右下角系统匣，右键可结束）")
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
    sk = C.get("stop_key")
    if sk:
        print("  按热键开始录音；再按一次热键 或 按 %s 停止" % sk.upper())
    else:
        print("  按热键开始录音，再按一次停止")
    print("  关闭：关掉这个视窗，或按 Ctrl+C")
    print("=" * 58 + "\n")

    threading.Thread(target=app.get_model, daemon=True).start()
    threading.Thread(target=app.idle_watch, daemon=True).start()
    keyboard.add_hotkey(hk, app.toggle, suppress=False)
    if sk:
        keyboard.on_press_key(sk, app.stop_if_recording, suppress=False)
    tray = setup_tray()

    if C.get("indicator", True):
        root = tk.Tk()
        Indicator(root)
        try:
            root.mainloop()
        except KeyboardInterrupt:
            pass
        S.quit = True
    else:
        while not S.quit:
            time.sleep(0.3)
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
