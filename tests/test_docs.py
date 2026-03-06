from pathlib import Path

from ansible_runner_service.auth import API_KEY_HEADER

PROJECT_ROOT = Path(__file__).parent.parent

DOC_FILES = [
    PROJECT_ROOT / "README.md",
    PROJECT_ROOT / "docs" / "usage-guide.md",
]


class TestDocsAuthHeader:
    def test_docs_use_correct_auth_header(self):
        """Documentation must use the actual header name from code."""
        for doc_path in DOC_FILES:
            if not doc_path.exists():
                continue
            content = doc_path.read_text()
            # Check the correct header appears
            assert API_KEY_HEADER in content, (
                f"{doc_path.name} does not reference {API_KEY_HEADER}"
            )

    def test_docs_do_not_use_wrong_header(self):
        """Documentation must not reference non-existent auth headers."""
        wrong_headers = ["X-Admin-Key", "X-Api-Key", "X-Auth-Key"]
        for doc_path in DOC_FILES:
            if not doc_path.exists():
                continue
            content = doc_path.read_text()
            for wrong in wrong_headers:
                assert wrong not in content, (
                    f"{doc_path.name} uses '{wrong}' instead of '{API_KEY_HEADER}'"
                )
