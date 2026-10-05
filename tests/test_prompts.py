"""Guards for the extraction prompt.

Every stored extraction is tagged with EXTRACTION_VERSION. If someone edits the prompt or schema but
forgets to bump the version, old and new extractions get mixed silently and nobody re-extracts.
This test catches that: each version is pinned to the fingerprint of the prompt it was made with.
"""
from jobpilot import prompts

# version -> fingerprint of prompt + schema + DDL at that version. Add a line when you bump.
KNOWN_VERSIONS = {
    "v1": "442679554410",
}


def test_prompt_change_requires_version_bump():
    current = prompts.extraction_fingerprint()
    assert prompts.EXTRACTION_VERSION in KNOWN_VERSIONS, (
        f"New version {prompts.EXTRACTION_VERSION!r}: add it to KNOWN_VERSIONS with fingerprint {current!r}")
    assert KNOWN_VERSIONS[prompts.EXTRACTION_VERSION] == current, (
        f"The extraction prompt, schema or DDL changed (fingerprint now {current!r}) but "
        f"EXTRACTION_VERSION is still {prompts.EXTRACTION_VERSION!r}. Bump it in src/jobpilot/prompts.py "
        "and add the new version here, so old postings get re-extracted with the new prompt.")


def test_schema_and_ddl_list_the_same_fields():
    # The model's JSON is parsed with the DDL; a field in one but not the other is silently lost.
    ddl_fields = {part.strip().split()[0] for part in prompts.EXTRACTION_DDL.split(", ") if part.strip()}
    assert ddl_fields == set(prompts.EXTRACTION_FIELDS)
