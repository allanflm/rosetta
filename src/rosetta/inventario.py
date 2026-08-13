"""Certeza real: roda o parser de verdade em lote sobre um conjunto de ddlnames.

Diferença essencial em relação ao `localizador` (Motor 01): lá a classificação é
estrutural (o fonte fecha? o tipo é suportado? as dependências estão limpas?).
Aqui a pergunta é outra — "o gerador consegue produzir SQL limpo para esta view?"
— e a única forma honesta de responder é executando parse + geração.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Set

import pandas as pd

from .gerador import gerar_sql
from .parser_cds import parse_cds
from .pipeline import Contexto


@dataclass
class Certeza:
    pdf: pd.DataFrame

    @property
    def garantidas(self) -> Set[str]:
        return set(self.pdf.loc[self.pdf["gerou_sql"] & self.pdf["n_avisos"].eq(0), "ddlname"])

    @property
    def com_aviso(self) -> Set[str]:
        return set(self.pdf.loc[self.pdf["gerou_sql"] & self.pdf["n_avisos"].gt(0), "ddlname"])

    @property
    def falhou(self) -> Set[str]:
        return set(self.pdf.loc[~self.pdf["gerou_sql"], "ddlname"])

    def resumo(self) -> str:
        total = len(self.pdf)
        return "\n".join(
            [
                "=" * 66,
                "  🏆 CERTEZA REAL — resultado de rodar o parser, não só o pré-filtro",
                "=" * 66,
                f"   ✅ 100% garantido (sem avisos) : {len(self.garantidas):>8,}",
                f"   ⚠️  Gera, mas com aviso(s)      : {len(self.com_aviso):>8,}",
                f"   ❌ Falhou no parser real        : {len(self.falhou):>8,}",
                "-" * 66,
                f"   de {total:,} processadas",
                "=" * 66,
            ]
        )


def certeza_real(ctx: Contexto, ddlnames: Iterable[str], verboso: bool = True) -> Certeza:
    """Executa parse + geração para cada ddlname e classifica o resultado."""
    alvos = [d.strip().upper() for d in ddlnames]
    if verboso:
        print(f"🏆 Rodando o parser real em {len(alvos):,} views...")
    t0 = time.time()

    linhas = []
    for ddl in alvos:
        src = ctx.indice.get(ddl, "")
        v = parse_cds(ddl, src)
        _sql, avisos = gerar_sql(v, ctx.resolvedor)
        linhas.append(
            {
                "ddlname": ddl,
                "entidade": v.nome_entidade,
                "tipo": v.tipo,
                "gerou_sql": v.parseou,
                "n_campos": len(v.campos),
                "n_avisos": len(avisos),
                "avisos": " | ".join(avisos),
            }
        )

    if verboso:
        print(f"✅ Processado em {time.time() - t0:.1f}s")

    return Certeza(pdf=pd.DataFrame(linhas))
