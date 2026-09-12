import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from taplist import create_app  # noqa: E402
from taplist import providers as prov  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)


class FakeProvider:
    name = "fake"
    label = "Fake"

    def __init__(self, results=None, fail=False):
        self.results = results or []
        self.fail = fail

    def search(self, query, limit=12):
        if self.fail:
            raise RuntimeError("boom")
        return [r for r in self.results if query.lower() in r["name"].lower()]


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = FakeProvider(
            [
                {
                    "name": "Hazy Little Thing",
                    "brewery": "Sierra Nevada",
                    "style": "Hazy IPA",
                    "abv": 6.7,
                    "ibu": 35,
                    "description": "Juicy, unfiltered IPA.",
                    "label_url": "https://example.test/hazy.png",
                    "source": "fake",
                    "source_id": "42",
                }
            ]
        )
        self.app = create_app(data_dir=self.tmp.name, tap_count=3, providers=[self.provider], house_name="Test House")
        self.client = self.app.test_client()

    def tearDown(self):
        self.app.extensions["taplist"]["db"].close()
        self.tmp.cleanup()

    def post(self, url, payload):
        return self.client.post(url, data=json.dumps(payload), content_type="application/json")

    def test_index_ships_splash_screen(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('id="splash"', body)
        self.assertIn('class="watermark"', body)
        img = self.client.get("/static/img/splash.jpg")
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.mimetype, "image/jpeg")

    def test_state_has_empty_taps(self):
        data = self.client.get("/api/state").get_json()
        self.assertEqual(data["house"], "Test House")
        self.assertEqual([t["number"] for t in data["taps"]], [1, 2, 3])
        self.assertTrue(all(t["beer"] is None for t in data["taps"]))

    def test_search_merges_library_and_online(self):
        with mock.patch("taplist.labels.requests.get") as get:
            get.return_value.__enter__.return_value = _fake_response(PNG, "image/png")
            resp = self.post("/api/taps/1", {"beer": {"name": "House Lager", "brewery": "Us", "style": "Lager"}})
        self.assertEqual(resp.status_code, 200)
        data = self.client.get("/api/search?q=la").get_json()
        sources = [r["source"] for r in data["results"]]
        self.assertEqual(sources, ["library"])
        self.assertTrue(data["results"][0]["on_tap"])
        data = self.client.get("/api/search?q=hazy").get_json()
        self.assertEqual([r["source"] for r in data["results"]], ["fake"])
        self.assertEqual(data["results"][0]["label"], "https://example.test/hazy.png")

    def test_search_survives_provider_failure(self):
        self.provider.fail = True
        data = self.client.get("/api/search?q=hazy").get_json()
        self.assertEqual(data["results"], [])
        self.assertEqual(data["errors"][0]["source"], "fake")

    def test_tap_online_result_downloads_label_and_archives_previous(self):
        with mock.patch("taplist.labels.requests.get") as get:
            get.return_value.__enter__.return_value = _fake_response(PNG, "image/png")
            resp = self.post("/api/taps/2", {"beer": self.provider.results[0]})
        self.assertEqual(resp.status_code, 200)
        tap = resp.get_json()["taps"][1]
        self.assertEqual(tap["beer"]["name"], "Hazy Little Thing")
        self.assertTrue(tap["beer"]["label"].startswith("/labels/"))
        label_resp = self.client.get(tap["beer"]["label"])
        self.assertEqual(label_resp.status_code, 200)
        self.assertEqual(label_resp.data, PNG)

        # Replace it: the hazy goes to the archive.
        resp = self.post("/api/taps/2", {"beer": {"name": "Replacement", "source": "manual"}})
        self.assertEqual(resp.get_json()["taps"][1]["beer"]["name"], "Replacement")
        archive = self.client.get("/api/archive").get_json()["beers"]
        self.assertEqual([b["name"] for b in archive], ["Hazy Little Thing"])
        self.assertEqual(archive[0]["times_tapped"], 1)
        self.assertIsNotNone(archive[0]["last_untapped"])

        # Same online beer again reuses the library row instead of duplicating.
        with mock.patch("taplist.labels.requests.get") as get:
            resp = self.post("/api/taps/3", {"beer": self.provider.results[0]})
        get.assert_not_called()  # label already cached
        self.assertEqual(resp.get_json()["taps"][2]["beer"]["id"], tap["beer"]["id"])

    def test_label_fetch_failure_falls_back_to_remote_url(self):
        with mock.patch("taplist.labels.requests.get", side_effect=OSError("offline")):
            resp = self.post("/api/taps/1", {"beer": self.provider.results[0]})
        beer = resp.get_json()["taps"][0]["beer"]
        self.assertEqual(beer["label"], "https://example.test/hazy.png")

    def test_clear_tap_and_retap_from_archive(self):
        resp = self.post("/api/taps/1", {"beer": {"name": "Kicked Keg"}})
        beer_id = resp.get_json()["taps"][0]["beer"]["id"]
        resp = self.client.delete("/api/taps/1")
        self.assertIsNone(resp.get_json()["taps"][0]["beer"])
        resp = self.post("/api/taps/3", {"beer_id": beer_id})
        self.assertEqual(resp.get_json()["taps"][2]["beer"]["id"], beer_id)
        history = self.client.get("/api/history").get_json()["history"]
        self.assertEqual(len(history), 2)

    def test_manual_beer_with_upload_and_edit(self):
        resp = self.post("/api/beers", {"name": "Homebrew #7", "abv": "6.1", "ibu": ""})
        self.assertEqual(resp.status_code, 201)
        beer = resp.get_json()
        self.assertEqual(beer["abv"], 6.1)
        self.assertIsNone(beer["ibu"])
        resp = self.client.post(
            f"/api/beers/{beer['id']}/label",
            data={"label": (io.BytesIO(PNG), "label.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["label"].startswith("/labels/"))
        resp = self.client.put(f"/api/beers/{beer['id']}", data=json.dumps({"style": "Saison"}),
                               content_type="application/json")
        self.assertEqual(resp.get_json()["style"], "Saison")
        # Bad uploads are rejected cleanly.
        resp = self.client.post(
            f"/api/beers/{beer['id']}/label",
            data={"label": (io.BytesIO(b"hi"), "x.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)

    def test_delete_refuses_beer_on_tap(self):
        resp = self.post("/api/taps/1", {"beer": {"name": "Stuck"}})
        beer_id = resp.get_json()["taps"][0]["beer"]["id"]
        self.assertEqual(self.client.delete(f"/api/beers/{beer_id}").status_code, 409)
        self.client.delete("/api/taps/1")
        self.assertEqual(self.client.delete(f"/api/beers/{beer_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/archive").get_json()["beers"], [])

    def test_brewery_logo_upload_and_display_choice(self):
        resp = self.post("/api/taps/1", {"beer": {"name": "Logo Beer", "brewery": "Logo Brewing"}})
        beer = resp.get_json()["taps"][0]["beer"]
        self.assertEqual(beer["display_art"], "label")
        self.assertIsNone(beer["brewery_logo"])
        # Upload a brewery logo through its own endpoint.
        resp = self.client.post(
            f"/api/beers/{beer['id']}/brewery-logo",
            data={"label": (io.BytesIO(PNG), "logo.png", "image/png")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        updated = resp.get_json()
        self.assertTrue(updated["brewery_logo"].startswith("/labels/"))
        self.assertIsNone(updated["label"])  # the beer label slot is untouched
        # A bad upload leaves the existing logo alone.
        resp = self.client.post(
            f"/api/beers/{beer['id']}/brewery-logo",
            data={"label": (io.BytesIO(b"x"), "x.txt", "text/plain")},
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.client.get(f"/api/beers/{beer['id']}").get_json()["brewery_logo"], updated["brewery_logo"])
        # Choose what the board shows.
        resp = self.client.put(f"/api/beers/{beer['id']}", data=json.dumps({"display_art": "brewery"}),
                               content_type="application/json")
        self.assertEqual(resp.get_json()["display_art"], "brewery")
        self.assertEqual(self.client.get("/api/state").get_json()["taps"][0]["beer"]["display_art"], "brewery")
        resp = self.client.put(f"/api/beers/{beer['id']}", data=json.dumps({"display_art": "sideways"}),
                               content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_brewery_logo_url_is_fetched_and_replaced(self):
        with mock.patch("taplist.labels.requests.get") as get:
            get.return_value.__enter__.return_value = _fake_response(PNG, "image/png")
            resp = self.post("/api/taps/2", {"beer": {"name": "Remote Logo", "brewery_logo_url": "https://x.test/logo.png",
                                                      "display_art": "both"}})
        beer = resp.get_json()["taps"][1]["beer"]
        self.assertTrue(beer["brewery_logo"].startswith("/labels/"))
        first = beer["brewery_logo"]
        with mock.patch("taplist.labels.requests.get") as get:
            get.return_value.__enter__.return_value = _fake_response(PNG, "image/png")
            resp = self.client.put(f"/api/beers/{beer['id']}", data=json.dumps({"brewery_logo_url": "https://x.test/new.png"}),
                                   content_type="application/json")
        self.assertNotEqual(resp.get_json()["brewery_logo"], first)
        self.assertEqual(self.client.get(first).status_code, 404)

    def test_brewery_logo_finder(self):
        payload = [
            {"name": "Sierra Nevada Brewing Co", "city": "Chico", "state_province": "California", "country": "United States",
             "website_url": "https://www.sierranevada.com/"},
            {"name": "No Site Brewing", "website_url": None},
        ]
        with mock.patch("taplist.providers.requests.get") as get:
            get.return_value.json.return_value = payload
            data = self.client.get("/api/brewery-logo?q=sierra").get_json()
        self.assertEqual(len(data["results"]), 1)
        self.assertEqual(data["results"][0]["brewery"], "Sierra Nevada Brewing Co")
        self.assertIn("domain=sierranevada.com", data["results"][0]["logo_url"])
        self.assertEqual(data["results"][0]["place"], "Chico, California, United States")
        with mock.patch("taplist.providers.requests.get", side_effect=OSError("offline")):
            data = self.client.get("/api/brewery-logo?q=sierra").get_json()
        self.assertEqual(data["results"], [])
        self.assertIn("offline", data["error"])

    def test_old_database_is_migrated(self):
        import sqlite3
        from taplist.db import Database
        path = os.path.join(self.tmp.name, "old.db")
        conn = sqlite3.connect(path)
        conn.executescript(
            "CREATE TABLE beers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, brewery TEXT NOT NULL DEFAULT '',"
            " style TEXT NOT NULL DEFAULT '', abv REAL, ibu REAL, description TEXT NOT NULL DEFAULT '', label_file TEXT,"
            " label_url TEXT, source TEXT NOT NULL DEFAULT 'manual', source_id TEXT, created_at REAL NOT NULL);"
            "INSERT INTO beers(name, created_at) VALUES ('Vintage', 1);"
        )
        conn.commit(); conn.close()
        db = Database(path)
        beer = db.get_beer(1)
        self.assertEqual(beer["display_art"], "label")
        self.assertIsNone(beer["brewery_logo_url"])
        db.close()

    def test_validation(self):
        self.assertEqual(self.post("/api/beers", {"name": ""}).status_code, 400)
        self.assertEqual(self.post("/api/taps/9", {"beer": {"name": "x"}}).status_code, 404)
        self.assertEqual(self.post("/api/taps/1", {}).status_code, 400)
        self.assertEqual(self.post("/api/taps/1", {"beer_id": 999}).status_code, 404)


class ProviderTests(unittest.TestCase):
    def test_openfoodfacts_parsing(self):
        payload = {
            "products": [
                {
                    "code": "5010038100301",
                    "product_name": "Pale Ale",
                    "brands": "Sierra Nevada, Sierra Nevada Brewing",
                    "generic_name": "American pale ale",
                    "image_front_url": "https://images.test/front.jpg",
                    "categories_tags": ["en:beverages", "en:alcoholic-beverages", "en:beers", "en:pale-ales"],
                    "nutriments": {"alcohol_100g": "5.6"},
                    "ingredients_text": "Water, malted barley, hops, yeast",
                },
                {"code": "1", "product_name": ""},
            ]
        }
        with mock.patch("taplist.providers.requests.get") as get:
            get.return_value.json.return_value = payload
            results = prov.OpenFoodFacts().search("pale ale")
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r["brewery"], "Sierra Nevada")
        self.assertEqual(r["style"], "Pale Ale")
        self.assertEqual(r["abv"], 5.6)
        self.assertEqual(r["source_id"], "5010038100301")
        self.assertIn("Ingredients: Water", r["description"])
        self.assertEqual(r["label_url"], "https://images.test/front.jpg")

    def test_untappd_parsing(self):
        payload = {
            "response": {
                "beers": {
                    "items": [
                        {
                            "beer": {
                                "bid": 7, "beer_name": "Pliny the Elder", "beer_style": "IPA - Imperial / Double",
                                "beer_abv": 8.0, "beer_ibu": 100, "beer_description": "Big hoppy.",
                                "beer_label": "https://img.test/pliny.jpg",
                            },
                            "brewery": {"brewery_name": "Russian River", "brewery_label": "https://img.test/rr.png"},
                        }
                    ]
                }
            }
        }
        with mock.patch("taplist.providers.requests.get") as get:
            get.return_value.json.return_value = payload
            results = prov.Untappd("id", "secret").search("pliny")
        self.assertEqual(results[0]["brewery"], "Russian River")
        self.assertEqual(results[0]["ibu"], 100)
        self.assertEqual(results[0]["source_id"], "7")
        self.assertEqual(results[0]["brewery_logo_url"], "https://img.test/rr.png")

    def test_env_toggles(self):
        with mock.patch.dict(os.environ, {"TAPLIST_UNTAPPD_CLIENT_ID": "a", "TAPLIST_UNTAPPD_CLIENT_SECRET": "b"}):
            names = [p.name for p in prov.providers_from_env()]
        self.assertEqual(names, ["untappd", "openfoodfacts"])
        with mock.patch.dict(os.environ, {"TAPLIST_DISABLE_OFF": "1"}, clear=True):
            self.assertEqual(prov.providers_from_env(), [])


def _fake_response(body, content_type):
    resp = mock.MagicMock()
    resp.headers = {"Content-Type": content_type}
    resp.iter_content.return_value = [body]
    resp.raise_for_status.return_value = None
    return resp


if __name__ == "__main__":
    unittest.main()
