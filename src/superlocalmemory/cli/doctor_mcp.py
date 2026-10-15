"""Diagnostics for code still loaded in long-lived MCP hosts."""

from superlocalmemory.infra.data_root import canonical_data_root
from superlocalmemory.infra.mcp_processes import snapshot


def connected_apps_finding() -> tuple[str, str, str]:
    view = snapshot(canonical_data_root())
    stale = sorted(
        {
            row["name"]
            for row in view["apps"]
            if row["stale_version"] or row.get("version_reported") is False
        }
    )
    if stale:
        apps = ", ".join(stale)
        return (
            "WARN",
            (f"{view['stale_count']} stale and {view['unreported_count']} "
             f"unreported MCP release(s): {apps}"),
            f"Reconnect in Dashboard → Connected apps, or restart: {apps}",
        )
    return "PASS", f"{view['count']} live MCP process(es), release {view['installed_version']}", ""
