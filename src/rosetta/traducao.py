"""Regras de tradução ABAP CDS → Databricks SQL.

REGRAS reescrevem a expressão. SINAIS não reescrevem nada — apenas registram um
aviso de que aquele trecho precisa de revisão humana. A ausência total de avisos
é o critério usado pelo inventário para dizer que uma view é "100% garantida".
"""
from __future__ import annotations

import re
from typing import Dict, List

REGRAS = [
    (
        re.compile(r"\bcast\s*\(\s*(.+?)\s+as\s+abap\.dec\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)\s*\)", re.I),
        r"CAST(\1 AS DECIMAL(\2,\3))",
        "cast abap.dec",
    ),
    (
        re.compile(r"\bcast\s*\(\s*(.+?)\s+as\s+abap\.char\s*\(\s*(\d+)\s*\)\s*\)", re.I),
        r"CAST(\1 AS STRING)",
        "cast abap.char",
    ),
    (
        re.compile(r"\bcast\s*\(\s*(.+?)\s+as\s+abap\.int\d\s*\)", re.I),
        r"CAST(\1 AS INT)",
        "cast abap.int",
    ),
    (re.compile(r"\s*&&\s*"), " || ", "concat &&"),
    (re.compile(r"\bpreserving\s+type\b", re.I), "", "preserving type"),
]

SINAIS = [
    (re.compile(r"\$session\.", re.I), "usa $session (contexto SAP não disponível)"),
    (re.compile(r"\bdats_\w+\s*\(", re.I), "usa função de data ABAP (dats_*)"),
    (re.compile(r"\btstmp_\w+\s*\(", re.I), "usa função de timestamp ABAP (tstmp_*)"),
    (re.compile(r"\btstmpl_\w+\s*\(", re.I), "usa função tstmpl_*"),
    (re.compile(r"\bcurrency_conversion\s*\(", re.I), "usa currency_conversion"),
    (re.compile(r"\bunit_conversion\s*\(", re.I), "usa unit_conversion"),
    (re.compile(r"\babap\.", re.I), "tipo abap.* não convertido"),
]


def traduzir(expr: str, avisos: List[str]) -> str:
    saida = expr
    for pat, repl, _ in REGRAS:
        saida = pat.sub(repl, saida)
    for pat, msg in SINAIS:
        if pat.search(saida):
            if msg not in avisos:
                avisos.append(f"{msg} — revisar manualmente")
    return re.sub(r"\s+", " ", saida).strip()


def resolver_projection(cond: str, mapa: Dict[str, str], alias_base: str, avisos: List[str]) -> str:
    """Troca $projection.campo pela origem real do campo na view."""

    def _sub(m):
        campo = m.group(1)
        origem = mapa.get(campo.lower())
        if origem:
            return origem
        avisos.append(f"$projection.{campo} sem origem simples — usando {alias_base}.{campo}")
        return f"{alias_base}.{campo}"

    return re.sub(r"\$projection\.([A-Za-z0-9_]+)", _sub, cond)
