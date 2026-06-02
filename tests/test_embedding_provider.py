from app.services.fake_embedding_provider import FakeEmbeddingProvider


def test_fake_embedding_dimension():
    provider = FakeEmbeddingProvider(dim=384)
    assert provider.embedding_dimension() == 384


def test_fake_embedding_deterministic():
    provider = FakeEmbeddingProvider(dim=384)
    first = provider.embed_texts(["hello world"])
    second = provider.embed_texts(["hello world"])
    assert first == second
    assert len(first[0]) == 384


def test_fake_embedding_different_texts_differ():
    provider = FakeEmbeddingProvider(dim=384)
    vectors = provider.embed_texts(["alpha", "beta"])
    assert vectors[0] != vectors[1]
