# 🪨 Rosetta

Traduz definições de **CDS views do SAP S/4HANA** (ABAP CDS, tabela `DDDDLSRC`) para
**SQL do Databricks**.

O código vive em um pacote Python (`src/rosetta/`) e os notebooks são só a interface de
operação. Isso permite testar parser, tradução e geração fora do cluster, em segundos.

---

## Estrutura do repositório

```
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
│   ├── nomes.py                 # entidade CDS/tabela SAP → nome físico no Databricks
│   ├── indice.py                # índice ddlname→source carregado em 1 consulta
│   ├── arvore.py                # árvore de dependências com memoização
│   ├── gerador.py               # gerar_sql (CREATE sai comentado)
│   ├── localizador.py           # Motor 01 — inventário/pré-seleção estrutural
│   ├── inventario.py            # certeza real — roda o parser em lote
│   ├── pipeline.py              # Contexto e Resultado — fachada usada pelos notebooks
│   └── bootstrap.py             # acha a raiz do repo a partir do notebook
│
├── notebooks/
│   ├── 01_gerar_view.ipynb      # ⭐ o do dia a dia: 1 ddlname → árvore + SQL + arquivos
│   └── 02_inventario.ipynb      # varredura completa (roda de vez em quando)
│
├── ddl/                         # 📂 saída: uma pasta por CDS view
│   ├── I_ADDRESS/
│   │   ├── I_ADDRESS.sql        # o SQL traduzido (CREATE comentado)
│   │   ├── arvore.txt           # árvore de dependências
│   │   ├── avisos.txt           # só existe quando há avisos
│   │   └── metadata.json        # entidade, tipo, tabelas físicas, contagens
│   └── _inventario/             # saída do notebook 02
│
├── tests/
│   └── test_rosetta.py          # 25 testes, rodam sem Spark
│
├── requirements.txt
├── .python-version
└── .gitignore
```

---

## Uso

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

### Inventário completo (notebook `02_inventario`)

Responde duas perguntas diferentes:

| Conceito | O que significa |
|---|---|
| **APTA** | Pré-seleção estrutural: fonte íntegro + tipo suportado + dependências limpas. É uma *candidata*. |
| **GARANTIDA** | Rodou parser + gerador de fato e saiu SQL com **zero avisos**. É *resultado verificado*. |

---

## 🔒 Somente leitura

Duas coisas diferentes que é fácil confundir:

- **Catálogo do Databricks: intocado.** Toda consulta ao Spark passa por
  `rosetta.seguranca.sql_leitura`, que recusa qualquer statement que não comece com
  `SELECT`, `WITH`, `DESCRIBE`, `SHOW` ou `EXPLAIN`. Não existe `CREATE`, `INSERT`,
  `MERGE`, `DROP`, `saveAsTable`, `.write` nem temp view em lugar nenhum do projeto.
- **Arquivos do repositório: é onde a saída é gravada.** O SQL traduzido é salvo como
  texto em `ddl/<DDLNAME>/`, com o `CREATE OR REPLACE VIEW` **comentado**. Nada executa
  esse SQL — aplicar no ambiente é uma decisão manual, futura e fora deste projeto.

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
