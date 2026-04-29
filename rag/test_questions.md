# RAG Test Questions
Tests for cross-encoder reranking + conversation history (interactive mode).
Sources: JORT_001_2026-01-02 (Finances) and JORT_002_2026-01-06 (Archives concours).

---

## 1. Single-turn — factual (French)
These have a precise answer in the indexed articles. Good baseline to verify retrieval works.

```
python rag/query.py "Quelles sont les conditions de diplôme pour participer au concours de gestionnaire de documents et d'archives ?"
```
Expected: Article 1 of JORT_002 — 2-year diploma in archives management OR 4-year master in library science, max age 40.

```
python rag/query.py "Quels documents faut-il fournir dans le dossier de candidature au concours d'archives ?"
```
Expected: Article 3 of JORT_002 — ID card, bac copy, academic diploma, medical certificate (≤3 months), birth certificate, criminal record (casier judiciaire).

```
python rag/query.py "Comment soumettre un dossier de candidature au Conseil national des régions et des districts ?"
```
Expected: Article 4 of JORT_002 — registered mail or direct deposit at the bureau d'ordre central; postmark or registration date is the reference.

```
python rag/query.py "Quelle est la durée du cycle de formation à l'École nationale des finances ouvert en 2026 ?"
```
Expected: Article 1 of JORT_001 — 6 months, starting January 19 2026.

```
python rag/query.py "Quelles sont les conditions pour s'inscrire au cycle de formation d'inspecteur central des services financiers ?"
```
Expected: Article 2 of JORT_001 — must have completed all preparatory unit credits (unités de valeurs préparatoires) per article 12 of the Sept 14 1999 decree.

---

## 2. Single-turn — vague / short queries (reranking impact zone)
Short or keyword-style queries where embeddings alone may mis-rank. The reranker should surface the right article over thematically similar but less relevant ones.

```
python rag/query.py "recrutement archives 2026"
```
Expected: Articles from JORT_002 about the archives concours conditions and procedure.

```
python rag/query.py "formation inspecteur finances"
```
Expected: Articles from JORT_001 about the ENF training cycle.

```
python rag/query.py "conditions candidature fonctionnaire tunisien"
```
Expected: A mix — JORT_002 Art.1 (age/diploma) and JORT_001 Art.2 (credits). The reranker should rank the most specific one first given the query wording.

```
python rag/query.py "limite d'âge concours"
```
Expected: JORT_002 Art.1 — age limit of 40 years for the archives exam.

```
python rag/query.py "inscription en ligne dossier"
```
Expected: JORT_002 Art.3 — online registration and document list.

---

## 3. Single-turn — Arabic queries
Tests language detection, Arabic content fields, and reranking on Arabic text pairs.

```
python rag/query.py "ما هي شروط الترشح لمناظرة متصرفي الوثائق والأرشيف؟"
```
Expected (in Arabic): Conditions from JORT_002 Art.1 — diploma level and max age 40.

```
python rag/query.py "ما هي الوثائق المطلوبة لملف الترشح؟"
```
Expected (in Arabic): Document list from JORT_002 Art.3.

```
python rag/query.py "متى تبدأ دورة التكوين بالمدرسة الوطنية للمالية؟"
```
Expected (in Arabic): JORT_001 Art.1 — starts January 19 2026, duration 6 months.

---

## 4. Conversation history chains (run with --interactive)
Each block is a sequence of questions to enter one after another in interactive mode.
The follow-up questions are deliberately ambiguous — they only make sense with prior history.

### Chain A — archives concours
```
python rag/query.py --interactive
```
Turn 1: `Quelles sont les conditions d'accès au concours externe de gestionnaire d'archives ?`
Turn 2: `Et quelle est la limite d'âge exacte ?`  ← needs history to know which concours
Turn 3: `Quels documents dois-je joindre au dossier ?`  ← continues same thread
Turn 4: `Puis-je les envoyer par courrier ?`  ← depends on Art.4 context

### Chain B — finances training
```
python rag/query.py --interactive
```
Turn 1: `Dis-moi ce que prévoit l'arrêté sur la formation à l'École nationale des finances.`
Turn 2: `Quand commence-t-elle exactement ?`  ← "elle" = la formation, needs history
Turn 3: `Qui peut s'y inscrire ?`  ← "s'y" = à cette formation

### Chain C — cross-topic (tests history doesn't bleed between topics)
```
python rag/query.py --interactive
```
Turn 1: `Quelles sont les conditions de diplôme pour le concours d'archives ?`
Turn 2: `Et pour la formation des inspecteurs des finances, c'est quoi les conditions ?`  ← topic switch, should retrieve JORT_001
Turn 3: `Quelle est la différence entre les deux procédures ?`  ← synthesis across history

---

## 5. Out-of-scope queries (confidence gating test)
The system should say it cannot answer from the available articles, not hallucinate.

```
python rag/query.py "Quelles sont les sanctions en cas de fraude fiscale en Tunisie ?"
```
Expected: No relevant articles found — the indexed documents don't cover tax penalties.

```
python rag/query.py "Quels sont les droits des salariés en cas de licenciement abusif ?"
```
Expected: No relevant articles — labor law termination rules are not in these two issues.

```
python rag/query.py "Comment créer une entreprise en Tunisie ?"
```
Expected: No relevant articles — company registration is not covered.

---

## 6. Reranking comparison (run same query with and without --no-rerank)
Run each pair and compare which articles appear in the sources list and their scores.

```
python rag/query.py "candidature concours fonction publique documents requis"
python rag/query.py "candidature concours fonction publique documents requis" --no-rerank
```

```
python rag/query.py "accès grade inspecteur formation"
python rag/query.py "accès grade inspecteur formation" --no-rerank
```

Look for: different ordering of sources, different scores (RRF ~0.01–0.03 vs reranker 0.0–1.0), and whether the most specific article is ranked first.

---

## 7. Year filter combined with reranking

```
python rag/query.py "conditions de recrutement dans la fonction publique" --year 2026
```
Expected: Only JORT_002 articles (year 2026). JORT_001 articles are tagged year 2025 (signed Dec 31 2025) — they should be filtered out.

```
python rag/query.py "conditions de recrutement dans la fonction publique" --year 2025
```
Expected: Only JORT_001 articles (year 2025).
