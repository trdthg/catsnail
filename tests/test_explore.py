from __future__ import annotations

from pathlib import Path

import pytest

from catsnail.explore import ExploreError, author_test_with_codex


def test_explore_dry_run_builds_a_codex_prompt_without_starting_it(tmp_path: Path) -> None:
    task = tmp_path / "需求.md"
    scenario = tmp_path / "scenario.py"
    task.write_text("打开设置并验证界面", encoding="utf-8")
    scenario.write_text("# scenario\n", encoding="utf-8")

    result = author_test_with_codex(
        task,
        scenario,
        "test_desktop_login",
        target_dir=tmp_path / "target",
        workspace=tmp_path,
        dry_run=True,
    )

    assert result.returncode is None
    assert result.session.startswith("explore-")
    assert result.command[:4] == (
        "codex",
        "exec",
        "--sandbox",
        "danger-full-access",
    )
    assert 'mcp_servers.catsnail_studio.command="uv"' in result.command
    assert any("mcp_servers.catsnail_studio.args=" in part for part in result.command)
    assert "test_desktop_login" in result.command[-1]
    assert str(scenario.resolve()) in result.command[-1]
    assert "preconfigured `catsnail_studio` MCP server" in result.command[-1]
    assert "assert_screen" in result.command[-1]
    assert "exits with code `0`" in result.command[-1]
    assert "An XPASS" in result.command[-1]
    assert "never XFAILs" in result.command[-1]
    assert "Evidence protocol for an automatically classified defect" in result.command[-1]
    assert "explore-confirmed:" in result.command[-1]
    assert "Do not catch `Exception`" in result.command[-1]
    assert "ScreenAssertionError" in result.command[-1]
    assert "Work in small vertical slices" in result.command[-1]
    assert "Never let two agents edit the same Python file" in result.command[-1]
    assert "Finish with one full-suite run" in result.command[-1]
    assert "Do not copy, hard-link, rename, edit" in result.command[-1]
    assert "exploration blocker" in result.command[-1]
    assert "studio_snapshot -> inspect the returned image" in result.command[-1]
    assert "Do not batch coordinate\n   actions" in result.command[-1]
    assert "Screenshots are the source of truth" in result.command[-1]
    assert "Do not request elevated\n  permissions" in result.command[-1]
    assert "Make one bounded source pass" in result.command[-1]
    assert "already-tested GUI route" in result.command[-1]
    assert "Do not\n   enumerate the fixture directory" in result.command[-1]
    assert "do not depend on ImageMagick" in result.command[-1]
    assert "Call `studio_snapshot`\n   first" in result.command[-1]
    assert "server rejects stale revisions" in result.command[-1]
    assert "two unrelated single\n   clicks do not establish an input failure" in result.command[-1]
    assert "call `studio_reset`" in result.command[-1]
    assert "changed_pixels: 0" in result.command[-1]
    assert "two complete reviewed routes" in result.command[-1]
    assert any(result.session in part for part in result.command)
    assert not result.prompt.exists()


def test_explore_requires_a_nonempty_task_and_existing_scenario(tmp_path: Path) -> None:
    task = tmp_path / "task.md"
    task.write_text("\n", encoding="utf-8")
    scenario = tmp_path / "scenario.py"
    scenario.write_text("# scenario\n", encoding="utf-8")

    with pytest.raises(ExploreError, match="task file is empty"):
        author_test_with_codex(
            task,
            scenario,
            "test_boot",
            target_dir=tmp_path / "target",
            workspace=tmp_path,
            dry_run=True,
        )
