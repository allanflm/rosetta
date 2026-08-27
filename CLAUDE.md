# Contexto do Projeto — Rosetta (SAP S/4HANA → Databricks CDS Translation)

## Propósito & Contexto Geral

**Rosetta** é uma iniciativa de migração SAP S/4HANA → Databricks, focada em fazer o parsing de definições de views ABAP CDS (Core Data Services) e traduzi-las para SQL executável no Databricks. A tabela fonte primária é `platform_dev.sap_s4_nc2_raw.tab_ddddlsrc` (tabela DDDDLSRC replicada do SAP).

- **Responsável**: Allan (data engineer)
- **Colaborador-chave**: Felipe — decisões arquiteturais, escopo do parser, planejamento de fases
- **Workstream paralelo (não coberto em detalhe aqui)**: Databricks Genie Room, com Marcus, para projeto de cliente

**Critérios de sucesso**: classificar de forma confiável todas as fontes CDS por traduzibilidade, gerar SQL Databricks correto para views elegíveis, e nunca alterar o ambiente Databricks (restrição estrita de somente-leitura em todo o pipeline).

## Restrição inegociável — somente leitura

Toda interação com Spark/SQL Warehouse (catálogo, tabelas, views) é somente
leitura. `src/rosetta/seguranca.py` intercepta e valida toda chamada Spark,
rejeitando qualquer coisa que não comece com `SELECT`/`WITH`/`DESCRIBE`/`SHOW`/
`EXPLAIN`. Nunca gerar código com `CREATE`, `INSERT`, `MERGE`, `DROP`,
`saveAsTable`, `.write`, temp views, ou escritas em DBFS em nenhum ponto do
pipeline. SQL gerado sempre tem `CREATE OR REPLACE VIEW` comentado (exceto o
notebook de PR gerado por `gerar_view_completa`, ver abaixo).

**Única exceção deliberada**: `src/rosetta/workspace_sync.py` (tool MCP
`enviar_para_workspace`) escreve notebooks no *workspace de arquivos* do
Databricks (REST API `/api/2.0/workspace/import`), não no catálogo/tabelas — é
um passo manual e separado, nunca chamado automaticamente por
`gerar_view_completa`, aprovado pelo Allan por ser ambiente Databricks Free
pessoal.

## Domínio técnico

- Arquitetura SAP CDS/VDM: views, projections, table functions, abstract entities, associations, annotations
- Sintaxe ABAP DDL: estilo entity e estilo SQL clássico
- Restrições de transporte RFC
- Databricks/PySpark, Delta Lake
- Ferramenta de replicação AecorSoft ADI

## Estrutura

- `src/rosetta/`: pacote Python com os módulos do pipeline (config, seguranca,
  parser_cds, localizador, gerador, dependencias, classificacao, fluxo,
  notebook_writer, escritor, insumos_gemini, ...)
- `mcp_server/`: servidor MCP (FastMCP, stdio) que expõe as tools somente-leitura do
  pipeline — registrado no Claude Code via `.mcp.json` (server `rosetta`)
- `.claude/skills/gerar-view/`: skill que orquestra as tools MCP pra gerar views
  Databricks a partir de CDS views (invocável via `/gerar-view <nome>`)
- Notebooks: `01_gerar_view`, `03_pacote_gemini`
- `ddl/`: artefatos versionados por view (`view_<nome>/view_<nome>_NNNNN.ipynb`, `.sql`, `metadata.json`)

## Convenções

- Conversas e comentários inline em PT-BR
- Nomes de função/variável em português quando fizer sentido no domínio
- Diagnóstico iterativo: cada passo deve gerar SQL pronto pra colar no Databricks;
  Allan roda queries diretamente em notebooks Databricks a cada passo
- Arquitetura modular: lógica Python extraída para módulos importáveis fora dos
  notebooks; notebooks servem como interface operacional fina
- Warnings como sinal: warnings do parser para `with parameters`, `$session.*` e
  funções de data/timestamp ABAP não mapeadas são preservados intencionalmente como
  flags legítimas de revisão manual. Warnings cosméticos (ex.: "classic style — key
  not identified") são reclassificados como comentários, não riscos

## CI/esteira — resolvido

A pasta `ddl/`/esteira GitHub **não** dispara auto-deploy, nunca (confirmado pelo
Allan). O notebook gerado por `Contexto.gerar_view_notebook`/tool MCP
`gerar_view_completa` (`view_<nome>/view_<nome>_NNNNN.ipynb`) sai com
`CREATE OR REPLACE VIEW` **descomentado** de propósito — é o artefato de
revisão/PR humana pra esteira, diferente do `.sql` solto de diagnóstico (que
continua comentado). `gerar_view_completa` já grava direto (`gravar_notebook=True`);
`salvar_notebook`/`Contexto.gerar_view_notebook` continuam com `gravar=False` como
padrão da função (defesa em profundidade), mas não há mais bloqueio ou incerteza
sobre isso.

## Aprendizados & princípios-chave

1. **Teto de 32KB do RFC é uma propriedade da camada de transporte**: os dados ficam intactos como NCLOB no HANA; qualquer canal não-RFC contorna o truncamento por completo. A config `String Field Max Length` do AecorSoft não afeta o modo Turbo Extractor.
2. **O bloco de metadados `/*+[internal]` precisa ser removido antes da análise de brace-balance**: o JSON de metadados tem suas próprias chaves e consome parte significativa do orçamento do RFC — incluí-lo na detecção de truncamento causa falsos positivos.
3. **`DDLDEPENDENCY` não é o grafo de dependências**: mapeia ddlname → view SQL gerada / entidade STOB. O grafo real de dependência view-to-view está embutido no JSON `/*+[internal]` (arrays `FROM`, `ASSOCIATED`, `BASE`).
4. **`I_ADDRESS` é a view truncada de maior impacto**: corrigi-la desbloqueia ~80% da contaminação downstream. Corrigir as top 40 views truncadas cobre ~95% da contaminação total — priorizar por impacto, não por contagem.
5. **O parser nunca deve estourar exceção publicamente**: `parse_cds` deve capturar todas as exceções (inclusive na fase de remoção de anotações) e retornar `truncado=True` com detalhes do erro em `avisos`, em vez de propagar erros.
6. **Inferência de schema do Spark falha em colunas totalmente None**: sempre definir `StructType` explícito para DataFrames em vez de confiar em inferência automática, especialmente para árvores pequenas ou views sem certas anotações.
7. **Associations são lazy em CDS**: só viram JOINs reais quando referenciadas em campos projetados ou cláusulas WHERE — a travessia de árvore e geração de SQL devem, por padrão, não seguir os alvos de associations.
8. **Somente-leitura é restrição dura**: nada de `CREATE`, `INSERT`, `MERGE`, `DROP`, `saveAsTable`, `.write`, temp views, ou escritas em DBFS em nenhum lugar do pipeline.

## Pendências / próximos passos

- Investigar a classificação de artefatos do bucket `OUTROS` em `01_Localizador_Aptas`.
- Possível melhoria de longo prazo: substituir o AecorSoft ADI para extração de `DDDDLSRC.source` por um canal não-RFC (HANA JDBC direto ou SAP ADT REST API), para contornar o teto de transporte RFC de 32KB — atualmente contornado via workaround de RFC em chunks.
- `src/rosetta/insumos_gemini.py` ainda depende de um passo manual (colar em
  `referencias/Texto gems.txt` no Gemini) — candidato a virar automação própria no
  futuro, fora do escopo da skill `gerar-view`.

## Ferramentas & recursos

- **Databricks** (Unity Catalog, PySpark, Spark SQL, Delta Lake) — ambiente de execução primário
- **AecorSoft ADI** (modo Turbo Extractor) — replicação SAP → Databricks; teto RFC de 32KB conhecido para `DDDDLSRC.source`
- **SAP S/4HANA** — sistema fonte; tabelas-chave: `DDDDLSRC`, `DD02L`, `DD03L`, `DDLDEPENDENCY`, `REPOSRC`
- **Git / template de repositório da empresa** — Rosetta estruturado para seguir as convenções do repo da empresa, com workflow de CI
