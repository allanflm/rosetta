"""Envio manual de notebooks já gerados em `ddl/` pro workspace do Databricks.

Diferente do resto do pacote: isto ESCREVE no Databricks (workspace de notebooks,
não catálogo/tabelas). É um passo separado e explícito — nunca roda dentro de
`Contexto.gerar_view_notebook`/`gerar_view_completa`. O Allan revisa o notebook
local em `ddl/view_<nome>/` antes de decidir mandar pro workspace.

Usa a REST API do Databricks (`/api/2.0/workspace/mkdirs` e `/api/2.0/workspace/import`)
com o mesmo host/token já configurados em `.env` para o SQL Warehouse — não abre
nenhuma credencial nova.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Optional

import requests


class ErroEnvioWorkspace(RuntimeError):
    """Erro ao conversar com a REST API do workspace Databricks."""


def _normalizar_host(host: str) -> str:
    return re.sub(r"^https?://", "", host).rstrip("/")


def montar_path_workspace(base_workspace: str, nome_pasta: str, nome_arquivo_notebook: str) -> str:
    """`/Workspace/Users/x/rosetta/ddl` + `view_i_address` + `view_i_address_00001.ipynb`
    -> `/Workspace/Users/x/rosetta/ddl/view_i_address/view_i_address_00001` (sem
    extensão — o workspace do Databricks não usa `.ipynb` no path do notebook)."""
    base = base_workspace.rstrip("/")
    nome_sem_ext = nome_arquivo_notebook[:-len(".ipynb")] if nome_arquivo_notebook.endswith(".ipynb") else nome_arquivo_notebook
    return f"{base}/{nome_pasta}/{nome_sem_ext}"


def enviar_notebook_workspace(
    caminho_local: Path,
    path_workspace: str,
    host: str,
    token: str,
    overwrite: bool = True,
    timeout: int = 30,
) -> str:
    """Sobe `caminho_local` (.ipynb) pro `path_workspace` no workspace Databricks.

    Cria os diretórios pai automaticamente (`mkdirs` é idempotente). Devolve o
    `path_workspace` em caso de sucesso; levanta `ErroEnvioWorkspace` caso contrário.
    """
    if not caminho_local.exists():
        raise ErroEnvioWorkspace(f"Notebook local não encontrado: {caminho_local}")

    host_limpo = _normalizar_host(host)
    headers = {"Authorization": f"Bearer {token}"}
    base_url = f"https://{host_limpo}/api/2.0/workspace"

    pasta_pai = path_workspace.rsplit("/", 1)[0]
    resp_mkdirs = requests.post(
        f"{base_url}/mkdirs", headers=headers, json={"path": pasta_pai}, timeout=timeout,
    )
    if resp_mkdirs.status_code != 200:
        raise ErroEnvioWorkspace(
            f"Falha ao criar pasta '{pasta_pai}' no workspace: "
            f"{resp_mkdirs.status_code} {resp_mkdirs.text[:300]}"
        )

    conteudo_b64 = base64.b64encode(caminho_local.read_bytes()).decode("ascii")
    resp_import = requests.post(
        f"{base_url}/import",
        headers=headers,
        json={
            "path": path_workspace,
            "format": "JUPYTER",
            "content": conteudo_b64,
            "overwrite": overwrite,
        },
        timeout=timeout,
    )
    if resp_import.status_code != 200:
        raise ErroEnvioWorkspace(
            f"Falha ao importar notebook para '{path_workspace}': "
            f"{resp_import.status_code} {resp_import.text[:300]}"
        )

    return path_workspace
