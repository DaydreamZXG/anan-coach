from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_public_edge_does_not_expose_local_development_tools() -> None:
    caddyfile = PROJECT_ROOT.joinpath("ops/caddy/Caddyfile").read_text(
        encoding="utf-8"
    )

    assert "/coding-tools-mcp" not in caddyfile
    assert "127.0.0.1:18765" not in caddyfile
    assert "reverse_proxy 127.0.0.1:8000" in caddyfile
