# -*- coding: utf-8 -*-
"""语音历史检视器 - 深色卡片式介面，点一下就复制到剪贴板"""
import sys, os, json, time, ctypes
import tkinter as tk

WIN_TITLE = "语音历史"


def single_instance_or_focus(mutex_name, title):
    """同一个视窗只开一份。已经开著的话就把它叫到最前面，然後结束自己。
    回传 True 表示可以继续开新视窗。"""
    k = ctypes.windll.kernel32
    k.CreateMutexW(None, False, mutex_name)
    if k.GetLastError() != 183:        # ERROR_ALREADY_EXISTS
        return True
    u = ctypes.windll.user32
    hwnd = u.FindWindowW(None, title)
    if hwnd:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)      # SW_RESTORE
        u.SetForegroundWindow(hwnd)
        # SetForegroundWindow 在非前景行程会被挡，用置顶闪一下当後备
        u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)   # HWND_TOPMOST
        u.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)   # HWND_NOTOPMOST
    return False

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.join(os.path.expanduser("~"), ".voicehist")
HIST = os.path.join(ROOT, "history.jsonl")
ICON = os.path.join(ROOT, "voicehistory.ico")

BG      = "#16181d"
CARD    = "#1e2128"
CARD_HI = "#282d38"
FG      = "#e4e7ec"
DIM     = "#868e9c"
ACCENT  = "#5c6ee8"
OK      = "#30a46c"
WARN    = "#f5a524"
FONT    = "Microsoft JhengHei UI"


def load():
    if not os.path.exists(HIST):
        return []
    out = []
    with open(HIST, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    return out[::-1]          # 最新的排最前面


class App:
    def __init__(self, root):
        self.root = root
        self.all = load()
        self.cards = []

        root.title(WIN_TITLE)
        root.configure(bg=BG)
        root.geometry("880x640")
        try:
            root.iconbitmap(ICON)
        except Exception:
            pass
        self._center()

        # ---- 顶部：标题 + 搜寻 ----
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=22, pady=(20, 12))

        tk.Label(top, text="语音历史", bg=BG, fg=FG,
                 font=(FONT, 17, "bold")).pack(side="left")
        self.count = tk.Label(top, text="", bg=BG, fg=DIM, font=(FONT, 10))
        self.count.pack(side="left", padx=(12, 0), pady=(6, 0))

        sf = tk.Frame(top, bg=CARD, highlightthickness=1,
                      highlightbackground="#2f3542", highlightcolor=ACCENT)
        sf.pack(side="right")
        tk.Label(sf, text="🔍", bg=CARD, fg=DIM,
                 font=(FONT, 10)).pack(side="left", padx=(10, 2))
        self.q = tk.StringVar()
        self.q.trace_add("write", lambda *a: self.render())
        e = tk.Entry(sf, textvariable=self.q, bg=CARD, fg=FG, insertbackground=FG,
                     relief="flat", font=(FONT, 11), width=26)
        e.pack(side="left", padx=(2, 10), pady=7)
        e.focus_set()

        # ---- 中间：可捲动卡片区 ----
        wrap = tk.Frame(root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=22)
        self.cv = tk.Canvas(wrap, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.cv.yview,
                          bg=BG, troughcolor=BG, width=11, relief="flat",
                          activebackground="#3a4150")
        self.inner = tk.Frame(self.cv, bg=BG)
        self.inner.bind("<Configure>",
                        lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        self.win = self.cv.create_window((0, 0), window=self.inner, anchor="nw")
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfig(self.win, width=e.width))
        self.cv.configure(yscrollcommand=sb.set)
        self.cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for w in (root, self.cv, self.inner):
            w.bind_all("<MouseWheel>",
                       lambda e: self.cv.yview_scroll(int(-e.delta / 120), "units"))

        # ---- 底部状态列 ----
        self.status = tk.Label(root, text="点任一则即可复制到剪贴板", bg=BG, fg=DIM,
                               font=(FONT, 10), anchor="w")
        self.status.pack(fill="x", padx=24, pady=(8, 16))

        root.bind("<Escape>", lambda e: root.destroy())
        root.bind("<F5>", lambda e: self.reload())
        self.render()

        # 盯著 history.jsonl，一有新纪录就自动长出来，不用关掉重开
        self._sig = self._file_sig()
        self.watch()

    def _center(self):
        self.root.update_idletasks()
        w, h = 880, 640
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, (sh - h) // 3))

    def _file_sig(self):
        """用 (修改时间, 档案大小) 当指纹，够便宜也够准"""
        try:
            st = os.stat(HIST)
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    def watch(self):
        """每秒看一下档案有没有变。没变就什么都不做，
        变了才重画 —— 所以平常几乎不吃资源。"""
        sig = self._file_sig()
        if sig != self._sig:
            self._sig = sig
            before = len(self.all)
            keep_pos = self.cv.yview()[0]
            self.all = load()
            self.render()
            # 本来就在最上面就留在最上面（新的排在最前，会自己跳出来）
            if keep_pos > 0.01:
                self.cv.yview_moveto(keep_pos)
            added = len(self.all) - before
            if added > 0:
                self.flash("刚讲的话已加入（+%d 则）" % added, OK)
        self.root.after(1000, self.watch)

    def reload(self):
        self.all = load()
        self._sig = self._file_sig()
        self.render()
        self.flash("已重新载入", OK)

    def flash(self, msg, color=OK):
        self.status.config(text=msg, fg=color)
        self.root.after(2600,
                        lambda: self.status.config(text="点任一则即可复制到剪贴板", fg=DIM))

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        short = text[:38] + ("…" if len(text) > 38 else "")
        self.flash("已复制：%s" % short, OK)

    def render(self):
        for c in self.cards:
            c.destroy()
        self.cards = []

        kw = self.q.get().strip().lower()
        recs = [r for r in self.all if kw in r.get("text", "").lower()] if kw else self.all

        self.count.config(text="共 %d 则%s" % (len(recs), "（已筛选）" if kw else ""))

        if not recs:
            msg = ("找不到包含「%s」的纪录" % self.q.get().strip()) if kw else \
                  "还没有任何纪录。\n\n启动 voicehist，按 Ctrl+空白 讲一句话就会出现在这里。"
            lb = tk.Label(self.inner, text=msg, bg=BG, fg=DIM,
                          font=(FONT, 12), justify="left", pady=50)
            lb.pack(fill="x")
            self.cards.append(lb)
            return

        for r in recs[:400]:
            self.cards.append(self.card(r))

    def card(self, r):
        f = tk.Frame(self.inner, bg=CARD, padx=16, pady=12,
                     highlightthickness=1, highlightbackground="#252a34")
        f.pack(fill="x", pady=(0, 9))

        head = tk.Frame(f, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=r.get("time", ""), bg=CARD, fg=DIM,
                 font=(FONT, 9)).pack(side="left")
        tk.Label(head, text="%.1fs" % r.get("seconds", 0), bg=CARD, fg=DIM,
                 font=(FONT, 9)).pack(side="left", padx=(12, 0))
        lang = r.get("lang", "")
        if lang:
            tk.Label(head, text=lang, bg=CARD, fg=ACCENT,
                     font=(FONT, 9)).pack(side="left", padx=(12, 0))
        if not r.get("pasted"):
            tk.Label(head, text="● 未贴上", bg=CARD, fg=WARN,
                     font=(FONT, 9)).pack(side="right")

        body = tk.Label(f, text=r.get("text", ""), bg=CARD, fg=FG,
                        font=(FONT, 12), justify="left", anchor="w", wraplength=700)
        body.pack(fill="x", pady=(7, 0))

        # 底列：来源视窗（左） + 复制钮（右）
        foot = tk.Frame(f, bg=CARD)
        foot.pack(fill="x", pady=(6, 0))

        win = r.get("window", "")
        sub = None
        if win:
            sub = tk.Label(foot, text="↳ %s" % win[:62], bg=CARD, fg=DIM,
                           font=(FONT, 9), anchor="w")
            sub.pack(side="left")

        btn = tk.Label(foot, text="⧉  复制", bg="#2b3140", fg="#b9c0cc",
                       font=(FONT, 9), padx=10, pady=3, cursor="hand2")
        btn.pack(side="right")

        plain = [f, head, body, foot] + list(head.winfo_children()) + ([sub] if sub else [])

        def enter(_=None):
            for w in plain:
                try:
                    w.configure(bg=CARD_HI)
                except Exception:
                    pass
            f.configure(highlightbackground=ACCENT)
            if btn._state == "idle":
                btn.configure(bg="#39404f")

        def leave(_=None):
            for w in plain:
                try:
                    w.configure(bg=CARD)
                except Exception:
                    pass
            f.configure(highlightbackground="#252a34")
            if btn._state == "idle":
                btn.configure(bg="#2b3140")

        btn._state = "idle"

        def do_copy(_=None):
            text = r.get("text", "")
            self.copy(text)
            # 按钮反白约 1.8 秒，让人明确知道按到了
            btn._state = "hit"
            btn.configure(text="✓  已复制", bg=OK, fg="#ffffff")

            def restore():
                btn._state = "idle"
                btn.configure(text="⧉  复制", bg="#2b3140", fg="#b9c0cc")
            self.root.after(1800, restore)

        for w in plain:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", do_copy)
        btn.bind("<Button-1>", do_copy)
        btn.bind("<Enter>", lambda e: btn.configure(bg="#454d5f")
                 if btn._state == "idle" else None)
        btn.bind("<Leave>", lambda e: btn.configure(bg="#39404f")
                 if btn._state == "idle" else None)
        return f


if __name__ == "__main__":
    if single_instance_or_focus("voicehist_history_gui_mutex_v1", WIN_TITLE):
        root = tk.Tk()
        App(root)
        root.mainloop()
