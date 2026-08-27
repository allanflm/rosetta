"""Testes do notebook gerado (`rosetta.notebook_writer`)."""
from __future__ import annotations

import json

from rosetta import ConteudoView, montar_notebook
from rosetta.parser_cds import parse_cds

CDS_SIMPLES = """
define view Z_Bsas
  as select from bsas as b
{
  key b.bukrs as CompanyCode,
      b.dmbtr as Amount
}
"""


def _view():
    return parse_cds("Z_BSAS", CDS_SIMPLES)


def test_notebook_e_json_serializavel_e_nbformat4():
    view = _view()
    conteudo = ConteudoView(fluxo_usado="B", sql_create="-- CREATE OR REPLACE VIEW x.y.z AS\nSELECT 1")
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    texto = json.dumps(nb, ensure_ascii=False)
    de_volta = json.loads(texto)
    assert de_volta["nbformat"] == 4
    assert de_volta["cells"][0]["cell_type"] == "markdown"
    assert any(c["cell_type"] == "code" for c in de_volta["cells"])


def test_create_view_sai_descomentado_no_notebook():
    """gerador.gerar_sql sempre comenta o CREATE (fase de tradução) — mas o notebook
    da esteira é o artefato de PR/revisão e precisa da linha executável."""
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="B",
        sql_create="-- Gerado a partir de Z_BSAS\n\n-- CREATE OR REPLACE VIEW platform_dev.sap_s4_nc2_replica.z_bsas AS\nSELECT 1",
    )
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    celula_sql = "".join(nb["cells"][1]["source"])
    assert "CREATE OR REPLACE VIEW platform_dev.sap_s4_nc2_replica.z_bsas AS" in celula_sql
    assert "-- CREATE OR REPLACE VIEW" not in celula_sql
    # cabeçalho informativo continua comentado
    assert "-- Gerado a partir de Z_BSAS" in celula_sql


def test_pendente_validacao_e_fonte_unica_aparece_em_sql_e_markdown():
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="B",
        sql_create="-- CREATE OR REPLACE VIEW x.y.z AS\nSELECT 1",
        pendente_validacao=True,
        motivo_pendencia=["sem view de referência para inferir padrão"],
    )
    nb = montar_notebook(view, conteudo, "TRUNCADO_CHAVE_ABERTA")

    markdown = "".join(nb["cells"][0]["source"])
    sql = "".join(nb["cells"][1]["source"])
    assert "PENDENTE_VALIDACAO" in markdown
    assert "sem view de referência para inferir padrão" in markdown
    assert "PENDENTE_VALIDACAO" in sql
    assert "sem view de referência para inferir padrão" in sql


def test_sem_pendencia_nao_aparece_marcador_em_lugar_nenhum():
    view = _view()
    conteudo = ConteudoView(fluxo_usado="A", sql_create="-- CREATE OR REPLACE VIEW x.y.z AS\nSELECT 1")
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    texto_completo = json.dumps(nb, ensure_ascii=False)
    assert "PENDENTE_VALIDACAO" not in texto_completo


def test_fonte_bruta_nunca_aparece_no_notebook():
    """O notebook tem que sair igual aos de referência da esteira (só SQL executável +
    comentário de pendência) — o fonte CDS original nunca é embutido nele, esteja a
    view pendente ou não. Fica só em ddl/<nome>/<nome>.sql / histórico da conversa."""
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="B",
        sql_create="-- ❌ Não foi possível gerar SQL (fonte truncado ou parse incompleto).",
        fonte_bruta="define view Z_Bsas as select from bsas as b {\n  key b.bukrs as CompanyC",
        pendente_validacao=True,
        motivo_pendencia=["fonte truncado/parse incompleto (status: TRUNCADO_CHAVE_ABERTA)"],
    )
    nb = montar_notebook(view, conteudo, "TRUNCADO_CHAVE_ABERTA")

    sql = "".join(nb["cells"][1]["source"])
    assert "Fonte CDS original" not in sql
    assert "define view Z_Bsas as select from bsas" not in sql


def test_show_create_referencia_nao_aparece_no_notebook():
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="A",
        sql_create="-- CREATE OR REPLACE VIEW x.y.z AS\nSELECT 1",
        show_create_referencia="CREATE VIEW x.y.z (\n  companycode STRING)",
    )
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    sql = "".join(nb["cells"][1]["source"])
    assert "SHOW CREATE TABLE" not in sql


def test_alter_view_e_comment_on_column_usam_fqn_destino_real():
    """Regressão: as células ALTER VIEW/COMMENT ON COLUMN/SET TAGS precisam apontar
    pro FQN real de destino, não pro nome de display 'view_<entidade>' do notebook —
    senão o SQL gerado referencia um objeto que não existe."""
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="B",
        sql_create="-- CREATE OR REPLACE VIEW platform_dev.sap_s4_nc2_replica.z_bsas AS\nSELECT 1",
        fqn_destino="platform_dev.sap_s4_nc2_replica.z_bsas",
    )
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    alter_view = "".join(nb["cells"][2]["source"])
    comment_col = "".join(nb["cells"][3]["source"])
    tags = "".join(nb["cells"][4]["source"])
    assert "ALTER VIEW platform_dev.sap_s4_nc2_replica.z_bsas SET TBLPROPERTIES" in alter_view
    assert "COMMENT ON COLUMN platform_dev.sap_s4_nc2_replica.z_bsas." in comment_col
    assert "ALTER TABLE platform_dev.sap_s4_nc2_replica.z_bsas" in tags
    assert "view_z_bsas." not in comment_col
    assert "ALTER VIEW view_z_bsas " not in alter_view


def test_comentarios_coluna_usam_fluxo_a_ou_ficam_todo():
    view = _view()
    conteudo = ConteudoView(
        fluxo_usado="A",
        sql_create="-- CREATE OR REPLACE VIEW x.y.z AS\nSELECT 1",
        comentarios_coluna={"companycode": "Empresa"},
    )
    nb = montar_notebook(view, conteudo, "COMPLETA_FECHA_CHAVE")

    celula_colunas = "".join(nb["cells"][3]["source"])
    assert "Empresa" in celula_colunas
    assert "TODO" in celula_colunas  # 'amount' não tem comentário -> fica TODO
