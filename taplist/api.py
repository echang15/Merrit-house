"""Flask application: static front-end plus a small JSON API."""

import logging
import os
import socket
import time

from flask import Flask, jsonify, request, send_from_directory, abort

from .db import Database
from .labels import LabelStore
from .providers import providers_from_env, search_all, find_brewery_logos

log = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_lan_cache = {"at": 0.0, "addrs": []}


def lan_addresses(ttl=60):
    """Best-effort list of names/addresses this machine answers to on the LAN.

    The LAN IP is found by asking the kernel which interface would route to a
    public address (no packet is sent). The `<hostname>.local` name works on
    Pi OS and most home networks via mDNS (avahi / Bonjour).
    """
    now = time.time()
    if now - _lan_cache["at"] < ttl:
        return list(_lan_cache["addrs"])
    addrs = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("10.255.255.255", 1))
            ip = sock.getsockname()[0]
        finally:
            sock.close()
        if ip and not ip.startswith("127."):
            addrs.append(ip)
    except OSError:
        pass
    host = socket.gethostname().split(".")[0].strip().lower()
    if host and host != "localhost":
        addrs.append(host + ".local")
    _lan_cache.update(at=now, addrs=addrs)
    return list(addrs)


def create_app(data_dir=None, tap_count=None, providers=None, house_name=None):
    data_dir = data_dir or os.environ.get("TAPLIST_DATA", os.path.join(os.getcwd(), "data"))
    tap_count = int(tap_count or os.environ.get("TAPLIST_TAPS", "3"))
    house_name = house_name or os.environ.get("TAPLIST_HOUSE", "Merrit House")

    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024
    app.config["JSON_SORT_KEYS"] = False

    db = Database(os.path.join(data_dir, "taplist.db"), tap_count=tap_count)
    labels = LabelStore(os.path.join(data_dir, "labels"))
    providers = providers_from_env() if providers is None else providers

    app.extensions["taplist"] = {"db": db, "labels": labels, "providers": providers}

    # -- helpers ------------------------------------------------------------

    def with_label(beer):
        """Attach the URLs the front-end should use for the beer label and brewery logo."""
        if beer is None:
            return None
        beer = dict(beer)
        beer["label"] = f"/labels/{beer['label_file']}" if beer.get("label_file") else beer.get("label_url")
        beer["brewery_logo"] = (
            f"/labels/{beer['brewery_logo_file']}" if beer.get("brewery_logo_file") else beer.get("brewery_logo_url")
        )
        return beer

    ART_KINDS = {"label": ("label_url", "label_file"), "brewery": ("brewery_logo_url", "brewery_logo_file")}

    def ensure_label(beer, kinds=("label", "brewery")):
        """Download remote artwork into local storage if we don't have it yet."""
        if not beer:
            return beer
        for kind in kinds:
            url_key, file_key = ART_KINDS[kind]
            if not beer.get(file_key) and beer.get(url_key):
                name = labels.fetch(beer["id"], beer[url_key])
                if name:
                    db.set_label_file(beer["id"], name, kind=kind)
                    beer[file_key] = name
        return beer

    def replace_art(beer_id, kind, url=None, upload=None):
        """Swap one artwork slot for a new remote URL or an uploaded file."""
        url_key, file_key = ART_KINDS[kind]
        old_file = db.get_beer(beer_id).get(file_key)
        if upload is not None:
            name = labels.save_upload(beer_id, upload.stream, upload.mimetype)  # validates before we touch anything
            db.set_label_file(beer_id, name, kind=kind)
            labels.remove(old_file)
            return db.get_beer(beer_id)
        labels.remove(old_file)
        db.set_label_file(beer_id, None, kind=kind)
        beer = db.update_beer(beer_id, {url_key: url})
        return ensure_label(beer, kinds=(kind,))

    def lan_urls():
        """Addresses phones on the same network can open this app at."""
        port = request.host.rpartition(":")[2] if ":" in request.host else ""
        suffix = f":{port}" if port and port != "80" else ""
        return [f"http://{a}{suffix}/" for a in lan_addresses()]

    def state():
        return {
            "version": db.version(),
            "house": house_name,
            "tap_count": tap_count,
            "urls": lan_urls(),
            "taps": [
                {"number": t["number"], "tapped_at": t["tapped_at"], "beer": with_label(t["beer"])}
                for t in db.taps()
            ],
            "providers": [{"source": p.name, "label": p.label} for p in providers],
            "now": time.time(),
        }

    def resolve_beer(payload):
        """Turn a request payload into a saved library beer.

        Accepts {"beer_id": 12} or {"beer": {...fields...}}. When the submitted
        beer carries a source/source_id that we've already saved, the existing
        row is updated instead of creating a duplicate.
        """
        if payload.get("beer_id") is not None:
            beer = db.get_beer(int(payload["beer_id"]))
            if beer is None:
                abort(404, "beer not found")
            if payload.get("beer"):
                beer = db.update_beer(beer["id"], payload["beer"])
            return beer
        data = payload.get("beer")
        if not isinstance(data, dict):
            abort(400, "beer_id or beer object required")
        existing = db.find_by_source(data.get("source"), data.get("source_id"))
        if existing and existing["source"] != "manual":
            return db.update_beer(existing["id"], data)
        try:
            return db.create_beer(data)
        except ValueError as exc:
            abort(400, str(exc))

    # -- pages & files -------------------------------------------------------

    @app.route("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/labels/<path:filename>")
    def label_file(filename):
        resp = send_from_directory(labels.directory, filename)
        resp.cache_control.max_age = 60 * 60 * 24 * 30
        return resp

    # -- state ---------------------------------------------------------------

    @app.get("/api/state")
    def api_state():
        return jsonify(state())

    @app.get("/api/version")
    def api_version():
        return jsonify({"version": db.version()})

    # -- search --------------------------------------------------------------

    @app.get("/api/search")
    def api_search():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"query": q, "results": [], "errors": []})
        on_tap = db.on_tap_ids()
        library = []
        for beer in db.search_library(q):
            item = with_label(beer)
            item["beer_id"] = beer["id"]
            item["on_tap"] = beer["id"] in on_tap
            item["source"] = "library"
            item["origin"] = beer["source"]
            library.append(item)
        online, errors = ([], []) if request.args.get("local") else search_all(providers, q)
        # Hide online hits we already have in the library.
        known = {(b["source"], b["source_id"]) for b in library if b.get("source_id")}
        known |= {(b["origin"], b["source_id"]) for b in library if b.get("source_id")}
        online = [r for r in online if (r["source"], r["source_id"]) not in known]
        for r in online:
            r["label"] = r.get("label_url")
        return jsonify({"query": q, "results": library + online, "errors": errors})

    # -- taps ----------------------------------------------------------------

    @app.post("/api/taps/<int:number>")
    def api_set_tap(number):
        if number < 1 or number > tap_count:
            abort(404, "no such tap")
        payload = request.get_json(silent=True) or {}
        beer = resolve_beer(payload)
        beer = ensure_label(beer)
        db.set_tap(number, beer["id"])
        return jsonify(state())

    @app.delete("/api/taps/<int:number>")
    def api_clear_tap(number):
        if number < 1 or number > tap_count:
            abort(404, "no such tap")
        db.set_tap(number, None)
        return jsonify(state())

    # -- beers / archive -----------------------------------------------------

    @app.get("/api/archive")
    def api_archive():
        return jsonify({"beers": [with_label(b) for b in db.archive()], "version": db.version()})

    @app.get("/api/history")
    def api_history():
        return jsonify({"history": db.history()})

    @app.get("/api/metrics")
    def api_metrics():
        data = db.metrics()
        for key in ("top_rated", "most_tapped", "longest"):
            data[key] = [with_label(b) for b in data[key]]
        data["version"] = db.version()
        return jsonify(data)

    @app.get("/api/beers/<int:beer_id>")
    def api_get_beer(beer_id):
        beer = db.get_beer(beer_id)
        if beer is None:
            abort(404)
        return jsonify(with_label(beer))

    @app.post("/api/beers")
    def api_create_beer():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "JSON body required")
        try:
            beer = db.create_beer(data)
        except ValueError as exc:
            abort(400, str(exc))
        beer = ensure_label(beer)
        return jsonify(with_label(beer)), 201

    @app.put("/api/beers/<int:beer_id>")
    @app.patch("/api/beers/<int:beer_id>")
    def api_update_beer(beer_id):
        if db.get_beer(beer_id) is None:
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "JSON body required")
        try:
            beer = db.update_beer(beer_id, data)
        except ValueError as exc:
            abort(400, str(exc))
        # A new remote image was chosen: drop the cached file and fetch again.
        if "label_url" in data:
            beer = replace_art(beer_id, "label", url=data.get("label_url"))
        if "brewery_logo_url" in data:
            beer = replace_art(beer_id, "brewery", url=data.get("brewery_logo_url"))
        return jsonify(with_label(beer))

    @app.delete("/api/beers/<int:beer_id>")
    def api_delete_beer(beer_id):
        if beer_id in db.on_tap_ids():
            abort(409, "beer is currently on tap")
        beer = db.delete_beer(beer_id)
        if beer is None:
            abort(404)
        labels.remove(beer.get("label_file"))
        labels.remove(beer.get("brewery_logo_file"))
        return jsonify({"deleted": beer_id, "version": db.version()})

    @app.post("/api/beers/<int:beer_id>/label")
    @app.post("/api/beers/<int:beer_id>/brewery-logo")
    def api_upload_art(beer_id):
        if db.get_beer(beer_id) is None:
            abort(404)
        kind = "brewery" if request.path.endswith("/brewery-logo") else "label"
        upload = request.files.get("label") or request.files.get("file")
        if upload is None:
            abort(400, "multipart field 'label' required")
        try:
            beer = replace_art(beer_id, kind, upload=upload)
        except ValueError as exc:
            abort(400, str(exc))
        return jsonify(with_label(beer))

    @app.get("/api/brewery-logo")
    def api_brewery_logo():
        q = (request.args.get("q") or "").strip()
        if len(q) < 2:
            return jsonify({"query": q, "results": [], "error": None})
        try:
            return jsonify({"query": q, "results": find_brewery_logos(q), "error": None})
        except Exception as exc:  # network down, API changed...
            log.warning("brewery lookup failed: %s", exc)
            return jsonify({"query": q, "results": [], "error": str(exc)})

    # -- errors --------------------------------------------------------------

    @app.errorhandler(400)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    def json_error(err):
        return jsonify({"error": getattr(err, "description", str(err))}), err.code

    return app
