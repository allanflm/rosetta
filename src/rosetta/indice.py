"""Índice `ddlname -> source` carregado em memória de uma vez só.

Motivo de existir: a versão original da árvore de dependências disparava um job
Spark por nó visitado, o que travava em views largas. Aqui a tabela inteira vem em
uma única consulta e a recursão roda 100% em memória.
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from .config import Config
from .seguranca import sql_leitura


def _coluna(colunas, *candidatas) -> str:
    mapa = {c.lower(): c for c in colunas}
    for c in candidatas:
        if c.lower() in mapa:
            return mapa[c.lower()]
    raise ValueError(f"Nenhuma de {candidatas} existe na tabela.")


def carregar_indice(spark, cfg: Config, verboso: bool = True) -> Dict[str, str]:
    """Retorna {DDLNAME_MAIUSCULO: source}. Em caso de ddlname duplicado, mantém a
    primeira linha na ordem de as4local (quando a coluna existe)."""
    t0 = time.time()
    colunas = spark.table(cfg.fqn_ddl).columns
    col_ddl = _coluna(colunas, "ddlname")
    col_src = _coluna(colunas, "source", "ddlsource")
    tem_as4local = any(c.lower() == "as4local" for c in colunas)

    ordem = f"UPPER(TRIM({col_ddl}))" + (", as4local ASC" if tem_as4local else "")
    sql = (
        f"SELECT UPPER(TRIM({col_ddl})) AS ddlname, {col_src} AS source "
        f"FROM {cfg.fqn_ddl} ORDER BY {ordem}"
    )

    indice: Dict[str, str] = {}
    for r in sql_leitura(spark, sql).collect():
        nome = r["ddlname"]
        if nome and nome not in indice:
            indice[nome] = (r["source"] or "").strip()

    if verboso:
        print(f"📇 Índice carregado: {len(indice):,} entradas em {time.time() - t0:.1f}s")
    return indice


def buscar_source(spark, cfg: Config, ddlname: str) -> Optional[str]:
    """Busca o fonte de UM ddlname sem carregar o índice inteiro — útil quando você
    só quer inspecionar uma view e não vai descer a árvore."""
    colunas = spark.table(cfg.fqn_ddl).columns
    col_ddl = _coluna(colunas, "ddlname")
    col_src = _coluna(colunas, "source", "ddlsource")
    alvo = (ddlname or "").strip().upper().replace("'", "''")
    sql = (
        f"SELECT {col_src} AS source FROM {cfg.fqn_ddl} "
        f"WHERE UPPER(TRIM({col_ddl})) = '{alvo}' LIMIT 1"
    )
    linhas = sql_leitura(spark, sql).collect()
    return (linhas[0]["source"] or "").strip() if linhas else None
