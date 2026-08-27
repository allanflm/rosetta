"""Gravação dos artefatos em `ddl/view_<nome>/`.

Escopo do que este módulo faz: escreve ARQUIVOS DE TEXTO na pasta do repositório.
Escopo do que ele NÃO faz: nada no catálogo do Databricks. O SQL solto (`.sql`) é
salvo com o CREATE comentado, e nunca é executado por lugar nenhum do projeto — a
trava está em `seguranca.py`. O notebook (`.ipynb`, ver `notebook_writer.py`) já
sai com `%sql CREATE OR REPLACE VIEW` descomentado — é um artefato de revisão/PR
humana pra esteira, não uma execução do Rosetta. Ver `notebook_writer.py` para a
pendência em aberto sobre se essa pasta/esteira dispara auto-deploy no CI.

Layout por view:

    ddl/
    └── view_i_address/
        ├── i_address.sql              # o SQL Databricks traduzido (CREATE comentado)
        ├── arvore.txt                 # árvore de dependências como exibida no notebook
        ├── avisos.txt                 # um aviso por linha (ausente quando não há avisos)
        ├── metadata.json              # entidade, tipo, tabelas físicas, contagens, timestamp
        └── view_i_address_00001.ipynb # notebook versionado pra esteira (ver notebook_writer.py)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .arvore import Arvore
from .modelos import ViewCds


def _sanitizar_nome(ddlname: str) -> str:
    limpo = (ddlname or "").strip().strip("/").upper()
    for ch in "/\\ :*?\"<>|":
        limpo = limpo.replace(ch, "_")
    return limpo or "SEM_NOME"


def nome_pasta(ddlname: str) -> str:
    """/AIF/C_INTERFACESTATISTICS → view_aif_c_interfacestatistics (nome de pasta válido,
    padrão `view_<nome>` usado pela esteira GitHub)."""
    return f"view_{_sanitizar_nome(ddlname).lower()}"


def nome_arquivo_notebook(ddlname: str, versao: int) -> str:
    """Nome do notebook versionado: view_<nome>_00001.ipynb."""
    return f"{nome_pasta(ddlname)}_{versao:05d}.ipynb"


def proxima_versao(pasta: Path, ddlname: str) -> int:
    """Varre `view_<nome>_*.ipynb` já existentes em `pasta` e devolve a próxima versão
    (1 se não houver nenhuma ainda). Não falha se a pasta não existir."""
    base = nome_pasta(ddlname)
    padrao = re.compile(rf"^{re.escape(base)}_(\d{{5}})\.ipynb$")
    maior = 0
    if pasta.exists():
        for arquivo in pasta.iterdir():
            m = padrao.match(arquivo.name)
            if m:
                maior = max(maior, int(m.group(1)))
    return maior + 1


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
    pendente_validacao: bool = False,
    motivo_pendencia: Optional[List[str]] = None,
    notebook_relpath: Optional[str] = None,
) -> Artefatos:
    """Monta (e opcionalmente grava) a pasta da view.

    `gravar=False` devolve os caminhos que seriam usados sem tocar no disco — útil
    para conferir o destino antes de escrever de fato.

    `pendente_validacao`/`motivo_pendencia` são a FONTE ÚNICA DE VERDADE desse estado
    (ver `.agents/workflows/completar-view-truncada.md`) — o comentário SQL e a célula
    markdown do notebook (`notebook_writer.py`) são derivados destes campos do
    `metadata.json`, nunca escritos de forma independente.
    """
    pasta = Path(raiz_ddl) / nome_pasta(ddlname)
    base = _sanitizar_nome(ddlname).lower()

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
        "pendente_validacao": pendente_validacao,
        "motivo_pendencia": motivo_pendencia or [],
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if notebook_relpath:
        meta["notebook"] = notebook_relpath
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


def salvar_notebook(
    raiz_ddl: Path,
    ddlname: str,
    notebook_dict: dict,
    versao: Optional[int] = None,
    gravar: bool = False,
) -> Path:
    """Grava (ou simula) o notebook versionado em `ddl/view_<nome>/view_<nome>_NNNNN.ipynb`.

    `gravar=False` por padrão (mesma convenção de `salvar_artefatos`/`salvar_pacote`):
    NADA é escrito em disco sem confirmação explícita de quem chama. Isso é proposital —
    ainda não está confirmado se a esteira GitHub roda `.ipynb` novos em `ddl/**`
    automaticamente (ver docstring de `notebook_writer.py`).

    `versao=None` (padrão) deriva a próxima versão livre via `proxima_versao`, evitando
    sobrescrever um notebook já existente.
    """
    pasta = Path(raiz_ddl) / nome_pasta(ddlname)
    if versao is None:
        versao = proxima_versao(pasta, ddlname)
    caminho = pasta / nome_arquivo_notebook(ddlname, versao)

    if gravar:
        pasta.mkdir(parents=True, exist_ok=True)
        caminho.write_text(json.dumps(notebook_dict, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

    return caminho
