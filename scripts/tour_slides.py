#!/usr/bin/env python3
"""Render an atlas tour as slides on the box, without a slides asset in the config.

    python3 scripts/tour_slides.py scvfd scvfd --gslides
    python3 scripts/tour_slides.py south_fork_eel south-fork-eel --gslides --materialize 3dview
    python3 scripts/tour_slides.py scvfd scvfd --deck-only     # rebuild decks from existing PNGs

Writes {atlas}/staging/outlets/slides/: NN_slug.png, slides.json, index.html (remark
deck) and, with --gslides, rebuilds the atlas's Google Slides deck (id in gslides.json).
A full render takes ~8-10 min (3D slides 1-3 min each). Stopgap until #231 adds
slides assets and a /refresh path.

Runs in a session (#159): the atlas is locked and hydrated from S3, the slides
outlet is written into the workspace and written back on success. The Google
deck is an outside side effect — a failed run may still have rebuilt it.
"""
import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))
import atlas  # noqa: E402
import atlas_session  # noqa: E402
import gslides  # noqa: E402
import outlets_slides  # noqa: E402
import slides  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("atlas", help="atlas key, e.g. south_fork_eel")
    ap.add_argument("tour", help="tour file name in _tours/, without .md, e.g. south-fork-eel")
    ap.add_argument("--gslides", action="store_true", help="also rebuild the Google Slides deck")
    ap.add_argument("--deck-only", action="store_true",
                    help="skip rendering; rebuild index.html (and the Google deck) from slides.json")
    ap.add_argument("--materialize", nargs="*", default=[], metavar="ASSET",
                    help="assets to re-materialize first, e.g. 3dview webmap")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO)

    # Up to ~10 min with 3D slides; the session heartbeat keeps the lease.
    with atlas_session.open_session(args.atlas, purpose=f"tour_slides {args.tour}") as session:
        render(session.config, session.staging_dir, args)


def render(config, staging_dir, args):
    for asset in args.materialize:
        atlas.materialize(config, asset)
    if not args.deck_only:
        config["assets"]["slides"] = {"type": "outlet", "config": {"fetch_type": "outlet_slides"},
                                      "tour": args.tour, "gslides": args.gslides}
        print(outlets_slides.outlet_slides(config, "slides"))
        return
    out = Path(staging_dir) / "outlets" / "slides"
    manifest = json.load(open(out / "slides.json"))
    meta = {**manifest.get("meta", {}), "byline": f"{args.atlas} · {date.today().isoformat()}"}
    ok = [r for r in manifest["slides"] if r["status"] == "ok"]
    (out / "index.html").write_text(slides.deck_html(meta, ok))
    if args.gslides:
        url = gslides.sync_deck(out, meta, ok, f"{config['base_url'].rstrip('/')}/staging/outlets/slides",
                                tuple(manifest["size"]), config.get("admin_emails", []))
        print(url)


if __name__ == "__main__":
    main()
