# 📂 ddl/

Saída do Rosetta: **uma pasta por CDS view**, criada pelo notebook `01_gerar_view`.

```
ddl/
├── I_ADDRESS/
│   ├── I_ADDRESS.sql     # SQL Databricks traduzido — o CREATE sai COMENTADO
│   ├── arvore.txt        # árvore de dependências no momento da geração
│   ├── avisos.txt        # só existe quando a tradução tem ressalvas
│   └── metadata.json     # entidade, tipo, tabelas físicas, contagens, timestamp
```

O nome da pasta é o ddlname em maiúsculas, com `/` virando `_`
(`/AIF/C_INTERFACESTATISTICS` → `AIF_C_INTERFACESTATISTICS`).

⚠️ Nenhum `.sql` daqui é executado pelo projeto. Aplicar no ambiente é decisão manual.
