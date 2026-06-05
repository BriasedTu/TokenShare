from pathlib import Path


def test_tokenshare_package_layout_imports() -> None:
    import tokenshare

    package_root = Path(tokenshare.__file__).resolve().parent

    assert package_root.name == "tokenshare"
    assert (package_root / "core").is_dir()
    assert (package_root / "storage").is_dir()
    assert (package_root / "plugins").is_dir()
    assert (package_root / "executors").is_dir()
    assert (package_root / "replay").is_dir()
    assert (package_root / "experiments").is_dir()
