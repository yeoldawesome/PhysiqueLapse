"""CLIP's byte-pair tokenizer without torch: a port of open_clip's SimpleTokenizer
(open_clip/tokenizer.py, MIT licence) that returns numpy arrays.

Used by the ONNX scorer so the desktop app can run CLIP without shipping PyTorch. The
vocabulary file (bpe_simple_vocab_16e6.txt.gz) is copied next to the ONNX models by
scripts/export_clip_onnx.py. tests/test_clip_onnx.py checks it matches open_clip token for token.
"""

from __future__ import annotations

import gzip
import html
from functools import lru_cache
from pathlib import Path

import ftfy
import numpy as np
import regex as re

SOT = "<start_of_text>"
EOT = "<end_of_text>"


@lru_cache
def _bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs], strict=True))


def _pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    return {(a, b) for a, b in zip(word, word[1:], strict=False)}


def _clean(text: str) -> str:
    text = html.unescape(html.unescape(ftfy.fix_text(text))).strip()
    return " ".join(text.split()).strip().lower()


class ClipTokenizer:
    def __init__(self, bpe_path: str | Path, context_length: int = 77) -> None:
        self.context_length = context_length
        self.byte_encoder = _bytes_to_unicode()
        merges = gzip.open(bpe_path).read().decode("utf-8").split("\n")
        merges = [tuple(m.split()) for m in merges[1 : 49152 - 256 - 2 + 1]]
        vocab = list(self.byte_encoder.values())
        vocab = vocab + [v + "</w>" for v in vocab]
        vocab += ["".join(m) for m in merges]
        vocab += [SOT, EOT]
        self.encoder = {v: i for i, v in enumerate(vocab)}
        self.bpe_ranks = {m: i for i, m in enumerate(merges)}
        self.cache = {SOT: SOT, EOT: EOT}
        self.pat = re.compile(
            rf"{SOT}|{EOT}|'s|'t|'re|'ve|'m|'ll|'d|[\p{{L}}]+|[\p{{N}}]|[^\s\p{{L}}\p{{N}}]+", re.IGNORECASE
        )
        self.sot_id = self.encoder[SOT]
        self.eot_id = self.encoder[EOT]

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new.extend(word[i:])
                    break
                new.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new.append(first + second)
                    i += 2
                else:
                    new.append(word[i])
                    i += 1
            word = tuple(new)
            if len(word) == 1:
                break
            pairs = _pairs(word)
        out = " ".join(word)
        self.cache[token] = out
        return out

    def encode(self, text: str) -> list[int]:
        ids: list[int] = []
        for token in re.findall(self.pat, _clean(text)):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            ids.extend(self.encoder[t] for t in self._bpe(token).split(" "))
        return ids

    def __call__(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.context_length), dtype=np.int64)
        for i, text in enumerate(texts):
            tokens = [self.sot_id, *self.encode(text), self.eot_id]
            if len(tokens) > self.context_length:
                tokens = tokens[: self.context_length]
                tokens[-1] = self.eot_id
            out[i, : len(tokens)] = tokens
        return out
