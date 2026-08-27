---
name: gerar-view
description: Gera a view Databricks a partir de uma CDS view SAP (ex. "/gerar-view I_ADDRESS", "quero a view databricks da I_ADDRESS", "gera a view X", "só quero ver o SQL/diagnóstico da view Y"). Usa as tools MCP do servidor "rosetta" (buscar_cds_source, analisar_dependencias, gerar_sql_view, classificar_status, buscar_views_irmas, gerar_view_completa, enviar_para_workspace). Também cobre o sub-fluxo de completar CDS truncadas com pendente_validacao.
---

# gerar-view

## Pedido direto ("quero a view databricks da CDS view X")

Quando o Allan pedir a view databricks de uma CDS (ex.: "quero a view databricks da
I_ADDRESS", "/gerar-view I_ADDRESS"), **não** peça pra ele colar nada nem monte um
prompt manual — chame a tool MCP `gerar_view_completa(ddlname)` diretamente. Ela
sozinha:

1. busca o source real na `DDDDLSRC`
2. decide Fluxo A (base já existe no destino) ou Fluxo B (gera do zero) checando o
   catálogo de verdade
3. monta e grava o notebook versionado `view_<nome>/view_<nome>_NNNNN.ipynb` + os
   artefatos (`.sql`, `metadata.json` com `pendente_validacao`)
4. se a fonte estiver truncada/com problema, o notebook ainda é gerado — nunca
   bloqueia — com o fragmento disponível embutido como referência e
   `pendente_validacao: true`

Depois de rodar, reporte o resumo que a tool devolve (fluxo usado, status, pendência,
caminho do notebook). Se `pendente_validacao` vier `true`, siga com a seção
"Completar CDS truncada" abaixo (que usa `buscar_views_irmas` pra achar referência
estrutural) — não pare no meio, mas também não apresente a view como pronta sem
passar por esse fluxo.

## Passo a passo manual/diagnóstico (quando o pedido for só inspecionar, não gerar)

1. Chame `buscar_cds_source` para obter o código-fonte
2. Chame `analisar_dependencias` (seguir_associacoes=false por padrão)
3. Chame `gerar_sql_view` para produzir o SQL Databricks (sempre comentado — é
   diagnóstico, nunca deploy)
4. Chame `classificar_status` e reporte qual das 9 classificações se aplica
5. Se a classificação indicar truncamento/suspeita (fora de `COMPLETA_*`), siga a
   seção "Completar CDS truncada" abaixo

## Fluxo A vs Fluxo B (o que `gerar_view_completa` decide por baixo)

- **Fluxo A** — a base já existe no destino (`spark.catalog.tableExists`): reaproveita
  `SHOW CREATE TABLE` + metadados DD03L/DD04T, cruzando com o SQL gerado do CDS
  (comentários de coluna preenchidos quando encontrados).
- **Fluxo B** — base não existe ainda: gera só a partir do parse do CDS, sem
  cruzamento externo (comentários/tags ficam como placeholder `TODO`).

O notebook (`view_<nome>/view_<nome>_NNNNN.ipynb`) sai com o `CREATE OR REPLACE VIEW`
**descomentado** — diferente do `.sql` solto de diagnóstico — porque é o artefato de
revisão/PR humana antes de entrar na esteira. Todas as células (`ALTER VIEW`,
`COMMENT ON COLUMN`, `SET TAGS`) apontam pro FQN real de destino, não pro nome de
display do notebook. A pasta `ddl/`/esteira GitHub **não** dispara auto-deploy — o
Allan já confirmou "não auto deploy, nunca".

## Completar CDS truncada

### Gatilho

`classificar_status` (tool MCP) devolve qualquer classificação fora de `COMPLETA_*`
— um dos status truncados/suspeitos já mapeados em `localizador.py`:
`TRUNCADO_CHAVE_ABERTA`, `TRUNCADO_SEM_CHAVES`, `TRUNCADO_LITERAL_ABERTO`,
`TRUNCADO_COMENTARIO_ABERTO`, `TRUNCADA_NO_SUFIXO`, `SUFIXO_NAO_RECONHECIDO`,
`CHAVES_NEGATIVAS`.

### Passos

1. Chame `analisar_dependencias` no ddlname pra ver o que já foi resolvido antes do
   corte (tabelas físicas encontradas, CDS visitadas, bloco `/*+[internal]` bruto).
2. Chame `buscar_views_irmas` em 1-2 **views irmãs** — mesmo prefixo ou mesma
   entidade base (ex.: outra `I_*` sobre a mesma tabela raiz) — como padrão de
   referência pra estrutura esperada (fechamento de chaves, sufixo
   `where`/`association`/`group by`).
3. **Se nenhuma view irmã for encontrada, pare aqui.** Não tente "chutar" a
   estrutura sem nenhuma referência (ver regra abaixo). Vá direto pro passo 5 com
   `pendente_validacao: true` e `motivo_pendencia: ["sem view de referência para
   inferir padrão"]`, sem propor nenhum SQL de fechamento.
4. Se encontrou referência, proponha o fechamento mínimo (ex.: fechar uma `}` que
   ficou aberta, completar a cláusula cortada) sem inventar campos que não estavam
   no fragmento original. Gere o SQL normalmente a partir dessa versão completada
   e marque `pendente_validacao: true` com o motivo detalhado (o que foi inferido +
   qual view serviu de referência).
5. Reporte o resultado sempre com `pendente_validacao` e `motivo_pendencia`
   explícitos — nunca deixe implícito.

### Regra dura, não negociável

A saída **nunca** é apresentada como definitiva. O `metadata.json` da view
(`"pendente_validacao": true/false` + `"motivo_pendencia": [...]`) é a **fonte
única de verdade** desse estado — o comentário SQL (`-- ⚠️ PENDENTE_VALIDACAO: ...`)
e a célula markdown do notebook são sempre *derivados* desse campo
(`rosetta.notebook_writer._bloco_pendencia`), nunca escritos à mão de forma
independente. Isso evita 3 lugares que podem ficar dessincronizados — auditar
quantas views estão pendentes é só varrer os `metadata.json` em `ddl/*/metadata.json`.

**Sem view de referência = sem inferência.** Se `buscar_views_irmas` não achar
nenhuma view irmã, recuse completar automaticamente e entregue 100% manual pro
Allan — não invente estrutura só porque foi pedido pra tentar.

Nunca marque uma view como pronta pra PR sozinho: sempre feche pedindo confirmação
explícita do Allan antes de considerar a pendência resolvida.

## Enviar o notebook pro workspace do Databricks (opcional, manual)

Depois de `gerar_view_completa`, o notebook fica só local em `ddl/view_<nome>/`. Se
o Allan pedir explicitamente pra ver/rodar ele no Databricks (ex.: "manda pro
workspace", "sobe esse notebook lá", "quero ver isso no Databricks"), chame a tool
MCP `enviar_para_workspace(ddlname)`. Ela grava em
`/Workspace/Users/allanfelipedk@gmail.com/rosetta/ddl/view_<nome>/` via REST API do
workspace — **não** é o mesmo canal somente-leitura usado pra ler a `DDDDLSRC`, é
escrita real no workspace de notebooks (não no catálogo/tabelas). Por isso:

- **Nunca chame essa tool automaticamente** dentro do fluxo de `gerar_view_completa`
  — é sempre um passo separado, só quando pedido explicitamente.
- Se `pendente_validacao` for `true`, avise antes de enviar (mas pode enviar mesmo
  assim se o Allan confirmar — o notebook já carrega o aviso de pendência dele
  mesmo).
- Reporte o path do workspace que a tool devolve.
