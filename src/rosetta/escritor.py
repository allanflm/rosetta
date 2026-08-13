"""Gravação dos artefatos em `ddl/<DDLNAME>/`.

Escopo do que este módulo faz: escreve ARQUIVOS DE TEXTO na pasta do repositório.
Escopo do que ele NÃO faz: nada no catálogo do Databricks. O SQL gerado é salvo
como texto, com o CREATE comentado, e nunca é executado por lugar nenhum do
projeto — a trava está em `seguranca.py`.

Layout por view:

    ddl/
    └── I_ADDRESS/
        ├── I_ADDRESS.sql       # o SQL Databricks traduzido (CREATE comentado)
        ├── arvore.txt          # árvore de dependências como exibida no notebook
        ├── avisos.txt          # um aviso por linha (ausente quando não há avisos)
        └── metadata.json       # entidade, tipo, tabelas físicas, contagens, timestamp
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .arvore import Arvore
from .modelos import ViewCds


def nome_pasta(ddlname: str) -> str:
    """/AIF/C_INTERFACESTATISTICS → AIF_C_INTERFACESTATISTICS (nome de pasta válido)."""
    limpo = (ddlname or "").strip().strip("/").upper()
    for ch in "/\\ :*?\"<>|":
        limpo = limpo.replace(ch, "_")
    return limpo or "SEM_NOME"


@dataclass
class Artefatos:
    pasta: Path
    arquivos: Dict[str, Path]
    gravado: bool

    def resumo(self) -> str:
        cabec = "💾 Gravado em" if self.gravado else "🔍 Simulação (nada gravado) —"
        linhas = [f"{cabec} {self.pasta}"]
        for rotulo, caminho in self.arquivos.items():
            linhas.append(f"   • {rotulo}: {caminho.name}")
        return "\n".join(linhas)


def salvar_artefatos(
    raiz_ddl: Path,
    ddlname: str,
    sql: str,
    avisos: List[str],
    view: ViewCds,
    arvore: Optional[Arvore] = None,
    gravar: bool = True,
) -> Artefatos:
    """Monta (e opcionalmente grava) a pasta da view.

    `gravar=False` devolve os caminhos que seriam usados sem tocar no disco — útil
    para conferir o destino antes de escrever de fato.
    """
    pasta = Path(raiz_ddl) / nome_pasta(ddlname)
    base = nome_pasta(ddlname)

    conteudos: Dict[str, tuple[Path, str]] = {
        "SQL": (pasta / f"{base}.sql", sql.rstrip() + "\n"),
    }

    if arvore is not None:
        conteudos["árvore"] = (pasta / "arvore.txt", arvore.texto().rstrip() + "\n")

    if avisos:
        conteudos["avisos"] = (
            pasta / "avisos.txt",
            "\n".join(f"{i:2}. {a}" for i, a in enumerate(avisos, 1)) + "\n",
        )

    meta = {
        "ddlname": ddlname,
        "entidade_cds": view.nome_entidade,
        "sql_view_hana": view.sql_view_name,
        "label": view.label,
        "tipo": view.tipo,
        "estilo": view.estilo,
        "entidade_base": view.entidade_base,
        "n_campos": len(view.campos),
        "n_associacoes": len(view.associacoes),
        "n_joins": len(view.joins),
        "n_branches_union": len(view.blocos_uniao),
        "truncado": view.truncado,
        "n_avisos": len(avisos),
        "avisos": avisos,
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if arvore is not None:
        meta["tabelas_fisicas"] = sorted(arvore.tabelas_fisicas)
        meta["cds_dependentes"] = sorted(arvore.cds_visitadas)
        meta["dependencias_truncadas"] = sorted(arvore.truncadas)
        meta["dependencias_nao_encontradas"] = sorted(arvore.nao_encontradas)

    conteudos["metadata"] = (pasta / "metadata.json", json.dumps(meta, indent=2, ensure_ascii=False) + "\n")

    if gravar:
        pasta.mkdir(parents=True, exist_ok=True)
        for caminho, texto in conteudos.values():
            caminho.write_text(texto, encoding="utf-8")

    return Artefatos(
        pasta=pasta,
        arquivos={rotulo: caminho for rotulo, (caminho, _) in conteudos.items()},
        gravado=gravar,
    )
