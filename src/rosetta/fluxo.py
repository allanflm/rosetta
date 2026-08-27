"""Decisão Fluxo A vs Fluxo B na geração da view final.

Fluxo A: a view/tabela já existe no destino (`cfg.fqn_target`) — reaproveita
`SHOW CREATE TABLE` + metadados DD03L/DD04T, cruzando com o SQL gerado a partir do
CDS. Fluxo B: base não existe ainda — gera só a partir do parse do CDS, sem
cruzamento externo (comentários/tags ficam como placeholder `TODO`).

Esta decisão não é responsabilidade do parser (`parser_cds.py`), do gerador de SQL
(`gerador.py`) nem do classificador (`localizador.py`) — é uma etapa de orquestração
nova entre eles, por isso mora em módulo próprio.

Nada aqui escreve no catálogo: `spark.catalog.tableExists` e `SHOW CREATE TABLE` são
somente-leitura (a segunda já passa por `seguranca.sql_leitura` dentro de
`insumos_gemini.buscar_show_create_table`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from .config import Config
from .gerador import nome_view_destino
from .insumos_gemini import buscar_metadados_campos, buscar_show_create_table
from .modelos import ViewCds
from .nomes import ResolvedorNomes

FLUXO_A = "A"
FLUXO_B = "B"


@dataclass
class ConteudoView:
    """Resultado de montar o conteúdo de uma view pelo Fluxo A ou B — o que o
    notebook_writer.py usa pra preencher as células. `pendente_validacao` +
    `motivo_pendencia` são a FONTE ÚNICA DE VERDADE desse estado: nada mais (SQL,
    markdown) escreve esse sinal de forma independente, tudo é derivado destes campos."""

    fluxo_usado: str
    sql_create: str
    fqn_destino: str = ""  # FQN real da view (gerador.nome_view_destino) — usado por ALTER VIEW/COMMENT ON COLUMN/SET TAGS no notebook
    comentario_tabela: str = ""  # texto de negócio p/ TBLPROPERTIES('comment'=...); não derivável, fica "" (=TODO) nos dois fluxos
    comentarios_coluna: Dict[str, str] = field(default_factory=dict)
    show_create_referencia: str = ""  # SHOW CREATE TABLE do destino (Fluxo A) — só informativo, não vai pro TBLPROPERTIES
    fonte_bruta: str = ""  # source CDS original (o que sobrou, mesmo truncado) — material pro agente iterar/completar
    avisos: List[str] = field(default_factory=list)
    pendente_validacao: bool = False
    motivo_pendencia: List[str] = field(default_factory=list)


def decidir_fluxo(spark, cfg: Config, view: ViewCds, resolvedor: ResolvedorNomes) -> Tuple[str, List[str]]:
    """Decide Fluxo A ou B checando `spark.catalog.tableExists` contra o destino.

    Devolve (fluxo, avisos). Distingue dois casos que NÃO podem virar o mesmo aviso:

    - `tableExists` responde `False` normalmente → Fluxo B legítimo, sem aviso (é o
      caminho normal, não um erro).
    - `tableExists` lança exceção (permissão, conexão, catálogo indisponível) → cai em
      "B" por segurança (nunca assume Fluxo A sem confirmar a existência), mas o aviso
      devolvido é destacado e carrega o motivo, pra não ser confundido com uma checagem
      normal que deu false.
    """
    fqn = nome_view_destino(view, resolvedor)
    try:
        existe = bool(spark.catalog.tableExists(fqn))
    except Exception as e:  # noqa: BLE001 — qualquer falha de checagem é defensiva, não fatal
        aviso = (
            f"ATENÇÃO: não foi possível verificar existência da tabela em '{fqn}' "
            f"({type(e).__name__}: {str(e)[:200]}) — assumindo Fluxo B por padrão, "
            f"resultado pode estar incompleto."
        )
        return FLUXO_B, [aviso]

    return (FLUXO_A if existe else FLUXO_B), []


def montar_conteudo_fluxo_b(
    view: ViewCds, resolvedor: ResolvedorNomes, sql_gerado: str, fonte_bruta: str = ""
) -> ConteudoView:
    """Sem base existente: só o que o parse do CDS já entrega. Comentários/tags ficam
    como placeholder — não têm de onde vir.

    `fonte_bruta`: passa o source CDS original (mesmo truncado/sem chave) — o
    notebook sempre é gerado, nunca bloqueado por parse incompleto; o fragmento vira
    material de referência pro agente (ver `.agents/workflows/completar-view-truncada.md`)
    tentar completar iterativamente."""
    return ConteudoView(
        fluxo_usado=FLUXO_B,
        sql_create=sql_gerado,
        fqn_destino=nome_view_destino(view, resolvedor),
        comentario_tabela="",
        comentarios_coluna={},
        fonte_bruta=fonte_bruta,
        avisos=[],
    )


def montar_conteudo_fluxo_a(
    spark,
    cfg: Config,
    view: ViewCds,
    resolvedor: ResolvedorNomes,
    sql_gerado: str,
    idioma: str = "P",
    fonte_bruta: str = "",
) -> ConteudoView:
    """Base já existe no destino: cruza SHOW CREATE TABLE + DD03L/DD04T com o CDS.

    Erros ao buscar SHOW CREATE TABLE / metadados não derrubam a geração — viram
    aviso e o Fluxo A segue com o que conseguiu (mesmo padrão defensivo do resto do
    projeto: nunca propagar exceção pra fora do pipeline de geração)."""
    avisos: List[str] = []
    fqn_destino = nome_view_destino(view, resolvedor)

    try:
        show_create = buscar_show_create_table(spark, cfg, fqn=fqn_destino)
    except Exception as e:  # noqa: BLE001
        show_create = ""
        avisos.append(f"Não foi possível obter SHOW CREATE TABLE de '{fqn_destino}': {str(e)[:200]}")

    comentarios_coluna: Dict[str, str] = {}
    try:
        tabname = (view.sql_view_name or view.nome_entidade or view.ddlname).strip().upper()
        linhas = buscar_metadados_campos(spark, cfg, [tabname], idioma=idioma)
        for r in linhas:
            campo = (r.get("fieldname") or "").strip().lower()
            texto = (r.get("ddtext") or "").strip()
            if campo and texto:
                comentarios_coluna[campo] = texto
        if not linhas:
            avisos.append(
                f"Fluxo A: nenhum metadado DD03L/DD04T encontrado para '{tabname}' — "
                f"comentários de coluna ficam como TODO."
            )
    except Exception as e:  # noqa: BLE001
        avisos.append(f"Não foi possível obter metadados DD03L/DD04T: {str(e)[:200]}")

    return ConteudoView(
        fluxo_usado=FLUXO_A,
        sql_create=sql_gerado,
        fqn_destino=fqn_destino,
        comentario_tabela="",  # descrição de negócio não é derivável do SHOW CREATE TABLE — fica TODO
        comentarios_coluna=comentarios_coluna,
        show_create_referencia=show_create,
        fonte_bruta=fonte_bruta,
        avisos=avisos,
    )
