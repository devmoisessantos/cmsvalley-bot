# src/services/paineis_manutencao.py
"""
Manutenção dos painéis persistentes.

Permite apagar o registro no banco (e a mensagem antiga, se ainda existir)
e publicar de novo o painel atualizado — sem reiniciar o bot.
"""

from __future__ import annotations

from dataclasses import dataclass

import discord
from sqlalchemy import select

from src.database.connection import async_session
from src.database.models import PainelPostado
from src.panels.setup_paineis import (
    garantir_painel_avaliacao,
    garantir_painel_boas_vindas,
    garantir_painel_eventos_gate,
    garantir_painel_fazer_chamada,
    garantir_painel_gerenciar_cargos,
    garantir_painel_gerenciar_membros,
    garantir_painel_plantao,
    garantir_painel_recrutamento,
    garantir_painel_whitelist,
)
from src.punicoes.cogs import garantir_painel_punicoes

# nome_no_banco → função que posta o painel se não existir registro
FUNCOES_GARANTIR_PAINEL = {
    "whitelist": garantir_painel_whitelist,
    "recrutamento": garantir_painel_recrutamento,
    "avaliacao": garantir_painel_avaliacao,
    "gerenciar_cargos": garantir_painel_gerenciar_cargos,
    "plantao": garantir_painel_plantao,
    "eventos_gate": garantir_painel_eventos_gate,
    "boas_vindas": garantir_painel_boas_vindas,
    "fazer_chamada": garantir_painel_fazer_chamada,
    "gerenciar_membros": garantir_painel_gerenciar_membros,
    "punicoes": garantir_painel_punicoes,
}

NOMES_DOS_PAINEIS = list(FUNCOES_GARANTIR_PAINEL.keys())


@dataclass
class ResultadoPainel:
    nome: str
    ok: bool
    mensagem: str


async def listar_paineis_no_banco() -> list[PainelPostado]:
    """Retorna todos os registros de painéis salvos."""
    async with async_session() as sessao:
        resultado = await sessao.execute(select(PainelPostado))
        return list(resultado.scalars().all())


async def buscar_painel(nome_painel: str) -> PainelPostado | None:
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        return resultado.scalar_one_or_none()


async def _apagar_mensagem_antiga(
    bot: discord.Client,
    canal_id: int,
    message_id: int,
) -> str:
    """Tenta apagar a mensagem antiga do painel. Devolve texto do que aconteceu."""
    canal = bot.get_channel(canal_id)
    if canal is None:
        return "Canal antigo não encontrado (mensagem não apagada)."

    try:
        mensagem = await canal.fetch_message(message_id)
        await mensagem.delete()
        return "Mensagem antiga apagada."
    except discord.NotFound:
        return "Mensagem antiga já não existia."
    except discord.Forbidden:
        return "Sem permissão para apagar a mensagem antiga."
    except discord.HTTPException as erro:
        return f"Falha ao apagar mensagem antiga: {erro}"


async def remover_registro_painel(nome_painel: str) -> bool:
    """Apaga só o registro no banco. Retorna True se havia registro."""
    async with async_session() as sessao:
        resultado = await sessao.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == nome_painel)
        )
        registro = resultado.scalar_one_or_none()
        if registro is None:
            return False
        await sessao.delete(registro)
        await sessao.commit()
        return True


async def recriar_painel(
    bot: discord.Client,
    nome_painel: str,
) -> ResultadoPainel:
    """
    1. Apaga a mensagem antiga (se possível)
    2. Remove o registro no banco
    3. Chama a função garantir_* para postar de novo
    """
    if nome_painel not in FUNCOES_GARANTIR_PAINEL:
        return ResultadoPainel(
            nome=nome_painel,
            ok=False,
            mensagem=f"Painel `{nome_painel}` desconhecido.",
        )

    detalhes: list[str] = []
    registro = await buscar_painel(nome_painel)

    if registro is not None:
        detalhe_mensagem = await _apagar_mensagem_antiga(
            bot, registro.canal_id, registro.message_id
        )
        detalhes.append(detalhe_mensagem)
        await remover_registro_painel(nome_painel)
        detalhes.append("Registro removido do banco.")
    else:
        detalhes.append("Não havia registro no banco.")

    funcao_garantir = FUNCOES_GARANTIR_PAINEL[nome_painel]
    try:
        await funcao_garantir(bot)
        detalhes.append("Painel publicado de novo.")
        return ResultadoPainel(
            nome=nome_painel,
            ok=True,
            mensagem=" · ".join(detalhes),
        )
    except Exception as erro:
        detalhes.append(f"Erro ao publicar: {erro}")
        return ResultadoPainel(
            nome=nome_painel,
            ok=False,
            mensagem=" · ".join(detalhes),
        )


async def recriar_todos_os_paineis(bot: discord.Client) -> list[ResultadoPainel]:
    """Recria todos os painéis conhecidos, um a um."""
    resultados: list[ResultadoPainel] = []
    for nome_painel in NOMES_DOS_PAINEIS:
        resultado = await recriar_painel(bot, nome_painel)
        resultados.append(resultado)
    return resultados
