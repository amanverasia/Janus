from janus.dashboard.api_v2 import _matching_catalog_id
from janus.dashboard.catalog import get_catalog


def test_matching_catalog_id_infers_legacy_provider_metadata() -> None:
    catalog = get_catalog()

    assert (
        _matching_catalog_id(
            {
                "id": "legacy-google-row",
                "catalog_id": None,
                "prefix": "gemini",
                "api_type": "gemini",
            },
            catalog,
        )
        == "gemini"
    )


def test_matching_catalog_id_does_not_guess_ambiguous_custom_provider() -> None:
    assert (
        _matching_catalog_id(
            {
                "id": "custom-row",
                "catalog_id": None,
                "prefix": "shared",
                "api_type": "openai_compat",
            },
            {
                "one": {"prefix": "shared", "api_type": "openai_compat"},
                "two": {"prefix": "shared", "api_type": "openai_compat"},
            },
        )
        is None
    )


def test_matching_catalog_id_does_not_trust_incompatible_row_id() -> None:
    catalog = get_catalog()

    assert (
        _matching_catalog_id(
            {
                "id": "openai",
                "catalog_id": None,
                "prefix": "company-proxy",
                "api_type": "openai_compat",
            },
            catalog,
        )
        is None
    )
