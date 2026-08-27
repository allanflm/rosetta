"""Rosetta — tradutor de CDS views SAP S/4HANA para SQL Databricks.

Uso típico dentro de um notebook:

    from rosetta import Config, Contexto
    cfg = Config.de_widgets(dbutils)
    ctx = Contexto(spark, cfg, raiz_ddl=RAIZ / "ddl")
    res = ctx.traduzir("BSAS_DDL", gravar=True)
    print(res.arvore.texto())
    print(res.sql)

Nada neste pacote escreve no catálogo do Databricks. A trava está em
`rosetta.seguranca`, por onde passa toda consulta ao Spark.
"""
from .arvore import Arvore, montar_arvore
from .avisos_fonte import AvisoFonte, classificar_cadeia, resumo_avisos
from .config import MARCA_META, Config
from .escritor import (
    Artefatos,
    nome_arquivo_notebook,
    nome_pasta,
    proxima_versao,
    salvar_artefatos,
    salvar_notebook,
)
from .fluxo import ConteudoView, decidir_fluxo, montar_conteudo_fluxo_a, montar_conteudo_fluxo_b
from .gerador import gerar_sql
from .indice import buscar_source, carregar_indice
from .insumos_gemini import (
    CadeiaDdl,
    PacoteGemini,
    buscar_metadados_campos,
    buscar_show_create_table,
    formatar_metadados,
    montar_cadeia_ddl,
    montar_pacote,
    salvar_pacote,
    sql_view_names_da_cadeia,
)
from .modelos import Associacao, Campo, JoinBruto, ViewCds
from .nomes import ResolvedorNomes, sanitizar
from .notebook_writer import montar_notebook
from .parser_cds import parse_cds
from .pipeline import Contexto, Resultado, ResultadoNotebook
from .seguranca import SqlNaoPermitido, assert_leitura, sql_leitura
from .traducao import resolver_projection, traduzir

__version__ = "0.1.0"

__all__ = [
    "Arvore", "Artefatos", "Associacao", "AvisoFonte", "CadeiaDdl", "Campo",
    "Config", "ConteudoView", "Contexto", "JoinBruto", "MARCA_META", "PacoteGemini",
    "ResolvedorNomes", "Resultado", "ResultadoNotebook", "SqlNaoPermitido", "ViewCds",
    "assert_leitura", "buscar_metadados_campos", "buscar_show_create_table",
    "buscar_source", "carregar_indice", "classificar_cadeia",
    "decidir_fluxo", "formatar_metadados", "gerar_sql", "montar_arvore",
    "montar_cadeia_ddl", "montar_conteudo_fluxo_a", "montar_conteudo_fluxo_b",
    "montar_notebook", "montar_pacote", "nome_arquivo_notebook", "nome_pasta",
    "parse_cds", "proxima_versao", "resolver_projection", "resumo_avisos",
    "salvar_artefatos", "salvar_notebook", "salvar_pacote", "sanitizar", "sql_leitura",
    "sql_view_names_da_cadeia", "traduzir", "__version__",
]
