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
    Contexto,
    ResolvedorNomes,
    SqlNaoPermitido,
    assert_leitura,
    gerar_sql,
    montar_arvore,
    nome_arquivo_notebook,
    nome_pasta,
    parse_cds,
    proxima_versao,
    salvar_artefatos,
    salvar_notebook,
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
    assert nome_pasta("/AIF/C_INTERFACESTATISTICS") == "view_aif_c_interfacestatistics"
    assert nome_pasta("i_address") == "view_i_address"
    assert sanitizar("/DMBE/C_X") == "dmbe_c_x"


def test_salvar_cria_pasta_por_ddl(tmp_path, resolvedor):
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, avisos = gerar_sql(v, resolvedor)
    arv = montar_arvore("Z_BSAS", resolvedor.indice, resolvedor)

    art = salvar_artefatos(tmp_path, "Z_BSAS", sql, avisos, v, arv, gravar=True)

    assert art.gravado
    assert (tmp_path / "view_z_bsas" / "z_bsas.sql").exists()
    assert (tmp_path / "view_z_bsas" / "arvore.txt").exists()
    assert (tmp_path / "view_z_bsas" / "metadata.json").exists()
    # sem avisos -> não cria avisos.txt
    assert not (tmp_path / "view_z_bsas" / "avisos.txt").exists()

    meta = json.loads((tmp_path / "view_z_bsas" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["ddlname"] == "Z_BSAS"
    assert meta["n_campos"] == 3
    assert meta["tabelas_fisicas"] == ["BSAS"]


def test_dry_run_nao_escreve(tmp_path, resolvedor):
    v = parse_cds("Z_BSAS", CDS_SIMPLES)
    sql, avisos = gerar_sql(v, resolvedor)
    art = salvar_artefatos(tmp_path, "Z_BSAS", sql, avisos, v, None, gravar=False)
    assert not art.gravado
    assert not (tmp_path / "view_z_bsas").exists()


def test_avisos_sao_gravados(tmp_path, resolvedor):
    v = parse_cds("Z_ABAP", CDS_ABAP)
    sql, avisos = gerar_sql(v, resolvedor)
    assert avisos
    salvar_artefatos(tmp_path, "Z_ABAP", sql, avisos, v, None, gravar=True)
    assert (tmp_path / "view_z_abap" / "avisos.txt").exists()


def test_nome_arquivo_notebook_e_versionado():
    assert nome_arquivo_notebook("Z_BSAS", 1) == "view_z_bsas_00001.ipynb"
    assert nome_arquivo_notebook("Z_BSAS", 12) == "view_z_bsas_00012.ipynb"


def test_proxima_versao_comeca_em_1_sem_notebook_existente(tmp_path):
    assert proxima_versao(tmp_path / "nao_existe", "Z_BSAS") == 1


def test_proxima_versao_incrementa_sobre_existentes(tmp_path):
    pasta = tmp_path / "view_z_bsas"
    pasta.mkdir()
    (pasta / "view_z_bsas_00001.ipynb").write_text("{}", encoding="utf-8")
    (pasta / "view_z_bsas_00002.ipynb").write_text("{}", encoding="utf-8")

    assert proxima_versao(pasta, "Z_BSAS") == 3


def test_salvar_notebook_gravar_false_nao_escreve(tmp_path):
    caminho = salvar_notebook(tmp_path, "Z_BSAS", {"cells": []}, gravar=False)
    assert caminho.name == "view_z_bsas_00001.ipynb"
    assert not caminho.exists()


def test_salvar_notebook_gravar_true_nao_sobrescreve_versao_anterior(tmp_path):
    p1 = salvar_notebook(tmp_path, "Z_BSAS", {"cells": ["v1"]}, gravar=True)
    p2 = salvar_notebook(tmp_path, "Z_BSAS", {"cells": ["v2"]}, gravar=True)

    assert p1.name == "view_z_bsas_00001.ipynb"
    assert p2.name == "view_z_bsas_00002.ipynb"
    assert json.loads(p1.read_text(encoding="utf-8"))["cells"] == ["v1"]
    assert json.loads(p2.read_text(encoding="utf-8"))["cells"] == ["v2"]


# --------------------------------------------------------- Contexto.gerar_view_notebook


def test_gerar_view_notebook_sem_spark_forca_fluxo_b(tmp_path):
    indice = {"Z_BSAS": CDS_SIMPLES}
    ctx = Contexto(spark=None, cfg=Config(), indice=indice, raiz_ddl=tmp_path, verboso=False)

    res = ctx.gerar_view_notebook("Z_BSAS", spark=None, gravar_notebook=True)

    assert res.conteudo.fluxo_usado == "B"
    assert not res.conteudo.pendente_validacao
    assert res.caminho_notebook.exists()
    assert res.artefatos.pasta == tmp_path / "view_z_bsas"

    meta = json.loads((tmp_path / "view_z_bsas" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["pendente_validacao"] is False
    assert meta["notebook"] == "view_z_bsas/view_z_bsas_00001.ipynb"


def test_gerar_view_notebook_levanta_erro_para_ddlname_desconhecido(tmp_path):
    ctx = Contexto(spark=None, cfg=Config(), indice={}, raiz_ddl=tmp_path, verboso=False)
    with pytest.raises(ValueError):
        ctx.gerar_view_notebook("NAO_EXISTE")


def test_gerar_view_notebook_marca_pendente_quando_fonte_trunca(tmp_path):
    """Fonte truncada = pendente_validacao automático, mesmo sem passar pelo
    workflow de completar truncadas (regressão do teste manual com I_ADDRESS real,
    que saiu com pendente_validacao=false apesar do SQL não ter sido gerado)."""
    fonte_truncada = "define view I_Address as select from vbap as a {\n  key a.vbeln as Sale"
    indice = {"I_ADDRESS": fonte_truncada}
    ctx = Contexto(spark=None, cfg=Config(), indice=indice, raiz_ddl=tmp_path, verboso=False)

    res = ctx.gerar_view_notebook("I_ADDRESS", spark=None, gravar_notebook=True)

    assert not res.view.parseou
    assert res.conteudo.pendente_validacao is True
    assert res.conteudo.motivo_pendencia
    assert "truncado" in res.conteudo.motivo_pendencia[0]

    meta = json.loads((tmp_path / "view_i_address" / "metadata.json").read_text(encoding="utf-8"))
    assert meta["pendente_validacao"] is True

    nb_texto = (tmp_path / "view_i_address" / "view_i_address_00001.ipynb").read_text(encoding="utf-8")
    assert "PENDENTE_VALIDACAO" in nb_texto
