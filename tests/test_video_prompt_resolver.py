"""video_prompt_resolver 抽出后的回归测试。"""

from __future__ import annotations

import pytest

from lib.video_prompt_resolver import (
    collect_sheet_paths,
    normalize_video_prompt,
)


class TestNormalizeVideoPrompt:
    def test_string_prompt_gets_negative_tail_appended(self):
        out = normalize_video_prompt("a cat dancing")
        assert "a cat dancing" in out
        assert out.startswith("a cat dancing")
        assert len(out) > len("a cat dancing")

    def test_structured_prompt_serialized_to_yaml(self):
        out = normalize_video_prompt(
            {
                "action": "cat walks across the room",
                "camera_motion": "slow pan",
                "ambiance_audio": "soft footsteps",
                "dialogue": [{"speaker": "Cat", "line": "meow"}],
            }
        )
        assert "Action:" in out
        assert "cat walks across the room" in out
        assert "Camera_Motion:" in out

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_video_prompt("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_video_prompt("   ")

    def test_missing_action_in_structured_raises(self):
        with pytest.raises(ValueError, match="action"):
            normalize_video_prompt({"action": ""})

    def test_invalid_dialogue_type_raises(self):
        with pytest.raises(ValueError, match="dialogue"):
            normalize_video_prompt({"action": "x", "dialogue": "not a list"})


class TestCollectSheetPaths:
    def test_returns_existing_paths_in_order(self, tmp_path):
        (tmp_path / "characters").mkdir()
        char_sheet = tmp_path / "characters" / "lilei.png"
        char_sheet.write_bytes(b"fake")
        (tmp_path / "scenes").mkdir()
        scene_sheet = tmp_path / "scenes" / "classroom.png"
        scene_sheet.write_bytes(b"fake")

        project = {
            "characters": {"李雷": {"character_sheet": "characters/lilei.png"}},
            "scenes": {"教室": {"scene_sheet": "scenes/classroom.png"}},
            "props": {},
        }
        items = [{"chars": ["李雷"], "scns": ["教室"], "prs": []}]

        paths, seen = collect_sheet_paths(
            project,
            tmp_path,
            items,
            char_field="chars",
            scene_field="scns",
            prop_field="prs",
        )

        assert paths == [char_sheet, scene_sheet]
        assert seen == {"characters/lilei.png", "scenes/classroom.png"}

    def test_skips_missing_files(self, tmp_path):
        project = {
            "characters": {"幽灵": {"character_sheet": "characters/ghost.png"}},
            "scenes": {},
            "props": {},
        }
        items = [{"chars": ["幽灵"], "scns": [], "prs": []}]

        paths, _ = collect_sheet_paths(
            project,
            tmp_path,
            items,
            char_field="chars",
            scene_field="scns",
            prop_field="prs",
        )

        assert paths == []
