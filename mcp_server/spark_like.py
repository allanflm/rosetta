"""Adaptador fino do `databricks-sql-connector` pra imitar o subconjunto da API do
`spark` que `rosetta` usa (`spark.sql(...).collect()`, `spark.table(...).columns`,
`spark.catalog.tableExists(...)`).

Só existe pra permitir testar o pipeline (`Contexto.gerar_view_notebook`, `fluxo.py`)
FORA de um notebook Databricks — no Databricks Free (SQL Warehouse serverless) não
tem cluster interativo com PySpark de verdade acessível de fora. Em produção, dentro
de um notebook Databricks real, `spark` já existe como global e este módulo nem entra
em cena.

Somente leitura: todo SQL que passa por `.sql()` ainda é validado por
`rosetta.seguranca.assert_leitura` (chamado de dentro do próprio `rosetta`, via
`sql_leitura`) — este adaptador não abre uma porta nova, só troca o transporte.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class _Row:
    _dados: Dict[str, Any]

    def asDict(self) -> Dict[str, Any]:
        return dict(self._dados)

    def __getitem__(self, chave):
        return self._dados[chave]


class _ResultadoSql:
    def __init__(self, linhas: List[_Row]):
        self._linhas = linhas

    def collect(self) -> List[_Row]:
        return self._linhas

    def toPandas(self):
        import pandas as pd

        return pd.DataFrame([r.asDict() for r in self._linhas])


class _TabelaRef:
    def __init__(self, colunas: List[str]):
        self.columns = colunas


class _Catalogo:
    def __init__(self, conexao_factory):
        self._conexao_factory = conexao_factory

    def tableExists(self, fqn: str) -> bool:
        try:
            with self._conexao_factory() as conn, conn.cursor() as cur:
                cur.execute(f"DESCRIBE TABLE {fqn}")
                cur.fetchall()
            return True
        except Exception as e:  # noqa: BLE001
            msg = str(e).upper()
            if "TABLE_OR_VIEW_NOT_FOUND" in msg or "NOT FOUND" in msg or "NO SUCH TABLE" in msg:
                return False
            raise  # erro de verdade (permissão/conexão) — deixa `decidir_fluxo` tratar


class SparkLikeDatabricks:
    """Uso: `spark = SparkLikeDatabricks(host, http_path, token)`, depois passa
    `spark` pra `Contexto`/`fluxo.py` normalmente."""

    def __init__(self, server_hostname: str, http_path: str, access_token: str):
        self._server_hostname = server_hostname
        self._http_path = http_path
        self._access_token = access_token
        self.catalog = _Catalogo(self._conectar)

    def _conectar(self):
        from databricks import sql as dbsql

        return dbsql.connect(
            server_hostname=self._server_hostname,
            http_path=self._http_path,
            access_token=self._access_token,
        )

    def sql(self, query: str) -> _ResultadoSql:
        with self._conectar() as conn, conn.cursor() as cur:
            cur.execute(query)
            colunas = [d[0] for d in (cur.description or [])]
            linhas = [_Row(dict(zip(colunas, row))) for row in cur.fetchall()]
        return _ResultadoSql(linhas)

    def table(self, fqn: str) -> _TabelaRef:
        res = self.sql(f"DESCRIBE TABLE {fqn}")
        colunas = [r["col_name"] for r in res.collect() if r["col_name"] and not r["col_name"].startswith("#")]
        return _TabelaRef(colunas)
