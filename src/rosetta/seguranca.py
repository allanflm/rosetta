"""Trava de somente-leitura.

A regra do projeto ("nada pode ser alterado no ambiente") deixa de ser uma
convenção que depende de disciplina e vira código: toda consulta ao Spark passa
por `sql_leitura`, que recusa qualquer statement que não seja de leitura.

Importante: a trava olha o *primeiro token* do statement, não procura palavras
proibidas no texto inteiro. Isso é proposital — as consultas legítimas do Motor 01
usam `REPLACE(...)` como função escalar, e uma busca ingênua por "replace" as
bloquearia. O que define um statement como perigoso é o verbo que o inicia.
"""
from __future__ import annotations

import re

INICIOS_PERMITIDOS = {"select", "with", "describe", "desc", "show", "explain"}

_RE_COMENT_BLOCO = re.compile(r"/\*.*?\*/", re.S)
_RE_COMENT_LINHA = re.compile(r"--[^\n]*")


class SqlNaoPermitido(RuntimeError):
    """Levantada quando alguém tenta executar algo que não é leitura pura."""


def _sem_comentarios(sql: str) -> str:
    limpo = _RE_COMENT_BLOCO.sub(" ", sql or "")
    limpo = _RE_COMENT_LINHA.sub(" ", limpo)
    return limpo.strip()


def _statements(sql: str) -> list[str]:
    """Divide por ';' ignorando o que estiver dentro de string literal, para que
    um `WHERE x = 'a;b'` não vire dois statements."""
    partes, atual, in_str = [], [], False
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if in_str:
            atual.append(c)
            if c == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    atual.append(sql[i + 1])
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
        if c == ";":
            partes.append("".join(atual))
            atual = []
            i += 1
            continue
        atual.append(c)
        i += 1
    partes.append("".join(atual))
    return [p for p in (x.strip() for x in partes) if p]


def assert_leitura(sql: str) -> None:
    """Valida que `sql` é um único statement de leitura. Levanta SqlNaoPermitido."""
    limpo = _sem_comentarios(sql)
    if not limpo:
        raise SqlNaoPermitido("SQL vazio.")

    partes = _statements(limpo)
    if len(partes) > 1:
        raise SqlNaoPermitido(
            f"Múltiplos statements em uma chamada ({len(partes)}) — não permitido."
        )

    m = re.match(r"\(*\s*([A-Za-z_]+)", partes[0])
    primeiro = m.group(1).lower() if m else ""
    if primeiro not in INICIOS_PERMITIDOS:
        raise SqlNaoPermitido(
            f"Statement iniciado por '{primeiro.upper() or '?'}' — o Rosetta é somente "
            f"leitura. Permitidos: {', '.join(sorted(INICIOS_PERMITIDOS)).upper()}."
        )


def sql_leitura(spark, sql: str):
    """Único ponto por onde o projeto fala com o Spark. Valida antes de executar."""
    assert_leitura(sql)
    return spark.sql(sql)
