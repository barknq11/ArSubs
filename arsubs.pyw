"""ArSubs - pick a movie / episode / anime file, get the matching Arabic subtitle next to it.

Sources: SubDL (great Arabic coverage) and OpenSubtitles (hash-exact matching).
Pure standard library: Python 3.9+ with tkinter.
"""
import io
import json
import os
import re
import struct
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import unicodedata
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP = "ArSubs"
UA = "ArSubs v1.0"
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".m4v", ".ts", ".webm", ".flv", ".mpg", ".mpeg"}
SUB_EXT = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
WINDOWS = sys.platform == "win32"
CONFIG_DIR = ((os.environ.get("APPDATA") if WINDOWS else os.environ.get("XDG_CONFIG_HOME"))
              or os.path.join(os.path.expanduser("~"), ".config"))
CONFIG_PATH = os.path.join(CONFIG_DIR, APP, "config.json")
UI_FONT = "Segoe UI" if WINDOWS else "TkDefaultFont"
MONO_FONT = "Consolas" if WINDOWS else "TkFixedFont"


# ---------------------------------------------------------------- config

def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---------------------------------------------------------------- filename parsing

QUALITY_WORDS = r"(2160p|1080p|720p|576p|480p|4k|web[- ]?dl|webrip|web|bluray|blu[- ]?ray|bdrip|brrip|hdrip|dvdrip|hdtv|x264|x265|h\.?264|h\.?265|hevc|aac|10bit|remux|proper|repack)"


def parse_name(path):
    """Guess title / year / season / episode from a file name (and its folder as fallback)."""
    base = os.path.splitext(os.path.basename(path))[0]
    raw = base
    s = re.sub(r"^\s*\[[^\]]*\]\s*", "", base)          # leading [SubGroup]
    s = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", s)          # [1080p] [ABCD1234]
    s = re.sub(r"[._]+", " ", s)

    info = {"title": "", "year": None, "season": None, "episode": None, "kind": "movie", "raw": raw}
    patterns = [
        r"\bS(\d{1,2})\s*E(\d{1,4})\b",
        r"\b(\d{1,2})x(\d{1,3})\b",
        r"\bSeason\s*(\d{1,2})\s*Episode\s*(\d{1,4})\b",
    ]
    cut = None
    for p in patterns:
        m = re.search(p, s, re.I)
        if m:
            info["season"], info["episode"] = int(m.group(1)), int(m.group(2))
            cut = m.start()
            break
    if cut is None:
        for p in (r"\s-\s*(\d{1,4})(?:v\d)?\b", r"\b(?:EP?|Episode)\s*(\d{1,4})\b", r"\s(\d{2,4})(?:v\d)?\s*$"):
            m = re.search(p, s, re.I)
            if m and not re.fullmatch(r"(19|20)\d\d", m.group(1)):
                info["episode"] = int(m.group(1))
                cut = m.start()
                break
        sm = re.search(r"\b(?:S|Season\s*)(\d{1,2})\b", s[:cut] if cut else s, re.I)
        if sm and cut is not None:
            info["season"] = int(sm.group(1))
            cut = min(cut, sm.start())
    if info["episode"] is not None:
        info["kind"] = "tv"

    y = re.search(r"[\s(]((?:19|20)\d\d)[\s)]", " " + s + " ")
    if y:
        info["year"] = int(y.group(1))
        if cut is None or y.start() < cut:
            cut = max(0, y.start() - 1)
    q = re.search(QUALITY_WORDS, s, re.I)
    if q and (cut is None or q.start() < cut):
        cut = q.start()

    title = s[:cut] if cut is not None else s
    title = re.sub(r"[\s\-()]+$", "", title).strip()
    if not title:  # e.g. "E05.mkv" inside "Show Name/Season 1/"
        folder = os.path.basename(os.path.dirname(path))
        if re.match(r"(?i)season\s*\d+|s\d+$", folder):
            folder = os.path.basename(os.path.dirname(os.path.dirname(path)))
        title = parse_name(os.path.join("x", folder + ".mkv"))["title"] if folder else ""
    info["title"] = title
    info["variants"] = [(info["season"], info["episode"])] if info["episode"] is not None else []
    return info


def tokens(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()  # "Shippūden" -> "Shippuden"
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t}


def similarity(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def describe(info):
    if not info["title"]:
        return ""
    if info["episode"] is not None:
        num = "S%02dE%02d" % (info["season"], info["episode"]) if info["season"] else "episode %d" % info["episode"]
        return f"{info['title']}  {num}"
    return info["title"] + (f" ({info['year']})" if info["year"] else "")


def episode_of(name):
    i = parse_name(name + ".srt")
    return i["season"], i["episode"]


# ---------------------------------------------------------------- http helpers

def http(url, data=None, headers=None, method=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def http_json(*a, **k):
    return json.loads(http(*a, **k).decode("utf-8"))


def opensubtitles_hash(path):
    size = os.path.getsize(path)
    if size < 131072:
        return None
    h = size
    with open(path, "rb") as f:
        for off in (0, max(0, size - 65536)):
            f.seek(off)
            buf = f.read(65536)
            for (v,) in struct.iter_unpack("<Q", buf):
                h = (h + v) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


# ---------------------------------------------------------------- episode numbering
# Anime is often numbered absolutely ("One Piece 1100") while subtitle sites list it as
# season/episode, or the other way round. TVmaze (free, no key) gives the episode order to convert.

_tvmaze_cache = {}


def _tvmaze(url):
    if url not in _tvmaze_cache:
        try:
            _tvmaze_cache[url] = http_json("https://api.tvmaze.com" + url, timeout=15)
        except (OSError, ValueError):  # not found, offline, bad JSON
            _tvmaze_cache[url] = []
    return _tvmaze_cache[url]


def tvmaze_episodes(title, season=None, episode=None):
    """Regular episodes of a show in airing order as [(season, number), ...]; [] if unknown.

    Several shows can share a name (One Piece anime vs. live action), so take the
    best-named one that actually has this episode."""
    if not title.strip():
        return []
    first = None
    for hit in _tvmaze("/search/shows?" + urllib.parse.urlencode({"q": title}))[:5]:
        show = hit.get("show") or {}
        name = show.get("name", "")
        if not tokens(title) <= tokens(name) and similarity(name, title) < 0.5:  # "Frieren" ⊂ "Frieren: Beyond…"
            continue
        eps = [(e["season"], e["number"]) for e in _tvmaze(f"/shows/{show['id']}/episodes")
               if e.get("season") and e.get("number")]
        if not eps:
            continue
        first = first or eps
        if episode is None or (season, episode) in eps or (season in (None, 1) and episode <= len(eps)):
            return eps
    return first or []


def episode_variants(season, episode, eps):
    """Every way this episode may be numbered: as written, plus absolute <-> season/episode."""
    if episode is None:
        return []
    out = [(season, episode)]
    if eps:
        first_season_len = sum(1 for s, _ in eps if s == eps[0][0])
        if season in (None, 1) and first_season_len < episode <= len(eps):
            if eps[episode - 1][0] < 100:  # skip year-numbered "seasons" (2024) no subtitle site uses
                out.append(eps[episode - 1])                        # absolute -> SxxEyy
        elif season is not None and (season, episode) in eps:
            out.append((None, eps.index((season, episode)) + 1))    # SxxEyy -> absolute
    return out


def ep_match(info, season, episode):
    """Does a subtitle numbered (season, episode) fit this video under any numbering?"""
    return any(e == episode and (s is None or season is None or s == season) for s, e in info["variants"])


def ep_status(c, info):
    """True = right episode, False = wrong episode, None = can't tell (movie, season pack, no data)."""
    if c["hash"]:
        return True
    if info["episode"] is None or c.get("pack"):
        return None
    lo, hi = c.get("range") or (None, None)
    if lo and hi and any(lo <= e <= hi for _, e in info["variants"]):
        return None
    if c.get("episode") is None:
        return None
    return ep_match(info, c.get("season"), c["episode"])


# ---------------------------------------------------------------- providers
# Each candidate: dict(source, release, hash, season, episode, fetch=callable -> (filename, bytes))

class SubDL:
    name = "SubDL"
    API = "https://api.subdl.com/api/v1/subtitles"

    def __init__(self, key):
        self.key = key

    def search(self, info, log):
        common = {"api_key": self.key, "languages": "AR", "subs_per_page": 30, "type": info["kind"]}
        if info["year"] and info["kind"] == "movie":
            common["year"] = info["year"]
        bases = []
        if info.get("imdb"):
            bases.append(dict(common, imdb_id=info["imdb"]))
        if info["title"]:
            bases.append(dict(common, film_name=info["title"]))
        seen, out = set(), []
        for base in bases:
            if info["kind"] == "tv":
                attempts = []
                for s, e in info["variants"]:
                    attempts += [dict(base, season_number=s or 1, episode_number=e), dict(base, episode_number=e)]
                attempts.append(dict(base, season_number=info["season"] or 1))
            else:
                attempts = [base, {k: v for k, v in base.items() if k != "year"}]
            for q in attempts:
                try:
                    res = http_json(self.API + "?" + urllib.parse.urlencode(q))
                except urllib.error.HTTPError as e:
                    log(f"  SubDL: HTTP {e.code}")
                    return out
                subs = (res.get("subtitles") or []) if res.get("status") else []
                for s in subs:
                    if s.get("url") and s["url"] not in seen:
                        seen.add(s["url"])
                        out.append(self._cand(s))
                if any(ep_status(c, info) for c in out) or (out and info["kind"] == "movie"):
                    return out
        return out

    def _cand(self, s):
        url = "https://dl.subdl.com" + s["url"]
        return {"source": self.name, "release": s.get("release_name") or s.get("name") or "", "hash": False,
                "season": s.get("season"), "episode": s.get("episode"), "pack": bool(s.get("full_season")),
                "range": (s.get("episode_from"), s.get("episode_end")),
                "fetch": lambda: ("sub.zip", http(url, timeout=60))}


class OpenSubtitles:
    name = "OpenSubtitles"
    API = "https://api.opensubtitles.com/api/v1"

    def __init__(self, key, user="", password=""):
        self.key, self.user, self.password = key, user, password
        self.token, self.base = None, self.API

    def _h(self):
        h = {"Api-Key": self.key}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    def login(self, log):
        if self.token or not (self.user and self.password):
            return
        try:
            r = http_json(self.API + "/login", {"username": self.user, "password": self.password}, self._h())
            self.token = r.get("token")
            if r.get("base_url"):
                self.base = "https://" + r["base_url"] + "/api/v1"
        except urllib.error.HTTPError as e:
            log(f"  OpenSubtitles login failed (HTTP {e.code}); continuing without login")

    def search(self, info, log, path):
        self.login(log)
        queries = []
        mh = opensubtitles_hash(path) if os.path.isfile(path) else None
        if mh:  # the file's fingerprint alone — works even when the file name says nothing
            queries.append({"languages": "ar", "moviehash": mh})
        if info["title"] or info.get("imdb"):
            base = {"languages": "ar"}
            if info.get("imdb"):
                base["parent_imdb_id" if info["kind"] == "tv" else "imdb_id"] = int(info["imdb"][2:])
            else:
                base["query"] = info["title"].lower()
            if info["kind"] == "tv":
                for s, e in info["variants"]:
                    q = dict(base, episode_number=e, type="episode")
                    if s is not None:
                        q["season_number"] = s
                    queries.append(q)
            else:
                queries.append(dict(base, year=info["year"]) if info["year"] else base)
        seen, out = set(), []
        for q in queries:
            url = self.base + "/subtitles?" + urllib.parse.urlencode(sorted(q.items()))
            try:
                res = http_json(url, headers=self._h())
            except urllib.error.HTTPError as e:
                log(f"  OpenSubtitles: HTTP {e.code}")
                break
            for d in res.get("data", []):
                a = d.get("attributes", {})
                files = a.get("files") or []
                if not files or files[0]["file_id"] in seen:
                    continue
                fid = files[0]["file_id"]
                seen.add(fid)
                fd = a.get("feature_details") or {}
                out.append({"source": self.name, "release": a.get("release") or files[0].get("file_name", ""),
                            "hash": bool(a.get("moviehash_match")),
                            "season": fd.get("season_number"), "episode": fd.get("episode_number"),
                            "ident": identity(fd), "downloads": a.get("download_count", 0),
                            "fetch": (lambda fid=fid: self._download(fid))})
        return out

    def _download(self, fid):
        r = http_json(self.base + "/download", {"file_id": fid}, self._h())
        if not r.get("link"):
            raise RuntimeError(r.get("message") or "download quota reached")
        return r.get("file_name") or "sub.srt", http(r["link"], timeout=60)


def identity(fd):
    """What OpenSubtitles says a subtitle belongs to, in parse_name() shape."""
    if (fd.get("feature_type") or "").lower() == "episode":
        imdb = fd.get("parent_imdb_id")
        return {"kind": "tv", "title": fd.get("parent_title") or "", "year": None,
                "season": fd.get("season_number"), "episode": fd.get("episode_number"),
                "imdb": "tt%07d" % imdb if imdb else None}
    imdb = fd.get("imdb_id")
    return {"kind": "movie", "title": fd.get("title") or "", "year": fd.get("year"),
            "season": None, "episode": None, "imdb": "tt%07d" % imdb if imdb else None}


def score(c, info):
    s = similarity(info["raw"], c["release"]) * 50
    if c["hash"]:
        s += 100
    if c["ep_ok"] is True:
        s += 30
    elif c["ep_ok"] is False:
        s -= 100
    raw = info["raw"].lower()
    rel = c["release"].lower()
    for grp in re.findall(r"-([a-z0-9]+)$", re.sub(r"\.\w+$", "", raw)):
        if grp in rel:
            s += 15
    for q in ("2160p", "1080p", "720p", "web-dl", "webrip", "bluray", "hdtv"):
        if q in raw and q in rel:
            s += 3
    s += min(c.get("downloads", 0), 5000) / 1000
    return s


# ---------------------------------------------------------------- saving

def decode_sub(data):
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1256", "replace")  # classic Arabic Windows encoding


def pick_from_zip(data, info):
    z = zipfile.ZipFile(io.BytesIO(data))
    subs = [n for n in z.namelist() if os.path.splitext(n)[1].lower() in SUB_EXT]
    if not subs:
        raise RuntimeError("archive has no subtitle file")
    best = subs[0]
    if len(subs) > 1:
        def rank(n):
            se, ep = episode_of(os.path.basename(n))
            r = similarity(info["raw"], os.path.basename(n))
            if info["episode"] is not None and ep is not None and ep_match(info, se, ep):
                r += 10
            return r
        best = max(subs, key=rank)
        se, ep = episode_of(os.path.basename(best))
        if info["episode"] is not None and ep is not None and not ep_match(info, se, ep):
            raise RuntimeError("season pack doesn't contain this episode")
    return os.path.basename(best), z.read(best)


def save_subtitle(video, cand, info):
    fname, data = cand["fetch"]()
    if data[:2] == b"PK":
        fname, data = pick_from_zip(data, info)
    ext = os.path.splitext(fname)[1].lower()
    if ext not in SUB_EXT:
        ext = ".srt"
    text = decode_sub(data).replace("\r\n", "\n")
    out = os.path.splitext(video)[0] + ".ar" + ext
    with open(out, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(text)
    return out


# ---------------------------------------------------------------- UI

class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.cands = []
        self.current = None  # (video, info)
        root.title(APP + " — Arabic subtitles")
        root.geometry("760x560")
        root.minsize(560, 420)
        assets = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "assets")
        try:
            if WINDOWS:
                root.iconbitmap(default=os.path.join(assets, "icon.ico"))
            else:
                self._icon = tk.PhotoImage(file=os.path.join(assets, "icon-64.png"))
                root.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        style = ttk.Style()
        for theme in ("vista", "clam") if WINDOWS else ("clam",):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                pass

        top = ttk.Frame(root, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="Arabic subtitle downloader", font=(UI_FONT, 14, "bold")).pack(side="left")
        ttk.Button(top, text="Settings", command=self.settings).pack(side="right")

        btns = ttk.Frame(root, padding=(12, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="Choose video file(s)…", command=self.pick_files).pack(side="left", ipadx=8, ipady=4)
        ttk.Button(btns, text="Choose folder…", command=self.pick_folder).pack(side="left", padx=8, ipadx=8, ipady=4)
        self.status = ttk.Label(btns, text="", foreground="#555")
        self.status.pack(side="left", padx=8)

        fix = ttk.Frame(root, padding=(12, 10, 12, 0))
        fix.pack(fill="x")
        ttk.Label(fix, text="Search as:").pack(side="left")
        self.f_title, self.f_season, self.f_episode, self.f_year = (tk.StringVar() for _ in range(4))
        ttk.Entry(fix, textvariable=self.f_title, width=28).pack(side="left", padx=(6, 10), fill="x", expand=True)
        for label, var in (("Season", self.f_season), ("Episode", self.f_episode), ("Year", self.f_year)):
            ttk.Label(fix, text=label).pack(side="left")
            ttk.Entry(fix, textvariable=var, width=5).pack(side="left", padx=(4, 10))
        ttk.Button(fix, text="Search", command=self.search_again).pack(side="left")

        mid = ttk.Frame(root, padding=12)
        mid.pack(fill="both", expand=True)
        ttk.Label(mid, text="Matches for the last file (double-click to use a different one):").pack(anchor="w")
        cols = ("score", "source", "release")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", height=7)
        for c, w in zip(cols, (60, 110, 500)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=w, stretch=(c == "release"))
        self.tree.pack(fill="both", expand=True, pady=(4, 8))
        self.tree.bind("<Double-1>", self.use_selected)

        self.log_box = tk.Text(mid, height=8, wrap="word", font=(MONO_FONT, 9), relief="flat",
                               background="#f6f6f6", state="disabled")
        self.log_box.pack(fill="both")

        if not (self.cfg.get("subdl_key") or self.cfg.get("os_key")):
            root.after(300, lambda: (messagebox.showinfo(APP, "Add at least one free API key (SubDL and/or "
                                                              "OpenSubtitles) to start."), self.settings()))
        files = [a for a in sys.argv[1:] if os.path.exists(a)]
        if files:
            root.after(500, lambda: self.run(self.expand(files)))

    # -- helpers
    def log(self, msg):
        def w():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, w)

    def set_status(self, msg):
        self.root.after(0, lambda: self.status.configure(text=msg))

    def providers(self):
        p = []
        if self.cfg.get("subdl_key"):
            p.append(SubDL(self.cfg["subdl_key"]))
        if self.cfg.get("os_key"):
            if not hasattr(self, "_os"):
                self._os = OpenSubtitles(self.cfg["os_key"], self.cfg.get("os_user", ""), self.cfg.get("os_pass", ""))
            p.append(self._os)
        return p

    @staticmethod
    def expand(paths):
        out = []
        for p in paths:
            if os.path.isdir(p):
                for d, _, fs in os.walk(p):
                    out += [os.path.join(d, f) for f in sorted(fs) if os.path.splitext(f)[1].lower() in VIDEO_EXT]
            elif os.path.splitext(p)[1].lower() in VIDEO_EXT:
                out.append(p)
        return out

    # -- actions
    def pick_files(self):
        fs = filedialog.askopenfilenames(title="Choose video files",
                                         filetypes=[("Video", " ".join("*" + x for e in sorted(VIDEO_EXT) for x in (e, e.upper()))),
                                                    ("All", "*")])
        if fs:
            self.run(list(fs))

    def pick_folder(self):
        d = filedialog.askdirectory(title="Choose a folder with episodes")
        if d:
            vids = self.expand([d])
            if not vids:
                messagebox.showinfo(APP, "No video files found in that folder.")
                return
            skip = [v for v in vids if not any(os.path.exists(os.path.splitext(v)[0] + ".ar" + e) for e in SUB_EXT)]
            self.run(skip)

    def run(self, videos):
        if not self.providers():
            self.settings()
            return
        threading.Thread(target=self.worker, args=(videos,), daemon=True).start()

    def worker(self, videos):
        ok = 0
        for i, v in enumerate(videos, 1):
            self.set_status(f"{i}/{len(videos)}")
            try:
                if self.process(v):
                    ok += 1
            except Exception as e:  # keep going with the next file
                self.log(f"  ✗ error: {e}")
        self.set_status(f"Done — {ok}/{len(videos)} subtitled")

    def process(self, video, info=None):
        info = info or parse_name(video)
        self.log(f"▶ {os.path.basename(video)}\n  detected: {describe(info) or '(nothing useful in the file name)'}")
        self.prepare(info)
        provs = self.providers()
        osub = [p for p in provs if isinstance(p, OpenSubtitles)]
        cands = []

        # OpenSubtitles first: its file-fingerprint match can identify the video when the name can't
        for p in osub:
            try:
                found = p.search(info, self.log, video)
                self.log(f"  {p.name}: {len(found)} Arabic result(s)")
                cands += found
            except Exception as e:
                self.log(f"  {p.name}: {e}")
        ident = next((c["ident"] for c in cands if c["hash"] and c["ident"]["title"]), None)
        if ident:
            info["imdb"] = ident["imdb"]
            if (similarity(ident["title"], info["title"]) < 0.5 or ident["episode"] != info["episode"]
                    or (info["season"] and ident["season"] != info["season"])):
                self.log(f"  identified by file fingerprint: {describe(ident)}")
                info.update(ident)
                self.prepare(info)

        if not info["title"] and not info.get("imdb"):
            self.log("  ✗ couldn't tell what this video is — type the title in 'Search as' below and press Search")
            self.root.after(0, self.show_cands, [], video, info)
            return False

        for p in provs:
            if p in osub:
                continue
            try:
                found = p.search(info, self.log)
                self.log(f"  {p.name}: {len(found)} Arabic result(s)")
                cands += found
            except Exception as e:
                self.log(f"  {p.name}: {e}")
        for c in cands:
            c["ep_ok"] = ep_status(c, info)
            c["score"] = score(c, info)
        cands.sort(key=lambda c: c["score"], reverse=True)
        self.root.after(0, self.show_cands, cands, video, info)
        for c in cands[:4]:  # fall through if a download fails (quota, bad pack, …)
            if c["ep_ok"] is False:
                break
            try:
                out = save_subtitle(video, c, info)
                self.log(f"  ✓ saved {os.path.basename(out)}  ← {c['source']}: {c['release']}")
                return True
            except Exception as e:
                self.log(f"  {c['source']} download failed: {e}")
        self.log("  ✗ no Arabic subtitle found — check 'Search as' below and press Search to retry")
        return False

    def prepare(self, info):
        """Work out other numberings of the episode (absolute <-> season/episode)."""
        info["kind"] = "tv" if info["episode"] is not None else "movie"
        eps = tvmaze_episodes(info["title"], info["season"], info["episode"]) if info["kind"] == "tv" else []
        info["variants"] = episode_variants(info["season"], info["episode"], eps)
        for s, e in info["variants"][1:]:
            self.log(f"  also searching as {'episode %d' % e if s is None else 'S%02dE%02d' % (s, e)}")

    def search_again(self):
        if not self.current:
            messagebox.showinfo(APP, "Choose a video first.")
            return
        video, old = self.current
        num = lambda v: int(v) if v.strip().isdigit() else None
        info = dict(old, title=self.f_title.get().strip(), season=num(self.f_season.get()),
                    episode=num(self.f_episode.get()), year=num(self.f_year.get()), imdb=None)
        if not self.providers():
            self.settings()
            return
        threading.Thread(target=lambda: self.process(video, info), daemon=True).start()

    def show_cands(self, cands, video, info):
        self.cands, self.current = cands, (video, info)
        for var, v in ((self.f_title, info["title"]), (self.f_season, info["season"]),
                       (self.f_episode, info["episode"]), (self.f_year, info["year"])):
            var.set("" if v is None else v)
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(cands):
            mark = " ★hash" if c["hash"] else ""
            self.tree.insert("", "end", iid=str(i), values=(f"{c['score']:.0f}", c["source"] + mark, c["release"]))

    def use_selected(self, _e=None):
        sel = self.tree.selection()
        if not sel or not self.current:
            return
        c = self.cands[int(sel[0])]
        video, info = self.current

        def go():
            try:
                out = save_subtitle(video, c, info)
                self.log(f"  ✓ replaced with {os.path.basename(out)}  ← {c['release']}")
            except Exception as e:
                self.log(f"  ✗ {e}")
        threading.Thread(target=go, daemon=True).start()

    def settings(self):
        w = tk.Toplevel(self.root)
        w.title("Settings")
        w.transient(self.root)
        w.resizable(False, False)
        f = ttk.Frame(w, padding=14)
        f.pack()
        fields = [("subdl_key", "SubDL API key", "get it free at subdl.com → Panel → API"),
                  ("os_key", "OpenSubtitles API key", "opensubtitles.com → Profile → API consumers"),
                  ("os_user", "OpenSubtitles username", "optional — raises daily download limit"),
                  ("os_pass", "OpenSubtitles password", "optional")]
        vars_ = {}
        for r, (k, label, hint) in enumerate(fields):
            ttk.Label(f, text=label).grid(row=r * 2, column=0, sticky="w")
            vars_[k] = tk.StringVar(value=self.cfg.get(k, ""))
            ttk.Entry(f, textvariable=vars_[k], width=46, show="•" if k == "os_pass" else "").grid(
                row=r * 2, column=1, padx=8, pady=(6, 0))
            ttk.Label(f, text=hint, foreground="#888", font=(UI_FONT, 8)).grid(row=r * 2 + 1, column=1, sticky="w", padx=8)

        def ok():
            for k, v in vars_.items():
                self.cfg[k] = v.get().strip()
            save_config(self.cfg)
            if hasattr(self, "_os"):
                del self._os
            w.destroy()
        ttk.Button(f, text="Save", command=ok).grid(row=99, column=1, sticky="e", pady=(12, 0), padx=8)
        w.grab_set()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on HiDPI Windows screens
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
