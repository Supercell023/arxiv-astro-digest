"""把 LaTeX 数学片段转成 Unicode 文本，供邮件正文直接显示。

邮件客户端不执行 JavaScript，MathJax / KaTeX 都用不了；把公式渲染成图片又要
外链或 CID 附件，一是不稳、二是把信变重。折中方案是**能转的就转成 Unicode**：

    $r_d$            → r_d（下标用 Unicode）
    $H_0 = 67.4 \\pm 0.5$ → H₀ = 67.4 ± 0.5
    $10^{-3}$        → 10⁻³
    $\\Lambda$CDM     → ΛCDM

转换是"尽力而为"的：认不出的命令去掉反斜杠保留名字，绝不抛异常——
这封信里最不该因为一个公式挂掉。
"""

from __future__ import annotations

import re

# 希腊字母（大小写）
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ",
    "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# 符号与运算符
_SYMBOLS = {
    "times": "×", "pm": "±", "mp": "∓", "approx": "≈", "sim": "∼", "simeq": "≃",
    "lesssim": "≲", "gtrsim": "≳", "propto": "∝", "rightarrow": "→", "to": "→",
    "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒", "cdot": "·",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "equiv": "≡",
    "ll": "≪", "gg": "≫", "circ": "°", "infty": "∞", "partial": "∂", "nabla": "∇",
    "sum": "∑", "int": "∫", "sqrt": "√", "langle": "⟨", "rangle": "⟩",
    "left": "", "right": "", "quad": " ", "qquad": " ", "degree": "°",
    "odot": "⊙", "oplus": "⊕", "prime": "′", "ldots": "…", "dots": "…",
    "dagger": "†", "star": "⋆", "ast": "∗", "perp": "⊥", "parallel": "∥",
    "ell": "ℓ", "hbar": "ℏ", "colon": ":", "mid": "|",
}

_SUB_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌", "(": "₍",
    ")": "₎", "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ", "s": "ₛ",
    "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}
_SUP_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽",
    ")": "⁾", "n": "ⁿ", "i": "ⁱ", "s": "ˢ", "t": "ᵗ",
}

_KNOWN = sorted({*_GREEK, *_SYMBOLS}, key=len, reverse=True)
_CMD_RE = re.compile(r"\\(" + "|".join(re.escape(name) for name in _KNOWN) + r"|[A-Za-z]+)")
_WRAPPER_RE = re.compile(
    r"\\(?:text|mathrm|mathit|mathbf|mathsf|mathtt|operatorname)\s*\{([^{}]*)\}"
)
_GROUP_STYLE_RE = re.compile(r"\{\s*\\(?:rm|it|bf)\s+([^{}]*)\}")
_SUB_WRAP_RE = re.compile(r"_\s*\\(?:mathrm|text|rm|it|bf|mathbf)\s*\{([^{}]*)\}")
_SUP_WRAP_RE = re.compile(r"\^\s*\\(?:mathrm|text|rm|it|bf|mathbf)\s*\{([^{}]*)\}")
_FRAC_RE = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SUB_RE = re.compile(r"_\s*\{([^{}]*)\}|_([A-Za-z0-9+\-=])")
_SUP_RE = re.compile(r"\^\s*\{([^{}]*)\}|\^([A-Za-z0-9+\-=])")
_DOLLAR_RE = re.compile(r"\${1,2}(.+?)\${1,2}", re.DOTALL)
_WS_RE = re.compile(r"[ \t]+")


def _script(body: str, mapping: dict[str, str], marker: str) -> str:
    """全部字符都能转才转；只要有一个转不了，就保留 `_xxx` 这种可读写法。"""

    if body and all(ch in mapping for ch in body):
        return "".join(mapping[ch] for ch in body)
    return f"{marker}{body}"


def _sub(match: re.Match[str]) -> str:
    body = match.group(1) if match.group(1) is not None else (match.group(2) or "")
    return _script(body, _SUB_MAP, "_")


def _sup(match: re.Match[str]) -> str:
    body = match.group(1) if match.group(1) is not None else (match.group(2) or "")
    return _script(body, _SUP_MAP, "^")


def _cmd(match: re.Match[str]) -> str:
    name = match.group(1)
    if name in _GREEK:
        return _GREEK[name]
    if name in _SYMBOLS:
        return _SYMBOLS[name]
    if name in {"rm", "it", "bf"}:
        return ""
    return name


def to_unicode(text: str) -> str:
    """把一段含 LaTeX 的文本转成接近排版的纯文本。认不出的部分原样保留。"""

    if not text:
        return text
    if "\\" not in text and "$" not in text:
        return text

    out = _DOLLAR_RE.sub(lambda m: m.group(1), text)

    # 下标/上标里的 \mathrm 等要先变成花括号组，否则去壳后 "_{200c}" 会散成 "_200c"
    out = _SUB_WRAP_RE.sub(lambda m: "_{" + m.group(1) + "}", out)
    out = _SUP_WRAP_RE.sub(lambda m: "^{" + m.group(1) + "}", out)

    for _ in range(3):
        new = _WRAPPER_RE.sub(lambda m: m.group(1), out)
        new = _GROUP_STYLE_RE.sub(lambda m: m.group(1), new)
        if new == out:
            break
        out = new

    def fraction(match: re.Match[str]) -> str:
        def group(value: str) -> str:
            return value if re.fullmatch(r"[A-Za-z0-9]+", value) else f"({value})"
        return f"{group(match.group(1))}/{group(match.group(2))}"

    out = _FRAC_RE.sub(fraction, out)
    out = re.sub(r"\\sqrt\s*\{([^{}]*)\}", lambda m: f"√({m.group(1)})", out)
    # Keep unsupported nested grouping instead of silently changing precedence.
    if re.search(r"\\(?:frac|sqrt)\b", out):
        return text
    out = _SUB_RE.sub(_sub, out)
    out = _SUP_RE.sub(_sup, out)
    out = _CMD_RE.sub(_cmd, out)

    out = out.replace("\\ ", " ").replace("\\,", " ").replace("\\;", " ")
    out = out.replace("\\!", "").replace("\\%", "%").replace("\\&", "&")
    out = out.replace("\\_", "_").replace("\\#", "#")
    out = out.replace("\\{", "{").replace("\\}", "}")
    out = out.replace("{", "(").replace("}", ")").replace("~", " ")
    out = _WS_RE.sub(" ", out)
    return out.strip()


def has_latex(text: str) -> bool:
    return bool(text) and ("\\" in text or "$" in text)
