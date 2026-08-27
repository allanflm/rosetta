"""Testes da decisão Fluxo A/B (`rosetta.fluxo`)."""
from __future__ import annotations

from unittest.mock import MagicMock

from rosetta import Config, ResolvedorNomes, decidir_fluxo, montar_conteudo_fluxo_a, montar_conteudo_fluxo_b
from rosetta.parser_cds import parse_cds

CDS_SIMPLES = """
define view Z_Bsas
  as select from bsas as b
{
  key b.bukrs as CompanyCode,
      b.dmbtr as Amount
}
"""


def _view_e_resolvedor():
    indice = {"Z_BSAS": CDS_SIMPLES}
    cfg = Config()
    resolvedor = ResolvedorNomes(indice, cfg)
    view = parse_cds("Z_BSAS", CDS_SIMPLES)
    return cfg, view, resolvedor


def test_decidir_fluxo_b_quando_tabela_nao_existe():
    cfg, view, resolvedor = _view_e_resolvedor()
    spark = MagicMock()
    spark.catalog.tableExists.return_value = False

    fluxo, avisos = decidir_fluxo(spark, cfg, view, resolvedor)

    assert fluxo == "B"
    assert avisos == []  # False normal não é erro, não gera aviso


def test_decidir_fluxo_a_quando_tabela_existe():
    cfg, view, resolvedor = _view_e_resolvedor()
    spark = MagicMock()
    spark.catalog.tableExists.return_value = True

    fluxo, avisos = decidir_fluxo(spark, cfg, view, resolvedor)

    assert fluxo == "A"
    assert avisos == []


def test_decidir_fluxo_erro_de_checagem_vira_b_com_aviso_destacado():
    """Erro de acesso/conexão != tabela não existe: cai em B por segurança, mas o
    aviso precisa deixar isso bem claro (não pode parecer uma checagem normal)."""
    cfg, view, resolvedor = _view_e_resolvedor()
    spark = MagicMock()
    spark.catalog.tableExists.side_effect = PermissionError("sem acesso ao catalogo")

    fluxo, avisos = decidir_fluxo(spark, cfg, view, resolvedor)

    assert fluxo == "B"
    assert len(avisos) == 1
    assert "ATENÇÃO" in avisos[0]
    assert "não foi possível verificar existência" in avisos[0]
    assert "assumindo Fluxo B por padrão" in avisos[0]


def test_montar_conteudo_fluxo_b_sem_cruzamento_externo():
    _, view, resolvedor = _view_e_resolvedor()
    conteudo = montar_conteudo_fluxo_b(view, resolvedor, "SELECT 1")

    assert conteudo.fluxo_usado == "B"
    assert conteudo.comentarios_coluna == {}
    assert conteudo.comentario_tabela == ""
    assert conteudo.fqn_destino == "platform_dev.sap_s4_nc2_replica.z_bsas"
    assert not conteudo.pendente_validacao


def test_montar_conteudo_fluxo_a_usa_show_create_table_e_metadados():
    cfg, view, resolvedor = _view_e_resolvedor()
    spark = MagicMock()
    spark.sql.return_value.collect.return_value = [
        MagicMock(asDict=lambda: {"col": "CREATE TABLE x (bukrs STRING)"})
    ]

    import rosetta.fluxo as fluxo_mod

    fluxo_mod.buscar_show_create_table = lambda *a, **kw: "CREATE TABLE x (bukrs STRING)"
    fluxo_mod.buscar_metadados_campos = lambda *a, **kw: [
        {"fieldname": "bukrs", "ddtext": "Empresa"}
    ]

    conteudo = montar_conteudo_fluxo_a(spark, cfg, view, resolvedor, "SELECT 1")

    assert conteudo.fluxo_usado == "A"
    assert conteudo.show_create_referencia == "CREATE TABLE x (bukrs STRING)"
    assert conteudo.comentarios_coluna == {"bukrs": "Empresa"}
    assert conteudo.comentario_tabela == ""  # descrição de negócio não é derivável
