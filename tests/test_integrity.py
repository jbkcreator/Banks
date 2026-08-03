"""Immutable Core hash check — Part 5: "hashed; only Josh edits directly"."""

from banks.integrity import ImmutableCoreTampered, verify, write_approved_hash


def test_verify_passes_on_unmodified_constitution(tmp_path):
    constitution = tmp_path / "constitution.md"
    constitution.write_text(
        "# Banks\n\n## HARD RULES — IMMUTABLE CORE\nDrafts only, permanently.\n\n## Other\nmore text\n",
        encoding="utf-8",
    )
    hash_path = tmp_path / "constitution.hash"

    write_approved_hash(constitution, hash_path)
    verify(constitution, hash_path)  # should not raise


def test_verify_fails_without_approved_hash(tmp_path):
    constitution = tmp_path / "constitution.md"
    constitution.write_text(
        "## HARD RULES — IMMUTABLE CORE\nDrafts only.\n", encoding="utf-8"
    )
    hash_path = tmp_path / "constitution.hash"  # never written

    try:
        verify(constitution, hash_path)
        assert False, "should have raised"
    except ImmutableCoreTampered:
        pass


def test_verify_detects_tampered_immutable_core(tmp_path):
    constitution = tmp_path / "constitution.md"
    constitution.write_text(
        "## HARD RULES — IMMUTABLE CORE\nDrafts only, permanently.\n", encoding="utf-8"
    )
    hash_path = tmp_path / "constitution.hash"
    write_approved_hash(constitution, hash_path)

    # Someone edits the hard rules without going through re-approval.
    constitution.write_text(
        "## HARD RULES — IMMUTABLE CORE\nDrafts AND sends, permanently.\n", encoding="utf-8"
    )

    try:
        verify(constitution, hash_path)
        assert False, "should have raised on tampered core"
    except ImmutableCoreTampered:
        pass


def test_edits_outside_immutable_core_do_not_trip_the_hash(tmp_path):
    """Only the Immutable Core section is hashed — editing memory notes
    or standing jobs elsewhere in the file shouldn't halt Banks."""
    constitution = tmp_path / "constitution.md"
    constitution.write_text(
        "## Standing Jobs\n1. Do the thing.\n\n"
        "## HARD RULES — IMMUTABLE CORE\nDrafts only, permanently.\n\n"
        "## Memory\nindex.md\n",
        encoding="utf-8",
    )
    hash_path = tmp_path / "constitution.hash"
    write_approved_hash(constitution, hash_path)

    constitution.write_text(
        "## Standing Jobs\n1. Do the thing, revised.\n\n"
        "## HARD RULES — IMMUTABLE CORE\nDrafts only, permanently.\n\n"
        "## Memory\nindex.md updated\n",
        encoding="utf-8",
    )

    verify(constitution, hash_path)  # should not raise
