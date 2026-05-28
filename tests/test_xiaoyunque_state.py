"""RunState 与各 sub-state dataclass 序列化往返测试。"""

from __future__ import annotations

from lib.xiaoyunque_shortplay.state import (
    CharacterState,
    EpisodeState,
    RunState,
    SceneState,
    ScriptSummary,
    ShotState,
    state_from_dict,
    state_to_dict,
)


def test_empty_runstate_roundtrip():
    s = RunState()
    d = state_to_dict(s)
    s2 = state_from_dict(d)
    assert s == s2


def test_full_runstate_roundtrip():
    s = RunState(
        parse_task_id="t1",
        design_task_id="t2",
        script=ScriptSummary(title="奶茶店", episode_count=3, core_setting="x"),
        characters=[
            CharacterState(
                character_id="C1",
                name="陈屿",
                body_image_url="https://x/body.png",
                bust_portrait_url="https://x/bust.png",
                appearance_count=3,
            )
        ],
        scenes=[SceneState(scene_id="S1", name="奶茶店", image_urls=["https://x/scene1.png"])],
        episodes=[
            EpisodeState(
                episode_id="1",
                episode_asset_id="ea1",
                title="求婚秘密",
                status="done",
                shots=[
                    ShotState(
                        shot_id="S1",
                        description="特写",
                        status=3,
                        video_url="https://x/s1.mp4",
                        duration_ms=5000,
                    )
                ],
                video_generate_task_id="vg1",
                video_compose_task_id="vc1",
                final_video_url="https://x/ep1.mp4",
                final_cover_url="https://x/ep1.png",
            )
        ],
        charge_count_total=23,
    )
    d = state_to_dict(s)
    s2 = state_from_dict(d)
    assert s2 == s
    assert d["episodes"][0]["status"] == "done"
    assert d["characters"][0]["name"] == "陈屿"


def test_runstate_partial_loads_with_missing_optional_fields():
    """老版本写入的 state 缺少新字段时也能解析。"""
    partial = {"parse_task_id": "t1"}
    s = state_from_dict(partial)
    assert s.parse_task_id == "t1"
    assert s.script is None
    assert s.episodes == []
    assert s.charge_count_total == 0
