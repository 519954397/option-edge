from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple


@dataclass(frozen=True)
class AssetProfile:
    key: str
    name: str
    jin10_code: str
    terms: Tuple[str, ...]
    calendar_terms: Tuple[str, ...]


ASSETS: Dict[str, AssetProfile] = {
    "oil": AssetProfile(
        key="oil",
        name="原油",
        jin10_code="USOIL",
        terms=(
            "原油", "石油", "油价", "WTI", "布伦特", "OPEC", "欧佩克",
            "EIA", "API原油", "汽油", "柴油", "炼油", "油轮", "霍尔木兹",
        ),
        calendar_terms=(
            "原油库存", "汽油库存", "精炼油库存", "钻井", "美联储", "非农",
            "CPI", "PCE", "GDP", "PMI", "美元",
        ),
    ),
    "gold": AssetProfile(
        key="gold",
        name="黄金",
        jin10_code="XAUUSD",
        terms=(
            "黄金", "金价", "现货金", "XAU", "贵金属", "美联储", "美元",
            "美债", "实际利率", "通胀", "非农", "避险", "央行购金",
        ),
        calendar_terms=(
            "美联储", "非农", "失业率", "CPI", "PCE", "PPI", "GDP", "PMI",
            "零售销售", "初请失业金", "国债",
        ),
    ),
    "silver": AssetProfile(
        key="silver",
        name="白银",
        jin10_code="XAGUSD",
        terms=(
            "白银", "银价", "现货银", "XAG", "贵金属", "美联储", "美元",
            "美债", "通胀", "非农", "光伏", "工业需求", "铜价",
        ),
        calendar_terms=(
            "美联储", "非农", "失业率", "CPI", "PCE", "PPI", "GDP", "PMI",
            "工业产出", "制造业", "中国", "零售销售",
        ),
    ),
}


SYMBOL_HINTS = {
    "oil": ("USOIL", "UKOIL", "WTI", "BRENT", "CL1!", "XTIUSD", "XBRUSD"),
    "gold": ("XAUUSD", "GOLD", "GC1!", "MGC1!"),
    "silver": ("XAGUSD", "SILVER", "SI1!", "SIL1!"),
}


def resolve_asset(value: str) -> AssetProfile:
    normalized = (value or "").strip().upper()
    if normalized.lower() in ASSETS:
        return ASSETS[normalized.lower()]
    for key, hints in SYMBOL_HINTS.items():
        if any(hint in normalized for hint in hints):
            return ASSETS[key]
    raise ValueError("Unsupported asset or symbol: {}".format(value))


def contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)
