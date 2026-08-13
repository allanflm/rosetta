"""Parser do fonte ABAP CDS.

Contrato central: `parse_cds` NUNCA levanta exceção. Qualquer falha vira
`ViewCds(truncado=True)` com o motivo em `avisos`. Isso existe porque a árvore de
dependências desce por centenas de nós — uma exceção não tratada aqui derrubaria a
célula inteira do notebook em vez de marcar um único nó como problemático.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .modelos import Associacao, Campo, JoinBruto, ViewCds
from .tokens import ABRE, FECHA, fim_bloco, remover_anotacoes, remover_comentarios, split_topo

RE_SQLVIEW = re.compile(r"@AbapCatalog\.sqlViewName\s*:\s*'([^']*)'", re.I)
RE_LABEL = re.compile(r"@EndUserText\.label\s*:\s*'([^']*)'", re.I)
RE_DEFINE = re.compile(
    r"\bdefine\s+(?:(root)\s+)?(abstract\s+entity|view\s+entity|view|table\s+function|hierarchy)"
    r"\s+([A-Za-z0-9_/]+)",
    re.I,
)
# 'as' opcional: o primeiro branch de uma view tem 'as select from ...', os branches
# seguintes de um UNION vêm sem o 'as' (só 'select from ...').
RE_FROM = re.compile(
    r"\b(?:as\s+)?select\s+(?:distinct\s+)?from\s+([A-Za-z0-9_/]+)(?:\s+as\s+([A-Za-z0-9_]+))?", re.I
)
RE_ASSOC = re.compile(
    r"\bassociation\s*(?:\[([^\]]*)\])?\s*to\s+(?:parent\s+)?([A-Za-z0-9_/]+)\s+as\s+([A-Za-z0-9_]+)\s+on\s+",
    re.I,
)
# Estilo clássico: "as select <col1, col2, ...> from tabela [as alias] [join ...]"
# — herdado de views de banco clássico (pool/cluster), sem a sintaxe de chaves {}.
RE_AS_SELECT = re.compile(r"\b(?:as\s+)?select\b(?:\s+distinct\b)?", re.I)
RE_JOIN = re.compile(
    r"\b(inner\s+join|left\s+outer\s+join|left\s+join|right\s+outer\s+join|"
    r"right\s+join|full\s+outer\s+join|full\s+join|join)\s+"
    r"([A-Za-z0-9_/]+)(?:\s+as\s+([A-Za-z0-9_]+))?\s+on\s+",
    re.I,
)
RE_JOIN_KW = re.compile(
    r"\b(?:inner\s+join|left\s+outer\s+join|left\s+join|right\s+outer\s+join|"
    r"right\s+join|full\s+outer\s+join|full\s+join|join)\b",
    re.I,
)
RE_UNIAO_KW = re.compile(r"\bunion\s+all\b|\bunion\s+distinct\b|\bunion\b", re.I)


def _dividir_por_uniao(texto: str) -> List[Tuple[str, Optional[str]]]:
    """Divide o texto em blocos separados por UNION / UNION ALL / UNION DISTINCT no
    nível 0. Retorna lista de (bloco, tipo_uniao_que_o_antecede)."""
    partes, ini, tipo_atual = [], 0, None
    i, n, prof, in_str = 0, len(texto), 0, False
    while i < n:
        c = texto[i]
        if in_str:
            if c == "'":
                if i + 1 < n and texto[i + 1] == "'":
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
            prof += 1
            i += 1
            continue
        if c in FECHA:
            prof -= 1
            i += 1
            continue
        if prof == 0:
            m = RE_UNIAO_KW.match(texto, i)
            if m:
                partes.append((texto[ini:i].strip(), tipo_atual))
                tipo_atual = re.sub(r"\s+", " ", m.group(0)).strip().upper()
                i = m.end()
                ini = i
                continue
        i += 1
    partes.append((texto[ini:].strip(), tipo_atual))
    return [(b, t) for b, t in partes if b]


def _parse_campo(bruto: str) -> Campo:
    is_key = False
    texto = bruto.strip()
    if re.match(r"^\bkey\b", texto, re.I):
        is_key = True
        texto = re.sub(r"^\bkey\b\s*", "", texto, flags=re.I)
    m = re.search(r"\bas\s+([A-Za-z0-9_]+)\s*$", texto, re.I)
    if m:
        expr = texto[: m.start()].strip()
        alias = m.group(1)
    else:
        expr = texto
        alias = None
    return Campo(bruto=bruto, expressao=expr, alias=alias, is_key=is_key)


def _parsear_bloco_select(texto: str):
    """Parseia um único bloco 'select ...' — a view inteira (sem UNION) ou um branch.
    Tenta primeiro o estilo entidade (chaves {}), depois o clássico.
    Retorna (dados, avisos); dados=None quando o bloco não fecha / está truncado."""
    avisos: List[str] = []

    m = RE_FROM.search(texto)
    if m:
        entidade_base, alias_base = m.group(1), m.group(2)
        associacoes = []
        for ma in RE_ASSOC.finditer(texto):
            resto = texto[ma.end():]
            corte = len(resto)
            for pat in [r"\bassociation\b", r"\{"]:
                mm = re.search(pat, resto, re.I)
                if mm:
                    corte = min(corte, mm.start())
            associacoes.append(
                Associacao(
                    cardinalidade=(ma.group(1) or "").strip(),
                    alvo=ma.group(2).strip(),
                    alias=ma.group(3).strip(),
                    condicao=resto[:corte].strip(),
                )
            )
        pos_chave = texto.find("{")
        if pos_chave == -1:
            return None, ["Corpo {} não encontrado — DDL provavelmente truncado."]
        try:
            fim = fim_bloco(texto, pos_chave)
        except ValueError as e:
            return None, [f"Corpo {{}} não fecha: {e}"]
        corpo = texto[pos_chave + 1: fim]
        campos = [_parse_campo(b) for b in split_topo(corpo, ",")]
        cauda = texto[fim + 1:].strip()
        where = None
        if cauda:
            mw = re.search(r"\bwhere\b", cauda, re.I)
            if mw:
                resto = cauda[mw.end():]
                mfim = re.search(r"\b(group\s+by|having|union)\b", resto, re.I)
                where = (resto[: mfim.start()] if mfim else resto).strip()
        return {
            "estilo": "entidade",
            "entidade_base": entidade_base,
            "alias_base": alias_base,
            "campos": campos,
            "associacoes": associacoes,
            "joins": [],
            "where": where,
        }, avisos

    m_sel = RE_AS_SELECT.search(texto)
    if not m_sel:
        return None, ["Não encontrei 'select ...' (nem estilo entidade nem clássico) — DDL provavelmente truncado."]
    resto = texto[m_sel.end():]
    m_from = re.search(r"\bfrom\b", resto, re.I)
    if not m_from:
        return None, ["Encontrei 'select' mas nenhum 'from' correspondente — DDL provavelmente truncado."]
    lista_campos = resto[: m_from.start()].strip()
    depois_from = resto[m_from.end():]
    m_tab = re.match(r"\s*([A-Za-z0-9_/]+)(?:\s+as\s+([A-Za-z0-9_]+))?", depois_from, re.I)
    if not m_tab:
        return None, ["Não consegui identificar a tabela após 'from' (estilo clássico) — DDL provavelmente truncado."]

    entidade_base = m_tab.group(1)
    alias_base = m_tab.group(2) or m_tab.group(1)
    campos = [_parse_campo(b) for b in split_topo(lista_campos, ",")]

    cursor = depois_from[m_tab.end():]
    joins = []
    pos = 0
    while True:
        mj = RE_JOIN.search(cursor, pos)
        if not mj:
            break
        ini_cond = mj.end()
        seguinte = cursor[ini_cond:]
        corte = len(seguinte)
        mm = RE_JOIN_KW.search(seguinte)
        if mm:
            corte = min(corte, mm.start())
        mm2 = re.search(r"\bwhere\b", seguinte, re.I)
        if mm2:
            corte = min(corte, mm2.start())
        joins.append(
            JoinBruto(
                tipo=re.sub(r"\s+", " ", mj.group(1)).strip(),
                tabela=mj.group(2).strip(),
                alias=(mj.group(3) or mj.group(2)).strip(),
                condicao=seguinte[:corte].strip(),
            )
        )
        pos = ini_cond + corte

    where = None
    m_where = re.search(r"\bwhere\b", cursor, re.I)
    if m_where:
        depois_where = cursor[m_where.end():]
        mfim = re.search(r"\b(group\s+by|having|union)\b", depois_where, re.I)
        condicao_where = (depois_where[: mfim.start()] if mfim else depois_where).strip()
        where = condicao_where.rstrip(";").strip()

    return {
        "estilo": "classico",
        "entidade_base": entidade_base,
        "alias_base": alias_base,
        "campos": campos,
        "associacoes": [],
        "joins": joins,
        "where": where,
    }, avisos


def parse_cds(ddlname: str, source: str) -> ViewCds:
    """Ponto de entrada público. Nunca levanta exceção."""
    try:
        return _parse_cds_interno(ddlname, source)
    except Exception as e:  # noqa: BLE001 — captura ampla é o contrato desta função
        v = ViewCds(ddlname=ddlname)
        v.truncado = True
        v.avisos.append(f"Falha ao parsear (provável truncamento): {type(e).__name__}: {e}")
        return v


def _parse_cds_interno(ddlname: str, source: str) -> ViewCds:
    v = ViewCds(ddlname=ddlname)

    sem_com = remover_comentarios(source)

    m = RE_SQLVIEW.search(sem_com)
    if m:
        v.sql_view_name = m.group(1).strip()
    m = RE_LABEL.search(sem_com)
    if m:
        v.label = m.group(1).strip()

    limpo = remover_anotacoes(sem_com)
    limpo = re.sub(r"\s+", " ", limpo).strip()

    m = RE_DEFINE.search(limpo)
    if not m:
        v.avisos.append("Não encontrei 'define view ...' — DDL fora do padrão ou truncado.")
        v.truncado = True
        return v
    v.tipo = re.sub(r"\s+", " ", m.group(2).lower())
    if m.group(1):
        v.tipo = "root " + v.tipo
    v.nome_entidade = m.group(3)

    if "table function" in v.tipo or "hierarchy" in v.tipo:
        v.avisos.append(f"Tipo '{v.tipo}' não suportado por este gerador (sem lógica AMDP/hierarquia).")
        return v

    if re.search(r"\bwith\s+parameters\b", limpo, re.I):
        v.avisos.append("View usa 'with parameters' — parâmetros não traduzidos.")

    # UNION: qualquer branch que falhar marca a view inteira como truncada — melhor
    # não gerar SQL do que gerar um UNION faltando pedaço, que passaria despercebido.
    blocos_texto = _dividir_por_uniao(limpo)
    blocos_dados = []
    for texto_bloco, tipo_uniao in blocos_texto:
        dados, avisos_bloco = _parsear_bloco_select(texto_bloco)
        if dados is None:
            v.avisos.extend(avisos_bloco)
            v.truncado = True
            return v
        dados["tipo_uniao"] = tipo_uniao
        blocos_dados.append(dados)
        v.avisos.extend(avisos_bloco)

    primeiro = blocos_dados[0]
    v.estilo = primeiro["estilo"]
    v.entidade_base = primeiro["entidade_base"]
    v.alias_base = primeiro["alias_base"]
    v.campos = primeiro["campos"]
    v.associacoes = primeiro["associacoes"]
    v.joins = primeiro["joins"]
    v.where = primeiro["where"]

    if len(blocos_dados) > 1:
        v.blocos_uniao = blocos_dados

    return v
