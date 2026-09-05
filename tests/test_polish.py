from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding='utf-8')


def test_home_copy_is_concrete_and_commercial():
    html = read('templates/home.html')
    assert 'Покажем, что на сайте мешает поиску и ИИ понять ваш бизнес' in html
    assert 'Если ИИ не нужен — так и скажем' in html
    assert 'Начать с сайта' in html


def test_home_has_non_fake_domain_interaction():
    html = read('templates/home.html')
    js = read('static/site.js')
    assert 'data-domain-preview' in html
    assert 'data-domain-input' in html
    assert 'domain-preview-active' in js


def test_reduced_motion_is_respected():
    css = read('static/site.css')
    assert '@media (prefers-reduced-motion: reduce)' in css
