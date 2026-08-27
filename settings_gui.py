# -*- coding: utf-8 -*-
"""voicehist 设定视窗 - 改完存档，重启主程式即生效"""
import sys, os, json, ctypes
import tkinter as tk

WIN_TITLE = "voicehist 设定"


def single_instance_or_focus(mutex_name, title):
    """同一个视窗只开一份。已经开著的话就把它叫到最前面，然後结束自己。"""
    k = ctypes.windll.kernel32
    k.CreateMutexW(None, False, mutex_name)
    if k.GetLastError() != 183:        # ERROR_ALREADY_EXISTS
        return True
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    if hwnd:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)
        u.SetForegroundWindow(hwnd)
        u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)
        u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)
    return False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.join(os.path.expanduser("~"), ".voicehist")
CFG = os.path.join(ROOT, "config.json")
ICON = os.path.join(ROOT, "voiceinput.ico")

BG, CARD, FG, DIM = "#16181d", "#1e2128", "#e4e7ec", "#868e9c"
ACCENT, OK = "#5c6ee8", "#30a46c"
FONT = "Microsoft JhengHei UI"

# (key, 标题, 说明, 型别, 选项)
FIELDS = [
    ("__g", "热键", None, None, None),
    ("hotkey", "启动／停止录音", "预设 ctrl+space。若跟中文输入法的中英切换打架，改成 ctrl+alt+space",
     "text", None),
    ("stop_key", "停止并转写（单键）", "录音中按这个就结束并转写，不用按组合键。留空则关闭",
     "text", None),
    ("cancel_key", "取消这一段", "误触时按这个直接丢弃，不转写、不进剪贴板、不写历史。" + chr(10) + "只在录音中或转写中生效，平时完全不拦截", "text", None),

    ("__g", "效能", None, None, None),
    ("idle_unload_minutes", "闲置几分钟释放显存",
     "0 = 一直常驻（反应最快）。5 = 闲置 5 分钟後把模型踢出显存，下次按热键约 2.4 秒载回", "int", None),
    ("model", "模型档次", "medium 是 4GB 显卡的甜蜜点；small 较快但中文会掉准", "choice",
     ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]),
    ("beam_size", "beam size", "1 最快。实测调大只会变慢，文字不会变好", "int", None),

    ("__g", "语音与断句", None, None, None),
    ("language", "辨识语言", "留空 = 自动侦测（中英混用建议留空）。也可填 zh 或 en 强制", "text", None),
    ("initial_prompt", "引导句",
     "喂一句带标点的范例，Whisper 就会照著加标点、也会偏向繁体。留空则不引导", "text", None),
    ("pause_space", "停顿多久插空格", "单位秒，预设 0.35", "float", None),
    ("pause_newline", "停顿多久换行", "单位秒，预设 1.0", "float", None),
    ("fix_punctuation", "修正滥用的全形冒号",
     "Whisper 会模仿引导句里的标点，有时整段乱塞冒号。开启後句尾的全形冒号" + chr(10) + "换成句号、句中换逗号。半形冒号不动（时间 10:30、网址、key: value 需要它）",
     "bool", None),

    ("__g", "行为", None, None, None),
    ("auto_paste", "转写完自动贴上", "关掉的话不会主动贴，要自己去历史视窗复制", "bool", None),
    ("clipboard", "剪贴板策略",
     "restore = 贴上後还原成你原本复制的东西（推荐，不会洗掉你的剪贴板）\n"
     "keep = 转写结果留在剪贴板里\n"
     "none = 完全不碰剪贴板，也不自动贴上，只写历史",
     "choice", ["restore", "keep", "none"]),
    ("restore_delay", "还原前等待秒数", "太短可能在贴上完成前就还原了，预设 0.35", "float", None),
    ("beep", "提示音", "开始／停止录音时的哔声", "bool", None),

    ("__g", "指示灯", None, None, None),
    ("indicator", "显示浮动指示灯", "关掉就只剩系统匣图示", "bool", None),
    ("ui_scale", "高度／字体倍率", "预设 2.1", "float", None),
    ("ui_width_scale", "宽度倍率", "预设 1.5", "float", None),
    ("indicator_pos", "指示灯位置", "br=右下 bl=左下 tr=右上 tl=左上（也可直接用滑鼠拖曳）",
     "choice", ["br", "bl", "tr", "tl"]),
]


def load():
    try:
        return json.load(open(CFG, encoding="utf-8"))
    except Exception:
        return {}


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load()
        self.vars = {}

        root.title(WIN_TITLE)
        root.configure(bg=BG)
        root.geometry("760x720")
        try:
            root.iconbitmap(ICON)
        except Exception:
            pass
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry("760x720+%d+%d" % ((sw - 760) // 2, max(0, (sh - 720) // 3)))

        head = tk.Frame(root, bg=BG)
        head.pack(fill="x", padx=24, pady=(20, 6))
        tk.Label(head, text="设定", bg=BG, fg=FG, font=(FONT, 17, "bold")).pack(side="left")
        tk.Label(head, text="改完按储存，重新启动 voicehist 生效",
                 bg=BG, fg=DIM, font=(FONT, 9)).pack(side="left", padx=(12, 0), pady=(7, 0))

        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=24, pady=(6, 0))
        cv = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=cv.yview, width=11,
                          bg=BG, troughcolor=BG, relief="flat")
        inner = tk.Frame(cv, bg=BG)
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        w = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.bind("<Configure>", lambda e: cv.itemconfig(w, width=e.width))
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        cv.bind_all("<MouseWheel>", lambda e: cv.yview_scroll(int(-e.delta / 120), "units"))

        for key, title, desc, kind, opts in FIELDS:
            if key == "__g":
                tk.Label(inner, text=title, bg=BG, fg=ACCENT,
                         font=(FONT, 11, "bold")).pack(fill="x", pady=(16, 6), anchor="w")
                continue
            self.row(inner, key, title, desc, kind, opts)

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=24, pady=14)
        self.msg = tk.Label(bar, text="", bg=BG, fg=OK, font=(FONT, 10))
        self.msg.pack(side="left")
        tk.Button(bar, text="回复预设值", command=self.reset, bg="#2b3140", fg=FG,
                  font=(FONT, 10), relief="flat", padx=14, pady=6,
                  activebackground="#39404f", activeforeground=FG,
                  cursor="hand2").pack(side="right", padx=(8, 0))
        tk.Button(bar, text="储存", command=self.save, bg=ACCENT, fg="#ffffff",
                  font=(FONT, 10, "bold"), relief="flat", padx=22, pady=6,
                  activebackground="#4a5ad4", activeforeground="#ffffff",
                  cursor="hand2").pack(side="right")

        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<Control-s>", lambda e: self.save())

    def row(self, parent, key, title, desc, kind, opts):
        f = tk.Frame(parent, bg=CARD, padx=14, pady=10)
        f.pack(fill="x", pady=(0, 7))
        left = tk.Frame(f, bg=CARD)
        left.pack(side="left", fill="x", expand=True)
        tk.Label(left, text=title, bg=CARD, fg=FG, font=(FONT, 11),
                 anchor="w").pack(fill="x")
        if desc:
            tk.Label(left, text=desc, bg=CARD, fg=DIM, font=(FONT, 9),
                     anchor="w", justify="left", wraplength=470).pack(fill="x", pady=(3, 0))

        cur = self.cfg.get(key)
        if kind == "bool":
            v = tk.BooleanVar(value=bool(cur))
            tk.Checkbutton(f, variable=v, bg=CARD, activebackground=CARD,
                           selectcolor="#2b3140", highlightthickness=0, bd=0,
                           cursor="hand2").pack(side="right")
        elif kind == "choice":
            v = tk.StringVar(value=str(cur) if cur is not None else opts[0])
            om = tk.OptionMenu(f, v, *opts)
            om.configure(bg="#2b3140", fg=FG, font=(FONT, 10), relief="flat",
                         highlightthickness=0, activebackground="#39404f",
                         width=14, cursor="hand2")
            om["menu"].configure(bg=CARD, fg=FG, font=(FONT, 10),
                                 activebackground=ACCENT)
            om.pack(side="right")
        else:
            v = tk.StringVar(value="" if cur is None else str(cur))
            tk.Entry(f, textvariable=v, bg="#2b3140", fg=FG, insertbackground=FG,
                     relief="flat", font=(FONT, 10), width=22).pack(side="right",
                                                                    ipady=4)
        self.vars[key] = (v, kind)

    def save(self):
        bad = []
        for key, (v, kind) in self.vars.items():
            raw = v.get()
            try:
                if kind == "bool":
                    self.cfg[key] = bool(raw)
                elif kind == "int":
                    self.cfg[key] = int(str(raw).strip() or 0)
                elif kind == "float":
                    self.cfg[key] = float(str(raw).strip() or 0)
                else:
                    s = str(raw).strip()
                    self.cfg[key] = s if s else None
            except ValueError:
                bad.append(key)
        if bad:
            self.msg.config(text="这些栏位格式不对：%s" % "、".join(bad), fg="#e5484d")
            return
        try:
            json.dump(self.cfg, open(CFG, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            self.msg.config(text="已储存 — 重新启动 voicehist 後生效", fg=OK)
        except Exception as e:
            self.msg.config(text="储存失败：%s" % e, fg="#e5484d")

    def reset(self):
        try:
            os.remove(CFG)
        except Exception:
            pass
        self.msg.config(text="已回复预设值 — 重新启动 voicehist 後生效，"
                             "重开这个视窗可看到新值", fg=OK)


if __name__ == "__main__":
    if single_instance_or_focus("voicehist_settings_gui_mutex_v1", WIN_TITLE):
        root = tk.Tk()
        App(root)
        root.mainloop()
