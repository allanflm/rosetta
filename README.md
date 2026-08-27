# 🪨 Rosetta

Traduz definições de **CDS views do SAP S/4HANA** (ABAP CDS, tabela `DDDDLSRC`) para
**SQL do Databricks**.

O código vive em um pacote Python (`src/rosetta/`) e os notebooks são só a interface de
operação. Isso permite testar parser, tradução e geração fora do cluster, em segundos.

---

## Estrutura do repositório

```text
├── .github/workflows/
│   └── testes.yml               # CI: roda pytest + lint a cada push
│
├── src/rosetta/                 # 📦 o pacote — toda a lógica mora aqui
│   ├── __init__.py              # API pública (from rosetta import Config, Contexto, ...)
│   ├── config.py                # catálogos, schemas e FQNs em um objeto só
│   ├── seguranca.py             # 🔒 trava de somente-leitura (toda query passa aqui)
│   ├── modelos.py               # dataclasses: ViewCds, Campo, Associacao, JoinBruto
│   ├── tokens.py                # tokenização do fonte CDS (sem Spark)
│   ├── parser_cds.py            # parse_cds — nunca levanta exceção
│   ├── traducao.py              # regras ABAP CDS → Databricks SQL + sinais de aviso
│   ├── avisos_fonte.py          # classifica cadeias de avisos herdados por dependência
│   ├── nomes.py                 # entidade CDS/tabela SAP → nome físico no Databricks
│   ├── indice.py                # índice ddlname→source carregado em 1 consulta
│   ├── arvore.py                # árvore de dependências com memoização
│   ├── gerador.py               # gerar_sql (CREATE sai comentado)
│   ├── localizador.py           # Motor 01 — inventário/pré-seleção estrutural
│   ├── fluxo.py                 # decide Fluxo A (base já existe) vs Fluxo B (do zero)
│   ├── notebook_writer.py       # monta o notebook versionado view_<nome>_NNNNN.ipynb
│   ├── escritor.py              # grava artefatos (.sql, .ipynb, metadata.json) em ddl/
│   ├── workspace_sync.py        # envia notebook gerado pro Workspace do Databricks
│   ├── insumos_gemini.py        # pacote de insumos (SQL SAP, DD03L/DD04T, SHOW CREATE) p/ Gemini
│   └── pipeline.py              # Contexto e Resultado — fachada usada pelos notebooks
│
├── mcp_server/                  # servidor MCP (FastMCP, stdio) — expõe as tools do
│   └── ...                      # pipeline somente-leitura pro Claude Code (`.mcp.json`)
│
├── .claude/skills/gerar-view/   # skill que orquestra as tools MCP (`/gerar-view <nome>`)
│
├── notebooks/
│   ├── 01_gerar_view.ipynb      # ⭐ o do dia a dia: 1 ddlname → árvore + SQL + arquivos
│   └── 03_pacote_gemini.ipynb   # monta o pacote de insumos pra colar no Gemini
│
├── ddl/                         # 📂 saída: uma pasta por CDS view
│   └── view_<nome>/
│       ├── view_<nome>_00001.ipynb  # notebook versionado (CREATE OR REPLACE descomentado)
│       ├── <nome>.sql               # SQL traduzido solto (CREATE comentado — diagnóstico)
│       ├── arvore.txt               # árvore de dependências
│       └── metadata.json            # entidade, tipo, contagens, pendente_validacao
│
├── tests/
│   ├── test_rosetta.py
│   ├── test_fluxo.py
│   ├── test_notebook_writer.py
│   └── test_mcp_server.py
│
├── requirements.txt
├── requirements-mcp.txt
├── .python-version
└── .gitignore
```

---

## Uso

### Via Claude Code (skill `gerar-view`)

Com o servidor MCP `rosetta` registrado (`.mcp.json`), peça direto: `/gerar-view
I_ADDRESS` ou "quero a view databricks da I_ADDRESS". A skill chama a tool MCP
`gerar_view_completa`, que decide Fluxo A/B, monta e grava o notebook versionado +
artefatos em `ddl/view_<nome>/`.

### Gerar o SQL de uma view (notebook `01_gerar_view`)

1. Preencha o widget **`ddlname`** (ex.: `BSAS_DDL`, `I_ADDRESS`, `/AIF/C_INTERFACESTATISTICS`)
2. Rode as células em ordem

O notebook mostra a árvore de dependências, lista as tabelas físicas que compõem a view,
gera o SQL e grava tudo em `ddl/<DDLNAME>/`.

### Ou direto em Python

```python
from rosetta import Config, Contexto

cfg = Config(catalog_raw="platform_dev", schema_raw="sap_s4_nc2_raw")
ctx = Contexto(spark, cfg, raiz_ddl=RAIZ / "ddl")

res = ctx.traduzir("BSAS_DDL", gravar=True)

print(res.arvore.texto())      # árvore de dependências
print(res.sql)                 # SQL Databricks
print(res.avisos)              # [] = tradução direta, sem revisão manual
print(res.selo())              # ✅ / ⚠️ / ❌
```

---

## 🔒 Somente leitura

Duas coisas diferentes que é fácil confundir:

- **Catálogo do Databricks: intocado.** Toda consulta ao Spark passa por
  `rosetta.seguranca.sql_leitura`, que recusa qualquer statement que não comece com
  `SELECT`, `WITH`, `DESCRIBE`, `SHOW` ou `EXPLAIN`. Não existe `CREATE`, `INSERT`,
  `MERGE`, `DROP`, `saveAsTable`, `.write` nem temp view em lugar nenhum do projeto.
- **Arquivos do repositório: é onde a saída é gravada.** O `.sql` solto de diagnóstico
  em `ddl/view_<nome>/` sai com o `CREATE OR REPLACE VIEW` **comentado**. Já o notebook
  versionado (`view_<nome>_NNNNN.ipynb`) sai com o `CREATE OR REPLACE VIEW`
  **descomentado** de propósito — é o artefato de revisão/PR humana antes de entrar na
  esteira, não uma execução automática do Rosetta. A pasta `ddl/`/esteira GitHub não
  dispara auto-deploy.
- **Única exceção de escrita real**: a tool MCP `enviar_para_workspace`
  (`src/rosetta/workspace_sync.py`) grava o notebook já gerado no *workspace de
  arquivos* do Databricks (REST API), nunca no catálogo/tabelas — sempre um passo
  manual e separado, nunca disparado automaticamente.

Se quiser rodar sem gravar nada, coloque o widget `gravar_arquivos` em `Nao`: o notebook
mostra os caminhos que usaria e não toca no disco.

---

## Testes

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Os testes não precisam de Spark nem de Databricks: parser, tradução, geração e escrita são
funções puras sobre texto.

---

## Conceitos do domínio

**Truncamento RFC 32K.** O protocolo RFC do SAP tem limite de ~32 KB por campo, o que corta
fontes CDS grandes em ~16.294 caracteres na replicação via AecorSoft. O dado está íntegro
como NCLOB no HANA — qualquer canal não-RFC (JDBC direto, ADT REST API) contorna isso.
A detecção aqui é **estrutural** (o bloco `{}` fecha?), não por faixa de comprimento — a
heurística antiga de `LENGTH BETWEEN 16.200 e 16.294` perdia ~90% dos casos.

**Bloco `/*+[internal]`.** O ativador do SAP anexa ao fonte um JSON de metadados com os
arrays `FROM`, `ASSOCIATED` e `BASE`. É dali que sai o grafo de dependências — não da
`DDLDEPENDENCY`, que é uma tabela de mapeamento objeto→definição, não um grafo.

**Memoização na árvore.** CDS views compartilham muita dependência comum (`I_Currency`,
`I_UnitOfMeasure`). Sem cache, a mesma subárvore é reexpandida por cada caminho que a
alcança, crescendo combinatorialmente com a profundidade.

**Associations são lazy.** Uma association só vira JOIN de verdade quando algum campo a
referencia. Por isso o padrão é **não** segui-las na árvore (widget
`seguir_associations` = `Nao`) — segui-las infla a árvore com dependências que o SQL final
nem usa.
