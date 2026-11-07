"""Inference-only PARSeq (scene-text reader) and the jersey-legibility classifier, written here so
the downloaded checkpoints load as weights only: none of the source repository's code is run.

PARSeq: Bautista & Atienza, ECCV 2022 (strhub, Apache-2.0). This mirrors its encoder (a timm
ViT without class token), its one-layer two-stream decoder and its greedy autoregressive decode
with one refinement pass, enough to load `state_dict` keys unchanged.

Weights: Koshkina & Elder, "A General Framework for Jersey Number Recognition in Sports Video"
(CVPRW 2024), SoccerNet fine-tuned PARSeq and ResNet-34 legibility classifier, from
github.com/mkoshkina/jersey-number-pipeline (CC BY-NC 3.0: research use). Kept in
PitchIQ-data/models/jersey/.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

MODELS = Path(__file__).resolve().parents[3] / 'PitchIQ-data' / 'models' / 'jersey'


def _safe_load(path):
    """torch.load with weights_only=True. The Lightning checkpoint also pickles its LR scheduler
    (getattr(SWALR, '_cosine_anneal')): those two names are mapped to inert stand-ins."""

    class _Inert:
        pass

    def _none(*a, **k):
        return None

    with torch.serialization.safe_globals(
        [(_none, 'builtins.getattr'), (_none, 'getattr'), (_Inert, 'torch.optim.swa_utils.SWALR')]
    ):
        return torch.load(path, map_location='cpu', weights_only=True)


class _Block(nn.Module):
    """timm ViT block: pre-LN attention and MLP, LayerNorm eps 1e-6."""

    def __init__(self, d, heads, ratio):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.LayerNorm(d, eps=1e-6)
        self.attn = nn.Module()
        self.attn.qkv = nn.Linear(d, 3 * d)
        self.attn.proj = nn.Linear(d, d)
        self.norm2 = nn.LayerNorm(d, eps=1e-6)
        self.mlp = nn.Module()
        self.mlp.fc1 = nn.Linear(d, d * ratio)
        self.mlp.fc2 = nn.Linear(d * ratio, d)

    def forward(self, x):
        B, N, C = x.shape
        qkv = (
            self.attn.qkv(self.norm1(x))
            .reshape(B, N, 3, self.heads, C // self.heads)
            .permute(2, 0, 3, 1, 4)
        )
        a = F.scaled_dot_product_attention(qkv[0], qkv[1], qkv[2])
        x = x + self.attn.proj(a.transpose(1, 2).reshape(B, N, C))
        return x + self.mlp.fc2(F.gelu(self.mlp.fc1(self.norm2(x))))


class _Encoder(nn.Module):
    def __init__(self, img, patch, d, depth, heads, ratio):
        super().__init__()
        self.patch_embed = nn.Module()
        self.patch_embed.proj = nn.Conv2d(3, d, patch, patch)
        n = (img[0] // patch[0]) * (img[1] // patch[1])
        self.pos_embed = nn.Parameter(torch.zeros(1, n, d))
        self.blocks = nn.ModuleList([_Block(d, heads, ratio) for _ in range(depth)])
        self.norm = nn.LayerNorm(d, eps=1e-6)

    def forward(self, x):
        x = self.patch_embed.proj(x).flatten(2).transpose(1, 2) + self.pos_embed
        for b in self.blocks:
            x = b(x)
        return self.norm(x)


class _DecoderLayer(nn.Module):
    """Two-stream (query / content) pre-LN decoder layer."""

    def __init__(self, d, heads, ff):
        super().__init__()
        self.self_attn = nn.MultiheadAttention(d, heads, batch_first=True)
        self.cross_attn = nn.MultiheadAttention(d, heads, batch_first=True)
        self.linear1, self.linear2 = nn.Linear(d, ff), nn.Linear(ff, d)
        self.norm1, self.norm2 = nn.LayerNorm(d), nn.LayerNorm(d)
        self.norm_q, self.norm_c = nn.LayerNorm(d), nn.LayerNorm(d)

    def _stream(self, tgt, tgt_norm, kv, memory, mask, pad):
        tgt = (
            tgt
            + self.self_attn(
                tgt_norm, kv, kv, attn_mask=mask, key_padding_mask=pad, need_weights=False
            )[0]
        )
        tgt = tgt + self.cross_attn(self.norm1(tgt), memory, memory, need_weights=False)[0]
        return tgt + self.linear2(F.gelu(self.linear1(self.norm2(tgt))))

    def forward(self, query, content, memory, query_mask, pad):
        # one layer = the last layer: the content stream is not updated
        return self._stream(
            query, self.norm_q(query), self.norm_c(content), memory, query_mask, pad
        )


class PARSeq(nn.Module):
    def __init__(self, hp):
        super().__init__()
        d = hp['embed_dim']
        self.charset = hp['charset_train']
        self.n_out = len(self.charset) + 1  # EOS first; BOS, PAD last (not predicted)
        self.eos, self.bos, self.pad = 0, self.n_out, self.n_out + 1
        self.max_len = hp['max_label_length']
        self.img_size = tuple(hp['img_size'])
        self.encoder = _Encoder(
            self.img_size,
            tuple(hp['patch_size']),
            d,
            hp['enc_depth'],
            hp['enc_num_heads'],
            hp['enc_mlp_ratio'],
        )
        assert hp['dec_depth'] == 1
        self.decoder = nn.Module()
        self.decoder.layers = nn.ModuleList(
            [_DecoderLayer(d, hp['dec_num_heads'], d * hp['dec_mlp_ratio'])]
        )
        self.decoder.norm = nn.LayerNorm(d)
        self.head = nn.Linear(d, self.n_out)
        self.text_embed = nn.Module()
        self.text_embed.embedding = nn.Embedding(self.n_out + 2, d)
        self.pos_queries = nn.Parameter(torch.zeros(1, self.max_len + 1, d))
        self.d = d

    def _emb(self, t):
        return math.sqrt(self.d) * self.text_embed.embedding(t)

    def _decode(self, tgt, memory, query, query_mask, pad=None):
        L = tgt.shape[1]
        content = torch.cat(
            [self._emb(tgt[:, :1]), self.pos_queries[:, : L - 1] + self._emb(tgt[:, 1:])], 1
        )
        q = self.decoder.layers[0](query, content, memory, query_mask, pad)
        return self.decoder.norm(q)

    @torch.no_grad()
    def forward(self, images, steps=4):
        """Logits (B, steps, n_out); `steps` positions is enough for 2 digits and EOS."""
        B, dev = images.shape[0], images.device
        memory = self.encoder(images)
        pos = self.pos_queries[:, :steps].expand(B, -1, -1)
        mask = torch.triu(torch.full((steps, steps), float('-inf'), device=dev), 1)
        tgt = torch.full((B, steps), self.pad, dtype=torch.long, device=dev)
        tgt[:, 0] = self.bos
        logits = []
        for i in range(steps):
            j = i + 1
            p = self.head(self._decode(tgt[:, :j], memory, pos[:, i:j], mask[i:j, :j]))
            logits.append(p)
            if j < steps:
                tgt[:, j] = p[:, 0].argmax(-1)
        logits = torch.cat(logits, 1)
        # one refinement pass with a cloze mask (context to the right is visible)
        qmask = mask.clone()
        qmask[torch.triu(torch.ones(steps, steps, dtype=torch.bool, device=dev), 2)] = 0
        tgt = torch.cat(
            [torch.full((B, 1), self.bos, dtype=torch.long, device=dev), logits[:, :-1].argmax(-1)],
            1,
        )
        pad = torch.zeros(tgt.shape, device=dev).masked_fill(
            (tgt == self.eos).int().cumsum(-1) > 0, float('-inf')
        )
        return self.head(self._decode(tgt, memory, pos, qmask, pad))


def load_parseq(path=MODELS / 'parseq_soccer.ckpt'):
    c = _safe_load(path)
    m = PARSeq(c['hyper_parameters'])
    m.load_state_dict(c['state_dict'], strict=True)
    return m.eval()


def load_legibility(path=MODELS / 'legibility_soccer.pth'):
    from torchvision.models import resnet34

    m = resnet34()
    m.fc = nn.Linear(m.fc.in_features, 1)
    sd = _safe_load(path)
    m.load_state_dict({k.removeprefix('model_ft.'): v for k, v in sd.items()}, strict=True)
    return m.eval()


class Reader:
    """Legibility and digits for a batch of RGB uint8 crops (H, W, 3)."""

    def __init__(self, device='cuda'):
        self.dev = device
        self.str = load_parseq().to(device)
        self.leg = load_legibility().to(device)
        self.mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
        self.std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)

    def _batch(self, crops, size, bicubic):
        t = [
            torch.from_numpy(np.ascontiguousarray(c)).permute(2, 0, 1)[None].float() / 255
            for c in crops
        ]
        mode = 'bicubic' if bicubic else 'bilinear'
        x = torch.cat(
            [
                F.interpolate(
                    a.to(self.dev), size=size, mode=mode, align_corners=False, antialias=True
                )
                for a in t
            ]
        )
        return x.clamp(0, 1)

    @torch.no_grad()
    def legibility(self, crops):
        x = self._batch(crops, (256, 256), False)
        return torch.sigmoid(self.leg((x - self.mean) / self.std))[:, 0].cpu().numpy()

    @torch.no_grad()
    def digits(self, crops):
        """Per crop: the string read (digits only, up to 2), the probability of each character
        and of the end token, and the full digit distributions of the first 3 positions."""
        x = self._batch(crops, self.str.img_size, True)
        lg = self.str((x - 0.5) / 0.5)
        p = lg[:, :3, :11].softmax(-1).cpu().numpy()  # EOS + '0'..'9' (charset starts with digits)
        out = []
        for row in p:
            s, probs = '', []
            for k in range(3):
                t = int(row[k].argmax())
                probs.append(float(row[k, t]))
                if t == 0:
                    break
                s += str(t - 1)
            out.append((s, probs, row))
        return out
