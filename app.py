#!/usr/bin/env python3
"""Run the Merritt House tap list.

    python3 app.py                 # http://0.0.0.0:8080
    python3 app.py --port 5000
    python3 app.py --demo          # seed three sample beers on first run

Environment:
    TAPLIST_DATA    directory for the SQLite db and cached labels (default ./data)
    TAPLIST_TAPS    number of taps (default 3)
    TAPLIST_HOUSE   name shown on the board (default "Merritt House")
    TAPLIST_UNTAPPD_CLIENT_ID / TAPLIST_UNTAPPD_CLIENT_SECRET
                    enable Untappd search in addition to Open Food Facts
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from taplist import create_app  # noqa: E402

DEMO_BEERS = [
    {
        "name": "Pale Ale",
        "brewery": "Sierra Nevada",
        "style": "American Pale Ale",
        "abv": 5.6,
        "ibu": 38,
        "description": "The classic Cascade-hopped pale ale that helped kick off American craft brewing: "
                       "piney, citrusy hops over a clean, balanced malt body.",
    },
    {
        "name": "Guinness Draught",
        "brewery": "Guinness",
        "style": "Irish Dry Stout",
        "abv": 4.2,
        "ibu": 45,
        "description": "Nitro-poured dry stout with a creamy, tight head, roasted barley bitterness "
                       "and a smooth, surprisingly light finish.",
    },
    {
        "name": "Pilsner Urquell",
        "brewery": "Plzeňský Prazdroj",
        "style": "Czech Pilsner",
        "abv": 4.4,
        "ibu": 40,
        "description": "The original pilsner from Plzeň: golden, spicy Saaz hop aroma, soft bready malt "
                       "and a crisp, dry finish.",
    },
]


def seed_demo(app):
    db = app.extensions["taplist"]["db"]
    if any(t["beer"] for t in db.taps()):
        return
    for number, data in enumerate(DEMO_BEERS, start=1):
        if number > db.tap_count:
            break
        beer = db.create_beer(dict(data, source="demo", source_id=str(number)))
        db.set_tap(number, beer["id"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Merritt House tap list")
    parser.add_argument("--host", default=os.environ.get("TAPLIST_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TAPLIST_PORT", "8080")))
    parser.add_argument("--demo", action="store_true", help="seed sample beers if the taps are empty")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    app = create_app()
    if args.demo:
        seed_demo(app)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
