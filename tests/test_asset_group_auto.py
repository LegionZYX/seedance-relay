import importlib
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class AssetGroupAutoCreateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        os.environ["DB_PATH"] = str(root / "relay.sqlite")
        os.environ["VIDEO_DIR"] = str(root / "videos")
        os.environ["UPLOAD_DIR"] = str(root / "uploads")
        os.environ["PUBLIC_DOMAIN"] = "media.example.test"
        os.environ["ADMIN_PASSWORD"] = ""
        os.environ["BYTEPLUS_ACCESS_KEY_ID"] = "ak-test"
        os.environ["BYTEPLUS_ACCESS_KEY_SECRET"] = "sk-test"
        os.environ["MODELARK_ASSET_AUTO_CREATE_GROUP"] = "true"
        os.environ["MODELARK_ASSET_GROUP_NAME"] = "relay-face-assets"
        os.environ["MODELARK_PROJECT_NAME"] = "seedance-project"
        os.environ.pop("MODELARK_ASSET_GROUP_ID", None)
        os.environ.pop("FACE_ASSET_ALLOWLIST", None)

        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("relay_server", None)
        self.server = importlib.import_module("relay_server")

    def tearDown(self):
        os.environ.pop("MODELARK_PROJECT_NAME", None)
        self.tmp.cleanup()

    def test_register_upload_creates_asset_group_once_and_reuses_cached_group_id(self):
        calls = []

        def fake_request(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateAssetGroup":
                return {"Result": {"Id": "group-auto"}}
            if action == "CreateAsset":
                return {"Result": {"Id": f"asset-created-{len(calls)}"}}
            raise AssertionError(f"unexpected action: {action}")

        self.server.request_asset_api = fake_request

        first = self.server._register_upload_asset(
            "https://media.example.test/uploads/face-a.jpg",
            "image",
        )
        second = self.server._register_upload_asset(
            "https://media.example.test/uploads/face-b.jpg",
            "image",
        )

        self.assertEqual(first["asset_url"], "asset://asset-created-2")
        self.assertEqual(second["asset_url"], "asset://asset-created-3")
        self.assertEqual(
            [call["action"] for call in calls],
            ["CreateAssetGroup", "CreateAsset", "CreateAsset"],
        )
        self.assertEqual(calls[0]["body"]["Name"], "relay-face-assets")
        self.assertEqual(calls[0]["body"]["GroupType"], "AIGC")
        self.assertEqual(calls[0]["body"]["ProjectName"], "seedance-project")
        self.assertEqual(calls[1]["body"]["GroupId"], "group-auto")
        self.assertEqual(calls[1]["body"]["Name"], "face-a.jpg")
        self.assertEqual(calls[1]["body"]["ProjectName"], "seedance-project")
        self.assertEqual(calls[2]["body"]["GroupId"], "group-auto")
        self.assertEqual(calls[2]["body"]["ProjectName"], "seedance-project")

        db = self.server.get_db()
        row = db.execute(
            "SELECT value FROM settings WHERE key=?",
            ("modelark_asset_group_id",),
        ).fetchone()
        db.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "group-auto")

    def test_register_upload_prefers_customer_asset_group_from_user_note(self):
        calls = []

        def fake_request(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateAsset":
                return {"Result": {"Id": "asset-customer-group"}}
            raise AssertionError(f"unexpected action: {action}")

        self.server.request_asset_api = fake_request

        result = self.server._register_upload_asset(
            "https://media.example.test/uploads/peter.jpg",
            "image",
            {"note": '{"modelark_asset_group_id":"group-customer","byteplus_project_name":"peterlv"}'},
        )

        self.assertEqual(result["asset_url"], "asset://asset-customer-group")
        self.assertEqual([call["action"] for call in calls], ["CreateAsset"])
        self.assertEqual(calls[0]["body"]["GroupId"], "group-customer")
        self.assertEqual(calls[0]["body"]["ProjectName"], "peterlv")
        self.assertEqual(calls[0]["body"]["Moderation"], {"Strategy": "Skip"})

    def test_register_upload_waits_for_asset_with_project_name(self):
        calls = []
        self.server.ASSET_AUTO_REGISTER_WAIT_SECONDS = 1
        self.server.ASSET_AUTO_REGISTER_WAIT_INTERVAL = 0

        def fake_request(action, body, ak, sk):
            calls.append({"action": action, "body": body, "ak": ak, "sk": sk})
            if action == "CreateAssetGroup":
                return {"Result": {"Id": "group-auto"}}
            if action == "CreateAsset":
                return {"Result": {"Id": "asset-wait", "Status": "Processing"}}
            if action == "GetAsset":
                return {"Result": {"Status": "Active"}}
            raise AssertionError(f"unexpected action: {action}")

        self.server.request_asset_api = fake_request

        result = self.server._register_upload_asset(
            "https://media.example.test/uploads/wait.jpg",
            "image",
        )

        self.assertEqual(result["asset_status"], "Active")
        self.assertEqual(result["asset_group_id"], "group-auto")
        self.assertEqual(result["project_name"], "seedance-project")
        self.assertEqual(result["group_type"], "AIGC")
        get_asset = next(call for call in calls if call["action"] == "GetAsset")
        self.assertEqual(get_asset["body"], {"Id": "asset-wait", "ProjectName": "seedance-project"})


if __name__ == "__main__":
    unittest.main()
