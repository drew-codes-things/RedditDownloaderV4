import os

import pytest

import worker


@pytest.mark.parametrize("segment", ["..", ".", "...", " .. "])
def test_dot_only_subreddit_names_stay_inside_the_download_folder(tmp_path, monkeypatch, segment):
    inner = tmp_path / "inner"
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", str(inner))
    target = os.path.realpath(worker._subreddit_dir(segment))
    assert os.path.dirname(target) == os.path.realpath(inner)


def test_normal_subreddit_names_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "DOWNLOAD_DIR", str(tmp_path))
    assert worker._subreddit_dir("Earth.Porn_2") == os.path.join(str(tmp_path), "Earth.Porn_2")
