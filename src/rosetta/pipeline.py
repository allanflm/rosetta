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
from .escritor import Artefatos, salvar_artefatos
from .gerador import gerar_sql
from .indice import carregar_indice
from .modelos import ViewCds
from .nomes import ResolvedorNomes
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
