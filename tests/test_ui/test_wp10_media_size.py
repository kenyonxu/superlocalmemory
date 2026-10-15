from superlocalmemory.server.routes import features


def test_media_size_reports_measured_install_and_separate_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "_ram_view", lambda: {})
    folder = tmp_path / "runtimes" / "media"
    folder.mkdir(parents=True)
    (folder / "model.bin").write_bytes(b"x" * 8192)
    (folder / "alias.bin").symlink_to(folder / "model.bin")
    view = features._media_view({"enabled": True, "env": {"state": "ready"}}, tmp_path)
    assert view["installed_bytes"] == 8192
    assert view["install_estimate_bytes"] >= int(2.8 * 1024**3)


def test_size_never_follows_a_symlink_outside_the_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(features, "_ram_view", lambda: {})
    folder = tmp_path / "runtimes" / "media"
    folder.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "data").write_bytes(b"x" * 10000)
    (folder / "outside").symlink_to(elsewhere, target_is_directory=True)
    assert features._media_view({}, tmp_path)["installed_bytes"] == 0
