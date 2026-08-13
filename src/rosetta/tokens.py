"""Utilitários de tokenização do fonte ABAP CDS.

Todos operam sobre texto puro — nenhuma dependência de Spark ou Databricks, o que
torna esta camada testável fora do cluster (ver tests/).
"""
from __future__ import annotations

from typing import List

ABRE = {"(": ")", "[": "]", "{": "}"}
FECHA = {v: k for k, v in ABRE.items()}


def remover_comentarios(src: str) -> str:
    """Remove /* */ e // preservando strings literais.

    Também elimina o bloco /*+[internal] {...} */ — o compilador CDS anexa esse JSON
    de metadados ao final do fonte, e ele contém chaves que quebrariam a contagem
    estrutural se não fosse removido antes do parse do corpo real.
    """
    out, i, n, in_str = [], 0, len(src), False
    while i < n:
        c = src[i]
        if in_str:
            out.append(c)
            if c == "'":
                if i + 1 < n and src[i + 1] == "'":
                    out.append(src[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            i = n if j == -1 else j + 2
            out.append(" ")
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            j = src.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
            continue
        out.append(c)
        i += 1
    return "".join(out)


def fim_bloco(src: str, inicio: int) -> int:
    """Dado o índice de um abre-delimitador, retorna o índice do fecha correspondente.

    Levanta ValueError se o bloco não fechar — ESTE é o teste real de truncamento,
    não a antiga heurística de faixa de comprimento (16.200–16.294), que dava
    falso-negativo para cortes abaixo do teto (ex.: I_ADDRESS, len=16.117).
    """
    if src[inicio] not in ABRE:
        raise ValueError(f"posição {inicio} não é abre-delimitador: {src[inicio]!r}")
    pilha, i, n, in_str = [src[inicio]], inicio + 1, len(src), False
    while i < n:
        c = src[i]
        if in_str:
            if c == "'":
                if i + 1 < n and src[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            i += 1
            continue
        if c in ABRE:
            pilha.append(c)
        elif c in FECHA:
            if pilha and pilha[-1] == FECHA[c]:
                pilha.pop()
                if not pilha:
                    return i
            else:
                raise ValueError(f"delimitador desbalanceado em {i}")
        i += 1
    raise ValueError("bloco não fechado — DDL possivelmente truncado")


def _consumir_valor(src: str, i: int) -> int:
    n = len(src)
    if i >= n:
        return i
    if src[i] in "{[":
        return fim_bloco(src, i) + 1
    if src[i] == "'":
        i += 1
        while i < n:
            if src[i] == "'":
                if i + 1 < n and src[i + 1] == "'":
                    i += 2
                    continue
                return i + 1
            i += 1
        return n
    while i < n and src[i] not in " \t\r\n,@":
        i += 1
    return i


def remover_anotacoes(src: str) -> str:
    """Remove @Anotacao.chave: valor (inclusive valores em bloco [] e {})."""
    out, i, n = [], 0, len(src)
    while i < n:
        if src[i] == "@":
            i += 1
            while i < n and (src[i].isalnum() or src[i] in "._#"):
                i += 1
            j = i
            while j < n and src[j] in " \t\r\n":
                j += 1
            if j < n and src[j] == ":":
                j += 1
                while j < n and src[j] in " \t\r\n":
                    j += 1
                i = _consumir_valor(src, j)
            out.append(" ")
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


def split_topo(texto: str, sep: str = ",") -> List[str]:
    """Divide por `sep` apenas no nível 0 (fora de (), [], {} e de string literal)."""
    partes, atual, prof, i, n, in_str = [], [], 0, 0, len(texto), False
    while i < n:
        c = texto[i]
        if in_str:
            atual.append(c)
            if c == "'":
                if i + 1 < n and texto[i + 1] == "'":
                    atual.append(texto[i + 1])
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if c == "'":
            in_str = True
            atual.append(c)
            i += 1
            continue
        if c in ABRE:
            prof += 1
        elif c in FECHA:
            prof -= 1
        if c == sep and prof == 0:
            partes.append("".join(atual))
            atual = []
        else:
            atual.append(c)
        i += 1
    if "".join(atual).strip():
        partes.append("".join(atual))
    return [p.strip() for p in partes if p.strip()]
