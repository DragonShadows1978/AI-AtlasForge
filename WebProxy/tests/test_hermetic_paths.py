from pathlib import Path

from WebProxy import service


def test_autouse_fixture_redirects_all_service_data_paths_outside_module_tree():
    module_dir = Path(service.__file__).resolve().parent

    for name in (
        "CACHE_DIR",
        "STATS_PATH",
        "IMAGE_CACHE_DIR",
        "PAPER_ARTIFACT_DIR",
    ):
        path = Path(getattr(service, name)).resolve()
        assert not path.is_relative_to(module_dir), f"{name} still points into {module_dir}"
