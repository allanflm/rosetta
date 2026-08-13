"""Motor 01 — inventário: classifica todos os fontes da DDDDLSRC.

Três eixos: completude (o texto não foi cortado pelo transporte RFC), tipo (vira
CREATE VIEW no Databricks ou não) e fecho de dependências (nenhuma origem, direta
ou indireta, está truncada).

Isto é uma PRÉ-SELEÇÃO estrutural — não roda o parser de verdade. Para saber quais
views realmente geram SQL limpo, use `inventario.certeza_real()`, que executa
parse + geração em lote.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Set

import pandas as pd

from .config import Config
from .seguranca import sql_leitura

# ---------------------------------------------------------------- classificação

KW_SUFIXO = {"where", "group", "having", "union", "except", "intersect", "order", "with"}
TOK_INCOMPLETO = {
    "and", "or", "not", "where", "by", "group", "having", "in", "like", "between",
    "case", "when", "then", "else", "union", "all", "select", "from", "as", "on",
    "key", "is", "left", "right", "inner", "outer", "join", "with", "set", "cast",
    "distinct", "association", "to", "of", "parameters", "preserving", "type",
}
CHAR_INCOMPLETO = set("=<>+-*/,.(&|:")
TRACO_E_COMENTARIO = True

COMPLETAS = {
    "COMPLETA_FECHA_CHAVE", "COMPLETA_COM_SUFIXO", "COMPLETA_SEM_CHAVES",
    "COMPLETA_TABLE_FUNCTION",
}
TRUNCADAS = {
    "TRUNCADO_CHAVE_ABERTA", "TRUNCADA_NO_SUFIXO", "TRUNCADO_SEM_CHAVES",
    "TRUNCADO_LITERAL_ABERTO", "TRUNCADO_COMENTARIO_ABERTO",
}
TRADUZIVEL = {
    "define view": "SIM",
    "define view entity": "SIM",
    "define root view": "SIM",
    "define root view entity": "SIM",
    "define transient view": "SIM",
    "define table function": "NAO_AMDP",  # corpo é AMDP/SQLScript, nunca vem na DDDDLSRC
    "define abstract entity": "NAO_ESTRUTURA",
    "define custom entity": "NAO_ESTRUTURA",
    "define hierarchy": "NAO_HIERARQUIA",
    "extend view": "MERGE",
    "extend view entity": "MERGE",
    "annotate view": "NAO_ANOTACAO",
    "annotate entity": "NAO_ANOTACAO",
    "outro": "REVISAR",
}

RX_ENT = re.compile(
    r"(?is)\b(?:define|extend|annotate)\s+(?:root\s+)?(?:transient\s+)?"
    r"(?:abstract\s+|custom\s+)?(?:view|entity|table\s+function|hierarchy)\s+"
    r"(?:entity\s+)?([A-Za-z0-9_/]+)"
)
RX_AMDP = re.compile(r"(?is)implemented\s+by\s+method\s+([A-Za-z0-9_/]+)\s*=>\s*([A-Za-z0-9_]+)")


def limpar_snippet(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"/\*.*$", " ", s, flags=re.S)
    s = re.sub(r"//[^\n]*", " ", s)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def _primeira_palavra(s: str) -> str:
    m = re.match(r"^\s*([A-Za-z_]+)", s or "")
    return m.group(1).lower() if m else ""


def _ultimo_token(s: str) -> str:
    m = re.search(r"([A-Za-z0-9_]+)\s*$", s or "")
    return m.group(1).lower() if m else ""


def termina_incompleto(cauda: str) -> bool:
    s = limpar_snippet(cauda).rstrip()
    if not s:
        return True
    if s.endswith(";") or s.endswith("}"):
        return False
    if s[-1] in CHAR_INCOMPLETO:
        return True
    if _ultimo_token(s) in TOK_INCOMPLETO:
        return True
    if s.count("(") > s.count(")"):
        return True
    return False


def classificar_sufixo(sufixo: str, cauda: str) -> str:
    suf = limpar_snippet(sufixo).strip()
    if suf in ("", ";"):
        return "COMPLETA_FECHA_CHAVE"
    if "implemented by" in suf.lower():
        return "COMPLETA_TABLE_FUNCTION"
    if _primeira_palavra(suf) in KW_SUFIXO:
        return "TRUNCADA_NO_SUFIXO" if termina_incompleto(cauda) else "COMPLETA_COM_SUFIXO"
    return "SUFIXO_NAO_RECONHECIDO"


def classificar_linha(r) -> str:
    if r["n_abre"] == 0 and r["n_fecha"] == 0:
        if r["tem_select"] == 1 and not termina_incompleto(r["cauda"]):
            return "COMPLETA_SEM_CHAVES"
        return "TRUNCADO_SEM_CHAVES"
    saldo = r["n_abre"] - r["n_fecha"]
    if saldo > 0:
        return "TRUNCADO_CHAVE_ABERTA"
    if saldo < 0:
        return "CHAVES_NEGATIVAS"
    return classificar_sufixo(r["sufixo_ini"], r["cauda"])


def varrer(src: str):
    """Varredura caractere a caractere com máquina de estados: N=normal, S=string,
    L=comentário de linha, B=comentário de bloco. Usada só nos casos de fronteira."""
    out, i, n, est = [], 0, len(src), "N"
    ini_aberto = -1
    while i < n:
        c = src[i]
        nx = src[i + 1] if i + 1 < n else ""
        if est == "N":
            if c == "'":
                est = "S"
                ini_aberto = i
            elif c == "/" and nx == "/":
                est = "L"
                ini_aberto = i
                i += 1
            elif c == "-" and nx == "-" and TRACO_E_COMENTARIO:
                est = "L"
                ini_aberto = i
                i += 1
            elif c == "/" and nx == "*":
                est = "B"
                ini_aberto = i
                i += 1
            else:
                out.append(c)
        elif est == "S":
            if c == "'":
                if nx == "'":
                    i += 1
                else:
                    est = "N"
                    ini_aberto = -1
                    out.append("'@'")
        elif est == "L":
            if c == "\n":
                est = "N"
                ini_aberto = -1
                out.append("\n")
        elif est == "B":
            if c == "*" and nx == "/":
                est = "N"
                ini_aberto = -1
                i += 1
        i += 1
    trecho = src[ini_aberto: ini_aberto + 20] if ini_aberto >= 0 else ""
    return "".join(out), est, trecho


def classificar_exato(limpo: str, estado: str, trecho_aberto: str) -> str:
    abre, fecha = limpo.count("{"), limpo.count("}")
    if estado == "B" and trecho_aberto.startswith("/*+"):
        estado = "N"
    if estado == "S":
        return "TRUNCADO_LITERAL_ABERTO"
    if estado == "B":
        return "TRUNCADO_COMENTARIO_ABERTO"
    if abre == 0 and fecha == 0:
        if "select" in limpo.lower() and not termina_incompleto(limpo[-300:]):
            return "COMPLETA_SEM_CHAVES"
        return "TRUNCADO_SEM_CHAVES"
    if abre > fecha:
        return "TRUNCADO_CHAVE_ABERTA"
    if abre < fecha:
        return "CHAVES_NEGATIVAS"
    p = limpo.rfind("}")
    return classificar_sufixo(limpo[p + 1: p + 401], limpo[-300:])


CLASSES_SUSPEITAS = {
    "CHAVES_NEGATIVAS", "SUFIXO_NAO_RECONHECIDO", "TRUNCADO_CHAVE_ABERTA",
    "TRUNCADA_NO_SUFIXO", "TRUNCADO_SEM_CHAVES",
}

# ------------------------------------------------------------------- resultado


@dataclass
class Inventario:
    pdf: pd.DataFrame
    pais: Dict[str, Set[str]] = field(default_factory=dict)
    filhos: Dict[str, Set[str]] = field(default_factory=dict)

    @property
    def aptas(self) -> Set[str]:
        return set(self.pdf.loc[self.pdf["apta"], "ddl_up"])

    @property
    def truncadas(self) -> Set[str]:
        return set(self.pdf.loc[self.pdf["status_fonte"].eq("TRUNCADO"), "ddl_up"])

    def resumo(self) -> str:
        linhas = ["=" * 60, "  🎯 INVENTÁRIO — MOTOR 01", "=" * 60]
        for m, n in self.pdf["motivo"].value_counts().items():
            linhas.append(f"   {n:>8,}  {m}")
        linhas.append("-" * 60)
        linhas.append(f"   ✅ APTAS (pré-seleção): {len(self.aptas):,} de {len(self.pdf):,}")
        linhas.append("=" * 60)
        return "\n".join(linhas)


# --------------------------------------------------------------------- etapas


def _colunas(spark, cfg: Config):
    colunas = spark.table(cfg.fqn_ddl).columns
    mapa = {c.lower(): c for c in colunas}
    col_ddl = mapa.get("ddlname")
    col_src = mapa.get("source") or mapa.get("ddlsource")
    if not col_ddl or not col_src:
        raise ValueError(f"Colunas ddlname/source não encontradas em {cfg.fqn_ddl}")
    return col_ddl, col_src


def carregar_metricas(spark, cfg: Config) -> pd.DataFrame:
    """Extrai, em uma consulta, as métricas estruturais de cada fonte."""
    col_ddl, col_src = _colunas(spark, cfg)
    marca = cfg_marca(cfg)
    sql = f"""
WITH b AS (
  SELECT TRIM({col_ddl}) AS ddlname, {col_src} AS source,
         LENGTH({col_src}) AS len_total, INSTR({col_src}, '{marca}') AS pos_meta
  FROM {cfg.fqn_ddl}
  WHERE {col_src} IS NOT NULL AND LENGTH({col_src}) > 0
),
s AS (
  SELECT ddlname, len_total, pos_meta,
         CASE WHEN pos_meta > 0 THEN SUBSTRING(source, 1, pos_meta - 1) ELSE source END AS corpo,
         RIGHT(source, 40) AS fim_total
  FROM b
),
c AS (
  SELECT ddlname, len_total, pos_meta, fim_total, corpo,
         LENGTH(corpo) AS len_corpo,
         LENGTH(corpo) - LENGTH(REPLACE(corpo, '{{', '')) AS n_abre,
         LENGTH(corpo) - LENGTH(REPLACE(corpo, '}}', '')) AS n_fecha,
         SUBSTRING(corpo, GREATEST(INSTR(LOWER(corpo), 'define '), 1), 220) AS decl,
         CASE
           WHEN LOWER(corpo) LIKE '%define root view entity%' THEN 'define root view entity'
           WHEN LOWER(corpo) LIKE '%define root view%'        THEN 'define root view'
           WHEN LOWER(corpo) LIKE '%define view entity%'      THEN 'define view entity'
           WHEN LOWER(corpo) LIKE '%define transient view%'   THEN 'define transient view'
           WHEN LOWER(corpo) LIKE '%define view%'             THEN 'define view'
           WHEN LOWER(corpo) LIKE '%define table function%'   THEN 'define table function'
           WHEN LOWER(corpo) LIKE '%define abstract entity%'  THEN 'define abstract entity'
           WHEN LOWER(corpo) LIKE '%define custom entity%'    THEN 'define custom entity'
           WHEN LOWER(corpo) LIKE '%define hierarchy%'        THEN 'define hierarchy'
           WHEN LOWER(corpo) LIKE '%extend view entity%'      THEN 'extend view entity'
           WHEN LOWER(corpo) LIKE '%extend view%'             THEN 'extend view'
           WHEN LOWER(corpo) LIKE '%annotate view%'           THEN 'annotate view'
           WHEN LOWER(corpo) LIKE '%annotate entity%'         THEN 'annotate entity'
           ELSE 'outro'
         END AS tipo_objeto,
         CASE WHEN LOWER(corpo) RLIKE 'with\\\\s+parameters' THEN 1 ELSE 0 END AS tem_parametros,
         CASE WHEN LOWER(corpo) LIKE '%select%' THEN 1 ELSE 0 END AS tem_select,
         CASE WHEN INSTR(REVERSE(corpo), '}}') = 0 THEN 0
              ELSE LENGTH(corpo) - INSTR(REVERSE(corpo), '}}') + 1 END AS pos_ult_fecha
  FROM s
)
SELECT ddlname, len_total, len_corpo, pos_meta, fim_total, decl, tipo_objeto, tem_select, tem_parametros,
       n_abre, n_fecha,
       CASE WHEN pos_ult_fecha = 0 THEN 0 ELSE len_corpo - pos_ult_fecha END AS len_sufixo,
       CASE WHEN pos_ult_fecha = 0 THEN ''
            ELSE SUBSTRING(corpo, pos_ult_fecha + 1, 400) END AS sufixo_ini,
       RIGHT(corpo, 300) AS cauda
FROM c
"""
    pdf = sql_leitura(spark, sql).toPandas()
    pdf["ddlname"] = pdf["ddlname"].str.strip()
    for c in ["sufixo_ini", "cauda", "fim_total", "decl"]:
        pdf[c] = pdf[c].fillna("")
    return pdf


def cfg_marca(cfg: Config) -> str:
    from .config import MARCA_META

    return MARCA_META


def verificar_fronteira(spark, cfg: Config, pdf: pd.DataFrame, tamanho_lote: int = 400) -> pd.DataFrame:
    """Reclassifica com varredura exata só os casos de fronteira — rodar isso em
    114 mil fontes seria desperdício, e a classificação rápida já resolve a maioria."""
    col_ddl, col_src = _colunas(spark, cfg)
    marca = cfg_marca(cfg)
    teto = int(pdf["len_total"].max())
    borda = teto - 400
    sel = pdf["classe"].isin(CLASSES_SUSPEITAS) | pdf["len_total"].ge(borda) | pdf["pos_meta"].eq(0)
    alvos = pdf.loc[sel, "ddlname"].tolist()
    print(f"🔬 Verificação exata em {len(alvos):,} fontes de fronteira...")

    regs = []
    for ini in range(0, len(alvos), tamanho_lote):
        lote = alvos[ini: ini + tamanho_lote]
        lista = ",".join("'" + x.replace("'", "''") + "'" for x in lote)
        q = (
            f"SELECT TRIM({col_ddl}) AS ddlname, {col_src} AS source "
            f"FROM {cfg.fqn_ddl} WHERE TRIM({col_ddl}) IN ({lista})"
        )
        for r in sql_leitura(spark, q).collect():
            src = r["source"] or ""
            p = src.find(marca)
            corpo = src[:p] if p >= 0 else src
            limpo, est, trecho = varrer(corpo)
            regs.append({"ddlname": r["ddlname"], "classe_exata": classificar_exato(limpo, est, trecho)})

    if not regs:
        pdf["classe_exata"] = None
        return pdf

    pdf_ex = pd.DataFrame(regs).drop_duplicates(subset=["ddlname"])
    pdf = pdf.merge(pdf_ex, on="ddlname", how="left")
    pdf["classe"] = pdf["classe_exata"].fillna(pdf["classe"])
    print(f"🔁 Reclassificados: {pdf['classe_exata'].notna().sum():,}")
    return pdf


def _extrair_entidade(decl: str, ddlname: str) -> str:
    m = RX_ENT.search(limpar_snippet(decl or ""))
    if m:
        nome = m.group(1).strip()
        if nome.lower() not in ("entity", "function", "view"):
            return nome
    return ddlname


def aplicar_status(pdf: pd.DataFrame) -> pd.DataFrame:
    pdf["status_fonte"] = pdf["classe"].map(
        lambda c: "COMPLETO" if c in COMPLETAS else ("TRUNCADO" if c in TRUNCADAS else "REVISAR")
    )
    pdf["traduzivel"] = pdf["tipo_objeto"].map(TRADUZIVEL).fillna("REVISAR")
    pdf["entidade"] = [_extrair_entidade(d, n) for d, n in zip(pdf["decl"], pdf["ddlname"])]
    pdf["ddl_up"] = pdf["ddlname"].str.upper()
    pdf["ent_up"] = pdf["entidade"].str.upper()

    def _amdp(sufixo):
        m = RX_AMDP.search(sufixo or "")
        return (m.group(1), m.group(2)) if m else (None, None)

    pdf[["amdp_classe", "amdp_metodo"]] = pd.DataFrame(
        [_amdp(s) for s in pdf["sufixo_ini"]], index=pdf.index
    )
    pdf["tem_parametros"] = pdf["tem_parametros"].fillna(0).astype(int).astype(bool)
    return pdf


def montar_grafo(spark, cfg: Config, pdf: pd.DataFrame):
    """Grafo CDS→CDS a partir do bloco /*+[internal] (JSON com FROM/ASSOCIATED/BASE).

    DDLDEPENDENCY entra só como dicionário de nomes: ela mapeia ddlname para os
    objetos que ele define, não é um grafo de dependências.
    """
    col_ddl, col_src = _colunas(spark, cfg)
    marca = cfg_marca(cfg)

    alias = {}
    for d, e in zip(pdf["ddl_up"], pdf["ent_up"]):
        alias[d] = d
        alias.setdefault(e, d)

    try:
        cd = {c.lower(): c for c in spark.table(cfg.fqn_depen).columns}
        co, ob, es = cd.get("ddlname"), cd.get("objectname"), cd.get("state")
        if co and ob:
            q = f"SELECT DISTINCT UPPER(TRIM({co})) AS fonte, UPPER(TRIM({ob})) AS objeto FROM {cfg.fqn_depen}"
            if es:
                q += f" WHERE TRIM({es}) = 'A'"
            objs = sql_leitura(spark, q).toPandas()
            conhecidos = set(pdf["ddl_up"])
            for f_, o_ in zip(objs["fonte"], objs["objeto"]):
                if f_ in conhecidos and o_ not in alias:
                    alias[o_] = f_
            print(f"📖 Dicionário de nomes via {cfg.fqn_depen}: {len(alias):,} aliases")
    except Exception as e:  # noqa: BLE001
        print(f"⚠️  {cfg.fqn_depen} indisponível como dicionário de nomes: {str(e)[:120]}")

    sql_dep = (
        r"""
WITH m AS (
  SELECT UPPER(TRIM(:COL_DDL:)) AS origem,
         CASE WHEN INSTR(:COL_SRC:, ':META:') > 0
              THEN SUBSTRING(:COL_SRC:, INSTR(:COL_SRC:, ':META:'))
              ELSE '' END AS meta
  FROM :FQN:
),
arr AS (
  SELECT origem,
         REGEXP_EXTRACT(meta, '"FROM"[^\\[]*\\[([^\\]]*)\\]', 1)       AS a_from,
         REGEXP_EXTRACT(meta, '"ASSOCIATED"[^\\[]*\\[([^\\]]*)\\]', 1) AS a_assoc,
         REGEXP_EXTRACT(meta, '"BASE"[^\\[]*\\[([^\\]]*)\\]', 1)       AS a_base
  FROM m
),
ex AS (
  SELECT origem, EXPLODE(REGEXP_EXTRACT_ALL(a_from, '"([^"]+)"', 1)) AS alvo FROM arr
  UNION ALL
  SELECT origem, EXPLODE(REGEXP_EXTRACT_ALL(a_assoc, '"([^"]+)"', 1)) FROM arr
  UNION ALL
  SELECT origem, EXPLODE(REGEXP_EXTRACT_ALL(a_base, '"([^"]+)"', 1)) FROM arr
)
SELECT origem, UPPER(TRIM(alvo)) AS alvo
FROM ex
WHERE alvo IS NOT NULL AND LENGTH(TRIM(alvo)) > 0
"""
        .replace(":COL_DDL:", col_ddl)
        .replace(":COL_SRC:", col_src)
        .replace(":FQN:", cfg.fqn_ddl)
        .replace(":META:", marca)
    )

    arestas = sql_leitura(spark, sql_dep).distinct().toPandas()
    arestas["alvo_cds"] = arestas["alvo"].map(alias)
    print(f"🔗 Arestas extraídas: {len(arestas):,}")

    e_cds = arestas[arestas["alvo_cds"].notna()]
    pais: Dict[str, Set[str]] = {}
    filhos: Dict[str, Set[str]] = {}
    for o, a in zip(e_cds["origem"], e_cds["alvo_cds"]):
        if o == a:
            continue
        pais.setdefault(o, set()).add(a)
        filhos.setdefault(a, set()).add(o)
    return pais, filhos


def propagar_contaminacao(pdf: pd.DataFrame, pais, filhos) -> pd.DataFrame:
    """BFS a partir dos fontes não-completos: quem herda deles fica contaminado."""
    quebradas = set(pdf.loc[pdf["status_fonte"].ne("COMPLETO"), "ddl_up"])
    contaminados, fila = set(), list(quebradas)
    while fila:
        at = fila.pop()
        for f in filhos.get(at, ()):
            if f not in contaminados and f not in quebradas:
                contaminados.add(f)
                fila.append(f)

    ruim = quebradas | contaminados

    def deps_ruins(d):
        return sorted(x for x in pais.get(d, ()) if x in ruim)

    pdf["deps_quebradas"] = pdf["ddl_up"].map(lambda d: len(deps_ruins(d)))
    pdf["exemplo_dep_ruim"] = pdf["ddl_up"].map(lambda d: ", ".join(deps_ruins(d)[:5]))
    print(f"🌱 Sementes (fonte não completo): {len(quebradas):,}")
    print(f"🦠 Contaminados por herança     : {len(contaminados):,}")
    return pdf


def marcar_aptas(pdf: pd.DataFrame) -> pd.DataFrame:
    def motivo(r):
        if r["status_fonte"] == "TRUNCADO":
            return "FONTE_TRUNCADO"
        if r["status_fonte"] == "REVISAR":
            return "FONTE_A_REVISAR"
        if r["traduzivel"] != "SIM":
            return "TIPO_" + r["traduzivel"]
        if r["deps_quebradas"] > 0:
            return "DEPENDENCIA_QUEBRADA"
        return "APTA"

    pdf["motivo"] = pdf.apply(motivo, axis=1)
    pdf["apta"] = pdf["motivo"].eq("APTA")
    return pdf


def rodar(spark, cfg: Config) -> Inventario:
    """Pipeline completo do Motor 01. Leva alguns minutos (varre a DDDDLSRC inteira)."""
    pdf = carregar_metricas(spark, cfg)
    print(f"✅ Fontes carregados: {len(pdf):,}")
    pdf["classe"] = pdf.apply(classificar_linha, axis=1)
    pdf = verificar_fronteira(spark, cfg, pdf)
    pdf = aplicar_status(pdf)
    pais, filhos = montar_grafo(spark, cfg, pdf)
    pdf = propagar_contaminacao(pdf, pais, filhos)
    pdf = marcar_aptas(pdf)
    return Inventario(pdf=pdf, pais=pais, filhos=filhos)
