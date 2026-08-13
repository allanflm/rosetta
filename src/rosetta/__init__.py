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
from .config import MARCA_META, Config
from .escritor import Artefatos, nome_pasta, salvar_artefatos
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
)
from .inventario import Certeza, certeza_real
from .modelos import Associacao, Campo, JoinBruto, ViewCds
from .nomes import ResolvedorNomes, sanitizar
from .parser_cds import parse_cds
from .pipeline import Contexto, Resultado
from .seguranca import SqlNaoPermitido, assert_leitura, sql_leitura
from .traducao import resolver_projection, traduzir

__version__ = "0.1.0"

__all__ = [
    "Arvore", "Artefatos", "Associacao", "CadeiaDdl", "Campo", "Certeza", "Config",
    "Contexto", "JoinBruto", "MARCA_META", "PacoteGemini", "ResolvedorNomes",
    "Resultado", "SqlNaoPermitido", "ViewCds", "assert_leitura",
    "buscar_metadados_campos", "buscar_show_create_table", "buscar_source",
    "carregar_indice", "certeza_real", "formatar_metadados", "gerar_sql",
    "montar_arvore", "montar_cadeia_ddl", "montar_pacote", "nome_pasta",
    "parse_cds", "resolver_projection", "salvar_artefatos", "salvar_pacote",
    "sanitizar", "sql_leitura", "traduzir", "__version__",
]
