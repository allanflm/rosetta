"""Cliente de acesso ao Databricks SQL Connector para o MCP Server Rosetta.

Somente leitura por design: toda query é validada por `rosetta.seguranca.assert_leitura`.
Autenticação via variáveis de ambiente:
- DATABRICKS_HOST (ou DATABRICKS_SERVER_HOSTNAME)
- DATABRICKS_TOKEN (ou DATABRICKS_ACCESS_TOKEN)
- DATABRICKS_HTTP_PATH
"""
from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

from rosetta.config import Config
from rosetta.seguranca import assert_leitura

# Cache de fontes em memória {DDLNAME_MAIUSCULO: source}
_CACHE_FONTES: Dict[str, str] = {}


def obter_config_databricks() -> Tuple[Config, Optional[str], Optional[str], Optional[str]]:
    """Lê configuração e credenciais das variáveis de ambiente."""
    host = os.getenv("DATABRICKS_HOST") or os.getenv("DATABRICKS_SERVER_HOSTNAME")
    token = os.getenv("DATABRICKS_TOKEN") or os.getenv("DATABRICKS_ACCESS_TOKEN")
    http_path = os.getenv("DATABRICKS_HTTP_PATH")

    catalog = os.getenv("DATABRICKS_CATALOG", "platform_dev")
    schema = os.getenv("DATABRICKS_SCHEMA", "sap_s4_nc2_raw")
    tabela = os.getenv("DATABRICKS_TABLE", "tab_ddddlsrc")

    cfg = Config(
        catalog_raw=catalog,
        schema_raw=schema,
        tabela_ddl=tabela,
    )
    return cfg, host, token, http_path


def registrar_fonte_cache(ddlname: str, source: str) -> None:
    """Registra manualmente um fonte no cache local (útil para testes ou índices pré-carregados)."""
    if ddlname:
        _CACHE_FONTES[ddlname.strip().upper()] = source


def obter_fonte_cache(ddlname: str) -> Optional[str]:
    """Recupera um fonte do cache local."""
    return _CACHE_FONTES.get((ddlname or "").strip().upper())


def executar_sql_leitura_cursor(cursor, sql_query: str):
    """Garante a trava de somente leitura no conector Databricks SQL antes de executar.

    Valida ativamente que a query é de leitura pura (SELECT, WITH, DESCRIBE, SHOW, EXPLAIN)
    e recusa múltiplos statements ou comandos DDL/DML, levantando SqlNaoPermitido.
    """
    assert_leitura(sql_query)
    cursor.execute(sql_query)
    return cursor


def buscar_candidatas_referencia(
    tabela_base: str, excluir_ddlname: str, cfg_custom: Optional[Config] = None, limite: int = 15
) -> list[str]:
    """Lista ddlnames cujo source referencia `FROM <tabela_base>` — candidatas a
    "view irmã" pro workflow `.agents/workflows/completar-view-truncada.md` (passo 2:
    usar view completa com a mesma base física como referência de padrão estrutural
    pra completar uma truncada). Só lista os nomes; classificar/buscar o source de
    cada uma é responsabilidade de quem chama (evita puxar fontes grandes à toa)."""
    alvo = (tabela_base or "").strip().upper()
    if not alvo:
        return []

    cfg_env, host, token, http_path = obter_config_databricks()
    cfg = cfg_custom or cfg_env
    if not host or not token or not http_path:
        raise ConnectionError("Credenciais do Databricks não configuradas no ambiente.")

    host_limpo = re.sub(r"^https?://", "", host).rstrip("/")

    from databricks import sql

    alvo_sql = alvo.replace("'", "''")
    excl_sql = (excluir_ddlname or "").strip().upper().replace("'", "''")
    query = (
        f"SELECT DISTINCT TRIM(ddlname) AS ddlname FROM {cfg.fqn_ddl} "
        f"WHERE UPPER(source) LIKE '%FROM {alvo_sql}%' "
        f"AND UPPER(TRIM(ddlname)) <> '{excl_sql}' "
        f"LIMIT {int(limite)}"
    )

    with sql.connect(server_hostname=host_limpo, http_path=http_path, access_token=token) as connection:
        with connection.cursor() as cursor:
            executar_sql_leitura_cursor(cursor, query)
            return [str(r[0]).strip() for r in cursor.fetchall() if r[0]]


def buscar_source_databricks(ddlname: str, cfg_custom: Optional[Config] = None) -> str:
    """Busca o campo source de uma CDS view na tabela DDDDLSRC no Databricks.

    Levanta ValueError, ConnectionError ou SqlNaoPermitido com mensagem clara e acionável.
    """
    alvo = (ddlname or "").strip().upper()
    if not alvo:
        raise ValueError("O parâmetro 'ddlname' não pode ser vazio.")

    # 1. Verifica cache local
    if alvo in _CACHE_FONTES:
        return _CACHE_FONTES[alvo]

    cfg_env, host, token, http_path = obter_config_databricks()
    cfg = cfg_custom or cfg_env

    # 2. Valida credenciais
    if not host or not token or not http_path:
        faltantes = []
        if not host:
            faltantes.append("DATABRICKS_HOST")
        if not token:
            faltantes.append("DATABRICKS_TOKEN")
        if not http_path:
            faltantes.append("DATABRICKS_HTTP_PATH")
        raise ConnectionError(
            f"Credenciais do Databricks não configuradas no ambiente. "
            f"Defina as variáveis: {', '.join(faltantes)}."
        )

    # Limpar formato de host caso venha com https://
    host_limpo = re.sub(r"^https?://", "", host).rstrip("/")

    try:
        from databricks import sql
    except ImportError as e:
        raise ImportError(
            "Pacote 'databricks-sql-connector' não instalado. "
            "Execute: pip install databricks-sql-connector"
        ) from e

    # Monta a query somente leitura
    alvo_sql = alvo.replace("'", "''")
    fqn = cfg.fqn_ddl
    query = (
        f"SELECT source FROM {fqn} "
        f"WHERE UPPER(TRIM(ddlname)) = '{alvo_sql}' LIMIT 1"
    )

    try:
        with sql.connect(
            server_hostname=host_limpo,
            http_path=http_path,
            access_token=token,
        ) as connection:
            with connection.cursor() as cursor:
                executar_sql_leitura_cursor(cursor, query)
                row = cursor.fetchone()
                if not row or not row[0]:
                    raise ValueError(
                        f"DDL '{alvo}' não encontrado na tabela '{fqn}'."
                    )
                source = str(row[0]).strip()
                _CACHE_FONTES[alvo] = source
                return source
    except Exception as e:
        if isinstance(e, (ValueError, ConnectionError)):
            raise
        raise RuntimeError(
            f"Erro ao consultar Databricks para o ddlname '{alvo}': {str(e)}"
        ) from e
