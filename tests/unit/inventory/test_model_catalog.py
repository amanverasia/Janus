from janus.inventory.model_catalog import enrich_model_with_catalog, get_model_catalog


def test_model_catalog_has_full_port():
    catalog = get_model_catalog()
    assert len(catalog) >= 80


def test_enrich_model_with_catalog_known_entry():
    entry = enrich_model_with_catalog("gpt-4o", "openai")
    assert entry is not None
    assert entry["display_name"] == "GPT-4o"
    assert entry["capabilities"]["vision"] is True


def test_enrich_model_with_catalog_unknown_entry():
    assert enrich_model_with_catalog("not-a-real-model", "openai") is None
