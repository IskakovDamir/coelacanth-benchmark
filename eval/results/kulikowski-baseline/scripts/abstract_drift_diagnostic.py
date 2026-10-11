#!/usr/bin/env python3
"""Locate the source of the small abstract-row drift in the Kulikowski baseline reproduction.

The five abstracts in data/restricted.csv are not redistributed; fetch_restricted.py rebuilt them
from OpenAlex (the publisher pages refused the request), so their bytes differ from the frozen files
the paper used. Every other input passes SHA256SUMS, and every fingerprint row reproduces exactly.
This script shows which of the 92 curated queries involve a rebuilt abstract (as the query, as the
top-1 cross-field candidate, or as the first true twin) and prints abstract+TF-IDF P@1 and MRR with
and without those queries.

Run from the root of a Kulikowski package clone:
    python /path/to/abstract_drift_diagnostic.py
"""
import csv
import os
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "src")
import embed as E  # noqa: E402
import reproduce as R  # noqa: E402

DATA = "data"
restricted = {r["id"] for r in csv.DictReader(open(os.path.join(DATA, "restricted.csv")))}

lab, mfield = R.load_labels(), R.load_field()
absr = E.load_rep(DATA, "abstract")
ids, field, fam = R.corpus("curated", absr, lab, mfield)
N = len(ids)
in_curated = sorted(restricted & set(ids))
print(f"curated papers: {N}; restricted abstracts inside the curated set: {len(in_curated)} {in_curated}")


def per_query(texts):
    S = E.cosine_matrix(R.vectors(texts, "tfidf"))
    np.fill_diagonal(S, -2)
    rows = []
    for q in range(N):
        if not fam[ids[q]]:
            continue
        cand = [k for k in np.argsort(-S[q]) if field[ids[k]] != field[ids[q]]]
        if not any(fam[ids[k]] == fam[ids[q]] for k in cand):
            continue
        rank = next(r for r, k in enumerate(cand) if fam[ids[k]] == fam[ids[q]]) + 1
        first_twin = cand[rank - 1]
        rows.append({"query": ids[q], "top1": ids[cand[0]], "first_twin": ids[first_twin],
                     "p1": 1.0 if rank == 1 else 0.0, "rr": 1.0 / rank})
    return rows


abstract_rows = per_query([absr[i] for i in ids])
fp_rows = per_query([R.faceted_text("skeletons_faceted_haiku", i) for i in ids])


def touched(r):
    return bool({r["query"], r["top1"], r["first_twin"]} & restricted)


def summary(rows, label):
    t = [r for r in rows if touched(r)]
    u = [r for r in rows if not touched(r)]
    mean = lambda xs, k: float(np.mean([x[k] for x in xs])) if xs else float("nan")  # noqa: E731
    print(f"{label}: all {len(rows)} queries P@1={mean(rows, 'p1'):.3f} MRR={mean(rows, 'rr'):.3f} | "
          f"{len(t)} touching a rebuilt abstract P@1={mean(t, 'p1'):.3f} | "
          f"{len(u)} untouched P@1={mean(u, 'p1'):.3f} MRR={mean(u, 'rr'):.3f}")
    return t


t = summary(abstract_rows, "abstract + TF-IDF   ")
summary(fp_rows, "fingerprint + TF-IDF")
print("\nabstract queries touching a rebuilt abstract (query / top-1 / first twin / hit):")
for r in t:
    print(f"  {r['query']:32} {r['top1']:32} {r['first_twin']:32} {'HIT' if r['p1'] else 'miss'}")
