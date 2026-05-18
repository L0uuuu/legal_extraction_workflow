#!/usr/bin/env python3
"""
RAG Query — JORT Legal Chatbot
Embeds a question with bge-m3, retrieves candidates from Qdrant (hybrid
dense+sparse, RRF fusion), optionally reranks with bge-reranker-v2-m3,
and streams a grounded answer from Ollama.

Run from the repo root:
    python rag/query.py "Quelles sont les obligations fiscales pour les entreprises?"
    python rag/query.py "ما هي التزامات الشركات الضريبية؟" --year 2024
    python rag/query.py --interactive
    python rag/query.py --interactive --year 2025 --no-rerank
"""

import re
import sys
import argparse

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter, FieldCondition, MatchValue,
    Prefetch, Fusion, FusionQuery, SparseVector,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME         = "BAAI/bge-m3"
RERANKER_NAME      = "BAAI/bge-reranker-v2-m3"
COLLECTION         = "jort_articles_v2"
QDRANT_URL         = "http://localhost:6333"
OLLAMA_MODEL       = "etafkna-qwen3.5-9b"
TOP_K              = 3
RERANK_CANDIDATE_K   = 10   # candidates fetched before reranking
MAX_HISTORY_TURNS    = 5    # conversation turns kept in context (each turn = 1 Q + 1 A)
QUERY_MAX_LEN        = 512  # shorter than indexing — queries don't need 8192 tokens
CONFIDENCE_THRESHOLD = 0.4  # reranker score below this → skip LLM, return "no relevant articles"
USE_SYSTEM_PROMPT    = True  # set to False to rely on the modelfile's baked-in SYSTEM instead

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str:
    """Return 'ar' if the text is predominantly Arabic, else 'fr'."""
    arabic = sum(1 for c in text if "؀" <= c <= "ۿ")
    return "ar" if arabic / max(len(text), 1) > 0.2 else "fr"

# ---------------------------------------------------------------------------
# Embedding model (lazy init — loaded once per process)
# ---------------------------------------------------------------------------

_model = None

def _get_model():
    global _model
    if _model is None:
        print("[rag] Loading BAAI/bge-m3 (fp16) …", flush=True)
        from FlagEmbedding import BGEM3FlagModel
        _model = BGEM3FlagModel(MODEL_NAME, use_fp16=True, device="cpu")
        print("[rag] Embedder ready.", flush=True)
    return _model


def _embed_query(question: str) -> tuple[list[float], SparseVector]:
    output = _get_model().encode(
        [question],
        batch_size=1,
        max_length=QUERY_MAX_LEN,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = output["dense_vecs"][0].tolist()
    raw   = output["lexical_weights"][0]
    sparse = SparseVector(
        indices=[int(k) for k in raw.keys()],
        values=[float(v) for v in raw.values()],
    )
    return dense, sparse

# ---------------------------------------------------------------------------
# Cross-encoder reranker (lazy init)
# ---------------------------------------------------------------------------

_reranker = None

def _get_reranker():
    global _reranker
    if _reranker is None:
        print("[rag] Loading BAAI/bge-reranker-v2-m3 (fp16) …", flush=True)
        from FlagEmbedding import FlagReranker
        _reranker = FlagReranker(RERANKER_NAME, use_fp16=True, device="cpu")
        print("[rag] Reranker ready.", flush=True)
    return _reranker


def _rerank(
    question: str,
    articles: list[tuple[dict, float]],
    lang: str,
    top_k: int,
) -> list[tuple[dict, float]]:
    """Re-score (question, article_text) pairs; return top_k sorted by reranker score."""
    body_key = "content_french" if lang == "fr" else "content_arabic"
    pairs = [
        [question, a.get(body_key) or a.get("embedding_text", "")]
        for a, _ in articles
    ]
    scores = _get_reranker().compute_score(pairs, normalize=True)
    if not isinstance(scores, list):
        scores = [scores]
    reranked = sorted(zip(articles, scores), key=lambda x: x[1], reverse=True)
    return [(article, float(score)) for (article, _), score in reranked[:top_k]]

# ---------------------------------------------------------------------------
# Qdrant hybrid search
# ---------------------------------------------------------------------------

def _search(
    client: QdrantClient,
    dense: list[float],
    sparse: SparseVector,
    top_k: int,
    year: str | None,
) -> list[tuple[dict, float]]:
    query_filter = None
    if year:
        query_filter = Filter(
            must=[FieldCondition(key="year", match=MatchValue(value=year))]
        )
    results = client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            Prefetch(query=dense,  using="dense",  limit=top_k * 4),
            Prefetch(query=sparse, using="sparse", limit=top_k * 4),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )
    return [(hit.payload, hit.score) for hit in results.points]

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_FR = """\
Vous êtes E-Tafakna, un assistant juridique spécialisé dans le droit tunisien.
- Si des documents sont fournis, basez votre réponse dessus et citez toujours les articles et sources.
- Si la question concerne un service (rédaction de contrat, consultation, etc.), orientez l'utilisateur vers le service approprié.
- Si aucun document pertinent n'est disponible, dites-le clairement et recommandez de consulter un professionnel.
- Répondez toujours dans la langue de l'utilisateur (français ou arabe).
- Ceci ne constitue pas un avis juridique professionnel."""

_SYSTEM_AR = """\
أنت E-Tafakna، مساعد قانوني متخصص في القانون التونسي.
- إذا تم توفير وثائق، استند إليها في إجابتك واذكر دائماً المراجع والمصادر.
- إذا كان السؤال يتعلق بخدمة (صياغة عقد، استشارة، إلخ)، وجّه المستخدم إلى الخدمة المناسبة.
- إذا لم تتوفر وثائق ذات صلة، صرّح بذلك بوضوح وأوصِ باستشارة متخصص.
- أجب دائماً بلغة المستخدم (العربية أو الفرنسية).
- هذا لا يُعدّ رأياً قانونياً مهنياً."""


def _format_context(articles: list[tuple[dict, float]], lang: str) -> str:
    parts = []
    for i, (a, score) in enumerate(articles, 1):
        law_type      = a.get("law_type", "")
        law_number    = a.get("law_number", "")
        article_number = a.get("article_number", "")
        ref           = f"{law_type} {law_number}".strip() or "—"
        if article_number:
            ref = f"article {article_number} du {ref}"
        date          = a.get("source_date") or a.get("publication_date", "—")
        title_key  = "title_french" if lang == "fr" else "title_arabic"
        body_key   = "content_french" if lang == "fr" else "content_arabic"
        title = a.get(title_key) or a.get("title_french", "—")
        body  = a.get(body_key)  or a.get("embedding_text", "—")
        parts.append(
            f"[{i}]  (similarité : {score:.3f})\n"
            f"Référence : {ref}  |  Date : {date}\n"
            f"Titre : {title}\n"
            f"{body}"
        )
    return "\n\n".join(parts)


def _build_messages(
    question: str,
    context: str,
    lang: str,
    history: list[dict],
    use_system_prompt: bool = True,
) -> list[dict]:
    """
    Build the full message list:  [system]  +  history  +  [current user turn with context].
    History contains plain Q&A pairs (no article context) to keep it compact.
    Pass use_system_prompt=False (or --no-system-prompt) to rely on the modelfile's baked-in SYSTEM.
    """
    if lang == "fr":
        user_content = (
            f"Question : {question}\n\nDocuments pertinents :\n\n{context}"
            if context else
            f"Question : {question}\n\nDocuments pertinents :\n\nAucun article pertinent trouvé."
        )
    else:
        user_content = (
            f"السؤال: {question}\n\nDocuments pertinents :\n\n{context}"
            if context else
            f"السؤال: {question}\n\nDocuments pertinents :\n\nلم يتم العثور على مقالات ذات صلة."
        )
    system_msg = (
        [{"role": "system", "content": _SYSTEM_FR if lang == "fr" else _SYSTEM_AR}]
        if use_system_prompt else []
    )
    return [
        *system_msg,
        *history,
        {"role": "user", "content": user_content},
    ]

# ---------------------------------------------------------------------------
# Ollama streaming call
# ---------------------------------------------------------------------------

# Strips <think>…</think> blocks that qwen3 emits before the real answer
_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


def _ask_ollama(messages: list[dict], model: str, keep_alive: int = 300) -> str:
    """Stream the LLM answer to stdout; return the full answer text."""
    import ollama

    print(f"\n[{model}] ", end="", flush=True)

    buffer   = ""
    answer   = ""
    in_think = False

    for chunk in ollama.chat(
        model=model,
        messages=messages,
        stream=True,
        options={"temperature": 0.1},
        keep_alive=keep_alive,
    ):
        token   = chunk["message"]["content"]
        buffer += token

        if not in_think and "<think>" in buffer:
            in_think = True

        if in_think:
            if "</think>" in buffer:
                buffer   = _THINK_BLOCK.sub("", buffer).lstrip("\n")
                in_think = False
                print(buffer, end="", flush=True)
                answer  += buffer
                buffer   = ""
        else:
            print(buffer, end="", flush=True)
            answer += buffer
            buffer  = ""

    if buffer:
        print(buffer, flush=True)
        answer += buffer
    print()
    return answer

# ---------------------------------------------------------------------------
# Core query flow
# ---------------------------------------------------------------------------

def _run_query(
    question: str,
    client: QdrantClient,
    args: argparse.Namespace,
    history: list[dict],
    keep_models: bool = False,
) -> str:
    """Run one retrieval+generation turn. Returns the assistant's answer."""
    lang = _detect_language(question)
    print(f"[rag] Language   : {'Arabic' if lang == 'ar' else 'French / other'}")
    print(f"[rag] Year filter: {args.year or 'none'}")
    print("[rag] Embedding query …", flush=True)

    dense, sparse = _embed_query(question)

    candidate_k = RERANK_CANDIDATE_K if not args.no_rerank else args.top_k
    print("[rag] Searching Qdrant …", flush=True)
    articles = _search(client, dense, sparse, candidate_k, args.year)
    print(f"[rag] {len(articles)} candidate(s) retrieved.")

    if not articles:
        print("[rag] No results. Try a broader question or a different --year.")
        return ""

    if not args.no_rerank and len(articles) > 1:
        print(f"[rag] Reranking {len(articles)} candidates …", flush=True)
        articles = _rerank(question, articles, lang, args.top_k)
        print(f"[rag] {len(articles)} article(s) kept after reranking.")

        best_score = max(score for _, score in articles)
        if best_score < args.confidence_threshold:
            print(f"[rag] Best score {best_score:.3f} below threshold {args.confidence_threshold} — passing empty context to model.")
            articles = []

    print("[rag] Sources:")
    for i, (a, score) in enumerate(articles, 1):
        ref  = f"{a.get('law_type', '')} {a.get('law_number', '')}".strip() or "—"
        date = a.get("source_date") or a.get("publication_date", "—")
        print(f"       {i}. {ref}  ({date})  [score: {score:.3f}]")

    context  = _format_context(articles, lang)
    messages = _build_messages(question, context, lang, history, use_system_prompt=USE_SYSTEM_PROMPT and not args.no_system_prompt)

    if not keep_models:
        import gc
        global _model, _reranker
        _model    = None
        _reranker = None
        gc.collect()

    return _ask_ollama(messages, model=args.model, keep_alive=300 if keep_models else 0)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="JORT RAG — query Tunisian legal articles with Ollama"
    )
    p.add_argument(
        "question", nargs="?", default=None,
        help="Question to ask (omit when using --interactive)",
    )
    p.add_argument(
        "--year", default=None,
        help="Filter retrieved articles to a specific year (e.g. 2024)",
    )
    p.add_argument(
        "--top-k", type=int, default=TOP_K,
        help=f"Articles passed to the LLM (default: {TOP_K}); reranking fetches {RERANK_CANDIDATE_K} first",
    )
    p.add_argument(
        "--model", default=OLLAMA_MODEL,
        help=f"Ollama model name (default: {OLLAMA_MODEL})",
    )
    p.add_argument(
        "--qdrant-url", default=QDRANT_URL,
        help=f"Qdrant base URL (default: {QDRANT_URL})",
    )
    p.add_argument(
        "--interactive", action="store_true",
        help="Start an interactive question-answer loop with conversation history",
    )
    p.add_argument(
        "--no-rerank", action="store_true",
        help=f"Skip cross-encoder reranking (faster; fetches top-k directly instead of {RERANK_CANDIDATE_K})",
    )
    p.add_argument(
        "--history-turns", type=int, default=MAX_HISTORY_TURNS,
        help=f"Max past turns kept in LLM context in interactive mode (default: {MAX_HISTORY_TURNS}; 0 = off)",
    )
    p.add_argument(
        "--confidence-threshold", type=float, default=CONFIDENCE_THRESHOLD,
        help=f"Minimum reranker score to call the LLM (default: {CONFIDENCE_THRESHOLD}); ignored with --no-rerank",
    )
    p.add_argument(
        "--no-system-prompt", action="store_true",
        help="Skip the code-side system prompt and rely on the modelfile's baked-in SYSTEM instead",
    )
    return p


def main() -> None:
    args   = _build_parser().parse_args()
    client = QdrantClient(url=args.qdrant_url)

    if args.interactive:
        _get_model()
        if not args.no_rerank:
            _get_reranker()
        history: list[dict] = []
        rerank_label = (
            "off" if args.no_rerank
            else f"on  (top-{RERANK_CANDIDATE_K} → top-{args.top_k})"
        )
        print("JORT Legal RAG  —  interactive mode  (Ctrl-C or empty line to exit)")
        print(f"Model     : {args.model}  |  Collection : {COLLECTION}  |  Year : {args.year or 'all'}")
        print(f"Reranking : {rerank_label}  |  History turns : {args.history_turns}\n")

        while True:
            try:
                question = input("Question: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nBye.")
                break
            if not question:
                break

            answer = _run_query(question, client, args, history, keep_models=True)

            if answer and args.history_turns > 0:
                history.append({"role": "user",      "content": question})
                history.append({"role": "assistant", "content": answer})
                # keep only the last N turns (2 messages per turn)
                max_msgs = args.history_turns * 2
                if len(history) > max_msgs:
                    history = history[-max_msgs:]
            print()

    elif args.question:
        _run_query(args.question, client, args, history=[])
    else:
        _build_parser().print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
