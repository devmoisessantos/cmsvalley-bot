"""
Reset do banco operacional na virada de temporada.

Apaga os dados da temporada. Mantém:
- migracoes_aplicadas (controle de schema)
- perguntas (gabarito do recrutamento)
- registros_wipe / diretoria_pendente_wipe (este domínio)
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from src.database.conexao import async_session

registrador = logging.getLogger(__name__)

# Ordem importa quando há FKs: filhos antes dos pais.
TABELAS_PARA_ESVAZIAR: list[str] = [
    "respostas_prova",
    "recrutamentos",
    "faltas_chamada",
    "chamadas",
    "log_plantao",
    "estado_plantao",
    "controle_chamada",
    "presencas",
    "eventos_gate",
    "ranking_historico",
    "punicoes",
    "snapshot_cargos_membro",
    "consultas_laudo",
    "laudos",
    "contadores_item_bau",
    "casos_bau",
    "advertencias_verbais_bau",
    "config_bau",
    "solicitacoes_curso",
    "solicitacoes_promocao",
    "historico_promocoes",
    "movimentacoes_moedas",
    "pedidos_deposito_moedas",
    "solicitacoes_demissao",
    "solicitacoes_ausencia",
    "tickets",
    "paineis_postados",
    "mensagens_hierarquia",
    "historico_cargos",
    "usuarios",
]


async def esvaziar_banco_da_temporada() -> list[str]:
    """
    TRUNCATE das tabelas operacionais da temporada.

    Devolve linhas descritivas para o relatório do wipe.
    """
    linhas: list[str] = []
    async with async_session() as sessao:
        for nome_tabela in TABELAS_PARA_ESVAZIAR:
            try:
                await sessao.execute(
                    text(f"TRUNCATE TABLE {nome_tabela} RESTART IDENTITY CASCADE")
                )
                linhas.append(f"Tabela esvaziada: {nome_tabela}")
            except Exception as erro:
                # Tabela pode não existir ainda em instalações parciais
                mensagem = f"Tabela ignorada ({nome_tabela}): {erro}"
                linhas.append(mensagem)
                registrador.warning("[wipe] %s", mensagem)
        await sessao.commit()
    return linhas
