#!/usr/bin/env python3
"""Kulikowski's fingerprint + TF-IDF on the closed pool (eval/protocol.md section 5; decision 35), as rankings the
harness scores like any system's: for each of the 109 pool papers, every cross-field candidate with its cosine score.

Two TF-IDF settings, because the package uses both:
- `unigram`: a plain TfidfVectorizer() fitted on the whole-fingerprint texts, as src/held_out_family.py does; it
  reproduces the paper's reported whole-fingerprint P@1 (0.826) and is P2's comparator (decision 35);
- `headline`: the package's embed.embed_texts setting (stop words, sublinear tf, unigrams and bigrams), as
  src/p1mrr_boot.py and reproduce.py do; P@1 0.870, a sensitivity line, and the paper's headline AP 0.557.

Each paper gets a ranking row. `query_role` is `query` for the 92 member papers with a cross-field twin in the pool
(P@1 and MRR are computed over these only) and `pool_only` for the other 17, whose rankings are needed because AP is
pair-level over all 5,812 cross-field pairs, each scored by the mean of its two directed scores (protocol 5).
The script asserts P@1 and MRR of both settings and the headline AP at three decimals (the package reports no AP
for the unigram setting, so that AP is computed and recorded, not checked), then writes, per setting:
    eval/results/measurement-a/closed-pool/kulikowski-fp/<setting>/rankings.jsonl   one row per paper
    eval/results/measurement-a/closed-pool/kulikowski-fp/<setting>/pairs.jsonl      one row per cross-field pair
    eval/results/measurement-a/closed-pool/kulikowski-fp/<setting>/summary.json     counts, P@1, MRR, AP
No model or network is called.

Run from the root of the Kulikowski package clone (data/raw/kulikowski-spdf):
    ~/.venvs/rediscovery-layer/bin/python ../../../eval/results/measurement-a/scripts/kulikowski_rank.py
The helpers above main() use only the standard library, so tests/test_kulikowski_rank.py can import this file.
"""
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
OUT = os.path.join(REPO, "eval", "results", "measurement-a", "closed-pool", "kulikowski-fp")
EXPECTED = {"unigram": (0.826, 0.870), "headline": (0.870, 0.912)}
EXPECTED_AP = {"headline": 0.557}  # reproduce.py, curated table, faceted-full + TF-IDF [Haiku]; no unigram AP exists
N_PAPERS, N_QUERIES, N_PAIRS, N_TWINS = 109, 92, 5812, 210
FORMAT = "coelacanth-closed-pool/v1"
PAIRS_FORMAT = "coelacanth-closed-pool-pairs/v1"
SUMMARY_FORMAT = "coelacanth-closed-pool-summary/v1"


def rankings(ids, field, fam, S, papers):
    """A ranking row for every paper; P@1 and MRR over the queries (a family and a cross-field twin in the pool)."""
    rows, p1, rr = [], [], []
    for q, qid in enumerate(ids):
        order = sorted(range(len(ids)), key=lambda k: -S[q][k])  # stable: ties keep corpus order, as np.argsort did
        cand = [k for k in order if field[ids[k]] != field[qid]]
        is_query = bool(fam[qid]) and any(fam[ids[k]] == fam[qid] for k in cand)
        if is_query:
            rank = next(r for r, k in enumerate(cand) if fam[ids[k]] == fam[qid]) + 1
            p1.append(1.0 if rank == 1 else 0.0)
            rr.append(1.0 / rank)
        rows.append({"query": qid, "query_role": "query" if is_query else "pool_only",
                     "query_openalex": papers.get(qid), "family": fam[qid], "field": field[qid],
                     "ranking": [{"id": ids[k], "openalex": papers.get(ids[k]), "score": round(float(S[q][k]), 6),
                                  "rank": n} for n, k in enumerate(cand, 1)]})
    return rows, sum(p1) / len(p1), sum(rr) / len(rr)


def directed_score(entry):
    """A ranking entry's score; a system that returns only ranks scores 1 / rank (protocol 5)."""
    return float(entry["score"]) if entry.get("score") is not None else 1.0 / entry["rank"]


def pair_rows(rows, ids, field, fam):
    """Every cross-field pair of the pool, scored by the mean of its two directed scores (protocol 5)."""
    directed = {(row["query"], e["id"]): directed_score(e) for row in rows for e in row["ranking"]}
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            if field[a] == field[b]:
                continue
            ab, ba = directed.get((a, b)), directed.get((b, a))
            if ab is None or ba is None:
                raise ValueError(f"pair {a} / {b} has {2 - [ab, ba].count(None)} directed scores, expected 2")
            out.append({"a": a, "b": b, "score_ab": ab, "score_ba": ba, "score": round((ab + ba) / 2, 7),
                        "twin": bool(fam[a]) and fam[a] == fam[b]})
    return out


def average_precision(labels, scores):
    """sklearn's average_precision_score: over distinct scores, high to low, the recall gained times the precision
    at that threshold (tied scores enter together)."""
    n_pos = sum(1 for x in labels if x)
    if not n_pos:
        raise ValueError("no positive pairs")
    order = sorted(range(len(scores)), key=lambda k: -scores[k])
    ap, tp, i = 0.0, 0, 0
    while i < len(order):
        j = i
        while j < len(order) and scores[order[j]] == scores[order[i]]:
            j += 1
        gained = sum(1 for k in order[i:j] if labels[k])
        tp += gained
        ap += gained / n_pos * tp / j
        i = j
    return ap


def check_counts(rows, pairs):
    """The pool's fixed sizes (protocol 5): 109 papers, 92 queries, 5,812 cross-field pairs, 210 twins."""
    n_queries = sum(row["query_role"] == "query" for row in rows)
    n_twins = sum(p["twin"] for p in pairs)
    if (len(rows), n_queries, len(pairs), n_twins) != (N_PAPERS, N_QUERIES, N_PAIRS, N_TWINS):
        raise AssertionError(f"{len(rows)} papers, {n_queries} queries, {len(pairs)} pairs, {n_twins} twins; expected "
                             f"{N_PAPERS}, {N_QUERIES}, {N_PAIRS}, {N_TWINS}")


def load_package():
    """The curated corpus and both similarity matrices, read from the package clone (the working directory)."""
    if not os.path.exists("reproduce.py"):
        sys.exit("run this script from the root of the Kulikowski package clone (data/raw/kulikowski-spdf)")
    sys.path.insert(0, ".")
    sys.path.insert(0, "src")
    import embed as E
    import reproduce as R
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    ids, field, fam = R.corpus("curated", E.load_rep("data", "abstract"), R.load_labels(), R.load_field())
    fp = [R.faceted_text("skeletons_faceted_haiku", i) for i in ids]
    sims = {"unigram": cosine_similarity(TfidfVectorizer().fit_transform(fp)),
            "headline": E.cosine_matrix(R.vectors(fp, "tfidf"))}
    return ids, field, fam, {k: v.astype(float).tolist() for k, v in sims.items()}


def main():
    from sklearn.metrics import average_precision_score

    ids, field, fam, sims = load_package()
    papers = {}
    with open(os.path.join(REPO, "data", "candidates", "kulikowski-papers.jsonl"), encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            papers[row["kid"]] = row.get("openalex")
    at = {i: n for n, i in enumerate(ids)}
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    results = {}
    for setting, (p1_expected, mrr_expected) in EXPECTED.items():
        S = sims[setting]
        rows, p1, mrr = rankings(ids, field, fam, S, papers)
        pairs = pair_rows(rows, ids, field, fam)
        check_counts(rows, pairs)
        assert (round(p1, 3), round(mrr, 3)) == (p1_expected, mrr_expected), \
            f"{setting}: P@1 {p1:.3f} MRR {mrr:.3f}, expected {p1_expected} {mrr_expected}"
        labels, scores = [p["twin"] for p in pairs], [p["score"] for p in pairs]
        ap = average_precision(labels, scores)
        assert abs(ap - average_precision_score(labels, scores)) < 1e-12, f"{setting}: AP differs from sklearn's"
        # the package scores each pair once, unrounded: the exported pair scores must give the same AP
        full = [S[at[p["a"]]][at[p["b"]]] for p in pairs]
        ap_full = float(average_precision_score(labels, full))
        assert round(ap, 3) == round(ap_full, 3), f"{setting}: AP {ap:.4f} from the export, {ap_full:.4f} unrounded"
        if setting in EXPECTED_AP:
            assert round(ap, 3) == EXPECTED_AP[setting], f"{setting}: AP {ap:.3f}, expected {EXPECTED_AP[setting]}"
        summary = {"format": SUMMARY_FORMAT, "setting": setting, "package_commit": commit,
                   "papers": len(rows), "queries": sum(r["query_role"] == "query" for r in rows),
                   "pool_only": sum(r["query_role"] == "pool_only" for r in rows),
                   "pairs": len(pairs), "twins": sum(p["twin"] for p in pairs),
                   "p_at_1": round(p1, 6), "mrr": round(mrr, 6), "ap": round(ap, 6),
                   "ap_unit": "cross-field pairs; pair score = mean of the two directed scores; twin = same family",
                   "checked_against_reproduction": {"p_at_1": p1_expected, "mrr": mrr_expected,
                                                    "ap": EXPECTED_AP.get(setting)}}
        results[setting] = rows, pairs, summary
        check = f"AP {ap:.3f} (as expected)" if setting in EXPECTED_AP else f"AP {ap:.3f} (no package value to check)"
        print(f"{setting}: {N_QUERIES} queries, P@1 {p1:.3f}, MRR {mrr:.3f} (as expected); {len(pairs)} pairs, {check}")
    for setting, (rows, pairs, summary) in results.items():
        path = os.path.join(OUT, setting)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "rankings.jsonl"), "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps({"format": FORMAT, "setting": setting, **row}, ensure_ascii=False) + "\n")
        with open(os.path.join(path, "pairs.jsonl"), "w", encoding="utf-8") as f:
            for pair in pairs:
                f.write(json.dumps({"format": PAIRS_FORMAT, "setting": setting, **pair}, ensure_ascii=False) + "\n")
        with open(os.path.join(path, "summary.json"), "w", encoding="utf-8") as f:
            f.write(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}/<setting>/{{rankings.jsonl, pairs.jsonl, summary.json}}")


if __name__ == "__main__":
    main()
