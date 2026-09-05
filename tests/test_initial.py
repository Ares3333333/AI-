from pathlib import Path


def test_server_entrypoint_exists():
    assert Path('siteapp/web.py').is_file(), 'Server entry point must exist'


def test_home_template_exists():
    assert Path('templates/home.html').is_file(), 'Home template must exist'
