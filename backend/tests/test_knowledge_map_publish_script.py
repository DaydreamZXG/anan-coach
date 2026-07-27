from __future__ import annotations

import os
from pathlib import Path
import subprocess


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLISH_SCRIPT = REPO_ROOT / "scripts" / "server_refresh_knowledge_map_nodes.sh"


def test_publish_script_sends_parameters_and_sql_to_one_psql_connection(
    tmp_path: Path,
) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ssh = fake_bin / "ssh"
    fake_ssh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
count_file="$CAPTURE_DIR/count"
count=0
if [[ -f "$count_file" ]]; then
  count="$(cat "$count_file")"
fi
count=$((count + 1))
printf '%s' "$count" > "$count_file"
printf '%s\\n' "$@" > "$CAPTURE_DIR/call_${count}.args"
cat > "$CAPTURE_DIR/call_${count}.stdin"
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    refresh_sql = tmp_path / "refresh.sql"
    refresh_sql.write_text(
        "\\set ON_ERROR_STOP on\nbegin;\nselect 'generator-body-marker';\ncommit;\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "GONGKAO_SSH_KEY": str(tmp_path / "unused-test-key"),
        "GONGKAO_KNOWLEDGE_MAP_SQL": str(refresh_sql),
        "GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION": "knowledge-map@test-v1",
        "GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION": "question-bank@test-v1",
        "GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL": "2099-01-01T00:00:00+08:00",
    }

    subprocess.run(
        [str(PUBLISH_SCRIPT)],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert (capture_dir / "count").read_text(encoding="utf-8") == "1"
    publish_command = (capture_dir / "call_1.args").read_text(encoding="utf-8")
    publish_input = (capture_dir / "call_1.stdin").read_text(encoding="utf-8")
    assert publish_command.count("psql") == 1
    assert "--set=release_version=knowledge-map@test-v1" in publish_command
    assert "--set=source_data_version=question-bank@test-v1" in publish_command
    assert "--set=fresh_until=2099-01-01T00:00:00+08:00" in publish_command
    assert publish_input.count("select set_config(") == 3
    assert "app.knowledge_map_release_version" in publish_input
    assert "app.knowledge_map_source_data_version" in publish_input
    assert "app.knowledge_map_fresh_until" in publish_input
    assert publish_input.index("select set_config(") < publish_input.index(
        "generator-body-marker"
    )
    assert publish_input.index("generator-body-marker") < publish_input.index(
        "verify committed knowledge map release"
    )
    assert (
        "committed knowledge-map release failed postcondition validation"
        in publish_input
    )
    for canonical_code, canonical_name in (
        ("political_theory", "政治理论"),
        ("general_knowledge", "常识判断"),
        ("verbal_comprehension", "言语理解与表达"),
        ("quantitative_relations", "数量关系"),
        ("judgment_reasoning", "判断推理"),
        ("data_analysis", "资料分析"),
    ):
        assert f"('{canonical_code}', '{canonical_name}')" in publish_input
    assert "knowledge map sample" in publish_input


def test_publish_script_local_mode_uses_one_psql_process(tmp_path: Path) -> None:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$@" > "$CAPTURE_DIR/local.args"
cat > "$CAPTURE_DIR/local.stdin"
""",
        encoding="utf-8",
    )
    fake_sudo.chmod(0o755)
    refresh_sql = tmp_path / "refresh.sql"
    refresh_sql.write_text(
        "\\set ON_ERROR_STOP on\nbegin;\nselect 'local-body-marker';\ncommit;\n",
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CAPTURE_DIR": str(capture_dir),
        "GONGKAO_KNOWLEDGE_MAP_SQL": str(refresh_sql),
        "GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION": "knowledge-map@local-v1",
        "GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION": "question-bank@local-v1",
        "GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL": "2099-01-01T00:00:00+08:00",
        "GONGKAO_KNOWLEDGE_MAP_DATABASE_URL": "postgresql:///test_db",
        "GONGKAO_KNOWLEDGE_MAP_DATABASE_OS_USER": "test_owner",
    }

    subprocess.run(
        [str(PUBLISH_SCRIPT), "--local"],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )

    command = (capture_dir / "local.args").read_text(encoding="utf-8")
    publish_input = (capture_dir / "local.stdin").read_text(encoding="utf-8")
    assert command.count("psql") == 1
    assert "-u\ntest_owner\npsql\n" in command
    assert "--dbname=postgresql:///test_db" in command
    assert "local-body-marker" in publish_input
    assert "verify committed knowledge map release" in publish_input


def test_publish_script_requires_all_release_parameters(tmp_path: Path) -> None:
    environment = {
        **os.environ,
        "GONGKAO_KNOWLEDGE_MAP_SQL": str(
            REPO_ROOT / "scripts" / "sql" / "refresh_knowledge_map_nodes.sql"
        ),
    }
    environment.pop("GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION", None)
    environment.pop("GONGKAO_KNOWLEDGE_MAP_SOURCE_DATA_VERSION", None)
    environment.pop("GONGKAO_KNOWLEDGE_MAP_FRESH_UNTIL", None)

    result = subprocess.run(
        [str(PUBLISH_SCRIPT)],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "GONGKAO_KNOWLEDGE_MAP_RELEASE_VERSION is required" in result.stderr
