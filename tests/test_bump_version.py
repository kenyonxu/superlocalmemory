"""Release-version updater must not hide a mixed generated-source tree."""

from __future__ import annotations

from scripts import bump_version


def test_read_glob_reports_every_distinct_version(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bump_version, "_ROOT", tmp_path)
    skills = tmp_path / "plugin-src" / "skills"
    skills.mkdir(parents=True)
    (skills / "a.md").write_text("SuperLocalMemory v4.1.10\n", encoding="utf-8")
    (skills / "b.md").write_text("SuperLocalMemory v4.1.9\n", encoding="utf-8")

    found = bump_version._read_glob(
        "plugin-src/**/*.md",
        r"SuperLocalMemory v([0-9]+\.[0-9]+\.[0-9]+)",
    )

    assert found == "<mixed: 4.1.10, 4.1.9>"
