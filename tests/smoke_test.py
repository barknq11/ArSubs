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

tk_root = m.tk.Tk()
m.App(tk_root)
tk_root.after(1500, tk_root.destroy)
tk_root.mainloop()
print(f"OK on {sys.platform}")
