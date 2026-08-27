"""Servidor MCP para o projeto Rosetta (FastMCP, transporte stdio).

Expõe tools somente-leitura para os workflows .agents/workflows/gerar-view.md e
completar-view-truncada.md:
1. buscar_cds_source(ddlname)
2. analisar_dependencias(ddlname, seguir_associacoes)
3. gerar_sql_view(ddlname)
4. classificar_status(ddlname)
5. buscar_views_irmas(ddlname) — candidatas de referência estrutural pra completar truncadas
6. gerar_view_completa(ddlname) — pipeline fim-a-fim: Fluxo A/B + notebook, sozinho
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# Garante que src/ está no PYTHONPATH para importar o pacote rosetta
RAIZ_PROJETO = Path(__file__).resolve().parents[1]
SRC_PATH = RAIZ_PROJETO / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# Carrega variáveis de ambiente do .env (credenciais Databricks) — o servidor MCP
# roda como subprocesso via .mcp.json e não herda o .env automaticamente.
from dotenv import load_dotenv

load_dotenv(RAIZ_PROJETO / ".env")

from rosetta.arvore import Arvore, montar_arvore
from rosetta.avisos_fonte import ROTULOS_AVISO
from rosetta.config import MARCA_META, Config
from rosetta.escritor import nome_pasta, proxima_versao
from rosetta.gerador import gerar_sql
from rosetta.localizador import COMPLETAS, TRUNCADAS, classificar_exato, varrer
from rosetta.nomes import ResolvedorNomes
from rosetta.parser_cds import parse_cds
from rosetta.pipeline import Contexto
from rosetta.workspace_sync import ErroEnvioWorkspace, enviar_notebook_workspace, montar_path_workspace

from .db_client import (
    buscar_candidatas_referencia,
    buscar_source_databricks,
    obter_config_databricks,
    obter_fonte_cache,
)
from .spark_like import SparkLikeDatabricks

# Inicializa o servidor FastMCP
mcp = FastMCP("rosetta")


# ------------------------------------------------------------------- Schemas


class DdlInput(BaseModel):
    ddlname: str = Field(
        ...,
        description="Nome da CDS view / DDL (ex.: 'I_ADDRESS', 'BSAS_DDL', '/AIF/C_INTERFACESTATISTICS')",
        min_length=1,
    )


class AnalisarDependenciasInput(BaseModel):
    ddlname: str = Field(
        ...,
        description="Nome da CDS view / DDL raiz para análise de dependências",
        min_length=1,
    )
    seguir_associacoes: bool = Field(
        default=False,
        description="Se True, segue associations CDS além do FROM principal (padrão: False)",
    )


# ----------------------------------------------------------- Helpers Internos


class IndiceOnDemand(dict):
    """Dicionário sob demanda que busca fontes do Databricks/cache conforme necessário."""

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def get(self, key: str, default: Any = None) -> Any:
        chave = (key or "").strip().upper()
        if super().__contains__(chave):
            return super().__getitem__(chave)
        try:
            src = buscar_source_databricks(chave, self.cfg)
            self[chave] = src
            return src
        except Exception:
            return default

    def __getitem__(self, key: str) -> str:
        res = self.get(key)
        if res is None:
            raise KeyError(key)
        return res

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        chave = key.strip().upper()
        if super().__contains__(chave):
            return True
        val = self.get(chave)
        return val is not None


def _extrair_bloco_internal(source: str) -> Dict[str, List[str]]:
    """Extrai listas de objetos FROM, ASSOCIATED e BASE do metadado /*+[internal]."""
    pos = source.find(MARCA_META)
    if pos == -1:
        return {}
    meta = source[pos:]
    res = {}
    m_from = re.search(r'"FROM"[^\[]*\[([^\]]*)\]', meta)
    if m_from:
        res["from"] = [
            x.strip(' "\r\n')
            for x in m_from.group(1).split(",")
            if x.strip(' "\r\n')
        ]
    m_assoc = re.search(r'"ASSOCIATED"[^\[]*\[([^\]]*)\]', meta)
    if m_assoc:
        res["associated"] = [
            x.strip(' "\r\n')
            for x in m_assoc.group(1).split(",")
            if x.strip(' "\r\n')
        ]
    m_base = re.search(r'"BASE"[^\[]*\[([^\]]*)\]', meta)
    if m_base:
        res["base"] = [
            x.strip(' "\r\n')
            for x in m_base.group(1).split(",")
            if x.strip(' "\r\n')
        ]
    return res


# --------------------------------------------------------------------- Tools


@mcp.tool(
    name="buscar_cds_source",
    description=(
        "Busca o código-fonte ABAP CDS original na tabela DDDDLSRC no Databricks "
        "para o ddlname informado. Operação estritamente de leitura (SELECT)."
    ),
    annotations={"readOnlyHint": True},
)
def buscar_cds_source(ddlname: str) -> str:
    """Retorna o campo source da CDS view."""
    chave = (ddlname or "").strip()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."
    try:
        source = buscar_source_databricks(chave)
        return source
    except (ValueError, ConnectionError, RuntimeError) as e:
        return f"Erro ao buscar '{chave}': {str(e)}"


@mcp.tool(
    name="analisar_dependencias",
    description=(
        "Analisa e monta a árvore de dependências a partir do ddlname raiz, "
        "inspecionando o grafo de fontes CDS, tabelas físicas e o bloco /*+[internal]*/. "
        "Retorna a hierarquia formatada em texto com emojis de status."
    ),
    annotations={"readOnlyHint": True},
)
def analisar_dependencias(ddlname: str, seguir_associacoes: bool = False) -> str:
    """Gera a árvore de dependências da CDS view."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    cfg, _, _, _ = obter_config_databricks()
    indice = IndiceOnDemand(cfg)

    # 1. Carrega fonte raiz
    src_raiz = indice.get(chave)
    if not src_raiz:
        return f"Erro: DDL '{chave}' não encontrado no catálogo/cache."

    # 2. Extrai bloco internal para enriquecer a resposta
    bloco_int = _extrair_bloco_internal(src_raiz)

    # 3. Monta a árvore
    resolvedor = ResolvedorNomes(indice, cfg)
    arvore = montar_arvore(
        ddlname_raiz=chave,
        indice=indice,
        resolvedor=resolvedor,
        seguir_assoc=seguir_associacoes,
    )

    linhas = [
        f"=== Árvore de Dependências: {chave} ===",
        f"Modo: {'Seguindo associations' if seguir_associacoes else 'Apenas FROM (padrão)'}",
        "",
        arvore.texto(),
        "",
        "--- Resumo de Dependências ---",
        f"🗄️  Tabelas físicas ({len(arvore.tabelas_fisicas)}): {', '.join(sorted(arvore.tabelas_fisicas)) or 'nenhuma'}",
        f"✅ CDS visitadas   ({len(arvore.cds_visitadas)}): {', '.join(sorted(arvore.cds_visitadas)) or 'nenhuma'}",
    ]

    if arvore.truncadas:
        linhas.append(f"❌ CDS truncadas   ({len(arvore.truncadas)}): {', '.join(sorted(arvore.truncadas))}")
    if arvore.nao_encontradas:
        linhas.append(f"❓ Não encontradas ({len(arvore.nao_encontradas)}): {', '.join(sorted(arvore.nao_encontradas))}")

    if bloco_int:
        linhas.append("")
        linhas.append("--- Metadados brutos da fonte (informativo, NÃO reflete seguir_associacoes) ---")
        if bloco_int.get("from"):
            linhas.append(f"FROM: {', '.join(bloco_int['from'])}")
        if bloco_int.get("associated"):
            linhas.append(f"ASSOCIATED: {', '.join(bloco_int['associated'])}")
        if bloco_int.get("base"):
            linhas.append(f"BASE: {', '.join(bloco_int['base'])}")

    return "\n".join(linhas)


@mcp.tool(
    name="gerar_sql_view",
    description=(
        "Traduz o fonte CDS em SQL Databricks equivalente com CREATE OR REPLACE VIEW comentado, "
        "conforme padrão do projeto Rosetta. Não executa DDL no catálogo."
    ),
    annotations={"readOnlyHint": True},
)
def gerar_sql_view(ddlname: str) -> str:
    """Gera o SQL Databricks da CDS view."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    cfg, _, _, _ = obter_config_databricks()
    indice = IndiceOnDemand(cfg)

    src = indice.get(chave)
    if not src:
        return f"Erro: DDL '{chave}' não encontrado no catálogo/cache."

    view = parse_cds(chave, src)
    resolvedor = ResolvedorNomes(indice, cfg)
    sql_gerado, avisos = gerar_sql(view, resolvedor)

    linhas = [sql_gerado]
    if avisos:
        linhas.append("")
        linhas.append(f"-- ⚠️  Avisos de tradução ({len(avisos)}):")
        for av in avisos:
            linhas.append(f"--    • {av}")

    return "\n".join(linhas)


@mcp.tool(
    name="classificar_status",
    description=(
        "Aplica a lógica de classificação estrutural nas 9 categorias do Rosetta "
        "(COMPLETA_FECHA_CHAVE, COMPLETA_COM_SUFIXO, TRUNCADO_CHAVE_ABERTA, "
        "COMPLETA_SEM_CHAVES, COMPLETA_TABLE_FUNCTION, CHAVES_NEGATIVAS, "
        "SUFIXO_NAO_RECONHECIDO, TRUNCADA_NO_SUFIXO, TRUNCADO_SEM_CHAVES). "
        "Alerta se a fonte está truncada pelo transporte RFC."
    ),
    annotations={"readOnlyHint": True},
)
def classificar_status(ddlname: str) -> str:
    """Classifica a integridade e tipo da CDS view."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    cfg, _, _, _ = obter_config_databricks()
    indice = IndiceOnDemand(cfg)

    src = indice.get(chave)
    if not src:
        return f"Erro: DDL '{chave}' não encontrado no catálogo/cache."

    pos_meta = src.find(MARCA_META)
    corpo = src[:pos_meta] if pos_meta >= 0 else src

    limpo, estado, trecho = varrer(corpo)
    classe = classificar_exato(limpo, estado, trecho)
    rotulo = ROTULOS_AVISO.get(classe, classe)

    if classe in COMPLETAS:
        status = "COMPLETO"
        emoji = "✅"
    elif classe in TRUNCADAS:
        status = "TRUNCADO"
        emoji = "❌"
    else:
        status = "REVISAR"
        emoji = "⚠️"

    resultado = [
        f"{emoji} Classificação: {classe}",
        f"Status: {status}",
        f"Descrição: {rotulo}",
        f"Tamanho do corpo: {len(corpo):,} caracteres",
    ]

    if status == "TRUNCADO":
        resultado.append("")
        resultado.append(
            "🚨 ATENÇÃO: O código-fonte desta CDS view está TRUNCADO "
            "(provavelmente pelo limite RFC de 32KB). "
            "A geração de SQL ou análise de dependências pode estar incompleta."
        )
    elif status == "REVISAR":
        resultado.append("")
        resultado.append(
            "⚠️  AVISO: Estrutura requer revisão manual antes de usar em produção."
        )

    return "\n".join(resultado)


_RX_FROM_BASE = re.compile(r"(?is)\bas\s+select\s+from\s+([A-Za-z0-9_/]+)")


@mcp.tool(
    name="buscar_views_irmas",
    description=(
        "Busca CDS views COMPLETAS que usam a mesma tabela base física da view informada "
        "(ex.: outras 'FROM ADRC') — candidatas a referência estrutural pra completar uma "
        "view truncada, conforme .agents/workflows/completar-view-truncada.md. Não infere "
        "nada sozinha: só lista e classifica as candidatas."
    ),
    annotations={"readOnlyHint": True},
)
def buscar_views_irmas(ddlname: str, limite: int = 10) -> str:
    """Lista candidatas a view irmã (mesma tabela base, já classificadas)."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    cfg, _, _, _ = obter_config_databricks()
    try:
        src = buscar_source_databricks(chave, cfg)
    except (ValueError, ConnectionError, RuntimeError) as e:
        return f"Erro ao buscar '{chave}': {str(e)}"

    m = _RX_FROM_BASE.search(src[:4000])
    if not m:
        return (
            f"❓ Não foi possível identificar a tabela base de '{chave}' no início do fonte "
            f"(nem no fragmento disponível, se estiver truncado). Sem tabela base, não dá "
            f"pra buscar views irmãs — informe manualmente qual tabela usar como referência."
        )
    tabela_base = m.group(1).strip().upper()

    try:
        candidatas = buscar_candidatas_referencia(tabela_base, chave, cfg, limite=limite)
    except (ValueError, ConnectionError, RuntimeError) as e:
        return f"Erro ao buscar candidatas: {str(e)}"

    if not candidatas:
        return (
            f"❓ Nenhuma outra CDS view referenciando 'FROM {tabela_base}' foi encontrada. "
            f"Sem view de referência para inferir padrão — conforme "
            f".agents/workflows/completar-view-truncada.md, isso significa NÃO tentar "
            f"completar automaticamente; marque pendente_validacao com esse motivo."
        )

    linhas = [f"🔎 Tabela base identificada: {tabela_base}", f"Candidatas encontradas ({len(candidatas)}):", ""]
    for nome in candidatas:
        try:
            src_c = buscar_source_databricks(nome, cfg)
        except (ValueError, ConnectionError, RuntimeError):
            linhas.append(f"  ❓ {nome}: erro ao buscar fonte")
            continue
        pos_meta = src_c.find(MARCA_META)
        corpo = src_c[:pos_meta] if pos_meta >= 0 else src_c
        limpo, estado, trecho = varrer(corpo)
        classe = classificar_exato(limpo, estado, trecho)
        emoji = "✅" if classe in COMPLETAS else ("❌" if classe in TRUNCADAS else "⚠️")
        linhas.append(f"  {emoji} {nome} — {classe} ({len(src_c):,} chars)")

    linhas.append("")
    linhas.append(
        "Use buscar_cds_source nas candidatas ✅ como referência de padrão estrutural — "
        "nunca como fonte de campos específicos da view truncada."
    )
    return "\n".join(linhas)


@mcp.tool(
    name="gerar_view_completa",
    description=(
        "Pipeline fim-a-fim, sozinho: busca a CDS view no Databricks, decide Fluxo A "
        "(base já existe no destino) ou Fluxo B (gera do zero), monta e grava o notebook "
        "versionado view_<nome>/view_<nome>_NNNNN.ipynb + os artefatos (.sql, metadata.json "
        "com pendente_validacao). Se a fonte estiver truncada, o notebook ainda é gerado — "
        "o fragmento disponível vira referência marcada PENDENTE_VALIDACAO, nunca bloqueia."
    ),
    annotations={"readOnlyHint": False},
)
def gerar_view_completa(ddlname: str) -> str:
    """Roda Contexto.gerar_view_notebook contra o Databricks real e grava em ddl/."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    cfg, host, token, http_path = obter_config_databricks()
    if not host or not token or not http_path:
        return (
            "Erro: credenciais do Databricks não configuradas (DATABRICKS_HOST/"
            "DATABRICKS_TOKEN/DATABRICKS_HTTP_PATH)."
        )
    host_limpo = re.sub(r"^https?://", "", host).rstrip("/")
    spark = SparkLikeDatabricks(host_limpo, http_path, token)

    indice = IndiceOnDemand(cfg)
    ctx = Contexto(spark, cfg, indice=indice, raiz_ddl=RAIZ_PROJETO / "ddl", verboso=False)

    if not ctx.existe(chave):
        return f"❓ '{chave}' não encontrado em {cfg.fqn_ddl}."

    try:
        res = ctx.gerar_view_notebook(
            chave, spark=spark, gravar_notebook=True, gravar_artefatos_texto=True,
        )
    except Exception as e:  # noqa: BLE001 — nunca propaga stacktrace pro agente
        return f"Erro ao gerar a view '{chave}': {type(e).__name__}: {str(e)[:300]}"

    linhas = [
        f"✅ Fluxo usado: {res.conteudo.fluxo_usado}",
        f"Status de classificação: {res.status_classificacao}",
        f"Pendente de validação: {'SIM' if res.conteudo.pendente_validacao else 'não'}",
    ]
    if res.conteudo.motivo_pendencia:
        linhas.append(f"Motivo: {'; '.join(res.conteudo.motivo_pendencia)}")
    if res.conteudo.avisos:
        linhas.append(f"Avisos ({len(res.conteudo.avisos)}):")
        linhas += [f"  - {a}" for a in res.conteudo.avisos]
    linhas.append(f"Notebook: {res.caminho_notebook}")
    if res.artefatos:
        linhas.append(f"Artefatos: {res.artefatos.pasta}")

    return "\n".join(linhas)


_WORKSPACE_BASE_PADRAO = "/Workspace/Users/allanfelipedk@gmail.com/rosetta/ddl"


@mcp.tool(
    name="enviar_para_workspace",
    description=(
        "Sobe o notebook JÁ GERADO em ddl/view_<nome>/view_<nome>_NNNNN.ipynb para o "
        "workspace do Databricks (REST API), na pasta pessoal do Allan. Passo MANUAL e "
        "SEPARADO de gerar_view_completa — só escreve no workspace de notebooks, nunca "
        "no catálogo/tabelas. Rode depois de revisar o notebook localmente."
    ),
    annotations={"readOnlyHint": False},
)
def enviar_para_workspace(ddlname: str, base_workspace: str = _WORKSPACE_BASE_PADRAO) -> str:
    """Envia o notebook mais recente da view pro workspace Databricks via REST API."""
    chave = (ddlname or "").strip().upper()
    if not chave:
        return "Erro: Parâmetro 'ddlname' não informado."

    _, host, token, _ = obter_config_databricks()
    if not host or not token:
        return "Erro: credenciais do Databricks não configuradas (DATABRICKS_HOST/DATABRICKS_TOKEN)."

    pasta = RAIZ_PROJETO / "ddl" / nome_pasta(chave)
    versao_atual = proxima_versao(pasta, chave) - 1
    if versao_atual < 1:
        return f"Erro: nenhum notebook encontrado em {pasta} — rode gerar_view_completa primeiro."
    nome_arquivo = f"{nome_pasta(chave)}_{versao_atual:05d}.ipynb"
    caminho_local = pasta / nome_arquivo

    path_workspace = montar_path_workspace(base_workspace, nome_pasta(chave), nome_arquivo)
    try:
        enviado = enviar_notebook_workspace(caminho_local, path_workspace, host, token)
    except ErroEnvioWorkspace as e:
        return f"Erro ao enviar para o workspace: {e}"

    return f"✅ Notebook enviado: {caminho_local.name} → {enviado}"


def main():
    """Ponto de entrada do servidor stdio."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
