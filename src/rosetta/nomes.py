"""Resolução de nomes: entidade CDS/tabela SAP → nome físico no Databricks.

Antes isso era uma função solta que lia `INDICE_DDL`, `FQN_TARGET` e `FQN_RAW` de
`globals()`. Agora é um objeto que carrega suas próprias dependências, o que
permite testar a tradução sem Spark e sem notebook.
"""
from __future__ import annotations

from typing import Dict

from .config import Config


def sanitizar(nome: str) -> str:
    """/AIF/C_INTERFACESTATISTICS → aif_c_interfacestatistics"""
    return (nome or "").strip().strip("/").replace("/", "_").lower()


class ResolvedorNomes:
    def __init__(self, indice: Dict[str, str], cfg: Config):
        self._indice = indice
        self._cfg = cfg

    def eh_cds(self, nome: str) -> bool:
        return (nome or "").strip().upper() in self._indice

    def fisico(self, nome: str) -> str:
        """CDS view → schema de destino; tabela SAP → schema raw."""
        limpo = sanitizar(nome)
        if self.eh_cds(nome):
            return f"{self._cfg.fqn_target}.{limpo}"
        return f"{self._cfg.fqn_raw}.{limpo}"

    @property
    def cfg(self) -> Config:
        return self._cfg

    @property
    def indice(self) -> Dict[str, str]:
        return self._indice
