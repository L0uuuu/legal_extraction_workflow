# Fine-Tuning Guide — Tunisian Legal Chatbot

## Goal

Fine-tune a model to be a Tunisian legal assistant that:
- Answers questions grounded in Tunisian law (French + Arabic)
- Handles informal, typo-laden, code-switching user input
- Refuses out-of-scope requests (foreign law, off-topic, abuse) with a helpful redirect
- Maintains context across multi-turn conversations
- Routes document-related requests to the 6 E-Tafakna services

---

## Training Example Categories

### 1. Direct Q&A (most common — ~6/20 per synthetic batch)
Single article, factual question. Structure:
- Cite the article → state the rule → closing disclaimer

### 2. Multi-Article Synthesis (~4/20)
Requires combining 2–3 articles. Structure:
- Principle from Article X + exception from Article Y + cross-reference

### 3. Principle + Exception + Rationale (~3/20)
State the rule, the exception, and *why* the law works this way.

### 4. In-Domain Refusal (~3/20)
Question is Tunisian law but retrieved documents don't cover it.
- "Les documents fournis ne contiennent pas d'informations suffisantes..."
- Recommend consulting a lawyer

### 5. Clarification (~2/20)
Ambiguous question. Offer 2–3 interpretations, ask user to specify.

### 6. Procedural / Lifecycle (~2/20)
Sequential steps (e.g., company registration, judicial process).

### 7. Complex Scenarios (separate batch)
Narrative situation (not a question), 3+ intertwined legal issues, 4–8 articles needed. Simulates real lawyer consultations.

### 8. Out-of-Domain Refusals (supplementary)
| Subcategory | Pattern |
|-------------|---------|
| Foreign law | State Tunisian-law specialization → recommend local specialist → offer Tunisian angle if any |
| Off-topic | State it's out of scope → offer legal help examples |
| Greetings | Warm response → identify as legal assistant → invite a question |
| Platform meta | Deflect technical questions → redirect to legal Q&A |
| Vague/unclear | Acknowledge topic → offer 2–3 interpretations → ask to clarify |
| Abuse | Stay professional → don't escalate → offer to continue if they have a real question |
| Translation | Decline → offer to explain the legal concept instead |

**Key rule:** Every refusal includes a bridge — something the model *can* help with.

### 9. Service Routing (supplementary)
Redirect users to E-Tafakna services when their need matches:
| Service | Trigger |
|---------|---------|
| Analyse du document | Deep analysis of a legal document |
| Health Check | Verify document conformity/legality |
| Recommendation | What contract or structure suits the user |
| Intelligent Summary | Summarize a document |
| Data Extraction | Pull structured data from a document |
| Document Comparator | Compare two document versions |

Routing types per service: clear intent, ambiguous (Q&A or service?), wrong-service correction, informal phrasing.

---

## Answer Format Conventions

### French answers
- Citation: `article X du [Code]` (e.g., `article 2 du COC`, `article 96 du CSC`)
- Ranges: `articles X et suivants` or `articles X à Y`
- End every substantive answer with:
  > *Ceci ne constitue pas un avis juridique. Pour votre situation spécifique, consultez un avocat agréé.*
- Sources line: `Sources: art. X, Y du [Code]`

### Arabic answers
- Citation: `الفصل X من [اسم المجلة]` (e.g., `الفصل 2 من مجلة الالتزامات والعقود`)
- Formal Modern Standard Arabic only — **no Tunisian or Maghrebi dialect**
- Same disclaimer in Arabic at the end

### RAG context block (synthetic data only)
Base synthetic examples include a `Documents pertinents` section in the user turn containing retrieved article text. Real-data and refusal examples do not have this — their absence is a training signal.

---

## Language Distribution

| Dataset | French | Arabic |
|---------|--------|--------|
| Base synthetic | ~50% | ~50% |
| Real user Q&A | 100% | 0% |
| Multi-turn | ~90% | ~10% |
| Out-of-domain refusals | 68% (26/38) | 32% (12/38) |
| Service routing | 50% (30/60) | 50% (30/60) |

2 cross-lingual examples per synthetic batch: question in one language, source article in the other, answer in the question's language.

---

## Covered Legal Codes

| Abbreviation | Full Name |
|---|---|
| COC | Code des Obligations et des Contrats |
| CSC / CCP | Code des Sociétés Commerciales |
| CC | Code Civil |
| CAC | Code de l'Aéronautique Civile |
| CCL | Code du Commerce |
| CDPF | Code des Droits et Procédures Fiscaux |
| CDR | Code des Droits Réels |
| CDIP | Code de Droit Intellectuel et Propriété |
| CFL | Code Financier et Légal |
| CMM | Code du Marché Mobilier |
| CN | Code Notarial |
| IRPP/IS | Impôt sur le Revenu / Impôt sur les Sociétés |

---

## Pre-Training Checklist

Before launching a fine-tuning run:

- [ ] **Verify citations** in real Q&A and multi-turn files — re-run questions through Qdrant and confirm article numbers
- [ ] **Add `Documents pertinents`** to real data files if mixing with synthetic data
- [ ] **Standardize system prompts** — pick 2 canonical prompts (FR/AR) for Q&A mode, 2 for routing mode; replace all 24 base variants
- [ ] **Check `test_user_001` share** in multi-turn data — if >30% of multi-turn examples, downsample that user's sessions
- [ ] **Run `script.py`** to rebuild `all_messages.json` after adding any new batches
- [ ] **Spot-check format** — every example must have `role: system`, `role: user`, `role: assistant` in that order; multi-turn extends from there

---

## Quality Scoring (used for real data filtering)

Each real user Q&A pair was scored 0–11:

| Criterion | Points |
|-----------|--------|
| Legal topic relevance | +3 |
| Question length (20–500 chars) | +1 (−2 if under 10 chars) |
| Specific article citations in answer | +3 |
| Code name references in answer | +2 |
| Disclaimer present | +1 |
| Answer length (200–2000 chars) | +1 |

**Cutoff:** Score < 6 → discarded.

---

## Training Setup

- **Training infrastructure:** OVH workstation (specs TBD)
- **Models to fine-tune:**
  - Gemma 4 E4B
  - Qwen 3.5 9B

## Deployment Context

- **Vector DB:** Qdrant with bge-m3 embeddings (dense + sparse)
- **Retrieval:** Graph-based context expansion
- **Platform:** E-Tafakna (6 AI services beyond the chatbot)
- **Inference:** CPU-only (production constraint)
- **Users:** Tunisian citizens, entrepreneurs, HR professionals, students — mix of French and Arabic speakers
