"""Monta o notebook `.ipynb` versionado (`view_<nome>_NNNNN.ipynb`) que alimenta a
esteira GitHub — um artefato por view, com o padrão de células observado em
`referencias/view_afko/view_afko_00001.py` (CREATE VIEW, comentário de tabela,
comentários de coluna, tags), adaptado pro que é derivável automaticamente do CDS
(Fluxo A/B, ver `fluxo.py`).

CI/esteira: confirmado pelo Allan que a pasta `ddl/`/esteira GitHub NÃO dispara
auto-deploy, nunca. Diferente do `.sql` solto em `ddl/<DDLNAME>/` (sempre comentado),
este notebook sai com `%sql CREATE OR REPLACE VIEW` DESCOMENTADO — é um artefato de
revisão/PR humana antes de rodar na esteira, não uma execução do Rosetta.
`escritor.salvar_notebook` mantém `gravar=False` como padrão da função (defesa em
profundidade), mas a tool MCP `gerar_view_completa` já grava direto de propósito.

Nada aqui fala com o Spark — só monta o dict do notebook.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List

from .fluxo import ConteudoView
from .modelos import ViewCds

_RX_CREATE_COMENTADO = re.compile(r"^-- (CREATE OR REPLACE VIEW .+ AS)$", re.MULTILINE)


def _descomentar_create(sql: str) -> str:
    """`gerador.gerar_sql` sempre comenta o `CREATE OR REPLACE VIEW` (projeto em fase de
    tradução, nunca de deploy — ver `gerador.py`). Este notebook é diferente: é o
    artefato de PR/revisão pra esteira, então a linha sai executável de propósito (as
    demais linhas de cabeçalho seguem como comentário informativo)."""
    return _RX_CREATE_COMENTADO.sub(r"\1", sql)

_METADATA_NOTEBOOK = {
    "application/vnd.databricks.v1+notebook": {
        "computePreferences": None,
        "dashboards": [],
        "environmentMetadata": {"base_environment": "", "environment_version": "2"},
        "inputWidgetPreferences": None,
        "language": "python",
        "notebookMetadata": {"pythonIndentUnit": 4},
        "notebookName": "",
        "widgets": {},
    },
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python", "version": "3.11"},
}

# Tags de negócio do exemplo AFKO (referencias/view_afko/view_afko_00001.py) — não são
# deriváveis do CDS nem do catálogo, ficam como placeholder pra preenchimento manual.
_TAGS_TEMPLATE = [
    "initiative", "data_criticality", "datahistory", "dataproduct",
    "ingestiontype", "openconsumption", "lastupdated", "frequency",
    "supportemail", "hypercare",
]


def _celula_markdown(source: str) -> Dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source}


def _celula_sql(sql_body: str) -> Dict[str, Any]:
    linhas = ["%sql\n"] + [f"{l}\n" for l in sql_body.splitlines()]
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": linhas}


def _bloco_pendencia(conteudo: ConteudoView) -> str:
    """Única função que sabe transformar `pendente_validacao`/`motivo_pendencia` em
    texto — chamada tanto pela célula markdown quanto pela SQL, pra nunca dessincronizar
    (ver ConteudoView, fonte única de verdade desse estado)."""
    if not conteudo.pendente_validacao:
        return ""
    motivos = "; ".join(conteudo.motivo_pendencia) or "motivo não especificado"
    return f"⚠️ PENDENTE_VALIDACAO: {motivos}"


def montar_notebook(view: ViewCds, conteudo: ConteudoView, status_classificacao: str) -> Dict[str, Any]:
    """Monta o dict nbformat4 (pronto pra `json.dumps`) do notebook da view."""
    # Nome de display do notebook (metadata) — NÃO usar em SQL: ALTER VIEW/COMMENT ON
    # COLUMN/SET TAGS precisam do FQN real de destino (conteudo.fqn_destino), senão
    # apontam pra um objeto que não existe.
    nome_notebook = f"view_{(view.nome_entidade or view.ddlname).strip().lower()}"
    fqn_destino = conteudo.fqn_destino or nome_notebook

    pendencia = _bloco_pendencia(conteudo)
    linhas_md = [f"# {view.ddlname}\n", "\n"]
    if pendencia:
        linhas_md += [f"## {pendencia}\n", "\n"]
    linhas_md += [
        f"- **Fluxo usado**: {conteudo.fluxo_usado}\n",
        f"- **Status de classificação**: {status_classificacao}\n",
    ]
    if conteudo.avisos:
        linhas_md.append(f"- **Avisos**: {len(conteudo.avisos)}\n")
        for a in conteudo.avisos:
            linhas_md.append(f"  - {a}\n")

    corpo_pendencia_sql = f"-- {pendencia}\n" if pendencia else ""

    # Fonte CDS original e SHOW CREATE TABLE de referência NÃO entram no notebook —
    # ele tem que sair igual aos notebooks de referência da esteira (só o SQL
    # executável + comentário de pendência), sem material de diagnóstico embutido.
    # O fragmento do fonte original continua disponível em ddl/<nome>/<nome>.sql e no
    # histórico da conversa que gerou a view, não precisa duplicar aqui.
    sql_create = f"{corpo_pendencia_sql}{_descomentar_create(conteudo.sql_create)}"

    comentario_tabela = conteudo.comentario_tabela or "TODO: descrição da tabela"

    linhas_comentario_coluna = []
    for campo in view.campos:
        nome_saida = (campo.alias or campo.expressao.split(".")[-1]).lower()
        texto = conteudo.comentarios_coluna.get(nome_saida, "TODO")
        linhas_comentario_coluna.append(
            f"COMMENT ON COLUMN {fqn_destino}.{nome_saida} IS '{texto}';"
        )
    sql_comentarios_coluna = "\n".join(linhas_comentario_coluna) or "-- nenhum campo identificado"

    sql_tags = "ALTER TABLE {tabela}\nSET TAGS\n    (\n" + ",\n".join(
        f"     '{t}' = 'TODO'" for t in _TAGS_TEMPLATE
    ) + "\n    );"
    sql_tags = sql_tags.format(tabela=fqn_destino)

    cells: List[Dict[str, Any]] = [
        _celula_markdown(linhas_md),
        _celula_sql(sql_create),
        _celula_sql(f"ALTER VIEW {fqn_destino} SET TBLPROPERTIES ('comment' = '{comentario_tabela}');"),
        _celula_sql(sql_comentarios_coluna),
        _celula_sql(sql_tags),
    ]

    metadata = deepcopy(_METADATA_NOTEBOOK)
    metadata["application/vnd.databricks.v1+notebook"]["notebookName"] = nome_notebook

    return {
        "cells": cells,
        "metadata": metadata,
        "nbformat": 4,
        "nbformat_minor": 0,
    }
