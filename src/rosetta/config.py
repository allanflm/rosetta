"""Configuração central do Rosetta.

Tudo que era global espalhado pelos notebooks (CATALOG_RAW, SCHEMA_RAW, FQN_*)
vive aqui em um objeto único, passado explicitamente para as funções. Nenhum
módulo lê `globals()` — isso era a maior fonte de acoplamento entre os notebooks.
"""
from __future__ import annotations

from dataclasses import dataclass

MARCA_META = "/*+[internal]"


@dataclass(frozen=True)
class Config:
    catalog_raw: str = "platform_dev"
    schema_raw: str = "sap_s4_nc2_raw"
    tabela_ddl: str = "tab_ddddlsrc"
    tabela_dep: str = "ddldependency"
    tabela_dd04t: str = "dd04t"
    catalog_target: str = "platform_dev"
    schema_target: str = "sap_s4_nc2_replica"
    tabela_dd03l: str = "dd03l"
    catalog_legado: str = "platform"
    schema_legado: str = "sap_s4_replica"

    @property
    def fqn_ddl(self) -> str:
        """Tabela DDDDLSRC replicada do SAP."""
        return f"{self.catalog_raw}.{self.schema_raw}.{self.tabela_ddl}"

    @property
    def fqn_depen(self) -> str:
        """DDLDEPENDENCY — usada só como dicionário de nomes, não como grafo."""
        return f"{self.catalog_raw}.{self.schema_raw}.{self.tabela_dep}"

    @property
    def fqn_dd04t(self) -> str:
        """DD04T — textos dos elementos de dados (descrição em PT dos campos)."""
        return f"{self.catalog_raw}.{self.schema_raw}.{self.tabela_dd04t}"

    @property
    def fqn_raw(self) -> str:
        """Schema onde ficam as tabelas físicas replicadas (BSEG, T001, ...)."""
        return f"{self.catalog_raw}.{self.schema_raw}"

    @property
    def fqn_target(self) -> str:
        """Schema onde as views traduzidas seriam criadas (hoje: só referência
        textual no SQL gerado — nada é criado de fato)."""
        return f"{self.catalog_target}.{self.schema_target}"

    @property
    def fqn_dd03l(self) -> str:
        """DD03L — metadados estruturais de campo (posição, chave, tipo, tamanho)."""
        return f"{self.catalog_target}.{self.schema_target}.{self.tabela_dd03l}"

    @property
    def fqn_legado(self) -> str:
        """Schema onde vivem as tabelas legadas (para o SHOW CREATE TABLE de comparação)."""
        return f"{self.catalog_legado}.{self.schema_legado}"

    @classmethod
    def de_widgets(cls, dbutils) -> "Config":
        """Monta a Config a partir dos widgets do notebook, caindo no padrão
        quando o widget não existe (ex.: módulo usado fora do Databricks)."""

        def _w(nome: str, padrao: str) -> str:
            try:
                valor = (dbutils.widgets.get(nome) or "").strip()
            except Exception:
                return padrao
            return valor or padrao

        return cls(
            catalog_raw=_w("catalog_raw", "platform_dev"),
            schema_raw=_w("schema_raw", "sap_s4_nc2_raw"),
            tabela_ddl=_w("tabela_ddl", "tab_ddddlsrc"),
            tabela_dep=_w("tabela_dep", "ddldependency"),
            tabela_dd04t=_w("tabela_dd04t", "dd04t"),
            catalog_target=_w("catalog_target", "platform_dev"),
            schema_target=_w("schema_target", "sap_s4_nc2_replica"),
            tabela_dd03l=_w("tabela_dd03l", "dd03l"),
            catalog_legado=_w("catalog_legado", "platform"),
            schema_legado=_w("schema_legado", "sap_s4_replica"),
        )

    def resumo(self) -> str:
        return (
            f"origem   : {self.fqn_ddl}\n"
            f"depend.  : {self.fqn_depen}\n"
            f"raw      : {self.fqn_raw}\n"
            f"destino  : {self.fqn_target}  (referência textual — nada é criado)\n"
            f"dd03l    : {self.fqn_dd03l}\n"
            f"dd04t    : {self.fqn_dd04t}\n"
            f"legado   : {self.fqn_legado}"
        )
