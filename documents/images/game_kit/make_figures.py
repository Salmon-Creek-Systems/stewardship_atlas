"""Illustrations for documents/game_kit.md, drawn from scvfd staging. Run on the box."""
import json, math, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
from matplotlib.patches import FancyBboxPatch
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
sys.path.insert(0, "/tmp/gkfig")
import game_kit as gk

ROOT = "/root/swales_dev/scvfd/staging"
OUT = "/tmp/gkfig/out"
cfg = json.load(open(f"{ROOT}/atlas_config.json"))
B = cfg["dataswale"]["bbox"]
roads = json.load(open(f"{ROOT}/layers/roads/roads.geojson"))["features"]
k = gk.meters_per_degree(B)
ASPECT = k[1] / k[0]

def hillshade(ax):
    with rasterio.open(f"{ROOT}/layers/lidar_basemap/lidar_basemap.tiff") as src:
        with WarpedVRT(src, crs="EPSG:4326") as vrt:
            w = vrt.window(B["west"], B["south"], B["east"], B["north"])
            a = vrt.read(1, window=w, out_shape=(900, int(900 * (B["east"]-B["west"]) / (B["north"]-B["south"]) / ASPECT)),
                         resampling=Resampling.average).astype(float)
    lo, hi = np.percentile(a[a > 0], [2, 98])
    a = np.clip((a - lo) / (hi - lo), 0, 1)
    a = 1 - 0.45 * (1 - a)          # half darkness
    ax.imshow(a, cmap="gray", vmin=0, vmax=1, extent=(B["west"], B["east"], B["south"], B["north"]), zorder=0)

def frame(title, sub):
    fig, ax = plt.subplots(figsize=(10, 10 * (B["north"]-B["south"]) / (B["east"]-B["west"]) * ASPECT), dpi=110)
    ax.set_xlim(B["west"], B["east"]); ax.set_ylim(B["south"], B["north"])
    ax.set_aspect(ASPECT); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_linewidth(2)
    hillshade(ax)
    ax.text(0, 1.075, title, transform=ax.transAxes, fontsize=17, fontweight="bold", va="bottom")
    ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=10.5, color="#444", va="bottom")
    return fig, ax

def lines(feats):
    for f in feats:
        for ln in gk._lines(f):
            yield np.array(ln)

halo = [pe.withStroke(linewidth=3.5, foreground="white")]

# ---------- 1. what the atlas guesses today
fig, ax = frame("What the atlas guesses today", "Every road segment, and a red ✕ wherever one crosses the atlas rectangle")
for xy in lines(roads):
    ax.plot(xy[:, 0], xy[:, 1], color="#333", lw=0.7, zorder=2)
crossings = gk.find_exits(roads, B)
for e in crossings:
    ax.plot(*e["lonlat"], marker="X", ms=11, color="#d62828", mec="white", mew=1.2, zorder=5, clip_on=False)
ax.text(0.01, 0.01, f"{len(crossings)} candidate exits · 0 safe places · 0 helispots · 0 named places\n"
        "≈50% of road length has no usable name",
        transform=ax.transAxes, fontsize=10.5, va="bottom", path_effects=halo, zorder=6)
fig.savefig(f"{OUT}/game_kit_today.png", bbox_inches="tight"); plt.close(fig)

# ---------- 2. a ready board (illustrative)
named, _ = gk.named_road_lengths(roads, B)
hw = {e["name"] for e in crossings if e["name"] and gk.is_highway(e["name"])}
top = [n for n, _ in sorted(named.items(), key=lambda kv: -kv[1]) if not gk.is_highway(n)][:6]
print("top named:", [(n, round(named[n])) for n in top])
ex = []
for e in gk.board_exits(crossings, named):
    if all(gk._dist_m(e["lonlat"], o["lonlat"], k) > 2500 for o in ex):
        ex.append(e)
ex = gk.board_exits(ex, named, max_exits=5)
print("exits:", [(e["letter"], e["name"], e["side"]) for e in ex])
big = hw | set(top[:4]) | {e["name"] for e in ex}
print("big roads:", big)
big_feats = [f for f in roads if gk.clean_name(gk.feature_name(f)) in big]
fig, ax = frame("A ready board  (illustration)",
                "Few big roads, exits the community chose, safe places, helispots, numbered places — locations here are placeholders")
for c in gk.grid_cells(B)["features"]:
    ring = np.array(c["geometry"]["coordinates"][0])
    ax.plot(ring[:, 0], ring[:, 1], color="#777", lw=0.8, ls=(0, (5, 5)), zorder=1)
    x0, y1 = ring[:, 0].min(), ring[:, 1].max()
    ax.text(x0 + 0.004 * (B["east"]-B["west"]) * 3, y1 - 0.012 * (B["north"]-B["south"]) * 3, str(c["properties"]["cell"]),
            fontsize=20, color="#999", fontweight="bold", va="top", zorder=1)
colors = {}
for xy in lines(big_feats):
    ax.plot(xy[:, 0], xy[:, 1], color="white", lw=9, solid_capstyle="round", zorder=2)
for f in big_feats:
    n = gk.clean_name(gk.feature_name(f))
    col = "#f4a259" if gk.is_highway(n) else "#e9c46a"
    for ln in gk._lines(f):
        xy = np.array(ln); ax.plot(xy[:, 0], xy[:, 1], color=col, lw=6, solid_capstyle="round", zorder=3)
for p in gk.step_points(big_feats, B, gk.default_step_m(B)):
    ax.plot(*p, "o", ms=4.5, color="white", mec="#555", mew=0.8, zorder=4)
# exits the community picked: one per highway side plus Salmon Creek Rd
inward = {"north": (0, -38), "south": (0, 38), "east": (-55, 0), "west": (55, 0)}
for e in ex:
    ax.plot(*e["lonlat"], marker="o", ms=10, color="#d62828", mec="white", mew=2, zorder=7, clip_on=False)
    ax.annotate(f"EXIT {e['letter']}", e["lonlat"], xytext=inward[e["side"]], textcoords="offset points",
                fontsize=12, fontweight="bold", color="white", ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.35", fc="#d62828", ec="white", lw=1.5),
                arrowprops=dict(arrowstyle="-", color="#d62828", lw=2.5), zorder=8, annotation_clip=False)

def along(name, frac):
    pts = [pt for f in big_feats if gk.clean_name(gk.feature_name(f)) == name for ln in gk._lines(f) for pt in ln
           if B["west"] < pt[0] < B["east"] and B["south"] < pt[1] < B["north"]]
    pts.sort(key=lambda p: (p[0], p[1]))
    return pts[int(frac * (len(pts) - 1))]

hwy = sorted(hw)[0]
safe = [("S1", "Community hall", along(top[0], 0.45)), ("S2", "Ball field", along(ex[1]["name"], 0.3))]
heli = [("H1", "Ridge meadow", along(ex[3]["name"], 0.75))]
places = [(i + 1, n, along(r, fr)) for i, (n, r, fr) in enumerate([
    ("School", top[0], 0.2), ("Fire station", top[0], 0.75), ("Store", hwy, 0.5),
    ("Water tank", top[1], 0.35), ("Bridge", top[3], 0.5), ("Church", ex[2]["name"], 0.5)])]
dx = 0.012 * (B["east"] - B["west"])
for tag, name, p in safe:
    ax.plot(*p, marker="s", ms=20, color="#2a9d8f", mec="white", mew=2, zorder=6)
    ax.text(*p, tag, color="white", fontsize=9, fontweight="bold", ha="center", va="center", zorder=7)
    ax.text(p[0] + dx, p[1], name, fontsize=11, fontweight="bold", color="#1d6f65", va="center", path_effects=halo, zorder=7)
for tag, name, p in heli:
    ax.plot(*p, marker="o", ms=22, color="white", mec="#264653", mew=2.5, zorder=6)
    ax.text(*p, "H", color="#264653", fontsize=13, fontweight="bold", ha="center", va="center", zorder=7)
    ax.text(p[0] + dx, p[1], name, fontsize=11, fontweight="bold", color="#264653", va="center", path_effects=halo, zorder=7)
for n, name, p in places:
    ax.plot(*p, marker="o", ms=19, color="#264653", mec="white", mew=1.5, zorder=6)
    ax.text(*p, str(n), color="white", fontsize=10, fontweight="bold", ha="center", va="center", zorder=7)
    ax.text(p[0] + dx, p[1], name, fontsize=10.5, color="#264653", va="center", path_effects=halo, zorder=7)
fig.savefig(f"{OUT}/game_kit_ready.png", bbox_inches="tight"); plt.close(fig)

# ---------- 3. two cards
fig, axs = plt.subplots(1, 3, figsize=(10, 3.6), dpi=110)
cards = [("CHANCE", "Exit C is blocked", "Fire has reached\nDutyville Rd. Anyone\nheading for Exit C\nturns back 3 steps.", "#d62828"),
         ("CHANCE", "Tree down", "A tree is down across\nSalmon Creek Rd.\nLose your next turn\nunless you carry a saw.", "#e9a23b"),
         ("READINESS", "Where do we go?", "This atlas has no\nsafe places. Everyone\nloses a turn agreeing\non one. Then add it\nto the atlas.", "#2a9d8f")]
for ax, (kind, title, body, col) in zip(axs, cards):
    ax.set_xlim(0, 1); ax.set_ylim(0, 1.4); ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.03, 0.03), 0.94, 1.34, boxstyle="round,pad=0,rounding_size=0.06", fc="white", ec=col, lw=3))
    ax.add_patch(FancyBboxPatch((0.03, 1.12), 0.94, 0.25, boxstyle="round,pad=0,rounding_size=0.06", fc=col, ec=col))
    ax.text(0.5, 1.245, kind, color="white", fontsize=11, fontweight="bold", ha="center", va="center")
    ax.text(0.5, 0.95, title, fontsize=13, fontweight="bold", ha="center", va="center")
    ax.text(0.5, 0.52, body, fontsize=10, ha="center", va="center", linespacing=1.5)
fig.savefig(f"{OUT}/game_kit_cards.png", bbox_inches="tight"); plt.close(fig)
print("ok")
