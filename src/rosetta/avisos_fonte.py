"""Avisos de qualidade da fonte para o pacote do Gemini (Motor 03).

O SQL SAP do pacote (arquivo 1) é gerado normalmente mesmo se algum fonte da
cadeia veio truncado pelo transporte RFC ou com sufixo estranho — isso não
bloqueia a montagem do pacote, só precisa ser revisado antes de colar no
Gemini. Este módulo classifica cada CDS da cadeia com a mesma varredura exata
usada no Motor 01 (`rosetta.localizador.varrer` + `classificar_exato`), sem
disparar nenhuma consulta nova ao Spark — roda em memória sobre o índice já
carregado pelo `Contexto`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from .config import Config, MARCA_META
from .insumos_gemini import CadeiaDdl
from .localizador import COMPLETAS, TRUNCADAS, classificar_exato, varrer

ROTULOS_AVISO = {
    "COMPLETA_FECHA_CHAVE":       "completo — fecha chave normalmente",
    "COMPLETA_COM_SUFIXO":        "completo — fecha chave + cláusula (WHERE/UNION/...)",
    "COMPLETA_SEM_CHAVES":        "completo — sem chaves (define table function/etc.)",
    "COMPLETA_TABLE_FUNCTION":    "completo — implementação AMDP (table function)",
    "TRUNCADO_CHAVE_ABERTA":      "truncado — chave { aberta, nunca fecha",
    "CHAVES_NEGATIVAS":           "saldo de chaves negativo — revisar manualmente",
    "SUFIXO_NAO_RECONHECIDO":     "sufixo após a última chave não reconhecido",
    "TRUNCADA_NO_SUFIXO":         "truncado dentro do sufixo (WHERE/UNION/... cortado)",
    "TRUNCADO_SEM_CHAVES":        "truncado — nunca chega a abrir chave",
    "TRUNCADO_LITERAL_ABERTO":    "truncado — dentro de uma string ' aberta",
    "TRUNCADO_COMENTARIO_ABERTO": "truncado — dentro de um comentário /* aberto",
}


@dataclass
class AvisoFonte:
    ddlname: str
    classe: str
    status: str  # COMPLETO | TRUNCADO | REVISAR
    rotulo: str


def classificar_cadeia(cadeia: CadeiaDdl, indice: Dict[str, str]) -> List[AvisoFonte]:
    """Classifica cada CDS da cadeia (raiz + intermediárias) pela mesma
    varredura exata do Motor 01, sem consultar o Spark de novo."""
    avisos = []
    for nome in cadeia.ddlnames:
        src = indice.get(nome) or ""
        pos_meta = src.find(MARCA_META)
        corpo = src[:pos_meta] if pos_meta >= 0 else src
        limpo, estado, trecho = varrer(corpo)
        classe = classificar_exato(limpo, estado, trecho)
        status = "COMPLETO" if classe in COMPLETAS else ("TRUNCADO" if classe in TRUNCADAS else "REVISAR")
        avisos.append(AvisoFonte(
            ddlname=nome, classe=classe, status=status,
            rotulo=ROTULOS_AVISO.get(classe, classe),
        ))
    return avisos


def resumo_avisos(avisos: List[AvisoFonte]) -> str:
    """Texto pronto pra imprimir no notebook — não bloqueia nada, só avisa."""
    completos = [a for a in avisos if a.status == "COMPLETO"]
    problematicos = [a for a in avisos if a.status != "COMPLETO"]

    linhas = ["=" * 78, f"  ⚠️  QUALIDADE DA FONTE — {len(avisos)} CDS na cadeia", "=" * 78]
    linhas.append(f"  ✅ completos    : {len(completos)}")
    linhas.append(f"  ⚠️  a revisar    : {len(problematicos)}")

    for a in problematicos:
        marca = "❌" if a.status == "TRUNCADO" else "⚠️ "
        linhas.append(f"\n  {marca} {a.ddlname}")
        linhas.append(f"      {a.classe} — {a.rotulo}")

    if not problematicos:
        linhas.append("\n  Nenhum aviso — todos os fontes da cadeia vieram completos.")
    else:
        linhas.append(
            "\n👉 O SQL SAP (arquivo 1) foi gerado normalmente com o que veio do "
            "catálogo. Revise os fontes acima antes de colar no Gemini — o "
            "transporte RFC pode ter cortado o texto."
        )
    linhas.append("=" * 78)
    return "\n".join(linhas)
