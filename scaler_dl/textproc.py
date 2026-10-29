"""Turn Whisper segments into readable, timestamped text and pick out what matters.

No AI service involved: sentence timing, paragraphs, extractive key sentences (TF-IDF over the
class's chapters), recurring key terms and the instructor's substantive questions.
"""

from __future__ import annotations

import math
import re
from collections import Counter

STOPWORDS = set(
    """a about above after again against all am an and any are as at be because been before being below
    between both but by can cannot could did do does doing down during each few for from further had has have
    having he her here hers herself him himself his how i if in into is it its itself just me more most my
    myself no nor not of off on once only or other our ours ourselves out over own same she should so some such
    than that the their theirs them themselves then there these they this those through to too under until up
    very was we were what when where which while who whom why will with would you your yours yourself
    yourselves s t don didn doesn isn wasn aren weren won wouldn couldn shouldn can't don't it's i'm we're
    you're they're that's there's let's i'll we'll you'll i've we've what's""".split()
)
# Spoken-lecture filler: frequent in every class, says nothing about the topic.
FILLERS = set(
    """okay ok yeah yes yep yup right alright like um uh hmm basically actually really guys everyone everybody
    fine see saw going gonna wanna let us know think thing things something anything everything also well
    get got getting take make want need say said saying tell telling come look looking please sir mam thank
    thanks doubt doubts clear understood able kind sort lot bit way today now then here one two three first
    second next just still even may might must shall simply already again whatever whichever someone
    anyone people person chat type typing screen visible audible hear minute minutes seconds sure good great
    nice correct wrong answer answers question questions mean means meant else""".split()
)
CUES = re.compile(
    r"\b(is called|is known as|defined as|means that|in other words|the idea is|the reason|because|important|"
    r"remember|note that|key|rule|always|never|difference between|time complexity|space complexity|big o|"
    r"o of|formula|property|observation|intuition|approach|brute force|optimi[sz]|edge case|example|"
    r"in interviews?|trick|default|range|syntax|returns?)\b",
    re.I,
)
CHATTER = re.compile(
    r"\b(can you (see|hear)|am i audible|is it visible|my screen|share (my|the) screen|in the chat|"
    r"type (it )?in|give me a (second|minute)|one (second|minute)|good (morning|evening|afternoon)|"
    r"welcome|break|attendance|feedback|let me know|yes or no|everyone (is )?clear|any doubts?|"
    r"are you (able|clear)|all good|wait|sorry|how many of you|anyone (who|having))\b",
    re.I,
)
# Words in flashcard titles that nobody says out loud ("Double vs Float", "String Handling").
TITLE_FILLER = {"vs", "versus", "handling", "basics", "basic", "introduction", "intro", "overview", "using",
                "usage", "concept", "concepts", "example", "examples", "types", "type", "statements", "statement"}
# "..., right?" / "okay?" style questions are checks for attention, not questions to study.
TAG_QUESTION = re.compile(r"(\b(right|okay|ok|correct|yes|no|na|clear|fine|done|everyone)|isn't it|is it)\s*\?$", re.I)
WORD = re.compile(r"[A-Za-z][A-Za-z0-9+#]*(?:\.[A-Za-z][A-Za-z0-9]*)*")


def tokens(text: str) -> list[str]:
    return [w for w in (m.group().lower() for m in WORD.finditer(text)) if w not in STOPWORDS and w not in FILLERS and len(w) > 1]


def sentences(segments: list[dict]) -> list[dict]:
    """Whisper gives phrase-sized segments; regroup them into sentences with interpolated times."""
    out: list[dict] = []
    buf, start = "", None
    for seg in segments:
        text = seg["text"].strip()
        if not text:
            continue
        span, n, pos = seg["end"] - seg["start"], len(text), 0
        for piece in re.split(r"(?<=[.!?])\s+", text):
            t0 = seg["start"] + span * pos / n
            pos += len(piece) + 1
            t1 = seg["start"] + span * min(pos / n, 1.0)
            if start is None:
                start = t0
            buf = f"{buf} {piece}".strip()
            if re.search(r"[.!?]['\")\]]*$", piece) or len(buf) > 500:
                out.append({"start": round(start, 2), "end": round(t1, 2), "text": buf})
                buf, start = "", None
    if buf and start is not None:
        out.append({"start": round(start, 2), "end": segments[-1]["end"], "text": buf})
    return out


def paragraphs(sents: list[dict], max_seconds: float = 60.0, pause: float = 3.0) -> list[dict]:
    paras: list[dict] = []
    for s in sents:
        p = paras[-1] if paras else None
        if p is None or s["start"] - p["end"] > pause or s["end"] - p["start"] > max_seconds:
            paras.append({"start": s["start"], "end": s["end"], "text": s["text"]})
        else:
            p["end"], p["text"] = s["end"], f"{p['text']} {s['text']}"
    return paras


def _idf(groups: list[list[dict]]) -> dict[str, float]:
    df: Counter[str] = Counter()
    for g in groups:
        df.update({w for s in g for w in tokens(s["text"])})
    n = len(groups)
    return {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}


def highlights(groups: list[list[dict]]) -> list[list[dict]]:
    """For each chapter, the sentences that carry the most chapter-specific vocabulary."""
    idf = _idf(groups)
    result = []
    for g in groups:
        minutes = (g[-1]["end"] - g[0]["start"]) / 60 if g else 0
        per_group = min(3 + int(minutes // 8), 8)  # a 40-minute chapter deserves more than 3 lines
        tf = Counter(w for s in g for w in tokens(s["text"]))
        scored = []
        for s in g:
            words = s["text"].split()
            toks = set(tokens(s["text"]))
            if not (8 <= len(words) <= 45) or len(toks) < 3 or s["text"].endswith("?") or CHATTER.search(s["text"]):
                continue
            score = sum(math.log1p(tf[w]) * idf[w] for w in toks) / math.sqrt(len(words))
            if CUES.search(s["text"]):
                score *= 1.4
            scored.append((score, s, toks))
        picked: list[tuple[float, dict, set]] = []
        for score, s, toks in sorted(scored, key=lambda x: -x[0]):
            if all(len(toks & t) / len(toks | t) < 0.35 for _, _, t in picked):
                picked.append((score, s, toks))
            if len(picked) == per_group:
                break
        result.append(sorted((s for _, s, _ in picked), key=lambda s: s["start"]))
    return result


def key_terms(sents: list[dict], limit: int = 20, vocabulary: set[str] | None = None) -> list[dict]:
    """Recurring 1-3 word phrases (a run of content words), with where they come up.

    `vocabulary` (words from Scaler's notes, flashcards and bookmarks) keeps the list on topic;
    without it only multi-word phrases count, since single spoken words are mostly verbal habits.
    """
    counts: Counter[str] = Counter()
    where: dict[str, list[float]] = {}
    for s in sents:
        run: list[str] = []
        for raw in re.split(r"[^A-Za-z0-9+#.]+", s["text"].lower()) + [""]:
            w = raw.strip(".")
            if (
                w and w not in STOPWORDS and w not in FILLERS and len(w) > 1 and not w.isdigit()
                and (vocabulary is None or w in vocabulary)
            ):
                run.append(w)
                continue
            for n in (1, 2, 3):
                for i in range(len(run) - n + 1):
                    phrase = " ".join(run[i:i + n])
                    counts[phrase] += 1
                    times = where.setdefault(phrase, [])
                    if not times or s["start"] - times[-1] > 60:
                        times.append(s["start"])
            run = []
    scored = []
    for phrase, c in counts.items():
        n = phrase.count(" ") + 1
        if c < (6 if n == 1 else 3) or len(where[phrase]) < 2 or (n == 1 and vocabulary is None):
            continue
        scored.append((c * (1 + 0.8 * (n - 1)), phrase))
    phrases = [p for _, p in sorted(scored, reverse=True)]
    chosen: list[str] = []
    for phrase in phrases:
        # Prefer "data types" over "data" when the phrase accounts for a good share of the word.
        if " " not in phrase:
            longer = [p for p in phrases if " " in p and phrase in p.split() and counts[p] >= 0.3 * counts[phrase]]
            if longer:
                phrase = max(longer, key=lambda p: counts[p])
        words = set(phrase.split())
        if any(words <= set(other.split()) or set(other.split()) <= words for other in chosen):
            continue
        chosen.append(phrase)
        if len(chosen) == limit:
            break
    return [{"term": p, "count": counts[p], "times": where[p][:6]} for p in chosen]


def mentions(sents: list[dict], phrase: str, limit: int = 4) -> list[float]:
    """Where a flashcard's term is talked about: sentences containing its rarest content word.

    Strict on purpose: if any word of the term is never said ("Scanner Class" before Scanner is
    taught), report nothing rather than every mention of the generic word ("class").
    """
    words = [w for w in tokens(phrase.replace(".", " ").replace("-", " ")) if w not in TITLE_FILLER]
    if not words:
        return []
    said = [set(tokens(s["text"].replace(".", " ").replace("-", " "))) for s in sents]
    freq = Counter(w for ws in said for w in ws)
    joined = "".join(words)  # Whisper often writes "Type Casting" as "typecasting"
    if len(words) > 1 and freq[joined]:
        key = joined
    elif all(freq[w] for w in words):
        key = min(words, key=lambda w: freq[w])
    else:
        return []
    times: list[float] = []
    for s, ws in zip(sents, said):
        if key in ws and (not times or s["start"] - times[-1] > 90):
            times.append(s["start"])
            if len(times) == limit:
                break
    return times


def instructor_questions(sents: list[dict], limit: int = 15) -> list[dict]:
    """Questions worth answering yourself (skips "can you see my screen?" / "..., right?" chatter)."""
    qs: list[dict] = []
    for s in sents:
        text = s["text"]
        toks = set(tokens(text))
        if not (text.endswith("?") and 7 <= len(text.split()) <= 40 and len(toks) >= 3):
            continue
        if CHATTER.search(text) or TAG_QUESTION.search(text):
            continue
        if any(len(toks & set(tokens(q["text"]))) / len(toks | set(tokens(q["text"]))) > 0.5 for q in qs[-5:]):
            continue  # the same question, rephrased a few seconds later
        qs.append(s)
    if len(qs) <= limit:
        return qs
    keep = sorted(qs, key=lambda s: -len(set(tokens(s["text"]))))[:limit]
    return sorted(keep, key=lambda s: s["start"])
