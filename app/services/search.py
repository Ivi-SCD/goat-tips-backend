"""
Web Search Service
==================
Vertex AI Search (Discovery Engine) + Groq LLM interpretation in PT-BR.

Engine: goat-tips-search-ai_1774214623111 (project goat-tips-491019, location global)
Auth:   config/gcp_service_account.json
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_PROJECT_ID = "goat-tips-491019"
_LOCATION = "global"
_ENGINE_ID = "goat-tips-search-ai_1774214623111"


def _fetch_snippets(query: str, num_results: int) -> list[dict]:
    """Fetch raw results from Vertex AI Search. Returns list of {title, snippet, link}."""
    from google.api_core.client_options import ClientOptions
    from google.cloud import discoveryengine_v1 as discoveryengine
    from google.oauth2 import service_account
    from app.core.settings import get_settings

    settings = get_settings()
    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SA_JSON_PATH,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client_options = (
        ClientOptions(api_endpoint=f"{_LOCATION}-discoveryengine.googleapis.com")
        if _LOCATION != "global"
        else None
    )
    client = discoveryengine.SearchServiceClient(
        credentials=credentials,
        client_options=client_options,
    )
    serving_config = (
        f"projects/{_PROJECT_ID}/locations/{_LOCATION}"
        f"/collections/default_collection/engines/{_ENGINE_ID}"
        f"/servingConfigs/default_config"
    )
    request = discoveryengine.SearchRequest(
        serving_config=serving_config,
        query=query,
        page_size=min(num_results, 10),
        content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True
            ),
        ),
        query_expansion_spec=discoveryengine.SearchRequest.QueryExpansionSpec(
            condition=discoveryengine.SearchRequest.QueryExpansionSpec.Condition.AUTO,
        ),
        spell_correction_spec=discoveryengine.SearchRequest.SpellCorrectionSpec(
            mode=discoveryengine.SearchRequest.SpellCorrectionSpec.Mode.AUTO,
        ),
    )

    items = []
    for result in client.search(request):
        data = result.document.derived_struct_data
        snippets = data.get("snippets", [])
        items.append({
            "title": data.get("title", ""),
            "snippet": snippets[0].get("snippet", "") if snippets else "",
            "link": data.get("link", ""),
        })
    return items


async def _interpret(query: str, snippets: list[dict]) -> str:
    """Use Groq to interpret raw snippets and return a coherent PT-BR answer."""
    from app.services.llm_client import client as groq_client

    if not snippets:
        return f"Nenhum resultado encontrado para: {query}"

    raw = "\n".join(
        f"[{i+1}] {s['title']}\n{s['snippet']}\n{s['link']}"
        for i, s in enumerate(snippets)
    )

    prompt = (
        f"Você recebeu resultados de busca na web para a pergunta: \"{query}\"\n\n"
        f"Resultados brutos:\n{raw}\n\n"
        "Interprete esses resultados e responda em português brasileiro de forma objetiva e informativa. "
        "Ignore resultados claramente irrelevantes. "
        "Se não houver informações suficientes, diga isso claramente. "
        "Seja conciso (máx. 3 parágrafos)."
    )

    response = await groq_client.chat.completions.create(
        model="moonshotai/kimi-k2-instruct",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=512,
    )
    return (response.choices[0].message.content or "").strip()


async def web_search(query: str, num_results: int = 5) -> str:
    """Search via Vertex AI Search, interpret results in PT-BR using LLM.

    Never raises — errors are returned as an informational string.
    """
    try:
        snippets = await asyncio.to_thread(_fetch_snippets, query, num_results)
        return await _interpret(query, snippets)
    except Exception as exc:
        logger.warning("web_search failed: %s", exc)
        return f"[web_search] Falha na busca: {exc}"
