"""Testes que rodam sem Spark e sem Databricks.

Essa é a maior vantagem prática de tirar o código dos notebooks: parser, tradução,
geração e escrita são funções puras sobre texto, então dá para testá-las em
segundos, num CI, sem subir cluster.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rosetta import (  # noqa: E402
    Config,
    ResolvedorNomes,
    SqlNaoPermitido,
    assert_leitura,
    gerar_sql,
    montar_arvore,
    nome_pasta,
    parse_cds,
    salvar_artefatos,
    sanitizar,
)

CDS_SIMPLES = """
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
"""

CDS_COM_ASSOC = """
define view entity Z_Com_Assoc
  as select from bkpf as k
  association [0..1] to I_CompanyCode as _Empresa on $projection.CompanyCode = _Empresa.CompanyCode
{
  key k.bukrs as CompanyCode,
      _Empresa.CompanyCodeName as NomeEmpresa
}
"""

CDS_UNION = """
define view Z_Uniao
  as select from bsis as a { key a.belnr as Doc, a.dmbtr as Valor }
  union all
  select from bsas as b { key b.belnr as Doc, b.dmbtr as Valor }
"""

CDS_TRUNCADO = """
define view Z_Cortada
  as select from bseg as s
{
  key s.belnr as Doc,
      s.dmbtr as Val
"""

CDS_ABAP = """
define view Z_Abap
  as select from bseg as s
{
  key s.belnr as Doc,
      cast(s.dmbtr as abap.dec(15,2)) as Valor,
      dats_add_days(s.budat, 30, 'NULL') as Vencimento
}
"""


@pytest.fixture
def resolvedor():
    cfg = Config()
    indice = {"Z_BSAS": CDS_SIMPLES, "I_COMPANYCODE": "define view I_CompanyCode as select from t001 as t { key t.bukrs as CompanyCode }"}
    return ResolvedorNomes(indice, cfg)


# ------------------------------------------------------------------ parser


def test_parse_extrai_metadados():
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    assert not v.truncado
    assert v.nome_entidade == "Z_Bsas"
    assert v.sql_view_name == "ZVBSAS"
    assert v.label == "Documentos em aberto"
    assert v.entidade_base == "bsas"
    assert v.alias_base == "b"
    assert len(v.campos) == 3
    assert v.campos[0].is_key
    assert v.where is not None


def test_parse_detecta_truncamento():
    v = parse_cds("Z_CORTADA", CDS_TRUNCADO)
    assert v.truncado
    assert v.avisos


def test_parse_nunca_levanta():
    """Contrato do parse_cds: entrada lixo vira truncado=True, não exceção."""
    for lixo in ["", "@@@", "define view", "{{{{", None]:
        v = parse_cds("X", lixo or "")
        assert v.truncado


def test_parse_union_gera_branches():
    v = parse_cds("Z_UNIAO", CDS_UNION)
    assert not v.truncado
    assert len(v.blocos_uniao) == 2


# ------------------------------------------------------------------ gerador


def test_gera_sql_sem_avisos(resolvedor):
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, avisos = gerar_sql(v, resolvedor)
    assert avisos == []
    assert "SELECT" in sql
    assert "FROM platform_dev.sap_s4_nc2_raw.bsas AS b" in sql


def test_create_view_sai_comentado(resolvedor):
    """O SQL gerado nunca pode conter um CREATE executável."""
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, _ = gerar_sql(v, resolvedor)
    for linha in sql.splitlines():
        if "CREATE" in linha.upper():
            assert linha.strip().startswith("--"), f"CREATE não comentado: {linha}"


def test_alias_consistente_entre_from_e_campos(resolvedor):
    """Regressão: quando o CDS omite 'AS alias', o FROM e os campos precisam usar
    o mesmo alias, senão o SQL não roda."""
    cds = "define view Z_S as select from bkpf { key belnr as Doc }"
    v = parse_cds("Z_S", cds)
    sql, _ = gerar_sql(v, resolvedor)
    assert "FROM platform_dev.sap_s4_nc2_raw.bkpf AS bkpf" in sql
    assert "bkpf.belnr AS Doc" in sql


def test_union_gera_dois_selects(resolvedor):
    v = parse_cds("Z_UNIAO", CDS_UNION)
    sql, _ = gerar_sql(v, resolvedor)
    assert sql.count("SELECT") == 2
    assert "UNION ALL" in sql


def test_sinais_abap_viram_avisos(resolvedor):
    v = parse_cds("Z_ABAP", CDS_ABAP)
    sql, avisos = gerar_sql(v, resolvedor)
    assert any("dats_" in a for a in avisos)
    assert "DECIMAL(15,2)" in sql  # cast abap.dec foi traduzido


def test_association_usada_vira_left_join(resolvedor):
    v = parse_cds("Z_COM_ASSOC", CDS_COM_ASSOC)
    sql, _ = gerar_sql(v, resolvedor)
    assert "LEFT JOIN" in sql
    assert "_Empresa" in sql


def test_view_truncada_nao_gera_sql(resolvedor):
    v = parse_cds("Z_CORTADA", CDS_TRUNCADO)
    sql, _ = gerar_sql(v, resolvedor)
    assert "Não foi possível gerar SQL" in sql


# ------------------------------------------------------------------ segurança


def test_leitura_permitida():
    assert_leitura("SELECT 1")
    assert_leitura("  with x as (select 1) select * from x")
    # REPLACE como função escalar não pode ser confundido com statement de escrita
    assert_leitura("SELECT LENGTH(REPLACE(corpo, '{', '')) FROM t")


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE OR REPLACE VIEW x AS SELECT 1",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "MERGE INTO t USING s ON t.a = s.a",
        "SELECT 1; DROP TABLE t",
        "",
    ],
)
def test_escrita_bloqueada(sql):
    with pytest.raises(SqlNaoPermitido):
        assert_leitura(sql)


def test_comentario_nao_burla_trava():
    """Um DROP escondido depois de um comentário continua sendo bloqueado."""
    with pytest.raises(SqlNaoPermitido):
        assert_leitura("-- select 1\nDROP TABLE t")


# ------------------------------------------------------------------ árvore


def test_arvore_marca_tabela_fisica(resolvedor):
    arv = montar_arvore("Z_BSAS", resolvedor.indice, resolvedor)
    assert "BSAS" in arv.tabelas_fisicas
    assert not arv.tem_problema
    assert "Z_BSAS" in arv.texto()


def test_arvore_nao_repete_no_compartilhado():
    """Memoização: uma dependência alcançada por dois caminhos só expande uma vez."""
    indice = {
        "RAIZ": "define view Raiz as select from FILHO_A as a { key a.x as X }",
        "FILHO_A": "define view FilhoA as select from COMUM as c { key c.x as X }",
        "COMUM": "define view Comum as select from t001 as t { key t.bukrs as X }",
    }
    resolvedor = ResolvedorNomes(indice, Config())
    arv = montar_arvore("RAIZ", indice, resolvedor)
    assert "COMUM" in arv.cds_visitadas


# ------------------------------------------------------------------ escritor


def test_nome_pasta_sanitiza():
    assert nome_pasta("/AIF/C_INTERFACESTATISTICS") == "AIF_C_INTERFACESTATISTICS"
    assert nome_pasta("i_address") == "I_ADDRESS"
    assert sanitizar("/DMBE/C_X") == "dmbe_c_x"


def test_salvar_cria_pasta_por_ddl(tmp_path, resolvedor):
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, avisos = gerar_sql(v, resolvedor)
    arv = montar_arvore("Z_BSAS", resolvedor.indice, resolvedor)

    art = salvar_artefatos(tmp_path, "Z_BSAS", sql, avisos, v, arv, gravar=True)

    assert art.gravado
    assert (tmp_path / "Z_BSAS" / "Z_BSAS.sql").exists()
    assert (tmp_path / "Z_BSAS" / "arvore.txt").exists()
    assert (tmp_path / "Z_BSAS" / "metadata.json").exists()
    # sem avisos -> não cria avisos.txt
    assert not (tmp_path / "Z_BSAS" / "avisos.txt").exists()

    meta = json.loads((tmp_path / "Z_BSAS" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["ddlname"] == "Z_BSAS"
    assert meta["n_campos"] == 3
    assert meta["tabelas_fisicas"] == ["BSAS"]


def test_dry_run_nao_escreve(tmp_path, resolvedor):
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, avisos = gerar_sql(v, resolvedor)
    art = salvar_artefatos(tmp_path, "Z_BSAS", sql, avisos, v, None, gravar=False)
    assert not art.gravado
    assert not (tmp_path / "Z_BSAS").exists()


def test_avisos_sao_gravados(tmp_path, resolvedor):
    v = parse_cds("Z_ABAP", CDS_ABAP)
    sql, avisos = gerar_sql(v, resolvedor)
    assert avisos
    salvar_artefatos(tmp_path, "Z_ABAP", sql, avisos, v, None, gravar=True)
    assert (tmp_path / "Z_ABAP" / "avisos.txt").exists()
