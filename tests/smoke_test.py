"""Headless check: filename parsing works and the window opens and closes cleanly."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cfg_dir = tempfile.mkdtemp()
os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = cfg_dir
os.makedirs(os.path.join(cfg_dir, "ArSubs"))
with open(os.path.join(cfg_dir, "ArSubs", "config.json"), "w") as f:
    json.dump({"subdl_key": "test"}, f)  # skip the first-run settings prompt

path = os.path.join(root_dir, "arsubs.pyw")
loader = importlib.machinery.SourceFileLoader("arsubs", path)
spec = importlib.util.spec_from_loader("arsubs", loader)
m = importlib.util.module_from_spec(spec)
loader.exec_module(m)

cases = {
    "Breaking.Bad.S05E14.1080p.BluRay.x264-DEMAND.mkv": ("Breaking Bad", None, 5, 14),
    "[SubsPlease] Jujutsu Kaisen - 47 (1080p) [ABCD1234].mkv": ("Jujutsu Kaisen", None, None, 47),
    "Inception.2010.1080p.BluRay.x264-SPARKS.mp4": ("Inception", 2010, None, None),
    "The Office 3x05 HDTV.avi": ("The Office", None, 3, 5),
}
for name, want in cases.items():
    i = m.parse_name(name)
    got = (i["title"], i["year"], i["season"], i["episode"])
    assert got == want, f"{name}: {got} != {want}"

assert m.CONFIG_PATH.startswith(cfg_dir), m.CONFIG_PATH

# --- episode numbering (offline: fake show with 24 + 23 episodes)
eps = [(1, n) for n in range(1, 25)] + [(2, n) for n in range(1, 24)]
assert m.episode_variants(None, 47, eps) == [(None, 47), (2, 23)]      # absolute -> SxxEyy
assert m.episode_variants(2, 23, eps) == [(2, 23), (None, 47)]         # SxxEyy -> absolute
assert m.episode_variants(None, 5, eps) == [(None, 5)]                 # season 1 needs no conversion
info = m.parse_name("[Group] Show - 47 [1080p].mkv")
info["variants"] = m.episode_variants(None, 47, eps)
assert m.ep_match(info, 2, 23) and m.ep_match(info, None, 47) and not m.ep_match(info, 1, 23)
assert m.ep_status({"hash": False, "season": 2, "episode": 23}, info) is True
assert m.ep_status({"hash": False, "season": 1, "episode": 23}, info) is False
assert m.ep_status({"hash": False, "pack": True}, info) is None

# --- season pack: picks the right episode under either numbering
import io, zipfile
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    for n in (22, 23, 24):
        z.writestr(f"Show.S02E{n:02d}.srt", f"episode {n}")
name, data = m.pick_from_zip(buf.getvalue(), info)
assert name == "Show.S02E23.srt", name

# --- unidentifiable file name: fingerprint match identifies it, then other sites search by that
class FakeOS(m.OpenSubtitles):
    def search(self, info, log, path):
        fd = {"feature_type": "Episode", "parent_title": "Breaking Bad", "season_number": 5,
              "episode_number": 14, "parent_imdb_id": 903747}
        return [{"source": "OS", "release": "x", "hash": True, "season": 5, "episode": 14,
                 "ident": m.identity(fd), "fetch": lambda: ("a.srt", b"1\n00:00:01,000 --> 00:00:02,000\nhi\n")}]
seen = {}
class FakeSubDL(m.SubDL):
    def search(self, info, log):
        seen.update(info)
        return []
m.tvmaze_episodes = lambda *a: []  # stay offline
tmp = tempfile.mkdtemp()
video = os.path.join(tmp, "video_final.mkv")
open(video, "wb").close()
assert m.parse_name(video)["title"] == "video final"

tk_root = m.tk.Tk()
app = m.App(tk_root)
app.providers = lambda: [FakeOS("k"), FakeSubDL("k")]
assert app.process(video) is True
assert (seen["title"], seen["season"], seen["episode"], seen["imdb"]) == ("Breaking Bad", 5, 14, "tt0903747"), seen
assert os.path.exists(os.path.join(tmp, "video_final.ar.srt"))

# --- nothing identifies it: user types the title in "Search as"
app.providers = lambda: [FakeSubDL("k")]
seen.clear()
assert app.process(os.path.join(tmp, "E05.mkv")) is False  # only a meaningless folder name to go on
tk_root.update()
seen.clear()
app.f_title.set("Dark")
app.f_season.set("1")


def check():
    if seen.get("title") == "Dark":
        tk_root.after(500, tk_root.destroy)
    else:
        tk_root.after(100, check)


tk_root.after(100, app.search_again)
tk_root.after(200, check)
tk_root.after(15000, tk_root.destroy)  # safety net
tk_root.mainloop()
assert (seen.get("title"), seen.get("season"), seen.get("episode")) == ("Dark", 1, 5), seen
print(f"OK on {sys.platform}")
