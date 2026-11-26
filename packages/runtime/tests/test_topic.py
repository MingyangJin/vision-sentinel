import pytest

from vs_runtime.contracts import OrchestratorAction
from vs_runtime.topic import ReadOnlyError, Topic, init_topic


def test_init_creates_layout(topic):
    for name in ("topic.toml", "goal.md", "vision_rubric.md", "orchestrator_notes.md"):
        assert (topic.dir / name).is_file()
    assert (topic.dir / "tools").is_dir() and (topic.dir / "state").is_dir()
    assert topic.id == "porch" and topic.config.camera_ids == ["cam1"]
    ctx = topic.context()
    assert ctx.goal.startswith("Tell me when a parcel arrives")
    assert ctx.main_model.model == "gemini-4-neon-medium"


def test_init_refuses_nonempty(topic):
    with pytest.raises(FileExistsError):
        init_topic(topic.dir, ["x"], "goal")


def test_init_from_template_skips_state(topic, tmp_path):
    (topic.state_dir / "ticks.jsonl").write_text("{}\n")
    (topic.tools_dir / "motion.py").write_text("print('{}')")
    t = init_topic(tmp_path / "copy", [], "", template=topic.dir)
    assert t.id == "copy" and t.config.camera_ids == ["cam1"]
    assert (t.tools_dir / "motion.py").exists()
    assert not (t.state_dir / "ticks.jsonl").exists()
    assert t.goal() == topic.goal()


def test_goal_is_read_only_to_every_agent_path(loop, topic):
    goal = topic.goal()
    attempts = [
        OrchestratorAction(name="edit_topic_tool", args={"path": "../goal.md", "code": "x"}),
        OrchestratorAction(name="edit_topic_tool", args={"path": "tools/../../goal.md", "code": "x"}),
        OrchestratorAction(name="edit_topic_tool", args={"path": "sub/../../goal.md", "code": "x"}),
    ]
    results = loop.dispatch(attempts, None)
    assert all(not r["ok"] for r in results)
    assert all("goal.md" in r["error"] for r in results)
    # The rubric tool writes the rubric and nothing else.
    loop.dispatch([OrchestratorAction(name="update_vision_rubric", args={"content": "new"})], None)
    assert topic.rubric() == "new"
    assert topic.goal() == goal
    with pytest.raises(ReadOnlyError):
        topic._agent_path("goal.md")


def test_tools_stay_in_tools_dir(topic):
    with pytest.raises(ReadOnlyError):
        topic.write_tool("../state/evil.py", "x")
    with pytest.raises(ReadOnlyError):
        topic.write_tool("nested/x.py", "x")
    p = topic.write_tool("tools/motion.py", "print(1)")
    assert p == topic.tools_dir.resolve() / "motion.py"
    assert list(topic.tool_versions()) == ["tools/motion.py"]


def test_update_config_round_trips(topic):
    topic.update_config(cadence_sec=30, review_policy={"digest_interval_sec": 600})
    again = Topic(topic.dir)
    assert again.config.cadence_sec == 30
    assert again.config.review_policy.digest_interval_sec == 600
    topic.update_config(review_policy={"digest_interval_sec": None})
    assert Topic(topic.dir).config.review_policy.digest_interval_sec is None
