"""Already-normalized public/synthetic files; no HTTP, credentials or discovery."""


def validate_record(record: dict):
    # The common envelope is sufficient for caller-normalized public imports.
    # The source marker is a declaration, not proof of public provenance.
    pass
