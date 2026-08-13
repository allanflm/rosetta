"""Monta os 3 insumos que hoje são colados manualmente no Gemini do mentor.

Fase intermediária do projeto: o Rosetta ainda não gera a view final sozinho,
mas já consegue extrair, 100% de dentro do Databricks e em modo leitura, os
três arquivos que alimentam o prompt (`referencias/Texto gems.txt`):

    1. SQL SAP    — DDL da CDS raiz + de toda a cadeia de CDS intermediárias
                     (de `tab_ddddlsrc`), achatada até chegar em tabelas físicas.
    2. Metadados  — DD03L (posição, chave, tipo ABAP, tamanho) + DD04T
                     (descrição em PT do elemento de dados), por campo.
    3. SHOW CREATE TABLE — estrutura da tabela legada equivalente no
                     Databricks (`platform.sap_s4_replica.*`), para o
                     comparativo de compatibilidade (regra 5.7 do prompt).

Nada aqui executa DDL: toda consulta passa por `seguranca.sql_leitura`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .arvore import Arvore, montar_arvore
from .config import Config
from .nomes import ResolvedorNomes
from .parser_cds import parse_cds
from .seguranca import sql_leitura


# --------------------------------------------------------------- 1. SQL SAP


@dataclass
class CadeiaDdl:
    ddlname_raiz: str
    ddlnames: List[str]  # ordem: raiz primeiro, depois dependências CDS
    texto: str
    arvore: Arvore


def montar_cadeia_ddl(
    ddlname_raiz: str,
    indice: Dict[str, str],
    resolvedor: ResolvedorNomes,
    seguir_assoc: bool = False,
    max_profundidade: int = 15,
) -> CadeiaDdl:
    """Concatena o DDL da CDS raiz com o de todas as CDS intermediárias da
    cadeia (não desce até tabela física — essa já é a fronteira natural).

    A ordem segue o pedido do prompt do Gemini: raiz primeiro, "e de todas as
    CDS intermediárias da cadeia, até que as fontes sejam apenas tabelas
    físicas" — quem achata as camadas em uma view final é o próprio Gemini.
    """
    chave_raiz = ddlname_raiz.strip().upper()
    arv = montar_arvore(
        chave_raiz,
        indice=indice,
        resolvedor=resolvedor,
        seguir_assoc=seguir_assoc,
        max_profundidade=max_profundidade,
    )

    ordenados = [chave_raiz] + sorted(arv.cds_visitadas - {chave_raiz})
    blocos = []
    for nome in ordenados:
        src = indice.get(nome)
        if src is None:
            continue
        blocos.append(f"-- ==================== {nome} ====================\n{src.strip()}")

    return CadeiaDdl(
        ddlname_raiz=chave_raiz,
        ddlnames=ordenados,
        texto="\n\n".join(blocos),
        arvore=arv,
    )


def sql_view_names_da_cadeia(cadeia: CadeiaDdl, indice: Dict[str, str]) -> List[str]:
    """DD03L não indexa pelo nome da CDS (`I_BILLINGDOCUMENT`) — indexa pela
    estrutura/SQL view gerada na compilação (`@AbapCatalog.sqlViewName`, ex.:
    `BILLINGDOCUMENTHEADER_S`). Parseia cada CDS da cadeia e devolve esses
    nomes gerados, que são o `tabname` correto para consultar o DD03L.
    """
    nomes: List[str] = []
    for ddlname in cadeia.ddlnames:
        src = indice.get(ddlname)
        if not src:
            continue
        view = parse_cds(ddlname, src)
        if view.sql_view_name:
            nomes.append(view.sql_view_name.strip().upper())
    vistos: Set[str] = set()
    unicos = []
    for n in nomes:
        if n not in vistos:
            vistos.add(n)
            unicos.append(n)
    return unicos


# --------------------------------------------------------------- 2. Metadados


def _coluna(colunas, *candidatas) -> Optional[str]:
    mapa = {c.lower(): c for c in colunas}
    for c in candidatas:
        if c.lower() in mapa:
            return mapa[c.lower()]
    return None


def buscar_metadados_campos(
    spark, cfg: Config, tabnames: List[str], idioma: str = "P"
) -> List[dict]:
    """DD03L join DD04T por ROLLNAME, filtrado pelas TABNAME informadas.

    `idioma` segue a chave de idioma SAP (ex.: 'P' = português em muitos
    sistemas). Se vier vazio na saída, o join provavelmente precisa de outra
    chave — rodar `SELECT DISTINCT ddlanguage FROM {fqn_dd04t}` para conferir.
    """
    if not tabnames:
        return []

    col03 = spark.table(cfg.fqn_dd03l).columns
    col04 = spark.table(cfg.fqn_dd04t).columns

    c_tab = _coluna(col03, "tabname")
    c_field = _coluna(col03, "fieldname")
    c_pos = _coluna(col03, "position")
    c_key = _coluna(col03, "keyflag")
    c_roll = _coluna(col03, "rollname")
    c_type = _coluna(col03, "datatype")
    c_leng = _coluna(col03, "leng")
    c_dec = _coluna(col03, "decimals")
    faltando03 = [n for n, v in {
        "tabname": c_tab, "fieldname": c_field, "position": c_pos,
        "rollname": c_roll,
    }.items() if v is None]
    if faltando03:
        raise ValueError(f"Colunas ausentes em {cfg.fqn_dd03l}: {faltando03}")

    c_roll04 = _coluna(col04, "rollname")
    c_lang = _coluna(col04, "ddlanguage")
    c_text = _coluna(col04, "ddtext")
    faltando04 = [n for n, v in {"rollname": c_roll04, "ddtext": c_text}.items() if v is None]
    if faltando04:
        raise ValueError(f"Colunas ausentes em {cfg.fqn_dd04t}: {faltando04}")

    lista = ",".join("'" + t.strip().upper().replace("'", "''") + "'" for t in tabnames)
    filtro_lang = f"AND UPPER(TRIM(d.{c_lang})) = '{idioma.upper()}'" if c_lang else ""

    sel_extra = []
    if c_key:
        sel_extra.append(f"l.{c_key} AS keyflag")
    else:
        sel_extra.append("NULL AS keyflag")
    if c_type:
        sel_extra.append(f"l.{c_type} AS datatype")
    else:
        sel_extra.append("NULL AS datatype")
    if c_leng:
        sel_extra.append(f"l.{c_leng} AS leng")
    else:
        sel_extra.append("NULL AS leng")
    if c_dec:
        sel_extra.append(f"l.{c_dec} AS decimals")
    else:
        sel_extra.append("NULL AS decimals")

    sql = f"""
        SELECT
            l.{c_tab}   AS tabname,
            l.{c_field} AS fieldname,
            l.{c_pos}   AS position,
            l.{c_roll}  AS rollname,
            {', '.join(sel_extra)},
            d.{c_text}  AS ddtext
        FROM {cfg.fqn_dd03l} l
        LEFT JOIN {cfg.fqn_dd04t} d
            ON UPPER(TRIM(l.{c_roll})) = UPPER(TRIM(d.{c_roll04}))
            {filtro_lang}
        WHERE UPPER(TRIM(l.{c_tab})) IN ({lista})
        ORDER BY l.{c_tab}, l.{c_pos}
    """
    return [r.asDict() for r in sql_leitura(spark, sql).collect()]


def formatar_metadados(linhas: List[dict]) -> str:
    """Formato texto simples (uma linha por campo) — pronto para colar no
    Gemini como <de_para_comentarios> + contexto estrutural (tipo/tamanho)."""
    if not linhas:
        return "-- nenhum metadado encontrado para as tabelas informadas"
    saida = []
    for r in linhas:
        pk = " [PK]" if str(r.get("keyflag") or "").strip().upper() in ("X", "TRUE", "1") else ""
        tipo = f"{r.get('datatype') or '?'}({r.get('leng') or '?'},{r.get('decimals') or 0})"
        desc = r.get("ddtext") or "(sem texto — revisar)"
        saida.append(
            f"{r.get('tabname')}.{r.get('fieldname')}{pk}  |  pos={r.get('position')}  "
            f"|  {tipo}  |  {r.get('rollname')}  -  {desc}"
        )
    return "\n".join(saida)


# --------------------------------------------------------- 3. SHOW CREATE TABLE


def buscar_show_create_table(spark, cfg: Config, tabela_legada: str) -> str:
    fqn = f"{cfg.fqn_legado}.{tabela_legada.strip().lower()}"
    linhas = sql_leitura(spark, f"SHOW CREATE TABLE {fqn}").collect()
    if not linhas:
        return f"-- {fqn}: SHOW CREATE TABLE não retornou nada"
    campo = linhas[0].asDict()
    chave = next(iter(campo))
    return "\n".join(r[chave] for r in linhas)


# ------------------------------------------------------------------- pacote


@dataclass
class PacoteGemini:
    ddlname: str
    tabnames_metadados: List[str]
    tabela_legada: str
    cadeia: CadeiaDdl
    metadados_linhas: List[dict] = field(default_factory=list)
    show_create_table: str = ""

    @property
    def arquivo_1_sql_sap(self) -> str:
        return self.cadeia.texto

    @property
    def arquivo_2_metadados(self) -> str:
        return formatar_metadados(self.metadados_linhas)

    @property
    def arquivo_3_show_create_table(self) -> str:
        return self.show_create_table


def montar_pacote(
    spark,
    cfg: Config,
    indice: Dict[str, str],
    resolvedor: ResolvedorNomes,
    ddlname: str,
    tabela_legada: str,
    tabnames_metadados: Optional[List[str]] = None,
    seguir_assoc: bool = False,
    max_profundidade: int = 15,
    idioma: str = "P",
) -> PacoteGemini:
    """`tabnames_metadados=None` (padrão) deriva automaticamente os nomes a
    consultar no DD03L a partir do `sql_view_name` de cada CDS da cadeia —
    o DD03L é indexado pela estrutura/SQL view gerada, não pelo nome da CDS.
    Passe a lista explicitamente só se souber que precisa de outros nomes."""
    cadeia = montar_cadeia_ddl(
        ddlname, indice=indice, resolvedor=resolvedor,
        seguir_assoc=seguir_assoc, max_profundidade=max_profundidade,
    )
    if not tabnames_metadados:
        tabnames_metadados = sql_view_names_da_cadeia(cadeia, indice)
        if not tabnames_metadados:
            tabnames_metadados = [cadeia.ddlname_raiz]

    metadados = buscar_metadados_campos(spark, cfg, tabnames_metadados, idioma=idioma)
    sct = buscar_show_create_table(spark, cfg, tabela_legada)
    return PacoteGemini(
        ddlname=cadeia.ddlname_raiz,
        tabnames_metadados=[t.strip().upper() for t in tabnames_metadados],
        tabela_legada=tabela_legada,
        cadeia=cadeia,
        metadados_linhas=metadados,
        show_create_table=sct,
    )


def salvar_pacote(raiz_ddl: Path, pacote: PacoteGemini, gravar: bool = True) -> Dict[str, Path]:
    """Grava os 3 arquivos em `ddl/<DDLNAME>/gemini/`, prontos para colar no
    prompt do Gemini. Só escreve arquivo de texto no repositório — nada no
    catálogo do Databricks."""
    from .escritor import nome_pasta

    pasta = Path(raiz_ddl) / nome_pasta(pacote.ddlname) / "gemini"
    conteudos = {
        "sql_sap": (pasta / "1_sql_sap.txt", pacote.arquivo_1_sql_sap),
        "metadados": (pasta / "2_metadados.txt", pacote.arquivo_2_metadados),
        "show_create_table": (pasta / "3_show_create_table.txt", pacote.arquivo_3_show_create_table),
    }
    if gravar:
        pasta.mkdir(parents=True, exist_ok=True)
        for caminho, texto in conteudos.values():
            caminho.write_text(texto.rstrip() + "\n", encoding="utf-8")
    return {rotulo: caminho for rotulo, (caminho, _) in conteudos.items()}
