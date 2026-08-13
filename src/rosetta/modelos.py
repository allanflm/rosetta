"""Estruturas de dados do parser CDS."""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import List, Optional


@dataclass
class Campo:
    bruto: str
    expressao: str
    alias: Optional[str] = None
    is_key: bool = False


@dataclass
class Associacao:
    cardinalidade: str
    alvo: str
    alias: str
    condicao: str


@dataclass
class JoinBruto:
    """JOIN de SQL puro (estilo clássico) — diferente de Associacao: a condicao já
    referencia aliases reais de tabela (ex.: 'a.rbukrs = v.bukrs'), não precisa
    passar por resolver_projection() como as associations do estilo entidade."""

    tipo: str
    tabela: str
    alias: str
    condicao: str


@dataclass
class ViewCds:
    ddlname: str = ""
    nome_entidade: str = ""
    sql_view_name: Optional[str] = None
    label: Optional[str] = None
    tipo: str = "view"
    # "entidade" (as select from x {campos}) ou "classico" (select campos from x join...)
    estilo: str = "entidade"
    entidade_base: str = ""
    alias_base: Optional[str] = None
    campos: List[Campo] = dc_field(default_factory=list)
    associacoes: List[Associacao] = dc_field(default_factory=list)
    joins: List[JoinBruto] = dc_field(default_factory=list)
    where: Optional[str] = None
    extras: Optional[str] = None
    blocos_uniao: List[dict] = dc_field(default_factory=list)
    avisos: List[str] = dc_field(default_factory=list)
    truncado: bool = False

    @property
    def parseou(self) -> bool:
        """True quando o parser conseguiu montar algo traduzível de fato."""
        return bool(self.entidade_base) and bool(self.campos) and not self.truncado
