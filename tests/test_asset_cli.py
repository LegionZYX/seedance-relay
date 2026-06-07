import importlib
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]


class AssetCliTests(unittest.TestCase):
    def setUp(self):
        if str(PROJECT_DIR) not in sys.path:
            sys.path.insert(0, str(PROJECT_DIR))
        sys.modules.pop("create_asset_white_label", None)
        self.cli = importlib.import_module("create_asset_white_label")

    def test_build_create_body_supports_skip_moderation(self):
        body = self.cli.build_create_asset_body(
            group_id="group-1",
            url="https://media.example.test/uploads/ref.mp4",
            asset_type="Video",
            skip_moderation=True,
            name="ref.mp4",
            project_name="seedance-project",
        )

        self.assertEqual(body["GroupId"], "group-1")
        self.assertEqual(body["URL"], "https://media.example.test/uploads/ref.mp4")
        self.assertEqual(body["AssetType"], "Video")
        self.assertEqual(body["Name"], "ref.mp4")
        self.assertEqual(body["ProjectName"], "seedance-project")
        self.assertEqual(body["Moderation"], {"Strategy": "Skip"})

    def test_extract_asset_id_handles_common_response_shapes(self):
        self.assertEqual(self.cli.extract_asset_id({"Result": {"Id": "asset-a"}}), "asset-a")
        self.assertEqual(self.cli.extract_asset_id({"Result": {"AssetId": "asset-b"}}), "asset-b")
        self.assertEqual(self.cli.extract_asset_id({"AssetId": "asset-c"}), "asset-c")

    def test_build_create_asset_group_body_includes_optional_project(self):
        body = self.cli.build_create_asset_group_body(
            name="relay-face-assets",
            description="Self-service relay face whitelist",
            project_name="seedance-project",
        )

        self.assertEqual(body["Name"], "relay-face-assets")
        self.assertEqual(body["GroupType"], "AIGC")
        self.assertEqual(body["Description"], "Self-service relay face whitelist")
        self.assertEqual(body["ProjectName"], "seedance-project")

    def test_build_create_asset_group_body_defaults_to_aigc_group_type(self):
        body = self.cli.build_create_asset_group_body(name="relay-face-assets")

        self.assertEqual(body["GroupType"], "AIGC")

    def test_get_asset_body_includes_project_name_when_provided(self):
        calls = []

        def fake_request(action, body, ak, sk):
            calls.append((action, body, ak, sk))
            return {"Result": {"Status": "Active"}}

        self.cli.request_api = fake_request

        result = self.cli.get_asset("asset-a", "ak", "sk", "seedance-project")

        self.assertEqual(result["Result"]["Status"], "Active")
        self.assertEqual(
            calls,
            [("GetAsset", {"Id": "asset-a", "ProjectName": "seedance-project"}, "ak", "sk")],
        )

    def test_extract_asset_group_id_handles_common_response_shapes(self):
        self.assertEqual(self.cli.extract_asset_group_id({"Result": {"Id": "group-a"}}), "group-a")
        self.assertEqual(self.cli.extract_asset_group_id({"Result": {"GroupId": "group-b"}}), "group-b")
        self.assertEqual(self.cli.extract_asset_group_id({"AssetGroupId": "group-c"}), "group-c")


if __name__ == "__main__":
    unittest.main()
