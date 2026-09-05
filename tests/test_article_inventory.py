from siteapp.content import load_articles


def test_full_journal_has_exactly_100_unique_articles():
    articles = load_articles()
    expected = {f'A{i:03d}' for i in range(1, 101)}
    assert len(articles) == 100
    assert {article['id'] for article in articles} == expected
    assert len({article['slug'] for article in articles}) == 100


def test_articles_are_not_counted_as_published_without_editorial_acceptance():
    articles = load_articles()
    for article in articles:
        if article['status'] == 'published':
            assert article['fact_check_status'] == 'checked'
            assert article['published_at']
