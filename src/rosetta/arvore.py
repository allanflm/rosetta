"""Árvore de dependências de uma CDS view, com memoização.

CDS views compartilham muita dependência comum (I_Currency, I_UnitOfMeasure,
I_BusinessPartner...). Sem cache, a mesma subárvore seria reexpandida toda vez que
fosse alcançada por outro caminho, crescendo combinatorialmente com a profundidade.
Aqui cada CDS view é expandida uma única vez em toda a árvore.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .nomes import ResolvedorNomes
from .parser_cds import parse_cds

EMOJI_TIPO = {
    "CDS": "✅",
    "TRUNCADO": "❌",
    "TABELA": "🗄️ ",
    "CICLO": "🔁",
    "REPETIDO": "♻️ ",
    "NÃO ENCONTRADO": "❓",
    "LIMITE": "⏳",
    "AVISO": "⚠️ ",
}


@dataclass
class Arvore:
    raiz: str
    linhas: List[dict] = field(default_factory=list)
    tabelas_fisicas: Set[str] = field(default_factory=set)
    cds_visitadas: Set[str] = field(default_factory=set)
    truncadas: Set[str] = field(default_factory=set)
    nao_encontradas: Set[str] = field(default_factory=set)

    @property
    def tem_problema(self) -> bool:
        return bool(self.truncadas or self.nao_encontradas)

    def texto(self) -> str:
        return "\n".join(l["hierarquia"] for l in self.linhas)


def montar_arvore(
    ddlname_raiz: str,
    indice: Dict[str, str],
    resolvedor: ResolvedorNomes,
    seguir_assoc: bool = False,
    max_profundidade: int = 15,
    max_nos: int = 4000,
    truncados_conhecidos: Optional[Set[str]] = None,
) -> Arvore:
    """Desce a árvore a partir de `ddlname_raiz` usando só o índice em memória.

    `truncados_conhecidos` (opcional) vem do inventário do Motor 01 — quando
    disponível, é a evidência mais confiável de truncamento; senão, o próprio
    parser decide (fim_bloco não fecha = truncado).
    """
    arv = Arvore(raiz=ddlname_raiz.strip().upper())
    ja_expandido: Set[str] = set()
    caminho_atual: Set[str] = set()
    contador = {"n": 0}

    class _LimiteExcedido(Exception):
        pass

    def _truncado(chave: str, v) -> bool:
        if truncados_conhecidos is not None and chave in truncados_conhecidos:
            return True
        return v.truncado

    def _rec(nome: str, nivel: int, prefixo: str, origem: str = "FROM"):
        chave = nome.strip().upper()
        sufixo_origem = "  (association)" if origem == "ASSOC" else ""

        contador["n"] += 1
        if contador["n"] > max_nos:
            arv.linhas.append(
                {
                    "level": nivel,
                    "tipo": "LIMITE_GLOBAL",
                    "hierarquia": f"{prefixo}⏳ ... [limite de {max_nos} nós atingido]",
                }
            )
            raise _LimiteExcedido()

        if chave in caminho_atual:
            arv.linhas.append(
                {"level": nivel, "tipo": "CICLO", "hierarquia": f"{prefixo}🔁 {nome}{sufixo_origem}  [CICLO]"}
            )
            return

        if not resolvedor.eh_cds(nome):
            arv.linhas.append(
                {"level": nivel, "tipo": "TABELA", "hierarquia": f"{prefixo}🗄️  {nome}{sufixo_origem}  [TABELA]"}
            )
            arv.tabelas_fisicas.add(chave)
            return

        if chave in ja_expandido:
            arv.linhas.append(
                {
                    "level": nivel,
                    "tipo": "REPETIDO",
                    "hierarquia": f"{prefixo}♻️  {nome}{sufixo_origem}  [já exibido acima ↑]",
                }
            )
            return

        if nivel > max_profundidade:
            arv.linhas.append(
                {
                    "level": nivel,
                    "tipo": "LIMITE",
                    "hierarquia": f"{prefixo}⏳ {nome}{sufixo_origem}  [limite de profundidade]",
                }
            )
            return

        src = indice.get(chave)
        if src is None:
            arv.linhas.append(
                {
                    "level": nivel,
                    "tipo": "ERRO",
                    "hierarquia": f"{prefixo}❓ {nome}{sufixo_origem}  [NÃO ENCONTRADO]",
                }
            )
            arv.nao_encontradas.add(chave)
            return

        v = parse_cds(chave, src)
        truncado = _truncado(chave, v)
        emoji = "❌" if truncado else "✅"
        tag = "TRUNCADO" if truncado else "CDS"
        sufixo_sql = f"  → SQL: {v.sql_view_name}" if v.sql_view_name else ""
        arv.linhas.append(
            {
                "level": nivel,
                "tipo": tag,
                "hierarquia": f"{prefixo}{emoji} {nome}{sufixo_origem}{sufixo_sql}  [{tag}]",
            }
        )
        ja_expandido.add(chave)
        arv.cds_visitadas.add(chave)

        if truncado:
            arv.truncadas.add(chave)
            arv.linhas.append(
                {
                    "level": nivel + 1,
                    "tipo": "AVISO",
                    "hierarquia": f"{'    ' * (nivel + 1)}└── ⚠️  dependências não confiáveis - DDL truncado",
                }
            )
            return

        deps = []
        if v.entidade_base:
            deps.append((v.entidade_base, "FROM"))
        if seguir_assoc:
            for a in v.associacoes:
                deps.append((a.alvo, "ASSOC"))

        caminho_atual.add(chave)
        for dep_nome, dep_origem in deps:
            _rec(dep_nome, nivel + 1, "    " * nivel + "└── ", dep_origem)
        caminho_atual.discard(chave)

    try:
        _rec(arv.raiz, 0, "")
    except _LimiteExcedido:
        pass

    return arv
