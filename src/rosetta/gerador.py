"""Geração do SQL Databricks a partir da ViewCds parseada.

O SQL sai com o `CREATE OR REPLACE VIEW` COMENTADO, de propósito: o projeto está
em fase de tradução, não de deploy. Quem for aplicar precisa descomentar
conscientemente — nada aqui executa DDL.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from .modelos import ViewCds
from .nomes import ResolvedorNomes, sanitizar
from .traducao import resolver_projection, traduzir

MAPA_JOIN_SQL = {
    "join": "INNER JOIN",
    "inner join": "INNER JOIN",
    "left join": "LEFT JOIN",
    "left outer join": "LEFT JOIN",
    "right join": "RIGHT JOIN",
    "right outer join": "RIGHT JOIN",
    "full join": "FULL OUTER JOIN",
    "full outer join": "FULL OUTER JOIN",
}


def _montar_corpo_select(bloco: dict, resolvedor: ResolvedorNomes, avisos: List[str]) -> str:
    """Monta o SELECT/FROM/JOIN/WHERE de um único bloco (branch de UNION ou a view
    inteira quando não há UNION). `avisos` é compartilhado entre blocos."""
    alias_base = bloco["alias_base"] or bloco["entidade_base"]
    tabela_base = resolvedor.fisico(bloco["entidade_base"])

    mapa = {}
    for c in bloco["campos"]:
        nome_saida = (c.alias or c.expressao.split(".")[-1]).lower()
        if re.fullmatch(r"[A-Za-z0-9_]+", c.expressao):
            mapa[nome_saida] = f"{alias_base}.{c.expressao}"
        elif re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+", c.expressao):
            mapa[nome_saida] = c.expressao

    texto_uso = " ".join(c.expressao for c in bloco["campos"]) + " " + (bloco["where"] or "")
    usadas = [a for a in bloco["associacoes"] if re.search(rf"\b{re.escape(a.alias)}\.", texto_uso)]
    nao_usadas = [a.alias for a in bloco["associacoes"] if a not in usadas]

    linhas_select = []
    total = len(bloco["campos"])
    for idx, c in enumerate(bloco["campos"]):
        expr = traduzir(c.expressao, avisos)
        if re.fullmatch(r"[A-Za-z0-9_]+", expr):
            expr = f"{alias_base}.{expr}"
        texto = f"    {expr} AS {c.alias}" if c.alias else f"    {expr}"
        if idx < total - 1:
            texto += ","
        if c.is_key:
            texto += "  -- key"
        linhas_select.append(texto)

    partes = ["SELECT", "\n".join(linhas_select), f"FROM {tabela_base} AS {alias_base}"]

    if bloco["estilo"] == "classico":
        # Joins de SQL puro (sem associations CDS) — a condição já referencia aliases
        # reais (ex.: a.rbukrs = v.bukrs), só passa por traduzir() (casts abap.*, etc.).
        for j in bloco["joins"]:
            tipo_sql = MAPA_JOIN_SQL.get(re.sub(r"\s+", " ", j.tipo.lower()).strip(), "INNER JOIN")
            cond = traduzir(j.condicao, avisos)
            partes.append(f"{tipo_sql} {resolvedor.fisico(j.tabela)} AS {j.alias}")
            partes.append(f"    ON {cond}")
    else:
        for a in usadas:
            cond = resolver_projection(a.condicao, mapa, alias_base, avisos)
            cond = traduzir(cond, avisos)
            partes.append(f"LEFT JOIN {resolvedor.fisico(a.alvo)} AS {a.alias}")
            partes.append(f"    ON {cond}")
            if a.cardinalidade and not a.cardinalidade.strip().endswith("1"):
                avisos.append(
                    f"Association {a.alias} tem cardinalidade [{a.cardinalidade}] — JOIN pode multiplicar linhas"
                )

    if bloco["where"]:
        partes.append(f"WHERE {traduzir(bloco['where'], avisos)}")

    if nao_usadas:
        avisos.append(f"Associations declaradas mas não usadas (ignoradas): {', '.join(nao_usadas)}")

    return "\n".join(partes)


def nome_view_destino(v: ViewCds, resolvedor: ResolvedorNomes) -> str:
    return f"{resolvedor.cfg.fqn_target}.{sanitizar(v.nome_entidade)}"


def gerar_sql(v: ViewCds, resolvedor: ResolvedorNomes) -> Tuple[str, List[str]]:
    """Retorna (sql, avisos). Avisos vazios = tradução direta, sem ressalvas."""
    avisos = list(v.avisos)

    if not v.parseou:
        return "-- ❌ Não foi possível gerar SQL (fonte truncado ou parse incompleto).", avisos

    blocos = (
        v.blocos_uniao
        if v.blocos_uniao
        else [
            {
                "estilo": v.estilo,
                "entidade_base": v.entidade_base,
                "alias_base": v.alias_base,
                "campos": v.campos,
                "associacoes": v.associacoes,
                "joins": v.joins,
                "where": v.where,
                "tipo_uniao": None,
            }
        ]
    )

    partes = [f"-- Gerado a partir de {v.ddlname}"]
    if v.label:
        partes.append(f"-- Label: {v.label}")
    if v.sql_view_name:
        partes.append(f"-- SQL View (HANA): {v.sql_view_name}")
    partes.append(f"-- Entidade CDS: {v.nome_entidade}  |  tipo: {v.tipo}")
    if any(b["estilo"] == "classico" for b in blocos):
        # Nota informativa, não aviso de risco: o marcador de key é só um comentário
        # decorativo, nunca afetou a corretude do SQL gerado.
        partes.append("-- Nota: estilo SQL clássico (SELECT ... FROM sem chaves {}) — chaves")
        partes.append("--       primárias não são marcadas automaticamente aqui.")
    if len(blocos) > 1:
        partes.append(f"-- View usa UNION — {len(blocos)} branches traduzidos.")
    partes.append("")
    partes.append(f"-- CREATE OR REPLACE VIEW {nome_view_destino(v, resolvedor)} AS")

    for idx, bloco in enumerate(blocos):
        if idx > 0:
            partes.append(bloco.get("tipo_uniao") or "UNION")
        partes.append(_montar_corpo_select(bloco, resolvedor, avisos))

    return "\n".join(partes), avisos
