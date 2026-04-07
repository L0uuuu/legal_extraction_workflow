#!/usr/bin/env python3
"""
Article Extraction — Phase 4
Reads .txt files produced by Phase 3 and extracts structured JSON articles
using Azure OpenAI (GPT-4.1).

Run from the repo root:
    # Full batch
    python article_extraction/extract_articles.py

    # Single file test
    python article_extraction/extract_articles.py --txt "txt/Journal_Officiel_.../2026/JORT_001_2026-01-02.txt"
"""

import os
import sys
import json
import time
import re
import argparse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict

from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AZURE_OPENAI_ENDPOINT   = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY    = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
DEPLOYMENT_NAME         = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")
MAX_OUTPUT_TOKENS       = int(os.getenv("OPENAI_OUTPUT_TOKENS", "32000"))

CHARS_PER_TOKEN  = 4
MAX_INPUT_CHARS  = 100000 * CHARS_PER_TOKEN   # ~100k tokens

CHECKPOINT_PATH  = Path("outputs/checkpoints/article_extraction.checkpoint.json")

# ---------------------------------------------------------------------------
# Azure OpenAI client (lazy init)
# ---------------------------------------------------------------------------

_client: AzureOpenAI | None = None

def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
        )
    return _client

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an expert Tunisian legal document analyzer and professional translator fluent in French and Arabic. Extract legal articles into a rich structured JSON format covering ALL metadata fields needed for vector embedding, graph databases, AI training, and full-text search.

CRITICAL JSON RULES:
- Return ONLY a single valid JSON object (no markdown, no code blocks, no array wrapper)
- Escape all special characters in strings (quotes, newlines, tabs)
- Use \\" for quotes inside strings
- Keep all text in single lines (no literal newlines in strings)
- NEVER truncate mid-string or mid-object — complete cleanly or stop at a field boundary
- Always close all JSON brackets and braces properly
- NEVER use null for string fields — use "" instead
- NEVER use null for array fields — use [] instead
- ALWAYS extract keywords (minimum 5-7 per article) — NEVER empty
- ALWAYS populate legal_domains with French names — NEVER empty
- ALWAYS translate French content to Arabic (title_arabic, content_arabic, summary_arabic)
- ALWAYS generate embedding_text combining title + summary (FR+AR) + content (FR+AR) + legal_concepts
- Use status values: "ACTIVE", "ABROGATED", "MODIFIED", "PENDING"
- Use business_impact values: "HIGH", "MEDIUM", "LOW"
- Use article_type values: "REGULATORY", "DEFINITIONAL", "PROCEDURAL", "TRANSITIONAL", "PENAL", "ABROGATION"
- Use ambiguity_level values: "LOW", "MEDIUM", "HIGH"
- Boolean fields (has_obligations, has_penalties, has_deadlines, has_exceptions, is_abrogation, is_transitional): always true or false
- All fields listed in the JSON TEMPLATE below MUST be present in the output"""

PDF_PARSING_PROMPT = """Extract individual articles from this Tunisian legal document text. Identify each article and return them as a JSON object with an "articles" array.

**STRICT REQUIREMENTS:**
1. Return ONLY a JSON object (no markdown, no ```json blocks)
2. The JSON must have an "articles" key containing an array
3. Each article should be a complete object
4. Identify article boundaries (look for "Article", "Art.", "المادة", etc.)

**ARTICLE BOUNDARY RULES:**
1. An article STARTS when you see: "Article", "Art.", "المادة", or "الفصل" followed by a number
2. An article ENDS when you encounter:
   - The next article marker (e.g., "Article 2", "Art. 3", "المادة 4", "الفصل 5")
   - A signature line (e.g., "Le Ministre", "Le Président")
   - A date line (e.g., "Fait à Tunis, le...")
   - The end of the document
3. If you see a range like "Articles 1-4" or "Art. 1 à 4", treat EACH number as a separate article
4. Do NOT combine multiple articles into one even if they share a heading

**JSON STRUCTURE:**
{
  "articles": [
    {
      "article": "full article text in French",
      "article_number": "Article 1",
      "titre": "law title if available",
      "loi": "law number if available",
      "chapitre": "chapter if available"
    }
  ]
}

**DOCUMENT TEXT:**
{document_text}

**CRITICAL:** Return ONLY a valid JSON object with an "articles" array. Extract all articles you can identify."""

EXTRACTION_PROMPT = """EXTRACT TUNISIAN LEGAL ARTICLE INTO COMPLETE JSON — ALL FIELDS REQUIRED

INSTRUCTIONS:
Analyze the article data below and produce a single JSON object containing EVERY field in the template.
Do NOT omit any field. Use "" for missing strings and [] for missing arrays.

═══════════════════════════════════════════════════════
JSON TEMPLATE  (fill every field — never skip any)
═══════════════════════════════════════════════════════
{
  "jurisdiction": "TUNISIA",
  "institution": "",
  "law_type": "",
  "law_number": "",
  "year": "",
  "status": "",

  "title_french": "",
  "title_arabic": "",

  "chapter": "",
  "chapter_normalized": "",
  "section": "",

  "article_number": "",
  "article_order": 0,
  "article_type": "",

  "content_french": "",
  "content_arabic": "",
  "content_combined": "",

  "summary_french": "",
  "summary_arabic": "",

  "search_content": "",
  "embedding_text": "",

  "keywords": [],
  "legal_domains": [],
  "legal_concepts": [],
  "business_impact": "",
  "target_audience": [],
  "related_laws": [],

  "ambiguity_level": "",
  "has_obligations": false,
  "has_penalties": false,
  "has_deadlines": false,
  "has_exceptions": false,
  "is_abrogation": false,
  "is_transitional": false,

  "institution_primary": "",
  "institution_secondary": "",
  "institutions": [],

  "source_name": "JORT",
  "source_number": "",
  "source_url": "",
  "source_date": "",
  "publication_date": "",
  "effective_date": "",

  "relation_target_ids": [],
  "relation_types": [],

  "entity_names": [],
  "entity_types": [],
  "entity_ids": [],

  "community_id": "",
  "community_label": "",
  "community_summary": "",

  "parent_document_id": "",
  "preceding_article_id": "",
  "following_article_id": "",
  "graph_level": 1,

  "version": 1,
  "repeal_date": "",
  "superseded_by_id": "",
  "supersedes_id": "",

  "last_checked": "",
  "next_check": ""
}

═══════════════════════════════════════════════════════
FIELD-BY-FIELD INSTRUCTIONS
═══════════════════════════════════════════════════════

IDENTITY & CLASSIFICATION
- jurisdiction          : Always "TUNISIA"
- institution           : Issuing body (e.g. "Présidence de la République", "Ministère des Finances")
- law_type              : "Loi" | "Décret" | "Arrêté" | "Loi de Finances" | "Décret-loi" etc.
- law_number            : Official number, e.g. "2023-97"
- year                  : 4-digit year extracted from dates in content, e.g. "2023"
- status                : "ACTIVE" | "ABROGATED" | "MODIFIED" | "PENDING"

TITLES (bilingual — BOTH required)
- title_french          : Full official French title of the law/decree
- title_arabic          : MUST translate title_french to Arabic using proper legal terminology

STRUCTURE
- chapter               : Chapter title/number if present, else ""
- chapter_normalized    : Normalized chapter label, e.g. "Chapitre 1 - Dispositions générales", else ""
- section               : Section title/number if present, else ""
- article_number        : e.g. "Article 1", "Article premier"
- article_order         : Integer position of article in the document (1, 2, 3 …)
- article_type          : "REGULATORY" | "DEFINITIONAL" | "PROCEDURAL" | "TRANSITIONAL" | "PENAL" | "ABROGATION"

CONTENT (bilingual — BOTH required)
- content_french        : Full French text of the article — REQUIRED
- content_arabic        : MUST translate content_french to Arabic — REQUIRED
- content_combined      : content_french + " " + content_arabic (concatenated)

SUMMARIES (both required)
- summary_french        : 2-3 sentence French summary of the article's purpose
- summary_arabic        : MUST translate summary_french to Arabic

SEARCH & EMBEDDING
- search_content        : Key terms in French and Arabic (law number, title keywords, domain terms)
- embedding_text        : Full concatenation: title_french + "\\n" + summary_french + "\\n" + summary_arabic + "\\n" + content_french + "\\n" + content_arabic + "\\n" + legal_concepts joined by " | "

ENRICHMENT (all arrays NEVER empty)
- keywords              : 5-7 relevant legal/business keywords extracted from content — NEVER []
- legal_domains         : 1-3 French domain names: "Droit Commercial" | "Droit Fiscal" | "Droit Administratif" | "Droit du Travail" | "Droit Civil" | "Droit de l'urbanisme" | "Droit des collectivités locales" etc. — NEVER []
- legal_concepts        : 3-5 core legal concepts
- business_impact       : "HIGH" | "MEDIUM" | "LOW"
- target_audience       : French labels, e.g. ["Entreprises", "Investisseurs", "Particuliers"] — at least 1
- related_laws          : Full citations of referenced laws/articles

LEGAL FLAGS (boolean — infer from content)
- ambiguity_level       : "LOW" | "MEDIUM" | "HIGH"
- has_obligations       : true if article imposes obligations
- has_penalties         : true if article mentions fines/sanctions/penalties
- has_deadlines         : true if article mentions deadlines/dates
- has_exceptions        : true if article contains exceptions/derogations
- is_abrogation         : true if article abrogates another law
- is_transitional       : true if article is a transitional/final provision

INSTITUTIONS
- institution_primary   : Main issuing institution
- institution_secondary : Secondary institution if any, else ""
- institutions          : Array of all institutions mentioned

SOURCE
- source_name           : "JORT"
- source_number         : JORT issue number if available, else ""
- source_url            : ""
- source_date           : ISO date of publication, e.g. "2023-02-06T00:00:00Z"
- publication_date      : Same as source_date
- effective_date        : Date the law takes effect (same as publication_date if not specified)

RELATIONS (graph edges)
- relation_target_ids   : IDs of related documents, e.g. ["tn-loi-2018-29"]
- relation_types        : Corresponding relation type: "REFERENCES" | "AMENDS" | "REPEALS" | "IMPLEMENTS"
- entity_names          : Named entities (ministries, organizations) mentioned
- entity_types          : Type per entity: "MINISTRY" | "ORGANIZATION" | "PERSON" | "PLACE"
- entity_ids            : Slugified IDs, e.g. ["tn-org-ministere-des-finances"]

GRAPH / COMMUNITY
- community_id          : Short slug grouping related laws, e.g. "urbanisme-collectivites"
- community_label       : Human label, e.g. "Urbanisme et Collectivités Locales"
- community_summary     : 1 sentence describing the community
- parent_document_id    : ID of the parent law/decree, e.g. "tn-decret-2023-97"
- preceding_article_id  : ID of the previous article, or ""
- following_article_id  : ID of the next article, or ""
- graph_level           : Depth in document hierarchy (1 = top-level article)

VERSIONING
- version               : Always 1 for new extraction
- repeal_date           : ISO date if repealed, else ""
- superseded_by_id      : ID of superseding document, else ""
- supersedes_id         : ID of document this supersedes, else ""
- last_checked          : Today's ISO date
- next_check            : ISO date 6 months from today

═══════════════════════════════════════════════════════
ARTICLE DATA TO PROCESS:
{article_data}
═══════════════════════════════════════════════════════

RETURN ONLY A SINGLE VALID JSON OBJECT. NO MARKDOWN. NO EXPLANATIONS. NO ARRAY WRAPPER."""

# ---------------------------------------------------------------------------
# Azure OpenAI helpers
# ---------------------------------------------------------------------------

def _call_azure(system_prompt: str, user_prompt: str) -> str:
    estimated = len(system_prompt + user_prompt) // CHARS_PER_TOKEN
    print(f"    📊 Estimated prompt tokens: ~{estimated}")

    response = _get_client().chat.completions.create(
        model=DEPLOYMENT_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=MAX_OUTPUT_TOKENS,
        temperature=0.1,
        top_p=0.95,
    )

    choice = response.choices[0]
    usage  = response.usage
    print(f"    📊 Usage: prompt={usage.prompt_tokens} / completion={usage.completion_tokens} | finish={choice.finish_reason}")

    if choice.finish_reason == "length":
        print("    ⚠️  Token limit hit — will attempt JSON repair")

    return choice.message.content or ""


def _parse_json(raw: str, label: str) -> dict:
    try:
        cleaned = re.sub(r"```json\n?|```\n?", "", raw.strip())
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"    ⚠️  [{label}] JSON parse error: {e} — attempting repair")
        try:
            fixed = re.sub(r"```json\n?|```\n?", "", raw.strip())
            last_brace = fixed.rfind("}")
            if last_brace == -1:
                raise ValueError("No closing brace")
            closed = False
            for i in range(last_brace, min(len(fixed), last_brace + 100)):
                if fixed[i] == "]":
                    fixed = fixed[:i + 1] + "}"
                    closed = True
                    break
            if not closed:
                fixed = fixed[:last_brace + 1] + "]}"
            parsed = json.loads(fixed)
            print(f"    ✅ [{label}] JSON repaired")
            return parsed
        except Exception as repair_err:
            raise ValueError(f"JSON parsing failed after repair: {e}") from repair_err

# ---------------------------------------------------------------------------
# Stage 1 — parse document into raw articles
# ---------------------------------------------------------------------------

def _regex_articles(text: str) -> list[dict]:
    articles = []
    pattern = r'(?:Article|Art\.?|المادة|الفصل)\s+(\d+)[\s\S]*?(?=(?:Article|Art\.?|المادة|الفصل)\s+\d+|$)'
    for match in re.finditer(pattern, text, re.I):
        articles.append({
            "article":        match.group(0).strip(),
            "article_number": f"Article {match.group(1)}",
            "titre": "", "loi": "", "chapitre": "",
        })
    print(f"    📄 Regex fallback: {len(articles)} articles")
    return articles


def parse_articles_from_text(text: str, retries: int = 3) -> list[dict]:
    """Stage 1: ask the model to identify article boundaries."""
    limited = text[:MAX_INPUT_CHARS]
    prompt  = PDF_PARSING_PROMPT.replace("{document_text}", limited)

    for attempt in range(1, retries + 1):
        try:
            print(f"    🤖 Parsing document (attempt {attempt}/{retries})…")
            raw    = _call_azure(SYSTEM_PROMPT, prompt)
            parsed = _parse_json(raw, "parse")
            articles = parsed if isinstance(parsed, list) else parsed.get("articles", [])
            print(f"    ✅ Found {len(articles)} articles")
            return articles
        except Exception as e:
            print(f"    ❌ Attempt {attempt}: {e}")
            if attempt == retries:
                return _regex_articles(text)
            time.sleep(2 ** attempt)

    return _regex_articles(text)

# ---------------------------------------------------------------------------
# Stage 2 — enrich each raw article
# ---------------------------------------------------------------------------

def _ensure_fields(article: dict, raw: dict) -> dict:
    """Post-process: fill any missing required fields."""
    content = article.get("content_french") or raw.get("article", "")

    # Keywords
    if len(article.get("keywords", [])) < 3:
        terms = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', content)
        kws   = list(article.get("keywords", []))
        for t in terms:
            if len(t) > 3 and t not in ["Article", "Société", "Constitution", "République", "Tunisienne"]:
                if not any(k.lower() == t.lower() for k in kws):
                    kws.append(t)
        article["keywords"] = (kws or ["article", "juridique", "tunisie"])[:7]

    if not article.get("keywords"):
        article["keywords"] = ["article", "juridique", "tunisie"]

    # Legal domains
    if not article.get("legal_domains"):
        cl = content.lower()
        domains = []
        if any(w in cl for w in ["commerce", "société", "entreprise"]):   domains.append("Droit Commercial")
        if any(w in cl for w in ["travail", "employé", "salarié"]):       domains.append("Droit du Travail")
        if any(w in cl for w in ["fiscal", "impôt", "taxe"]):             domains.append("Droit Fiscal")
        if any(w in cl for w in ["administratif", "ministère", "décret"]): domains.append("Droit Administratif")
        if any(w in cl for w in ["civil", "contrat", "obligation"]):       domains.append("Droit Civil")
        article["legal_domains"] = domains or ["Droit Administratif"]

    # Derived fields
    if not article.get("legal_concepts"):
        article["legal_concepts"] = article["keywords"][:3]
    if not article.get("content_combined"):
        article["content_combined"] = (
            (article.get("content_french") or "") + " " + (article.get("content_arabic") or "")
        ).strip()
    if not article.get("summary_arabic"):
        article["summary_arabic"] = ""
    if not article.get("embedding_text"):
        article["embedding_text"] = "\n".join(filter(None, [
            article.get("title_french", ""),
            article.get("summary_french", ""),
            article.get("summary_arabic", ""),
            article.get("content_french", ""),
            article.get("content_arabic", ""),
            " | ".join(article.get("legal_concepts", [])),
        ]))
    if not article.get("search_content"):
        article["search_content"] = " ".join(filter(None, [
            article.get("law_number", ""),
            article.get("title_french", ""),
            " ".join(article.get("keywords", [])),
        ]))

    # Booleans
    for f in ["has_obligations", "has_penalties", "has_deadlines", "has_exceptions", "is_abrogation", "is_transitional"]:
        if not isinstance(article.get(f), bool):
            article[f] = False

    # Defaults
    for field, default in {
        "article_type": "REGULATORY", "ambiguity_level": "LOW",
        "business_impact": "MEDIUM", "source_name": "JORT",
        "institution_primary": article.get("institution", ""),
    }.items():
        if not article.get(field):
            article[field] = default

    for f in ["graph_level", "version"]:
        if article.get(f) is None:
            article[f] = 1
    if article.get("article_order") is None:
        article["article_order"] = 0

    # Arrays
    for f in ["target_audience", "related_laws", "institutions", "relation_target_ids",
              "relation_types", "entity_names", "entity_types", "entity_ids"]:
        if not isinstance(article.get(f), list):
            article[f] = []

    # Nulls → empty
    for k, v in list(article.items()):
        if v is None:
            article[k] = [] if k in ["keywords", "legal_domains"] else ""

    now = datetime.now()
    article["last_checked"] = now.strftime("%Y-%m-%dT00:00:00Z")
    article["next_check"]   = (now + timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z")

    return article


def _build_fallback(raw: dict, error: str = "") -> dict:
    now = datetime.now()
    law_num  = re.search(r"[\d-]+", raw.get("loi", ""))
    year     = re.search(r"\d{4}", raw.get("loi", ""))
    art_num  = re.search(r"Art\.?\s*\d+", raw.get("article", ""), re.I)
    result   = {
        "jurisdiction": "TUNISIA", "institution": "", "law_type": "",
        "law_number": law_num.group(0) if law_num else "",
        "year": year.group(0) if year else "",
        "status": "ACTIVE",
        "title_french": raw.get("titre", ""), "title_arabic": "",
        "chapter": raw.get("chapitre", ""), "chapter_normalized": "", "section": "",
        "article_number": art_num.group(0) if art_num else "",
        "article_order": 0, "article_type": "REGULATORY",
        "content_french": raw.get("article", ""), "content_arabic": "",
        "content_combined": raw.get("article", ""),
        "summary_french": "", "summary_arabic": "",
        "search_content": raw.get("article", ""), "embedding_text": raw.get("article", ""),
        "keywords": [], "legal_domains": [], "legal_concepts": [],
        "business_impact": "MEDIUM", "target_audience": [], "related_laws": [],
        "ambiguity_level": "LOW",
        "has_obligations": False, "has_penalties": False, "has_deadlines": False,
        "has_exceptions": False, "is_abrogation": False, "is_transitional": False,
        "institution_primary": "", "institution_secondary": "", "institutions": [],
        "source_name": "JORT", "source_number": "", "source_url": "",
        "source_date": "", "publication_date": "", "effective_date": "",
        "relation_target_ids": [], "relation_types": [],
        "entity_names": [], "entity_types": [], "entity_ids": [],
        "community_id": "", "community_label": "", "community_summary": "",
        "parent_document_id": "", "preceding_article_id": "", "following_article_id": "",
        "graph_level": 1, "version": 1,
        "repeal_date": "", "superseded_by_id": "", "supersedes_id": "",
        "last_checked": now.strftime("%Y-%m-%dT00:00:00Z"),
        "next_check": (now + timedelta(days=180)).strftime("%Y-%m-%dT00:00:00Z"),
    }
    if error:
        result["_extraction_error"] = error
    return result


def enrich_article(raw: dict, retries: int = 3) -> dict:
    """Stage 2: enrich a single raw article with the full schema."""
    prompt = EXTRACTION_PROMPT.replace("{article_data}", json.dumps(raw, ensure_ascii=False))

    for attempt in range(1, retries + 1):
        try:
            preview = raw.get("article", "")[:80]
            print(f"    🤖 Enriching article (attempt {attempt}/{retries}): {preview}…")
            raw_response = _call_azure(SYSTEM_PROMPT, prompt)
            parsed = _parse_json(raw_response, "enrich")
            return _ensure_fields(parsed, raw)
        except Exception as e:
            print(f"    ❌ Attempt {attempt}: {e}")
            if attempt == retries:
                return _ensure_fields(_build_fallback(raw, str(e)), raw)
            time.sleep(2 ** attempt)

    return _ensure_fields(_build_fallback(raw), raw)

# ---------------------------------------------------------------------------
# Per-file processor
# ---------------------------------------------------------------------------

def process_txt_file(txt_path: Path, parent_id: str, delay_ms: int) -> list[dict]:
    """Read a .txt file and return enriched articles."""
    text = txt_path.read_text(encoding="utf-8")
    print(f"  📄 {len(text)} chars")

    raw_articles = parse_articles_from_text(text)
    if not raw_articles:
        raise RuntimeError("No articles found in document")

    print(f"  🇹🇳 Enriching {len(raw_articles)} articles…")
    enriched = []

    for i, raw in enumerate(raw_articles):
        print(f"  📋 Article {i + 1}/{len(raw_articles)} — {raw.get('loi', '')}")
        try:
            result = enrich_article(raw)
        except Exception as e:
            result = _ensure_fields(_build_fallback(raw, str(e)), raw)

        result["parent_document_id"] = parent_id
        enriched.append(result)

        if i < len(raw_articles) - 1:
            print(f"  ⏳ Waiting {delay_ms}ms…")
            time.sleep(delay_ms / 1000)

    return enriched

# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            return json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "script_name": "extract_articles.py",
        "created_at":  _utc_now(),
        "updated_at":  _utc_now(),
        "totals":      {"extracted": 0, "skipped": 0, "failed": 0},
        "files":       [],
    }


def save_checkpoint(cp: dict) -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    cp["updated_at"] = _utc_now()
    CHECKPOINT_PATH.write_text(json.dumps(cp, indent=2, ensure_ascii=False), encoding="utf-8")


def _already_done(cp: dict, txt_path: str) -> bool:
    for entry in cp.get("files", []):
        if entry.get("txt_path") == txt_path and entry.get("status") == "extracted":
            return True
    return False


def _upsert_entry(cp: dict, entry: dict) -> None:
    for i, e in enumerate(cp["files"]):
        if e.get("txt_path") == entry["txt_path"]:
            cp["files"][i] = entry
            return
    cp["files"].append(entry)

# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _txt_to_json_path(txt_path: Path, txt_dir: Path, json_dir: Path) -> Path:
    rel = txt_path.relative_to(txt_dir)
    return json_dir / rel.with_suffix(".json")


def _section_year(txt_path: Path, txt_dir: Path) -> tuple[str, str]:
    parts = txt_path.relative_to(txt_dir).parts
    section = parts[0] if len(parts) >= 3 else ""
    year    = parts[1] if len(parts) >= 3 else ""
    return section, year


def discover_txt_files(txt_dir: Path) -> list[Path]:
    return sorted(txt_dir.rglob("*.txt"))


def _single_txt_entry(txt_path: Path, txt_dir: Path, json_dir: Path) -> tuple[Path, Path, str, str]:
    json_path        = _txt_to_json_path(txt_path, txt_dir, json_dir)
    section, year    = _section_year(txt_path, txt_dir)
    return txt_path, json_path, section, year

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 4 — Article Extraction")
    parser.add_argument("--txt",      default=None,  help="Single .txt file path (test mode)")
    parser.add_argument("--txt-dir",  default="outputs/txt",  help="Root of .txt files (batch mode)")
    parser.add_argument("--json-dir", default="outputs/json", help="Root for output .json files")
    parser.add_argument("--delay",    type=int, default=5000, help="Delay between articles in ms")
    args = parser.parse_args()

    txt_dir  = Path(args.txt_dir)
    json_dir = Path(args.json_dir)

    # Single-file mode
    if args.txt:
        txt_path = Path(args.txt)
        if not txt_path.exists():
            print(f"❌ File not found: {txt_path}")
            sys.exit(1)
        txt_path, json_path, section, year = _single_txt_entry(txt_path, txt_dir, json_dir)
        cp      = load_checkpoint()
        txt_rel = str(txt_path).replace("\\", "/")
        if json_path.exists() or _already_done(cp, txt_rel):
            print(f"⏭  Already extracted: {txt_path.name} — skipping. Delete {json_path} to re-extract.")
            cp["totals"]["skipped"] += 1
            save_checkpoint(cp)
            sys.exit(0)
        todo = [(txt_path, json_path, section, year)]
    else:
        if not txt_dir.exists():
            print(f"❌ txt directory not found: {txt_dir}")
            sys.exit(1)
        cp   = load_checkpoint()
        todo = []
        for txt_path in discover_txt_files(txt_dir):
            json_path     = _txt_to_json_path(txt_path, txt_dir, json_dir)
            section, year = _section_year(txt_path, txt_dir)
            txt_rel       = str(txt_path).replace("\\", "/")
            if json_path.exists() or _already_done(cp, txt_rel):
                print(f"⏭  Skipping (already extracted): {txt_path.name}")
                cp["totals"]["skipped"] += 1
                continue
            todo.append((txt_path, json_path, section, year))

    if not todo:
        print("✅ Nothing to extract — all files already done.")
        save_checkpoint(cp)
        sys.exit(0)

    print(f"📂 Files to process: {len(todo)}")

    failed = 0
    for txt_path, json_path, section, year in todo:
        txt_rel   = str(txt_path).replace("\\", "/")
        json_rel  = str(json_path).replace("\\", "/")
        parent_id = "tn-" + txt_path.stem.lower().replace("_", "-")

        print(f"\n{'='*60}")
        print(f"📄 {txt_path.name}  [{section} / {year}]")

        try:
            articles = process_txt_file(txt_path, parent_id, args.delay)

            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(
                json.dumps(articles, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            entry = {
                "txt_path":       txt_rel,
                "json_path":      json_rel,
                "section":        section,
                "year":           year,
                "filename":       json_path.name,
                "articles_count": len(articles),
                "status":         "extracted",
                "error":          "",
                "updated_at":     _utc_now(),
            }
            _upsert_entry(cp, entry)
            cp["totals"]["extracted"] += 1
            print(f"  ✅ {len(articles)} articles → {json_path}")

        except Exception as e:
            failed += 1
            print(f"  ❌ Failed: {e}")
            entry = {
                "txt_path":       txt_rel,
                "json_path":      json_rel,
                "section":        section,
                "year":           year,
                "filename":       json_path.name,
                "articles_count": 0,
                "status":         "failed",
                "error":          str(e),
                "updated_at":     _utc_now(),
            }
            _upsert_entry(cp, entry)
            cp["totals"]["failed"] += 1

        save_checkpoint(cp)

    print(f"\n{'='*60}")
    print(f"✅ Extracted: {cp['totals']['extracted']}  ⏭ Skipped: {cp['totals']['skipped']}  ❌ Failed: {cp['totals']['failed']}")

    if cp["totals"]["extracted"] == 0 and cp["totals"]["skipped"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
