"""Fachada de alto nível — é isto que o notebook usa.

O notebook não deve conhecer parser, resolvedor nem escritor separadamente: ele
monta um `Contexto` uma vez e chama `ctx.traduzir("BSAS_DDL")`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set

from .arvore import Arvore, montar_arvore
from .config import Config
from .escritor import Artefatos, nome_pasta, salvar_artefatos, salvar_notebook
from .fluxo import ConteudoView, decidir_fluxo, montar_conteudo_fluxo_a, montar_conteudo_fluxo_b
from .gerador import gerar_sql
from .indice import carregar_indice
from .localizador import classificar_exato, varrer
from .modelos import ViewCds
from .nomes import ResolvedorNomes
from .notebook_writer import montar_notebook
from .parser_cds import parse_cds


@dataclass
class Resultado:
    ddlname: str
    encontrada: bool
    view: ViewCds
    arvore: Optional[Arvore]
    sql: str
    avisos: List[str]
    artefatos: Optional[Artefatos] = None

    @property
    def gerou(self) -> bool:
        return self.encontrada and self.view.parseou

    @property
    def garantida(self) -> bool:
        """Gerou SQL e sem nenhum aviso — tradução direta, sem revisão manual."""
        return self.gerou and not self.avisos

    def selo(self) -> str:
        if not self.encontrada:
            return f"❓ {self.ddlname} não existe na DDDDLSRC"
        if not self.gerou:
            return f"❌ {self.ddlname}: não foi possível gerar SQL (fonte truncado ou parse incompleto)"
        if self.avisos:
            return f"⚠️  {self.ddlname}: SQL gerado, mas com {len(self.avisos)} aviso(s) para revisar"
        return f"✅ {self.ddlname}: SQL gerado sem ressalvas"


class Contexto:
    """Estado carregado uma vez por sessão: config, índice de fontes e resolvedor."""

    def __init__(
        self,
        spark,
        cfg: Config,
        indice: Optional[Dict[str, str]] = None,
        raiz_ddl: Optional[Path] = None,
        truncados_conhecidos: Optional[Set[str]] = None,
        verboso: bool = True,
    ):
        self.spark = spark
        self.cfg = cfg
        self.indice = indice if indice is not None else carregar_indice(spark, cfg, verboso=verboso)
        self.resolvedor = ResolvedorNomes(self.indice, cfg)
        self.raiz_ddl = Path(raiz_ddl) if raiz_ddl else None
        self.truncados_conhecidos = truncados_conhecidos

    def existe(self, ddlname: str) -> bool:
        return (ddlname or "").strip().upper() in self.indice

    def source(self, ddlname: str) -> Optional[str]:
        return self.indice.get((ddlname or "").strip().upper())

    def traduzir(
        self,
        ddlname: str,
        seguir_assoc: bool = False,
        max_profundidade: int = 15,
        max_nos: int = 4000,
        com_arvore: bool = True,
        gravar: bool = False,
        raiz_ddl: Optional[Path] = None,
    ) -> Resultado:
        """Parse + árvore + SQL (+ gravação opcional em ddl/<DDLNAME>/)."""
        chave = (ddlname or "").strip().upper()
        src = self.indice.get(chave)

        if src is None:
            vazia = ViewCds(ddlname=chave, truncado=True)
            vazia.avisos.append("ddlname não encontrado na DDDDLSRC.")
            return Resultado(
                ddlname=chave, encontrada=False, view=vazia, arvore=None,
                sql="-- ❓ ddlname não encontrado.", avisos=list(vazia.avisos),
            )

        view = parse_cds(chave, src)

        arv = None
        if com_arvore:
            arv = montar_arvore(
                chave,
                indice=self.indice,
                resolvedor=self.resolvedor,
                seguir_assoc=seguir_assoc,
                max_profundidade=max_profundidade,
                max_nos=max_nos,
                truncados_conhecidos=self.truncados_conhecidos,
            )

        sql, avisos = gerar_sql(view, self.resolvedor)

        res = Resultado(
            ddlname=chave, encontrada=True, view=view, arvore=arv, sql=sql, avisos=avisos
        )

        destino = Path(raiz_ddl) if raiz_ddl else self.raiz_ddl
        if destino is not None:
            res.artefatos = salvar_artefatos(
                raiz_ddl=destino,
                ddlname=chave,
                sql=sql,
                avisos=avisos,
                view=view,
                arvore=arv,
                gravar=gravar,
            )
        return res

    def classe_status(self, ddlname: str) -> str:
        """Classificação exata (`localizador.classificar_exato`) do fonte, sem passar
        pelo pré-filtro rápido do inventário — mesma lógica usada pela tool MCP
        `classificar_status`. Devolve `""` se o ddlname não estiver no índice."""
        src = self.source(ddlname)
        if src is None:
            return ""
        from .config import MARCA_META

        pos_meta = src.find(MARCA_META)
        corpo = src[:pos_meta] if pos_meta >= 0 else src
        limpo, estado, trecho = varrer(corpo)
        return classificar_exato(limpo, estado, trecho)

    def gerar_view_notebook(
        self,
        ddlname: str,
        spark=None,
        idioma: str = "P",
        raiz_ddl: Optional[Path] = None,
        gravar_notebook: bool = False,
        gravar_artefatos_texto: bool = True,
    ) -> "ResultadoNotebook":
        """Fluxo A/B completo: parse → decide Fluxo A/B (`fluxo.decidir_fluxo`, requer
        `spark` pra checar existência no destino) → monta `ConteudoView` → monta o
        notebook (`notebook_writer.montar_notebook`) → grava via `escritor.py`.

        `spark=None` pula a checagem de existência e força Fluxo B (sem catálogo pra
        consultar, não tem como confirmar Fluxo A) — útil pra rodar fora do Databricks.
        `gravar_notebook=False` por padrão (ver docstring de `notebook_writer.py`: ainda
        não está confirmado se a esteira dispara auto-deploy ao ver um `.ipynb` novo).
        """
        chave = (ddlname or "").strip().upper()
        src = self.indice.get(chave)
        if src is None:
            raise ValueError(f"'{chave}' não encontrado na DDDDLSRC/índice.")

        view = parse_cds(chave, src)
        sql, avisos_geracao = gerar_sql(view, self.resolvedor)
        status = self.classe_status(chave)

        arv = montar_arvore(
            chave,
            indice=self.indice,
            resolvedor=self.resolvedor,
            truncados_conhecidos=self.truncados_conhecidos,
        )

        if spark is None:
            fluxo_usado, avisos_decisao = "B", []
        else:
            fluxo_usado, avisos_decisao = decidir_fluxo(spark, self.cfg, view, self.resolvedor)

        if fluxo_usado == "A":
            conteudo = montar_conteudo_fluxo_a(
                spark, self.cfg, view, self.resolvedor, sql, idioma=idioma, fonte_bruta=src,
            )
        else:
            conteudo = montar_conteudo_fluxo_b(view, self.resolvedor, sql, fonte_bruta=src)
        conteudo.avisos = avisos_decisao + conteudo.avisos

        # Fonte truncada/parse incompleto = pendente por padrão, mesmo que ninguém
        # tenha rodado `.agents/workflows/completar-view-truncada.md` ainda. Sem isso,
        # uma view quebrada sairia com `pendente_validacao: false` (parece OK e não
        # está) só porque a tentativa de completar é manual/opcional.
        if not view.parseou and not conteudo.pendente_validacao:
            conteudo.pendente_validacao = True
            conteudo.motivo_pendencia = conteudo.motivo_pendencia + [
                f"fonte truncado/parse incompleto (status: {status or 'desconhecido'}) — "
                f"SQL não gerado, revisão manual necessária. Ver "
                f".agents/workflows/completar-view-truncada.md."
            ]

        notebook_dict = montar_notebook(view, conteudo, status)

        destino = Path(raiz_ddl) if raiz_ddl else self.raiz_ddl
        artefatos = None
        caminho_notebook = None
        if destino is not None:
            caminho_notebook = salvar_notebook(
                raiz_ddl=destino, ddlname=chave, notebook_dict=notebook_dict, gravar=gravar_notebook,
            )
            artefatos = salvar_artefatos(
                raiz_ddl=destino,
                ddlname=chave,
                sql=sql,
                avisos=avisos_geracao + conteudo.avisos,
                view=view,
                arvore=arv,
                gravar=gravar_artefatos_texto,
                pendente_validacao=conteudo.pendente_validacao,
                motivo_pendencia=conteudo.motivo_pendencia,
                notebook_relpath=f"{nome_pasta(chave)}/{caminho_notebook.name}",
            )

        return ResultadoNotebook(
            ddlname=chave,
            view=view,
            conteudo=conteudo,
            status_classificacao=status,
            notebook=notebook_dict,
            caminho_notebook=caminho_notebook,
            artefatos=artefatos,
        )


@dataclass
class ResultadoNotebook:
    ddlname: str
    view: ViewCds
    conteudo: ConteudoView
    status_classificacao: str
    notebook: dict
    caminho_notebook: Optional[Path] = None
    artefatos: Optional[Artefatos] = None
