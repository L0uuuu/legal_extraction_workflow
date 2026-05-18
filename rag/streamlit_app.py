# rag/streamlit_app.py
"""
Streamlit Chat Interface for JORT Legal RAG
Features: Chat history, token counting, source citations
"""

import streamlit as st
import re
import time
import subprocess
import requests
import atexit
import ollama as _ollama
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Filter, FieldCondition, MatchValue,
    Prefetch, Fusion, FusionQuery, SparseVector,
)
from transformers import AutoTokenizer
import ollama
from FlagEmbedding import BGEM3FlagModel, FlagReranker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_NAME         = "BAAI/bge-m3"
RERANKER_NAME      = "BAAI/bge-reranker-v2-m3"
COLLECTION         = "jort_articles_v2"
QDRANT_URL         = "http://localhost:6333"
OLLAMA_MODEL       = "etafakna"
TOP_K              = 3
RERANK_CANDIDATE_K = 10
QUERY_MAX_LEN      = 512
CONFIDENCE_THRESHOLD = 0.4

# ---------------------------------------------------------------------------
# Cleanup on exit — unload LLM from Ollama and free BGE models from RAM
# ---------------------------------------------------------------------------

def _cleanup():
    try:
        _ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": ""}],
            keep_alive=0,
            stream=False,
        )
    except Exception:
        pass
    st.cache_resource.clear()

atexit.register(_cleanup)

# ---------------------------------------------------------------------------
# Cache les modèles lourds (chargés 1 seule fois)
# ---------------------------------------------------------------------------

@st.cache_resource
def get_embedder():
    """Load BGE-M3 embedder once and cache it"""
    with st.spinner("Chargement du modèle d'embedding (BGE-M3)..."):
        return BGEM3FlagModel(MODEL_NAME, use_fp16=True, device="cpu")

@st.cache_resource
def get_reranker():
    """Load reranker once and cache it"""
    with st.spinner("Chargement du modèle de reranking..."):
        return FlagReranker(RERANKER_NAME, use_fp16=True, device="cpu")

@st.cache_resource
def get_qdrant_client():
    """Start Docker Desktop if needed, start Qdrant container, wait until ready."""
    # Check if Docker daemon is reachable; if not, launch Docker Desktop
    result = subprocess.run(["docker", "info"], capture_output=True)
    if result.returncode != 0:
        docker_desktop = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
        subprocess.Popen([docker_desktop])
        # Wait up to 60s for Docker daemon to become available
        for _ in range(60):
            if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
                break
            time.sleep(1)

    subprocess.run(["docker", "start", "qdrant"], capture_output=True)

    # Wait up to 15s for Qdrant to be ready
    for _ in range(15):
        try:
            requests.get(f"{QDRANT_URL}/healthz", timeout=1)
            break
        except Exception:
            time.sleep(1)

    return QdrantClient(url=QDRANT_URL)

@st.cache_resource
def get_tokenizer():
    """Load tokenizer for counting tokens"""
    return AutoTokenizer.from_pretrained("BAAI/bge-m3")

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def detect_language(text: str) -> str:
    arabic = sum(1 for c in text if "\u0600" <= c <= "\u06FF")
    return "ar" if arabic / max(len(text), 1) > 0.2 else "fr"

# ---------------------------------------------------------------------------
# Embedding query
# ---------------------------------------------------------------------------

def embed_query(question: str, embedder):
    output = embedder.encode(
        [question],
        batch_size=1,
        max_length=QUERY_MAX_LEN,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )
    dense = output["dense_vecs"][0].tolist()
    raw = output["lexical_weights"][0]
    sparse = SparseVector(
        indices=[int(k) for k in raw.keys()],
        values=[float(v) for v in raw.values()],
    )
    return dense, sparse

# ---------------------------------------------------------------------------
# Qdrant search
# ---------------------------------------------------------------------------

def search_qdrant(client, dense, sparse, top_k, year=None):
    query_filter = None
    if year:
        query_filter = Filter(
            must=[FieldCondition(key="year", match=MatchValue(value=year))]
        )
    
    results = client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            Prefetch(query=dense, using="dense", limit=top_k * 4),
            Prefetch(query=sparse, using="sparse", limit=top_k * 4),
        ],
        query=FusionQuery(fusion=Fusion.RRF),
        limit=top_k,
        with_payload=True,
        query_filter=query_filter,
    )
    return [(hit.payload, hit.score) for hit in results.points]

# ---------------------------------------------------------------------------
# Reranking
# ---------------------------------------------------------------------------

def rerank_articles(question, articles, lang, top_k, reranker):
    body_key = "content_french" if lang == "fr" else "content_arabic"
    pairs = [
        [question, a.get(body_key) or a.get("embedding_text", "")]
        for a, _ in articles
    ]
    scores = reranker.compute_score(pairs, normalize=True)
    if not isinstance(scores, list):
        scores = [scores]
    reranked = sorted(zip(articles, scores), key=lambda x: x[1], reverse=True)
    return [(article, float(score)) for (article, _), score in reranked[:top_k]]

# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------

def count_tokens(text: str, tokenizer) -> int:
    """Count tokens in a text string"""
    return len(tokenizer.encode(text))

def format_token_info(text: str, tokenizer, label: str = "") -> str:
    """Format token count for display"""
    count = count_tokens(text, tokenizer)
    return f"📊 {label}: {count} tokens" if label else f"📊 {count} tokens"

# ---------------------------------------------------------------------------
# Format context for LLM
# ---------------------------------------------------------------------------

def format_context(articles, lang):
    parts = []
    for i, (a, score) in enumerate(articles, 1):
        law_type = a.get("law_type", "")
        law_number = a.get("law_number", "")
        article_number = a.get("article_number", "")
        ref = f"{law_type} {law_number}".strip() or "—"
        if article_number:
            ref = f"article {article_number} du {ref}"
        date = a.get("source_date") or a.get("publication_date", "—")
        title_key = "title_french" if lang == "fr" else "title_arabic"
        body_key = "content_french" if lang == "fr" else "content_arabic"
        title = a.get(title_key) or a.get("title_french", "—")
        body = a.get(body_key) or a.get("embedding_text", "—")
        parts.append(
            f"[{i}] (score: {score:.3f})\n"
            f"Référence: {ref} | Date: {date}\n"
            f"Titre: {title}\n"
            f"{body}"
        )
    return "\n\n".join(parts)

# ---------------------------------------------------------------------------
# Build messages for Ollama
# ---------------------------------------------------------------------------

SYSTEM_FR = """Vous êtes E-Tafakna, un assistant juridique spécialisé dans le droit tunisien.
- Basez votre réponse sur les documents fournis et citez toujours les sources.
- Si aucun document pertinent n'est disponible, dites-le clairement.
- Répondez toujours dans la langue de l'utilisateur (français ou arabe)."""

SYSTEM_AR = """أنت E-Tafakna، مساعد قانوني متخصص في القانون التونسي.
- استند إلى الوثائق المقدمة في إجابتك واذكر دائماً المصادر.
- إذا لم تتوفر وثائق ذات صلة، صرّح بذلك بوضوح.
- أجب دائماً بلغة المستخدم (العربية أو الفرنسية)."""

def build_messages(question, context, lang, history):
    if lang == "fr":
        user_content = f"Question: {question}\n\nDocuments pertinents:\n\n{context}"
        system_content = SYSTEM_FR
    else:
        user_content = f"السؤال: {question}\n\nالوثائق ذات الصلة:\n\n{context}"
        system_content = SYSTEM_AR
    
    messages = [{"role": "system", "content": system_content}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})
    return messages

# ---------------------------------------------------------------------------
# Stream LLM response
# ---------------------------------------------------------------------------

def stream_llm_response(messages, model):
    """Stream response from Ollama and return full text + token count"""
    full_response = ""
    placeholder = st.empty()
    response_text = ""
    
    # Remove think tags pattern
    think_pattern = re.compile(r"<think>.*?</think>", re.DOTALL)
    buffer = ""
    in_think = False
    
    for chunk in ollama.chat(
        model=model,
        messages=messages,
        stream=True,
        options={"temperature": 0.1},
    ):
        token = chunk["message"]["content"]
        buffer += token
        
        if not in_think and "<think>" in buffer:
            in_think = True
        
        if in_think:
            if "</think>" in buffer:
                buffer = think_pattern.sub("", buffer).lstrip("\n")
                in_think = False
                response_text += buffer
                placeholder.markdown(response_text + "▌")
                buffer = ""
        else:
            response_text += buffer
            placeholder.markdown(response_text + "▌")
            buffer = ""
    
    if buffer:
        response_text += buffer
    
    placeholder.markdown(response_text)
    return response_text

# ---------------------------------------------------------------------------
# Main Streamlit App
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="E-Tafakna - Assistant Juridique Tunisien",
        page_icon="⚖️",
        layout="wide"
    )
    
    st.title("⚖️ E-Tafakna - Assistant Juridique Tunisien")
    st.markdown("Questions sur le droit tunisien basées sur le JORT")
    
    # Sidebar configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        
        year_filter = st.selectbox(
            "Filtrer par année",
            options=["Toutes", "2024", "2025"],
            help="Limiter les articles à une année spécifique"
        )
        year_value = None if year_filter == "Toutes" else year_filter
        
        st.markdown("---")
        st.header("📊 Statistiques de session")
        
        # Initialize session state
        if "total_tokens_input" not in st.session_state:
            st.session_state.total_tokens_input = 0
            st.session_state.total_tokens_output = 0
            st.session_state.total_tokens_context = 0
            st.session_state.message_count = 0
        
        token_col1, token_col2 = st.columns(2)
        with token_col1:
            st.metric("💬 Messages", st.session_state.message_count)
            st.metric("📥 Input tokens", f"{st.session_state.total_tokens_input:,}")
        with token_col2:
            st.metric("📤 Output tokens", f"{st.session_state.total_tokens_output:,}")
            st.metric("📚 Context tokens", f"{st.session_state.total_tokens_context:,}")
        
        st.markdown("---")
        st.metric("🎯 Total tokens", 
                  f"{st.session_state.total_tokens_input + st.session_state.total_tokens_output + st.session_state.total_tokens_context:,}")
        
        if st.button("🗑️ Reset conversation", use_container_width=True):
            st.session_state.messages = []
            st.session_state.total_tokens_input = 0
            st.session_state.total_tokens_output = 0
            st.session_state.total_tokens_context = 0
            st.session_state.message_count = 0
            st.rerun()
        
        st.markdown("---")
        st.caption("Modèle: " + OLLAMA_MODEL)
        st.caption("Collection: " + COLLECTION)
    
    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []
    
    # Load cached models
    with st.spinner("Initialisation des modèles..."):
        embedder = get_embedder()
        reranker = get_reranker()
        qdrant_client = get_qdrant_client()
        tokenizer = get_tokenizer()
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "sources" in message and message["sources"]:
                with st.expander("📚 Sources"):
                    for src in message["sources"]:
                        st.caption(src)
            if "tokens" in message:
                st.caption(f"🔢 Tokens: {message['tokens']}")
    
    # Chat input
    if prompt := st.chat_input("Posez votre question sur le droit tunisien..."):
        
        # Update message count
        st.session_state.message_count += 1
        
        # Display user message
        with st.chat_message("user"):
            st.markdown(prompt)
        
        # Count input tokens
        input_tokens = count_tokens(prompt, tokenizer)
        st.session_state.total_tokens_input += input_tokens
        
        # Prepare history for LLM (last 5 turns)
        history = []
        for msg in st.session_state.messages[-10:]:  # Keep last 10 messages = 5 turns
            if msg["role"] in ["user", "assistant"]:
                history.append({"role": msg["role"], "content": msg["content"]})
        
        # Get response
        with st.chat_message("assistant"):
            with st.spinner("🔍 Recherche dans le JORT..."):
                # Detect language
                lang = detect_language(prompt)
                
                # Embed query
                dense, sparse = embed_query(prompt, embedder)
                
                # Search Qdrant
                articles = search_qdrant(
                    qdrant_client, dense, sparse, 
                    RERANK_CANDIDATE_K, year_value
                )
                
                if articles:
                    # Rerank
                    articles = rerank_articles(
                        prompt, articles, lang, TOP_K, reranker
                    )
                    
                    # Check confidence
                    best_score = max(score for _, score in articles)
                    if best_score < CONFIDENCE_THRESHOLD:
                        st.warning(f"⚠️ Confiance faible ({best_score:.2f} < {CONFIDENCE_THRESHOLD})")
                        articles = []
                
                # Display sources
                sources = []
                for i, (a, score) in enumerate(articles[:TOP_K], 1):
                    ref = f"{a.get('law_type', '')} {a.get('law_number', '')}".strip()
                    if not ref:
                        ref = "Source JORT"
                    date = a.get('publication_date') or a.get('source_date', 'Date inconnue')
                    sources.append(f"{i}. {ref} ({date}) — Score: {score:.3f}")
                
                with st.expander("📚 Sources utilisées"):
                    if sources:
                        for src in sources:
                            st.caption(src)
                    else:
                        st.caption("Aucune source pertinente trouvée")
                
                # Count context tokens
                if articles:
                    context = format_context(articles, lang)
                else:
                    context = "Aucun document pertinent trouvé dans le JORT pour cette question."
                
                context_tokens = count_tokens(context, tokenizer)
                st.session_state.total_tokens_context += context_tokens
                
                # Build messages and count total input tokens for LLM
                messages = build_messages(prompt, context, lang, history)
                total_input_for_llm = sum(count_tokens(m["content"], tokenizer) for m in messages)
                
                # Free bge models from RAM before Ollama inference to avoid OOM
                import gc
                get_embedder.clear()
                get_reranker.clear()
                gc.collect()

                # Generate response
                with st.spinner("🤖 Génération de la réponse..."):
                    response = stream_llm_response(messages, OLLAMA_MODEL)
                
                # Count output tokens
                output_tokens = count_tokens(response, tokenizer)
                st.session_state.total_tokens_output += output_tokens
                
                # Display token info for this message
                st.caption(f"🔢 Input: {input_tokens} | Context: {context_tokens} | Output: {output_tokens} | Total: {input_tokens + context_tokens + output_tokens}")
        
        # Save to history
        st.session_state.messages.append({"role": "user", "content": prompt, "tokens": input_tokens})
        st.session_state.messages.append({
            "role": "assistant", 
            "content": response, 
            "sources": sources,
            "tokens": output_tokens
        })

if __name__ == "__main__":
    main()