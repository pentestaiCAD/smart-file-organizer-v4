"""Smart File Organizer 4.0 — "Findable First" — dark UI over the engine.

Tabs
  Desk      the few files that matter right now (recent + frequently opened +
            pinned), always visible, one click to open. A VIEW, never re-sorted.
  Find      type any word -> filename + content + AI-description search. The
            safety net: if sorting ever fails you, this catches it.
  Tidy      preview -> pick/edit -> organize a messy folder into the six
            home-root folders. Nothing moves without a preview you can change.
  Downloads notify-only watch of your Downloads landing strip + Clear-on-demand.
  Settings  home root, buckets, optional AI provider, naming, re-run wizard.

A persistent "Undo last organize" button lives in the header (Rule 9), not
buried in a menu. First launch runs a short setup wizard.

Run:  python organizer_gui.py     (or double-click Organizer.bat)
"""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import app_config
import ai_provider
import classify as classify_mod
import desk as desk_mod
import engine
import index as index_mod
import pins as pins_mod
import usage

BG = "#12121a"; PANEL = "#1c1c28"; PANEL2 = "#232333"
FG = "#e6e6f0"; DIM = "#8a8aa3"; GREEN = "#4ade80"; YELLOW = "#facc15"
RED = "#f87171"; ACCENT = "#7c6cff"
FONT = ("Segoe UI", 10); FONT_B = ("Segoe UI Semibold", 10)
FONT_H = ("Segoe UI Semibold", 14); FONT_M = ("Consolas", 9)
CAT_COLORS = ["#7c6cff", "#4ade80", "#38bdf8", "#facc15", "#f87171", "#f472b6"]
ICON_PATH = app_config.bundled_dir() / "organizer.ico"


def _style(root: tk.Misc) -> ttk.Style:
    s = ttk.Style(root)
    try:
        s.theme_use("clam")
    except tk.TclError:
        pass
    s.configure("TFrame", background=BG)
    s.configure("Panel.TFrame", background=PANEL)
    s.configure("TLabel", background=BG, foreground=FG, font=FONT)
    s.configure("Panel.TLabel", background=PANEL, foreground=FG, font=FONT)
    s.configure("Dim.TLabel", background=PANEL, foreground=DIM, font=FONT_M)
    s.configure("DimBg.TLabel", background=BG, foreground=DIM, font=FONT_M)
    s.configure("H.TLabel", background=BG, foreground=FG, font=FONT_H)
    s.configure("Good.TLabel", background=PANEL, foreground=GREEN, font=FONT_B)
    s.configure("Bad.TLabel", background=PANEL, foreground=RED, font=FONT_B)
    s.configure("TButton", font=FONT_B, padding=(12, 6))
    s.configure("Accent.TButton", font=FONT_B, padding=(12, 6), background=ACCENT, foreground="white")
    s.map("Accent.TButton", background=[("active", "#8f81ff")])
    s.configure("Warn.TButton", font=FONT_B, padding=(12, 6))
    for cb in ("TCheckbutton", "TRadiobutton"):
        s.configure(cb, background=PANEL, foreground=FG, font=FONT)
        s.map(cb, background=[("active", PANEL)])
    s.configure("TNotebook", background=BG, borderwidth=0)
    s.configure("TNotebook.Tab", background=PANEL, foreground=DIM, font=FONT_B, padding=(16, 8))
    s.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", "white")])
    s.configure("Treeview", background=PANEL2, foreground=FG, fieldbackground=PANEL2,
                font=FONT, rowheight=24, borderwidth=0)
    s.configure("Treeview.Heading", background=PANEL, foreground=DIM, font=FONT_B, borderwidth=0)
    s.map("Treeview", background=[("selected", ACCENT)])
    s.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=FG)
    return s


def _icon(win: tk.Misc) -> None:
    if ICON_PATH.exists():
        try:
            win.iconbitmap(str(ICON_PATH))
        except tk.TclError:
            pass


def _entry(parent, var, width=None, show=None) -> tk.Entry:
    e = tk.Entry(parent, textvariable=var, bg=PANEL2, fg=FG, insertbackground=FG,
                 relief="flat", font=FONT, show=show)
    if width:
        e.configure(width=width)
    return e


def _open_file(path: str) -> None:
    try:
        usage.record_open(path)
        os.startfile(path)                      # noqa: S606 - user-initiated open
    except OSError as exc:
        messagebox.showerror("Open", f"Couldn't open:\n{exc}")


# ==========================================================================
# First-run setup wizard
# ==========================================================================

class SetupWizard(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Smart File Organizer — Setup")
        self.configure(bg=BG); self.geometry("640x540"); self.resizable(False, False)
        _style(self); _icon(self)
        self.cfg = app_config.load()
        self.home_var = tk.StringVar(value=self.cfg.get("home_root") or str(app_config.default_home_root()))
        self.downloads_var = tk.StringVar(value=self.cfg["downloads"].get("folder") or str(app_config.default_downloads_folder()))
        self.watch_var = tk.BooleanVar(value=True)
        self.provider_var = tk.StringVar(value=self.cfg["ai"].get("provider", "none"))
        self.key_var = tk.StringVar(value=""); self.model_var = tk.StringVar(value="")
        self.finished = False
        self.pages = [self._welcome, self._folders, self._ai, self._finish]
        self.index = 0
        self.body = ttk.Frame(self); self.body.pack(fill="both", expand=True, padx=28, pady=(24, 8))
        nav = ttk.Frame(self); nav.pack(fill="x", padx=28, pady=(0, 20))
        self.back = ttk.Button(nav, text="< Back", command=self._go_back); self.back.pack(side="left")
        ttk.Button(nav, text="Skip", command=self._skip).pack(side="left", padx=8)
        self.next = ttk.Button(nav, text="Next >", style="Accent.TButton", command=self._go_next)
        self.next.pack(side="right")
        self._render(); self.protocol("WM_DELETE_WINDOW", self._skip)

    def _render(self):
        for w in self.body.winfo_children():
            w.destroy()
        self.pages[self.index]()
        self.back.configure(state="disabled" if self.index == 0 else "normal")
        self.next.configure(text="Finish" if self.index == len(self.pages) - 1 else "Next >")

    def _go_next(self):
        if self.index == len(self.pages) - 1:
            return self._finish_save()
        self.index += 1; self._render()

    def _go_back(self):
        self.index = max(0, self.index - 1); self._render()

    def _skip(self):
        self.cfg["setup_complete"] = True
        self.cfg["home_root"] = self.cfg.get("home_root") or str(app_config.default_home_root())
        self.cfg["downloads"]["folder"] = self.cfg["downloads"].get("folder") or str(app_config.default_downloads_folder())
        app_config.save(self.cfg); self.finished = True; self.destroy()

    def _finish_save(self):
        self.cfg["home_root"] = self.home_var.get().strip() or str(app_config.default_home_root())
        self.cfg["downloads"]["folder"] = self.downloads_var.get().strip()
        self.cfg["downloads"]["watch_enabled"] = self.watch_var.get()
        prov = self.provider_var.get()
        self.cfg["ai"]["provider"] = prov
        self.cfg["ai"]["api_key"] = self.key_var.get().strip()
        self.cfg["ai"]["model"] = self.model_var.get().strip()
        self.cfg["ai"]["enabled"] = prov != "none"
        self.cfg["setup_complete"] = True
        app_config.save(self.cfg)
        try:
            Path(self.cfg["home_root"]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.finished = True; self.destroy()

    def _welcome(self):
        ttk.Label(self.body, text="◈ Smart File Organizer", style="H.TLabel").pack(anchor="w")
        ttk.Label(self.body, text="4.0 — Findable First", foreground=ACCENT, background=BG, font=FONT_H).pack(anchor="w", pady=(0, 14))
        msg = ("A desk, not a cabinet.\n\n"
               "• Instead of deep-sorting everything, it surfaces the few files "
               "you actually use on a always-visible _DESK.\n\n"
               "• Everything else goes into at most six plain folders, never more "
               "than two levels deep.\n\n"
               "• A Find box searches names AND contents, so nothing is ever lost.\n\n"
               "• Every organize is one-click undoable. Pinned files are never touched.")
        ttk.Label(self.body, text=msg, wraplength=560, justify="left").pack(anchor="w")

    def _folders(self):
        ttk.Label(self.body, text="Folders", style="H.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Label(self.body, text="Home — where the six folders live", style="DimBg.TLabel").pack(anchor="w")
        r1 = ttk.Frame(self.body); r1.pack(fill="x", pady=(4, 16))
        _entry(r1, self.home_var).pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(r1, text="Browse…", command=lambda: self._browse(self.home_var)).pack(side="left", padx=(6, 0))
        ttk.Label(self.body, text="Downloads — a landing strip that's watched but never auto-sorted",
                  style="DimBg.TLabel").pack(anchor="w")
        r2 = ttk.Frame(self.body); r2.pack(fill="x", pady=(4, 12))
        _entry(r2, self.downloads_var).pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(r2, text="Browse…", command=lambda: self._browse(self.downloads_var)).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(self.body, text="Watch Downloads (notify only — nothing auto-moves)",
                        variable=self.watch_var).pack(anchor="w")

    def _ai(self):
        ttk.Label(self.body, text="AI (optional)", style="H.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Label(self.body, text="Used only to suggest better names and describe files for search — "
                  "never to guess which folder a file belongs in. Everything works offline on “None”.",
                  wraplength=560, justify="left", style="DimBg.TLabel").pack(anchor="w", pady=(0, 14))
        row = ttk.Frame(self.body); row.pack(fill="x", pady=(0, 10))
        ttk.Label(row, text="Provider").pack(side="left")
        opts = ["none"] + list(ai_provider.PROVIDERS.keys())
        labels = {"none": "None"}; labels.update({k: v["label"] for k, v in ai_provider.PROVIDERS.items()})
        combo = ttk.Combobox(row, state="readonly", width=36, values=[labels[o] for o in opts])
        combo.set(labels[self.provider_var.get()]); combo.pack(side="left", padx=8)
        kr = ttk.Frame(self.body); kr.pack(fill="x", pady=6)
        ttk.Label(kr, text="API key").pack(side="left")
        ke = _entry(kr, self.key_var, show="*"); ke.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        mr = ttk.Frame(self.body); mr.pack(fill="x", pady=6)
        ttk.Label(mr, text="Model").pack(side="left")
        _entry(mr, self.model_var).pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        hint = ttk.Label(self.body, text="", wraplength=560, justify="left", style="DimBg.TLabel"); hint.pack(anchor="w", pady=(4, 0))

        def pick(_e=None):
            key = next((o for o in opts if labels[o] == combo.get()), "none")
            self.provider_var.set(key)
            meta = ai_provider.PROVIDERS.get(key)
            if meta:
                if not self.model_var.get():
                    self.model_var.set(meta["default_model"])
                hint.configure(text=meta["hint"]); ke.configure(state="normal" if meta["needs_key"] else "disabled")
            else:
                hint.configure(text=""); ke.configure(state="disabled")
        combo.bind("<<ComboboxSelected>>", pick); pick()

    def _finish(self):
        ttk.Label(self.body, text="All set", style="H.TLabel").pack(anchor="w", pady=(0, 12))
        prov = self.provider_var.get()
        ai_line = "off" if prov == "none" else ai_provider.PROVIDERS[prov]["label"]
        for line in (f"Home: {self.home_var.get()}",
                     f"Downloads: {self.downloads_var.get()}  (watch: {'on' if self.watch_var.get() else 'off'})",
                     f"AI: {ai_line}"):
            ttk.Label(self.body, text=f"• {line}", wraplength=560, justify="left").pack(anchor="w", pady=2)
        ttk.Label(self.body, text="\nClick Finish to open Smart File Organizer.", style="DimBg.TLabel").pack(anchor="w", pady=(16, 0))

    def _browse(self, var):
        chosen = filedialog.askdirectory(parent=self, initialdir=var.get() or str(Path.home()))
        if chosen:
            var.set(chosen)


def run_setup_wizard() -> bool:
    w = SetupWizard(); w.mainloop(); return w.finished


# ==========================================================================
# Downloads watcher — detects, never moves
# ==========================================================================

class DownloadsWatcher(threading.Thread):
    def __init__(self, folder, rules, cfg, on_new, on_error, interval=3.0):
        super().__init__(daemon=True)
        self.folder = Path(folder); self.rules = rules; self.cfg = cfg
        self.on_new = on_new; self.on_error = on_error; self.interval = interval
        self._stop = threading.Event(); self._sizes = {}; self._announced = set()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            for p in self.folder.glob("*"):
                if p.is_file():
                    self._announced.add(str(p))
        except OSError as exc:
            self.on_error(str(exc)); return
        while not self._stop.is_set():
            try:
                current = {str(p): p for p in self.folder.glob("*")
                           if p.is_file() and p.name.lower() not in engine.NEVER_TOUCH}
            except OSError as exc:
                self.on_error(str(exc)); self._stop.wait(self.interval); continue
            for key, p in current.items():
                if key in self._announced:
                    continue
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if self._sizes.get(key) == size:
                    self._announced.add(key)
                    d = classify_mod.classify(p, self.rules, self.cfg)
                    label = d.bucket if d.action == "route" else app_config.UNSORTED
                    self.on_new(p, label)
                else:
                    self._sizes[key] = size
            for key in set(self._sizes) - set(current):
                self._sizes.pop(key, None); self._announced.discard(key)
            self._stop.wait(self.interval)


# ==========================================================================
# Main window
# ==========================================================================

class OrganizerGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{app_config.APP_NAME} {app_config.APP_VERSION}")
        self.configure(bg=BG); self.geometry("960x740"); self.minsize(820, 620)
        _style(self); _icon(self)
        self.cfg = app_config.load()
        self._q: queue.Queue = queue.Queue()
        self._planned: list[tuple[tk.BooleanVar, engine.PlannedMove, str]] = []
        self._find_rows: dict[str, dict] = {}
        self._desk_rows: dict[str, dict] = {}
        self._watcher: DownloadsWatcher | None = None
        self._pending: list = []

        self.source = tk.StringVar(value=str(app_config.default_desktop_folder()))
        self.include_sub = tk.BooleanVar(value=False)
        self.find_dupes = tk.BooleanVar(value=False)
        self.downloads_folder = tk.StringVar(value=self.cfg["downloads"].get("folder") or str(app_config.default_downloads_folder()))
        self.watch_enabled = tk.BooleanVar(value=self.cfg["downloads"].get("watch_enabled", False))
        self.query_var = tk.StringVar(value="")
        # AI settings vars
        self.ai_enabled = tk.BooleanVar(value=self.cfg["ai"].get("enabled", False))
        self.ai_provider_var = tk.StringVar(value=self.cfg["ai"].get("provider", "none"))
        self.ai_key_var = tk.StringVar(value=self.cfg["ai"].get("api_key", ""))
        self.ai_model_var = tk.StringVar(value=self.cfg["ai"].get("model", ""))
        self.ai_base_var = tk.StringVar(value=self.cfg["ai"].get("base_url", ""))
        self.ai_test_var = tk.StringVar(value="")
        self.home_var = tk.StringVar(value=self.cfg.get("home_root") or str(app_config.default_home_root()))

        self._header()
        self.nb = ttk.Notebook(self); self.nb.pack(fill="both", expand=True, padx=14, pady=(4, 14))
        self._desk_tab(); self._find_tab(); self._tidy_tab(); self._downloads_tab(); self._settings_tab()
        self.after(200, self._poll)
        self.after(300, self.refresh_desk)
        self._refresh_undo()
        if self.watch_enabled.get():
            self.after(500, self._start_watch)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- header + undo ----
    def _header(self):
        top = ttk.Frame(self); top.pack(fill="x", padx=18, pady=(16, 4))
        ttk.Label(top, text="◈ SMART FILE ORGANIZER", style="H.TLabel").pack(side="left")
        ttk.Label(top, text=app_config.APP_VERSION, style="H.TLabel", foreground=ACCENT).pack(side="left", padx=(6, 0))
        self.undo_btn = ttk.Button(top, text="↩ Undo last organize", style="Warn.TButton", command=self.undo_last)
        self.undo_btn.pack(side="right")
        self.stats = ttk.Label(top, text="", style="H.TLabel", foreground=DIM)
        self.stats.pack(side="right", padx=12)

    def _refresh_undo(self):
        summ = engine.last_batch_summary()
        if summ:
            self.undo_btn.configure(text=f"↩ Undo last organize  ({summ})", state="normal")
        else:
            self.undo_btn.configure(text="↩ Undo last organize", state="disabled")

    def undo_last(self):
        if not messagebox.askyesno("Undo", "Restore the most recent organize batch exactly?", parent=self):
            return
        def work():
            res = engine.undo(self.cfg, log=lambda s: self._q.put((self._log_tidy, s)))
            self._q.put((self._after_undo, res))
        threading.Thread(target=work, daemon=True).start()

    def _after_undo(self, res):
        self._refresh_undo(); self.refresh_desk()
        if not res.get("empty"):
            messagebox.showinfo("Undo", f"Restored {res['restored']} file(s) from {res.get('when','')}.", parent=self)

    def _poll(self):
        try:
            while True:
                fn, payload = self._q.get_nowait(); fn(payload)
        except queue.Empty:
            pass
        self.after(200, self._poll)

    def _current_ai_cfg(self):
        if not self.ai_enabled.get() or self.ai_provider_var.get() == "none":
            return None
        return {"provider": self.ai_provider_var.get(), "api_key": self.ai_key_var.get(),
                "model": self.ai_model_var.get(), "base_url": self.ai_base_var.get(), "enabled": True}

    # ================= Desk tab =================
    def _desk_tab(self):
        tab = ttk.Frame(self.nb); self.nb.add(tab, text="  🗂 Desk  ")
        bar = ttk.Frame(tab, style="Panel.TFrame"); bar.pack(fill="x", padx=4, pady=(10, 6))
        ttk.Label(bar, text="The few files that matter right now — recent, frequently opened, or pinned. "
                  "These are shortcuts; the real files stay in their folders.",
                  style="Dim.TLabel", wraplength=880).pack(anchor="w", padx=12, pady=8)
        btns = ttk.Frame(tab, style="Panel.TFrame"); btns.pack(fill="x", padx=4)
        ttk.Button(btns, text="Open", command=self._desk_open).pack(side="left", padx=(12, 4), pady=8)
        ttk.Button(btns, text="Unpin", command=self._desk_unpin).pack(side="left", padx=4)
        ttk.Button(btns, text="↻ Refresh", command=self.refresh_desk).pack(side="right", padx=12)
        frame = ttk.Frame(tab, style="Panel.TFrame"); frame.pack(fill="both", expand=True, padx=4, pady=6)
        cols = ("where", "opened")
        self.desk_tree = ttk.Treeview(frame, columns=cols, show="tree headings")
        self.desk_tree.heading("#0", text="File"); self.desk_tree.heading("where", text="Lives in"); self.desk_tree.heading("opened", text="Last opened")
        self.desk_tree.column("#0", width=420); self.desk_tree.column("where", width=200); self.desk_tree.column("opened", width=160)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.desk_tree.yview)
        self.desk_tree.configure(yscrollcommand=sb.set)
        self.desk_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=10)
        sb.pack(side="right", fill="y", pady=10, padx=(0, 12))
        self.desk_tree.bind("<Double-1>", lambda e: self._desk_open())

    def refresh_desk(self):
        def work():
            built = desk_mod.rebuild(self.cfg)
            self._q.put((self._render_desk, built["desk_items"]))
        threading.Thread(target=work, daemon=True).start()

    def _render_desk(self, items):
        import time
        self.desk_tree.delete(*self.desk_tree.get_children()); self._desk_rows.clear()
        for it in items:
            when = time.strftime("%Y-%m-%d", time.localtime(it["last_open"])) if it["last_open"] else \
                time.strftime("%Y-%m-%d", time.localtime(it["mtime"]))
            tag = "📌 " if it["pinned"] else ""
            iid = self.desk_tree.insert("", "end", text=tag + it["name"],
                                        values=(it["bucket"], when))
            self._desk_rows[iid] = it
        self.stats.configure(text=f"{len(items)} on desk")

    def _desk_selected(self):
        sel = self.desk_tree.selection()
        return self._desk_rows.get(sel[0]) if sel else None

    def _desk_open(self):
        it = self._desk_selected()
        if it:
            _open_file(it["path"])

    def _desk_unpin(self):
        it = self._desk_selected()
        if it and it["pinned"]:
            pins_mod.remove(it["path"]); self.refresh_desk()
        elif it:
            messagebox.showinfo("Desk", "That item isn't pinned (it's here because it's recent/used).", parent=self)

    # ================= Find tab =================
    def _find_tab(self):
        tab = ttk.Frame(self.nb); self.nb.add(tab, text="  🔎 Find  ")
        bar = ttk.Frame(tab, style="Panel.TFrame"); bar.pack(fill="x", padx=4, pady=(10, 6))
        row = ttk.Frame(bar, style="Panel.TFrame"); row.pack(fill="x", padx=12, pady=10)
        ttk.Label(row, text="Find", style="Dim.TLabel").pack(side="left")
        e = _entry(row, self.query_var); e.pack(side="left", fill="x", expand=True, padx=8, ipady=5)
        e.bind("<Return>", lambda ev: self.do_find())
        ttk.Button(row, text="Search", style="Accent.TButton", command=self.do_find).pack(side="left")
        ttk.Label(bar, text="Searches file names, file contents (text/PDF/Word), and AI descriptions. "
                  "Type any word — even the topic, not the filename.", style="Dim.TLabel", wraplength=880).pack(anchor="w", padx=12, pady=(0, 8))
        btns = ttk.Frame(tab, style="Panel.TFrame"); btns.pack(fill="x", padx=4)
        ttk.Button(btns, text="Open", command=self._find_open).pack(side="left", padx=(12, 4), pady=8)
        ttk.Button(btns, text="📌 Pin to _DESK", command=self._find_pin).pack(side="left", padx=4)
        ttk.Button(btns, text="↻ Rebuild index", command=self.rebuild_index).pack(side="right", padx=12)
        frame = ttk.Frame(tab, style="Panel.TFrame"); frame.pack(fill="both", expand=True, padx=4, pady=6)
        cols = ("where", "when")
        self.find_tree = ttk.Treeview(frame, columns=cols, show="tree headings")
        self.find_tree.heading("#0", text="File"); self.find_tree.heading("where", text="Location"); self.find_tree.heading("when", text="Modified")
        self.find_tree.column("#0", width=380); self.find_tree.column("where", width=380); self.find_tree.column("when", width=110)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.find_tree.yview)
        self.find_tree.configure(yscrollcommand=sb.set)
        self.find_tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=10)
        sb.pack(side="right", fill="y", pady=10, padx=(0, 12))
        self.find_tree.bind("<Double-1>", lambda e: self._find_open())

    def do_find(self):
        q = self.query_var.get().strip()
        if not q:
            return
        def work():
            con = index_mod.connect()
            empty = index_mod.stats(con) == 0
            rows = index_mod.search(con, q, limit=100); con.close()
            self._q.put((self._render_find, (rows, empty)))
        threading.Thread(target=work, daemon=True).start()

    def _render_find(self, payload):
        import time
        rows, empty = payload
        self.find_tree.delete(*self.find_tree.get_children()); self._find_rows.clear()
        if empty:
            self.find_tree.insert("", "end", text="(index is empty — click “Rebuild index”)", values=("", ""))
            return
        for r in rows:
            when = time.strftime("%Y-%m-%d", time.localtime(r["mtime"])) if r["mtime"] else ""
            flag = "  🔒" if r["sensitive"] else ""
            iid = self.find_tree.insert("", "end", text=r["name"] + flag,
                                        values=(str(Path(r["path"]).parent), when))
            self._find_rows[iid] = r
        if not rows:
            self.find_tree.insert("", "end", text="(no matches)", values=("", ""))

    def _find_selected(self):
        sel = self.find_tree.selection()
        return self._find_rows.get(sel[0]) if sel else None

    def _find_open(self):
        r = self._find_selected()
        if r:
            _open_file(r["path"])

    def _find_pin(self):
        r = self._find_selected()
        if r:
            pins_mod.add(r["path"])
            engine._rebuild_views(self.cfg)
            messagebox.showinfo("Pinned", f"{Path(r['path']).name} is pinned to _DESK and will never be moved.", parent=self)
            self.refresh_desk()

    def rebuild_index(self):
        self.stats.configure(text="indexing…")
        def work():
            try:
                app_config.INDEX_PATH.unlink()
            except OSError:
                pass
            n = engine.reindex(self.cfg, with_ai_description=False)
            self._q.put((lambda c: self.stats.configure(text=f"indexed {c} files"), n))
        threading.Thread(target=work, daemon=True).start()

    # ================= Tidy tab =================
    def _tidy_tab(self):
        tab = ttk.Frame(self.nb); self.nb.add(tab, text="  ✨ Tidy  ")
        bar = ttk.Frame(tab, style="Panel.TFrame"); bar.pack(fill="x", padx=4, pady=(10, 6))
        row = ttk.Frame(bar, style="Panel.TFrame"); row.pack(fill="x", padx=12, pady=(10, 4))
        ttk.Label(row, text="Folder", style="Dim.TLabel").pack(side="left")
        _entry(row, self.source).pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        ttk.Button(row, text="Browse…", command=self._browse_source).pack(side="left")
        ttk.Button(row, text="Desktop", command=lambda: self.source.set(str(app_config.default_desktop_folder()))).pack(side="left", padx=(6, 0))
        ttk.Label(bar, text=f"Files move into the six folders under your Home root "
                  f"({app_config.home_root(self.cfg)}). Low-confidence files stay in _UNSORTED — never guessed. "
                  f"Double-click a row to edit its new name.", style="Dim.TLabel", wraplength=880).pack(anchor="w", padx=12, pady=(0, 6))
        row2 = ttk.Frame(bar, style="Panel.TFrame"); row2.pack(fill="x", padx=12, pady=(2, 10))
        ttk.Checkbutton(row2, text="Include subfolders", variable=self.include_sub).pack(side="left", padx=(0, 16))
        ttk.Checkbutton(row2, text="Flag duplicates (hash)", variable=self.find_dupes).pack(side="left")
        ttk.Button(row2, text="👁 Preview", command=self.preview).pack(side="right", padx=4)
        ttk.Button(row2, text="✨ Organize", style="Accent.TButton", command=self.organize).pack(side="right", padx=4)

        frame = ttk.Frame(tab, style="Panel.TFrame"); frame.pack(fill="both", expand=True, padx=4, pady=6)
        ttk.Label(frame, text="PREVIEW — uncheck to leave a file alone; double-click to rename",
                  style="Dim.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        self.tree = ttk.Treeview(frame, columns=("target",), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="✓  File"); self.tree.heading("target", text="Goes to")
        self.tree.column("#0", width=440); self.tree.column("target", width=380)
        self.tree.tag_configure("category", foreground=FG, font=FONT_B)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(4, 10))
        sb.pack(side="right", fill="y", pady=(4, 10), padx=(0, 12))
        self.tree.bind("<Button-1>", self._toggle_item)
        self.tree.bind("<Double-1>", self._rename_item)

        logf = ttk.Frame(tab, style="Panel.TFrame"); logf.pack(fill="both", padx=4, pady=(6, 4))
        ttk.Label(logf, text="LOG", style="Dim.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        self.log = tk.Text(logf, height=6, bg=PANEL, fg=DIM, font=FONT_M, relief="flat", state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(2, 10))

    def _log_tidy(self, text):
        self.log.configure(state="normal"); self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end"); self.log.configure(state="disabled")

    def _browse_source(self):
        chosen = filedialog.askdirectory(parent=self, initialdir=self.source.get())
        if chosen:
            self.source.set(chosen)

    def preview(self):
        src = Path(self.source.get())
        if not src.is_dir():
            messagebox.showerror("Tidy", "That folder doesn't exist.", parent=self); return
        self.tree.delete(*self.tree.get_children()); self._planned.clear()
        self._log_tidy(f"scanning {src} …")
        def work():
            plan = engine.plan([src], self.cfg, include_subfolders=self.include_sub.get(),
                               skip_dupes=self.find_dupes.get())
            self._q.put((self._render_preview, plan))
        threading.Thread(target=work, daemon=True).start()

    def _render_preview(self, plan):
        if not plan.moves:
            self.stats.configure(text="clean ✨")
            self._log_tidy(f"nothing to move. {len(plan.skipped)} file(s) left in place.")
            return
        by_bucket: dict[str, list] = {}
        for mv in plan.moves:
            key = mv.bucket + ("\\" + mv.subfolder if mv.subfolder else "")
            by_bucket.setdefault(key, []).append(mv)
        for i, bucket in enumerate(sorted(by_bucket)):
            self.tree.tag_configure(f"b{i}", foreground=CAT_COLORS[i % len(CAT_COLORS)])
            node = self.tree.insert("", "end", text=f"📁 {bucket}  ({len(by_bucket[bucket])})",
                                    values=("",), open=True, tags=("category", f"b{i}"))
            for mv in by_bucket[bucket]:
                var = tk.BooleanVar(value=True)
                arrow = f"{mv.new_name}" + ("   (renamed)" if mv.is_rename else "")
                iid = self.tree.insert(node, "end", text="☑  " + mv.src.name, values=(arrow,))
                self._planned.append((var, mv, iid))
        self.stats.configure(text=f"{len(plan.moves)} to move · {len(plan.skipped)} left")
        self._log_tidy(f"preview: {len(plan.moves)} would move, {len(plan.skipped)} left in place")

    def _toggle_item(self, event):
        item = self.tree.identify_row(event.y)
        if not item or self.tree.get_children(item):
            return
        for var, _mv, iid in self._planned:
            if iid == item:
                var.set(not var.get())
                self.tree.item(item, text=("☑  " if var.get() else "☐  ") + _mv.src.name)
                break

    def _rename_item(self, event):
        item = self.tree.identify_row(event.y)
        for var, mv, iid in self._planned:
            if iid == item:
                self._rename_dialog(mv, iid); return

    def _rename_dialog(self, mv, iid):
        dlg = tk.Toplevel(self); dlg.title("Rename"); dlg.configure(bg=BG); dlg.resizable(False, False)
        _icon(dlg); dlg.transient(self); dlg.grab_set()
        ttk.Label(dlg, text=f"New name for {mv.src.name}", style="H.TLabel").pack(anchor="w", padx=20, pady=(18, 8))
        var = tk.StringVar(value=mv.new_name)
        ent = _entry(dlg, var, width=52); ent.pack(fill="x", padx=20, ipady=5); ent.focus_set()
        def ok():
            new = var.get().strip()
            if new:
                mv.new_name = new; mv.is_rename = (new != mv.src.name)
                self.tree.item(iid, values=(new + ("   (renamed)" if mv.is_rename else ""),))
            dlg.destroy()
        br = ttk.Frame(dlg); br.pack(fill="x", padx=20, pady=16)
        ttk.Button(br, text="Cancel", command=dlg.destroy).pack(side="right")
        ttk.Button(br, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=6)
        ent.bind("<Return>", lambda e: ok())

    def organize(self):
        chosen = [mv for var, mv, _ in self._planned if var.get()]
        if not chosen:
            messagebox.showinfo("Tidy", "Nothing selected — run Preview first.", parent=self); return
        src = Path(self.source.get())
        self._log_tidy(f"moving {len(chosen)} file(s)…")
        def work():
            plan = engine.Plan(root=app_config.home_root(self.cfg), moves=chosen)
            res = engine.execute(plan, self.cfg, selected=chosen,
                                 log=lambda s: self._q.put((self._log_tidy, s)))
            self._q.put((self._after_organize, res))
        threading.Thread(target=work, daemon=True).start()

    def _after_organize(self, res):
        self._log_tidy(f"done — {res['moved']} organized (undo available)")
        self._refresh_undo(); self.refresh_desk(); self.preview()

    # ================= Downloads tab =================
    def _downloads_tab(self):
        tab = ttk.Frame(self.nb); self.nb.add(tab, text="  ⬇ Downloads  ")
        bar = ttk.Frame(tab, style="Panel.TFrame"); bar.pack(fill="x", padx=4, pady=(10, 6))
        row = ttk.Frame(bar, style="Panel.TFrame"); row.pack(fill="x", padx=12, pady=(10, 6))
        ttk.Label(row, text="Downloads", style="Dim.TLabel").pack(side="left")
        _entry(row, self.downloads_folder).pack(side="left", fill="x", expand=True, padx=8, ipady=4)
        ttk.Button(row, text="Browse…", command=self._browse_downloads).pack(side="left")
        row2 = ttk.Frame(bar, style="Panel.TFrame"); row2.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Checkbutton(row2, text="Watch for new files (notify only — never auto-moves)",
                        variable=self.watch_enabled, command=self._toggle_watch).pack(side="left")
        ttk.Label(bar, text="Your Downloads is a landing strip: new files are only counted here. "
                  "Nothing moves until you click Clear Downloads.", style="Dim.TLabel", wraplength=880).pack(anchor="w", padx=12, pady=(0, 10))
        status = ttk.Frame(tab, style="Panel.TFrame"); status.pack(fill="x", padx=4, pady=6)
        self.pending_label = ttk.Label(status, text="0 new files waiting", style="H.TLabel")
        self.pending_label.pack(side="left", padx=16, pady=14)
        ttk.Button(status, text="🧹 Clear Downloads Now", style="Accent.TButton", command=self.clear_downloads).pack(side="right", padx=16, pady=10)
        lf = ttk.Frame(tab, style="Panel.TFrame"); lf.pack(fill="both", expand=True, padx=4, pady=6)
        ttk.Label(lf, text="WAITING", style="Dim.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        self.pending_list = tk.Listbox(lf, bg=PANEL2, fg=FG, font=FONT_M, relief="flat", selectmode="none", height=10)
        self.pending_list.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        lf2 = ttk.Frame(tab, style="Panel.TFrame"); lf2.pack(fill="both", padx=4, pady=(6, 4))
        ttk.Label(lf2, text="LOG", style="Dim.TLabel").pack(anchor="w", padx=12, pady=(8, 0))
        self.dl_log = tk.Text(lf2, height=5, bg=PANEL, fg=DIM, font=FONT_M, relief="flat", state="disabled", wrap="word")
        self.dl_log.pack(fill="both", expand=True, padx=12, pady=(2, 10))

    def _dl_log(self, text):
        self.dl_log.configure(state="normal"); self.dl_log.insert("end", text.rstrip() + "\n")
        self.dl_log.see("end"); self.dl_log.configure(state="disabled")

    def _browse_downloads(self):
        chosen = filedialog.askdirectory(parent=self, initialdir=self.downloads_folder.get())
        if chosen:
            self.downloads_folder.set(chosen); self._save_dl_cfg()
            if self.watch_enabled.get():
                self._stop_watch(); self._start_watch()

    def _save_dl_cfg(self):
        self.cfg["downloads"]["folder"] = self.downloads_folder.get()
        self.cfg["downloads"]["watch_enabled"] = self.watch_enabled.get()
        app_config.save(self.cfg)

    def _toggle_watch(self):
        self._save_dl_cfg()
        self._start_watch() if self.watch_enabled.get() else self._stop_watch()

    def _start_watch(self):
        folder = Path(self.downloads_folder.get())
        if not folder.is_dir():
            self._dl_log(f"can't watch — {folder} doesn't exist"); self.watch_enabled.set(False); return
        if self._watcher:
            return
        rules = engine._load_rules()
        self._watcher = DownloadsWatcher(
            folder, rules, self.cfg,
            on_new=lambda p, cat: self._q.put((self._on_new_download, (p, cat))),
            on_error=lambda m: self._q.put((self._dl_log, f"watch error: {m}")))
        self._watcher.start()
        self._dl_log(f"watching {folder} — arrivals counted, nothing auto-moves")

    def _stop_watch(self):
        if self._watcher:
            self._watcher.stop(); self._watcher = None; self._dl_log("watch stopped")

    def _on_new_download(self, payload):
        path, cat = payload
        self._pending.append((path, cat))
        self.pending_list.insert("end", f"{path.name}   ->  {cat}\\")
        self.pending_label.configure(text=f"{len(self._pending)} new file(s) waiting")
        self._dl_log(f"new: {path.name} (would go to {cat}\\)")

    def clear_downloads(self):
        folder = Path(self.downloads_folder.get())
        if not folder.is_dir():
            messagebox.showerror("Downloads", "That folder doesn't exist.", parent=self); return
        self._dl_log(f"organizing {folder} …")
        def work():
            plan = engine.plan([folder], self.cfg)
            res = engine.execute(plan, self.cfg, kind="clear_downloads",
                                 log=lambda s: self._q.put((self._dl_log, s)))
            self._q.put((self._after_clear, res))
        threading.Thread(target=work, daemon=True).start()

    def _after_clear(self, res):
        self._dl_log(f"done — {res['moved']} organized out of Downloads (undo available)")
        self._pending.clear(); self.pending_list.delete(0, "end")
        self.pending_label.configure(text="0 new files waiting")
        self._refresh_undo(); self.refresh_desk()
        if self._watcher:
            self._stop_watch(); self._start_watch()

    # ================= Settings tab =================
    def _settings_tab(self):
        tab = ttk.Frame(self.nb); self.nb.add(tab, text="  ⚙ Settings  ")
        p0 = ttk.Frame(tab, style="Panel.TFrame"); p0.pack(fill="x", padx=4, pady=(10, 6))
        ttk.Label(p0, text="HOME ROOT", style="Dim.TLabel").pack(anchor="w", padx=12, pady=(10, 2))
        hr = ttk.Frame(p0, style="Panel.TFrame"); hr.pack(fill="x", padx=12, pady=(0, 12))
        _entry(hr, self.home_var).pack(side="left", fill="x", expand=True, ipady=4)
        ttk.Button(hr, text="Browse…", command=lambda: self._browse_var(self.home_var)).pack(side="left", padx=(6, 0))
        ttk.Button(hr, text="Save", style="Accent.TButton", command=self._save_home).pack(side="left", padx=6)

        panel = ttk.Frame(tab, style="Panel.TFrame"); panel.pack(fill="x", padx=4, pady=6)
        ttk.Label(panel, text="AI (names + search descriptions only — never used to pick a folder)",
                  style="Dim.TLabel").pack(anchor="w", padx=12, pady=(10, 8))
        r = ttk.Frame(panel, style="Panel.TFrame"); r.pack(fill="x", padx=12, pady=4)
        ttk.Checkbutton(r, text="Enable AI", variable=self.ai_enabled, command=self._save_ai).pack(side="left")
        r2 = ttk.Frame(panel, style="Panel.TFrame"); r2.pack(fill="x", padx=12, pady=6)
        ttk.Label(r2, text="Provider", width=10).pack(side="left")
        opts = ["none"] + list(ai_provider.PROVIDERS.keys())
        labels = {"none": "None"}; labels.update({k: v["label"] for k, v in ai_provider.PROVIDERS.items()})
        self.pcombo = ttk.Combobox(r2, state="readonly", width=36, values=[labels[o] for o in opts])
        self.pcombo.set(labels.get(self.ai_provider_var.get(), "None")); self.pcombo.pack(side="left", padx=8)
        r3 = ttk.Frame(panel, style="Panel.TFrame"); r3.pack(fill="x", padx=12, pady=6)
        ttk.Label(r3, text="API key", width=10).pack(side="left")
        self.key_entry = _entry(r3, self.ai_key_var, show="*"); self.key_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(r3, text="show", variable=self.show_key,
                        command=lambda: self.key_entry.configure(show="" if self.show_key.get() else "*")).pack(side="left")
        r4 = ttk.Frame(panel, style="Panel.TFrame"); r4.pack(fill="x", padx=12, pady=6)
        ttk.Label(r4, text="Model", width=10).pack(side="left")
        _entry(r4, self.ai_model_var).pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        r5 = ttk.Frame(panel, style="Panel.TFrame"); r5.pack(fill="x", padx=12, pady=6)
        ttk.Label(r5, text="Base URL", width=10).pack(side="left")
        self.base_entry = _entry(r5, self.ai_base_var); self.base_entry.pack(side="left", fill="x", expand=True, padx=8, ipady=3)
        self.phint = ttk.Label(panel, text="", wraplength=800, justify="left", style="Dim.TLabel"); self.phint.pack(anchor="w", padx=12, pady=(2, 8))
        r6 = ttk.Frame(panel, style="Panel.TFrame"); r6.pack(fill="x", padx=12, pady=(0, 14))
        ttk.Button(r6, text="Test connection", command=self._test_ai).pack(side="left")
        ttk.Button(r6, text="Save", style="Accent.TButton", command=self._save_ai).pack(side="left", padx=8)
        self.ai_test_label = ttk.Label(r6, textvariable=self.ai_test_var, style="Dim.TLabel"); self.ai_test_label.pack(side="left", padx=12)

        p2 = ttk.Frame(tab, style="Panel.TFrame"); p2.pack(fill="x", padx=4, pady=6)
        ttk.Label(p2, text="MAINTENANCE", style="Dim.TLabel").pack(anchor="w", padx=12, pady=(10, 4))
        mr = ttk.Frame(p2, style="Panel.TFrame"); mr.pack(fill="x", padx=12, pady=(0, 14))
        ttk.Button(mr, text="Rebuild search index", command=self.rebuild_index).pack(side="left")
        ttk.Button(mr, text="Re-run setup wizard…", command=self._rerun_wizard).pack(side="left", padx=8)
        ttk.Button(mr, text="Open AI-call log", command=self._open_ai_log).pack(side="left")

        def on_change(_e=None):
            key = next((o for o in opts if labels[o] == self.pcombo.get()), "none")
            self.ai_provider_var.set(key)
            meta = ai_provider.PROVIDERS.get(key)
            if meta:
                if not self.ai_model_var.get():
                    self.ai_model_var.set(meta["default_model"])
                if key != "custom":
                    self.ai_base_var.set(meta["base_url"]); self.base_entry.configure(state="disabled")
                else:
                    self.base_entry.configure(state="normal")
                self.key_entry.configure(state="normal" if meta["needs_key"] else "disabled")
                self.phint.configure(text=meta["hint"])
            else:
                self.base_entry.configure(state="disabled"); self.key_entry.configure(state="disabled"); self.phint.configure(text="")
            self.ai_test_var.set("")
        self.pcombo.bind("<<ComboboxSelected>>", on_change); on_change()

    def _browse_var(self, var):
        chosen = filedialog.askdirectory(parent=self, initialdir=var.get() or str(Path.home()))
        if chosen:
            var.set(chosen)

    def _save_home(self):
        self.cfg["home_root"] = self.home_var.get().strip() or str(app_config.default_home_root())
        app_config.save(self.cfg)
        try:
            Path(self.cfg["home_root"]).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.refresh_desk(); messagebox.showinfo("Settings", "Home root saved.", parent=self)

    def _save_ai(self):
        self.cfg["ai"] = {"provider": self.ai_provider_var.get(), "api_key": self.ai_key_var.get().strip(),
                          "model": self.ai_model_var.get().strip(), "base_url": self.ai_base_var.get().strip(),
                          "enabled": self.ai_enabled.get()}
        app_config.save(self.cfg); self.ai_test_var.set("saved")

    def _test_ai(self):
        cfg = self._current_ai_cfg()
        if not cfg:
            self.ai_test_var.set("pick a provider and enable it first"); return
        self.ai_test_var.set("testing…")
        resolved = ai_provider.resolve_config(cfg)
        def work():
            ok, msg = ai_provider.test_connection(resolved)
            self._q.put((self._after_test, (ok, msg)))
        threading.Thread(target=work, daemon=True).start()

    def _after_test(self, payload):
        ok, msg = payload
        self.ai_test_label.configure(style="Good.TLabel" if ok else "Bad.TLabel")
        self.ai_test_var.set(("connected: " if ok else "failed: ") + msg)

    def _open_ai_log(self):
        if app_config.AI_LOG_PATH.exists():
            _open_file(str(app_config.AI_LOG_PATH))
        else:
            messagebox.showinfo("AI-call log", "No AI calls have been made yet.", parent=self)

    def _rerun_wizard(self):
        self.withdraw()
        finished = run_setup_wizard()
        self.cfg = app_config.load()
        if finished:
            self.home_var.set(self.cfg.get("home_root", self.home_var.get()))
            self.downloads_folder.set(self.cfg["downloads"].get("folder", self.downloads_folder.get()))
            nw = self.cfg["downloads"].get("watch_enabled", False)
            if nw != self.watch_enabled.get():
                self.watch_enabled.set(nw); self._toggle_watch()
            self.ai_provider_var.set(self.cfg["ai"].get("provider", "none"))
            self.ai_key_var.set(self.cfg["ai"].get("api_key", ""))
            self.ai_model_var.set(self.cfg["ai"].get("model", ""))
            self.ai_enabled.set(self.cfg["ai"].get("enabled", False))
        self.deiconify(); self.refresh_desk()

    def _on_close(self):
        self._stop_watch(); self.destroy()


def main():
    cfg = app_config.load()
    if not cfg.get("setup_complete"):
        if not run_setup_wizard():
            return
    OrganizerGUI().mainloop()


if __name__ == "__main__":
    main()
