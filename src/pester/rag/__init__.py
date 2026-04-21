"""RAG engine — requires the [search] optional extra."""

from pester.core.extras import make_optional_check

HAS_SEARCH, require_search = make_optional_check("chromadb", "search")
