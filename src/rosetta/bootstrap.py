"""Localização da raiz do repositório a partir de um notebook.

No Databricks Repos o notebook roda de dentro da pasta do repo, então dá para
subir a árvore até achar o marcador. Fora dele (execução local, CI), o mesmo
código funciona sem alteração.
"""
from __future__ import annotations

import sys
from pathlib import Path

MARCADOR = Path("src") / "rosetta" / "__init__.py"


def achar_raiz(inicio: Path | None = None) -> Path:
    """Sobe a partir de `inicio` (ou do diretório atual) até achar src/rosetta/."""
    atual = Path(inicio) if inicio else Path.cwd()
    for cand in [atual, *atual.parents]:
        if (cand / MARCADOR).exists():
            return cand
    raise FileNotFoundError(
        f"Não achei a raiz do repositório a partir de {atual}. "
        f"Esperava encontrar {MARCADOR} em algum diretório acima."
    )


def preparar(inicio: Path | None = None) -> Path:
    """Acha a raiz e coloca src/ no sys.path. Retorna a raiz."""
    raiz = achar_raiz(inicio)
    caminho_src = str(raiz / "src")
    if caminho_src not in sys.path:
        sys.path.insert(0, caminho_src)
    return raiz
