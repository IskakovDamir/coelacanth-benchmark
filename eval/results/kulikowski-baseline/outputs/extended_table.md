# extended table

EXTENDED / wild run (incomplete labels): DETECTION. Headline = recall of known twins; AP is not a score, only a like-for-like comparator.

| method | AP | AUROC | R@100 | R@1000 | R@3000 |
|---|---|---|---|---|---|
| abstract + TF-IDF (cheap) | 0.04 | 0.777 | 0.067 | 0.21 | 0.329 |
| faceted-full + TF-IDF [Haiku (ours)] | 0.155 | 0.951 | 0.09 | 0.61 | 0.733 |
| faceted-full + TF-IDF [Opus] | 0.14 | 0.943 | 0.133 | 0.448 | 0.629 |
| faceted-full + TF-IDF [Qwen3-14b] | 0.079 | 0.932 | 0.071 | 0.433 | 0.629 |
| skeleton (MECHANISM) + TF-IDF [Haiku] | 0.258 | 0.925 | 0.219 | 0.524 | 0.652 |
