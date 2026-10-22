# Coelacanth

Coelacanth v0 is a benchmark for precedent checkers, the tools that check whether an idea was published before, on earlier work stated in another field's vocabulary. Its 60 items include the 10 documented cross-field rediscoveries of the main finding below, 8 attributed controls, 10 AI-generated methods and 15 negatives. A system under test sees only a field-native claim and its date, and each claim-candidate pair carries one of six typed relations: equivalent, specialization, generalization, partial, similar mechanism or different. This repository holds the code, the frozen protocol with its deviations log, the labelling guide, the datasheet and the tests; the items and their gold ship in an encrypted archive.

[![Coelacanth overview](docs/media/overview.gif)](docs/media/overview.mp4)

A silent 24.8-second overview of the main finding. The same clip as an [MP4](docs/media/overview.mp4) (519 KB) downloads from GitHub, which has no player for it. Worked example E1 in its first seconds is excluded from the benchmark.

## Main finding

Three Claude models with no tools recall 9 of the 10 documented cross-field rediscoveries in v0, so a retrieval score on them cannot be separated from memory. For six of them a model names a quiz-gold cross-field work; three count only through works the models named, which an annotator rated and the author adjudicated. The tenth would be a closed-book failure only if the declines of Opus 5.5 were ignored and four undecided loose matches of Haiku 4.5 read as misses. An attribution reading the annotators made on 21 October, after the rules freeze, would add an eleventh rediscovery, cf-0031, whose earlier work Opus 5.5 and Sonnet 5.5 name under both prompts, so 10 of 11 would be recalled; the frozen 10 stay the primary set, and the agents below ran on them.

Two Claude Code agents with web search, Sonnet 5.5 with and without a prior-art skill, find a cross-field gold work in their top 10 for 3 and 5 of the 10 items. Sonnet 5.5 without tools names one in at least one of five samples for 6 (4 on quiz gold alone). On the 9 near-negatives the single agent answers `nothing_close` for 1 and the prior-art skill for none.

Five paid annotators labelled 136 claim-candidate pairs twice, choosing the relation from three options Claude proposed (the page also allowed any other relation, and no answer used it), and the author settled the 51 disagreements. Of the 272 answers, 189 equal the option Claude ranked first. Krippendorff's alpha on the six-way relation is 0.542, below the 0.667 the protocol fixed before labelling.

## Paper

A preprint is in preparation; its link will be added here once it is posted.

## Data

The items with their gold, the closed-book answers, the run records and the annotators' returns are one encrypted archive, `coelacanth-v0-data.tar.gz.enc`:

- OSF: https://doi.org/10.17605/OSF.IO/672RJ
- Hugging Face mirror: https://huggingface.co/datasets/DamirIskakov/coelacanth-v0
- Figshare mirror: https://doi.org/10.6084/m9.figshare.34337127

The password and the decryption command are in [eval/benchmark/DATASHEET.md](eval/benchmark/DATASHEET.md). The archive is encrypted so that crawlers do not pick up the gold, and it carries a canary string; please do not train or tune models on these files or republish them in plain text. Unpacked into the repository root, the archive restores the paths the data tests expect, and `.gitignore` keeps them out of commits.

## Reproduce

Python 3.12 or later; the package uses only the standard library.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

On 21 October 2026 the 647 tests ran without a failure on Python 3.12 and 3.14, in a git clone and in a copy without git history such as GitHub's ZIP download; the ones that need the data archive, cached full texts, a clean-room prompt or scikit-learn were skipped.

The reproduction of Kulikowski's TF-IDF baseline (paper, Section 6) needs git, curl, unzip, shasum, md5 and network access, and no model or API key:

```sh
bash eval/results/kulikowski-baseline/run.sh
```

It clones his package at tag v1.0.0 into `data/raw/`, checks it against the KU Leuven RDR deposit, rebuilds the five abstracts that cannot be redistributed and reruns his tables into `eval/results/kulikowski-baseline/outputs/`. It installs its requirements in a virtual environment at `~/.venvs/rediscovery-layer` unless `VENV` is set. Run from a fresh clone on macOS on 21 October 2026, it reproduced every committed output file byte for byte except the date line of `environment.txt`.

## Repository

- `src/coelacanth/`: the item loader with its validation rules, the closed-book runner and the harness and scorer of measurement A
- `eval/protocol-v1.0.md`: the protocol exactly as tagged at the freeze on 11 October 2026, whose SHA-256 the paper prints; `eval/protocol.md` adds the deviations logged since
- `eval/benchmark/`: schema, labelling guide, datasheet and data licence
- `eval/results/`: the splits as item IDs and hashes, the Kulikowski reproduction, the agents' configurations and prompts, and the count of human decisions
- `docs/`: decisions, design notes and the annotators' kit in Russian
- `tools/`: the annotation, quiz and check pages and the freeze script

## Licences

Code: MIT ([LICENSE](LICENSE)). Data: CC BY 4.0 ([eval/benchmark/LICENSE](eval/benchmark/LICENSE)).

## Citation

```bibtex
@misc{iskakov2026coelacanth,
  author       = {Damir Iskakov},
  title        = {Coelacanth: A Cross-Field Rediscovery Benchmark for Precedent Checkers, and What Closed-Book Recall Does to It},
  year         = {2026},
  howpublished = {Preprint},
  url          = {https://github.com/IskakovDamir/coelacanth-benchmark}
}

@misc{iskakov2026coelacanthdata,
  author       = {Damir Iskakov},
  title        = {Coelacanth v0: a benchmark of cross-field rediscoveries for evaluating novelty and precedent checkers (encrypted data archive)},
  year         = {2026},
  publisher    = {OSF},
  doi          = {10.17605/OSF.IO/672RJ},
  url          = {https://doi.org/10.17605/OSF.IO/672RJ}
}
```

Contact: Damir Iskakov, iskakov.dam@gmail.com.
