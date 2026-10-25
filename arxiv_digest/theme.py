"""邮件主题配色。

模板里不写死任何颜色，只用语义 token（``{{ P.read }}``、``{{ P.muted }}``），
具体色值由这里按主题提供。这样做的好处：

* 深色/浅色两套主题共用一份模板，不会出现"改了一版忘了另一版"；
* 想换风格（比如更冷的蓝、更亮的青）只改这一张表，模板一行不动；
* 颜色意图写进名字（``read`` 是精读区的主色，不是"某个橙色"），
  以后再调不用猜这个色值当初是干什么的。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

# 深空主题：夜里的观测界面。底色近黑带蓝，信号色只是"发光点"。
DARK: dict[str, str] = {
    "page": "#05080f",
    "card": "#0f1a29",
    "card_edge": "#1d2f44",
    "glow": "0 0 0 1px rgba(34,211,238,0.06), 0 0 24px rgba(34,211,238,0.10)",
    "grid": "rgba(34,211,238,0.055)",
    "panel": "#101d2d",
    "panel2": "#14273a",
    "panel3": "#16324a",
    "panel4": "#10302a",
    "panel5": "#1f3b4d",
    "line": "#1d2f44",
    "line2": "#16253a",
    "line3": "#1b2b40",
    "header_bg": "#060b13",
    "header_line": "#22374f",
    "footer_bg": "#070d17",
    "text": "#e8f0f8",
    "body": "#cfe0f0",
    "body2": "#b3c6d8",
    "body3": "#a7bbcd",
    "muted": "#8ba1b6",
    "muted2": "#7e93a8",
    "muted3": "#6f8299",
    "muted4": "#8fa6ba",
    "muted5": "#94a9bd",
    "faint": "#8296ab",
    "faint2": "#7d90a6",
    "faint3": "#8092a6",
    "faint4": "#7286a0",
    "read": "#22d3ee",
    "skim": "#818cf8",
    "field": "#2dd4bf",
    "pulse": "#c084fc",
    "radar": "#38bdf8",
    "cal": "#fbbf24",
    "cal_soft": "#7fb3c8",
    "soft": "#7fe3d3",
    "ok": "#34d399",
    "warn": "#fb923c",
    "bad": "#fb7185",
    "tint_read": "#071620",
    "tint_skim": "#070f1c",
    "tint_field": "#061713",
    "tint_pulse": "#0d1726",
    "tint_pulse2": "#161029",
    "tint_pulse3": "#241a3d",
    "verdict_bg": "#2a1708",
    "chip_bg": "#16283c",
    "chip_new": "#0c2a22",
    "chain_bg": "#10263a",
    "src_aas": "#60a5fa",
    "src_eso": "#155e75",
    "note_bg": "#0a1a22",
    "note_text": "#c7d8e6",
}

# 暖纸主题：白天、打印、以及不认深色背景的客户端。
LIGHT: dict[str, str] = {
    "page": "#f3f0e9",
    "card": "#ffffff",
    "card_edge": "#e6e1d7",
    "glow": "0 1px 2px rgba(26,26,46,0.05), 0 10px 24px rgba(26,26,46,0.06)",
    "grid": "rgba(120,113,100,0.10)",
    "panel": "#f7f4ed",
    "panel2": "#f7f4ed",
    "panel3": "#d9e3ee",
    "panel4": "#d7e7dd",
    "panel5": "#ecdfc9",
    "line": "#e6e1d7",
    "line2": "#f4f1ea",
    "line3": "#d8d3c9",
    "header_bg": "#fbf8f2",
    "header_line": "#e6e1d7",
    "footer_bg": "#f2eee5",
    "text": "#1b2431",
    "body": "#29323c",
    "body2": "#46525e",
    "body3": "#4a5c6a",
    "muted": "#7b8794",
    "muted2": "#6f7885",
    "muted3": "#93a1ab",
    "muted4": "#55616d",
    "muted5": "#6f7885",
    "faint": "#9aa5b1",
    "faint2": "#a2aab4",
    "faint3": "#adb3c0",
    "faint4": "#bcc1cb",
    "read": "#b45309",
    "skim": "#2b5f8a",
    "field": "#2f6b52",
    "pulse": "#6b4c9a",
    "radar": "#3f6273",
    "cal": "#8a6a2f",
    "cal_soft": "#4a6b80",
    "soft": "#0f766e",
    "ok": "#157a42",
    "warn": "#b9770e",
    "bad": "#b3261e",
    "tint_read": "#fdfaf1",
    "tint_skim": "#f6f9fc",
    "tint_field": "#f5faf7",
    "tint_pulse": "#fbf9fd",
    "tint_pulse2": "#f0ecf7",
    "tint_pulse3": "#e9e3f3",
    "verdict_bg": "#fff8ec",
    "chip_bg": "#f0ece2",
    "chip_new": "#e9f5ee",
    "chain_bg": "#eef4fa",
    "src_aas": "#1f4e6b",
    "src_eso": "#2f6b52",
    "note_bg": "#fdf7ec",
    "note_text": "#6b5310",
}

DEFAULT_THEME = "dark"

_THEMES: dict[str, Mapping[str, str]] = {
    "dark": MappingProxyType(DARK),
    "light": MappingProxyType(LIGHT),
}


def available() -> tuple[str, ...]:
    """可用的主题名，按推荐顺序。"""
    return tuple(_THEMES)


def normalize(name: str | None) -> str:
    """把用户输入（大小写、空格、别名）收敛成合法主题名。"""
    cleaned = (name or "").strip().lower()
    aliases = {"深色": "dark", "暗色": "dark", "夜": "dark", "浅色": "light", "亮色": "light"}
    cleaned = aliases.get(cleaned, cleaned)
    return cleaned if cleaned in _THEMES else DEFAULT_THEME


def palette(name: str | None = None) -> Mapping[str, str]:
    """返回该主题的 token 表；不认识的名字回落到默认主题，绝不抛。"""
    return _THEMES[normalize(name)]
