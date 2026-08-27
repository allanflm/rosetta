"""Testes unitários para o servidor MCP Rosetta (mcp_server)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))
sys.path.insert(0, str(RAIZ))

from mcp_server.db_client import registrar_fonte_cache
from mcp_server.server import (
    analisar_dependencias,
    buscar_cds_source,
    classificar_status,
    gerar_sql_view,
)

CDS_BSAS = """
@AbapCatalog.sqlViewName: 'ZVBSAS'
@EndUserText.label: 'Documentos em aberto'
define view Z_Bsas
  as select from bsas as b
{
  key b.bukrs as CompanyCode,
  key b.belnr as DocumentNumber,
      b.dmbtr as Amount
}
where b.bukrs = '1000'
/*+[internal] {"BASE":["BSAS"],"FROM":["BSAS"]} */
"""

CDS_FECHA_CHAVE = """
define view Z_Fecha_Chave
  as select from t001 as t
{
  key t.bukrs as CompanyCode,
      t.butxt as CompanyCodeName
}
"""

CDS_ASSOC = """
define view entity Z_Assoc
  as select from bkpf as k
  association [0..1] to I_CompanyCode as _Empresa on $projection.CompanyCode = _Empresa.CompanyCode
{
  key k.bukrs as CompanyCode,
      _Empresa.CompanyCodeName as NomeEmpresa
}
/*+[internal] {"ASSOCIATED":["I_COMPANYCODE"],"FROM":["BKPF"]} */
"""

CDS_COMPANYCODE = """
define view I_CompanyCode
  as select from t001 as t
{
  key t.bukrs as CompanyCode,
      t.butxt as CompanyCodeName
}
"""

CDS_TRUNCADA = """
define view Z_Trunc
  as select from bseg as s
{
  key s.belnr as Doc,
      s.dmbtr as Val
"""


@pytest.fixture(autouse=True)
def carregar_fontes(monkeypatch):
    # Isola das credenciais/catálogo reais do .env (mcp_server.server carrega via
    # load_dotenv na importação) — estes testes usam só o cache em memória via
    # registrar_fonte_cache, nunca devem tocar o Databricks real nem herdar o
    # catálogo/schema de produção configurados no .env.
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_SERVER_HOSTNAME", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_HTTP_PATH", raising=False)
    monkeypatch.delenv("DATABRICKS_CATALOG", raising=False)
    monkeypatch.delenv("DATABRICKS_SCHEMA", raising=False)
    monkeypatch.delenv("DATABRICKS_TABLE", raising=False)

    registrar_fonte_cache("Z_BSAS", CDS_BSAS)
    registrar_fonte_cache("Z_FECHA_CHAVE", CDS_FECHA_CHAVE)
    registrar_fonte_cache("Z_ASSOC", CDS_ASSOC)
    registrar_fonte_cache("I_COMPANYCODE", CDS_COMPANYCODE)
    registrar_fonte_cache("Z_TRUNC", CDS_TRUNCADA)


def test_buscar_cds_source_encontrada():
    src = buscar_cds_source("Z_BSAS")
    assert "define view Z_Bsas" in src
    assert "ZVBSAS" in src


def test_buscar_cds_source_nao_encontrada():
    res = buscar_cds_source("VIEW_INEXISTENTE")
    assert "Erro ao buscar 'VIEW_INEXISTENTE'" in res


def test_analisar_dependencias_simples():
    resultado = analisar_dependencias("Z_BSAS")
    assert "=== Árvore de Dependências: Z_BSAS ===" in resultado
    assert "🗄️  Tabelas físicas (1): BSAS" in resultado
    assert "FROM: BSAS" in resultado


def test_analisar_dependencias_associacao():
    res_sem_assoc = analisar_dependencias("Z_ASSOC", seguir_associacoes=False)
    # Valida apenas a seção da árvore/resumo (antes da seção informativa de metadados brutos)
    secao_arvore = res_sem_assoc.split("--- Metadados brutos")[0]
    assert "🗄️  Tabelas físicas (1): BKPF" in secao_arvore
    assert "I_COMPANYCODE" not in secao_arvore

    res_com_assoc = analisar_dependencias("Z_ASSOC", seguir_associacoes=True)
    secao_arvore_com = res_com_assoc.split("--- Metadados brutos")[0]
    assert "I_CompanyCode" in secao_arvore_com or "I_COMPANYCODE" in secao_arvore_com
    assert "T001" in secao_arvore_com


def test_gerar_sql_view():
    sql = gerar_sql_view("Z_BSAS")
    assert "-- Gerado a partir de Z_BSAS" in sql
    assert "-- CREATE OR REPLACE VIEW platform_dev.sap_s4_nc2_replica.z_bsas AS" in sql
    assert "SELECT" in sql
    assert "FROM platform_dev.sap_s4_nc2_raw.bsas AS b" in sql
    assert "b.bukrs AS CompanyCode,  -- key" in sql


def test_classificar_status_completa_com_sufixo():
    status = classificar_status("Z_BSAS")
    assert "COMPLETA_COM_SUFIXO" in status
    assert "Status: COMPLETO" in status


def test_classificar_status_completa_fecha_chave():
    status = classificar_status("Z_FECHA_CHAVE")
    assert "COMPLETA_FECHA_CHAVE" in status
    assert "Status: COMPLETO" in status


def test_classificar_status_truncada():
    status = classificar_status("Z_TRUNC")
    assert "TRUNCADO_CHAVE_ABERTA" in status
    assert "Status: TRUNCADO" in status
    assert "TRUNCADO" in status
    assert "🚨 ATENÇÃO" in status


def test_executar_sql_leitura_cursor_permite_apenas_leitura():
    from unittest.mock import MagicMock
    from rosetta.seguranca import SqlNaoPermitido
    from mcp_server.db_client import executar_sql_leitura_cursor

    mock_cursor = MagicMock()

    # Queries válidas de leitura
    executar_sql_leitura_cursor(mock_cursor, "SELECT * FROM tab_ddddlsrc LIMIT 1")
    executar_sql_leitura_cursor(mock_cursor, "WITH cte AS (SELECT 1) SELECT * FROM cte")
    executar_sql_leitura_cursor(mock_cursor, "DESCRIBE platform_dev.sap_s4_nc2_raw.tab_ddddlsrc")
    executar_sql_leitura_cursor(mock_cursor, "SHOW TABLES IN platform_dev.sap_s4_nc2_raw")
    executar_sql_leitura_cursor(mock_cursor, "EXPLAIN SELECT 1")
    assert mock_cursor.execute.call_count == 5

    # Queries proibidas de escrita/DDL/DML devem levantar SqlNaoPermitido
    with pytest.raises(SqlNaoPermitido):
        executar_sql_leitura_cursor(mock_cursor, "DROP TABLE tab_ddddlsrc")

    with pytest.raises(SqlNaoPermitido):
        executar_sql_leitura_cursor(mock_cursor, "CREATE OR REPLACE VIEW v AS SELECT 1")

    with pytest.raises(SqlNaoPermitido):
        executar_sql_leitura_cursor(mock_cursor, "INSERT INTO tab VALUES (1)")

    with pytest.raises(SqlNaoPermitido):
        executar_sql_leitura_cursor(mock_cursor, "DELETE FROM tab WHERE 1=1")

    with pytest.raises(SqlNaoPermitido):
        executar_sql_leitura_cursor(mock_cursor, "SELECT 1; DROP TABLE tab")
