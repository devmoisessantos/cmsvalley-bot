"""
Garante que os paineis fixos existam nos canais, sem duplicar.

Por que "garantir"
------------------
Cada funcao daqui se chama `garantir_...` e nao `criar_...` de proposito. Ela
procura o painel no canal primeiro: se ja existe, apenas atualiza; se nao
existe, cria. Rodar duas vezes nao gera dois paineis.

Isso importa porque estas funcoes rodam a cada vez que o bot liga. Sem a
conferencia, cada reinicio deixaria mais um painel abandonado no canal.
"""

import logging

import discord
from sqlalchemy import select

from src.avaliacao.avaliacao_panel import PainelAvaliacaoLayout
from src.config import (
    CANAIS,
    CANAL_PAINEL_RECRUTAMENTO_ID,
    GUILD_ID,
)
from src.database.conexao import async_session
from src.database.models import PainelPostado
from src.gate.gate_panel import PainelEventosGate
from src.plantao.chamada.chamada_persistente_panel import PainelFazerChamadaLayout
from src.plantao.plantao_panel import PainelPlantaoLayout
from src.recrutamento.recrutamento_panel import PainelRecrutamentoLayout
from src.whitelist.whitelist_panel import PainelWhitelistLayout

registrador = logging.getLogger(__name__)


async def garantir_painel_whitelist(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Garante que o painel de whitelist está postado no canal.
    Se já existir no banco, não duplica.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "whitelist")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["WHITELIST_CANAL_ID"])
        if canal is None:
            registrador.error("❌ Canal de WhiteList não foi encontrado ou definido.")
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild do bot ou do interaction
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            registrador.error("❌ Guild não encontrada!")
            return

        # Envia o painel no canal
        mensagem = await canal.send(view=PainelWhitelistLayout(guild))

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="whitelist",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        registrador.info(f"✅ Painel de Whitelist postado no canal #{canal.name}.")


async def garantir_painel_recrutamento(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Garante que o painel de recrutamento está postado no canal.
    Se já existir no banco, não duplica.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "recrutamento")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAL_PAINEL_RECRUTAMENTO_ID)
        if canal is None:
            registrador.error(
                "❌ Canal do painel de recrutamento não encontrado. Confira "
                "CANAL_PAINEL_RECRUTAMENTO_ID."
            )
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild do bot ou do interaction
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            registrador.error("❌ Guild não encontrada!")
            return

        # Painéis usam o ícone do servidor (Thumbnail); não dependem de assets/logo.png
        mensagem = await canal.send(view=PainelRecrutamentoLayout(guild))

        novo_registro = PainelPostado(
            nome_painel="recrutamento",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        registrador.info(f"✅ Painel de Recrutamento postado no canal #{canal.name}.")


async def garantir_painel_avaliacao(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Garante que o painel de avaliação está postado no canal.
    Se já existir no banco, não duplica.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "avaliacao")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["AVALIACAO"])
        if canal is None:
            registrador.error(
                "❌ Canal de Avaliação não encontrado. Confira CANAIS['AVALIACAO']."
            )
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild do bot ou do interaction
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            registrador.error("❌ Guild não encontrada!")
            return

        mensagem = await canal.send(view=PainelAvaliacaoLayout(guild))

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="avaliacao",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        registrador.info(f"✅ Painel de Avaliação postado no canal #{canal.name}.")


async def garantir_painel_plantao(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Publica uma única entrada persistente do painel de plantão quando ela falta.

    Consulta o banco antes de enviar a view ao Discord e registra a mensagem criada,
    comportamento reutilizado para evitar duplicações após reinícios. A interação
    opcional oferece a guilda atual quando a tarefa não consegue inferi-la do bot.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "plantao")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["CANAL_PAINEL_PLANTAO_ID"])
        if canal is None:
            registrador.error("Canal do painel de plantão não encontrado.")
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild para passar ao layout (necessário para o ícone)
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            registrador.error("❌ Guild não encontrada!")
            return

        mensagem = await canal.send(view=PainelPlantaoLayout(guild=guild))

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="plantao",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        registrador.info(f"✅ Painel de Plantão Médico postado no canal #{canal.name}.")


async def garantir_painel_eventos_gate(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Garante uma única mensagem persistente para iniciar eventos Gate.

    Usa o registro no banco como trava contra duplicidade, encontra o canal e envia
    o painel no Discord somente quando necessário. Salva a nova mensagem após o
    envio para que reinicializações não transformem cada carga em outra publicação.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "eventos_gate")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["CRIAR_EVENTO_GATE"])
        if canal is None:
            registrador.error("Canal do painel de eventos não encontrado.")
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild para passar ao layout (necessário para o ícone)
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            registrador.error("❌ Guild não encontrada!")
            return

        mensagem = await canal.send(view=PainelEventosGate(guild=guild))

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="eventos_gate",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        registrador.info(f"✅ Painel de Eventos Gate postado no canal #{canal.name}.")


async def garantir_painel_fazer_chamada(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Cria o painel de chamada apenas se ainda não houver uma mensagem registrada.

    Esta rotina transversal consulta a persistência, envia a view no canal configurado
    e grava identificadores após o efeito no Discord. Assim, tarefas de inicialização
    podem ser repetidas sem poluir o servidor com botões equivalentes.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "fazer_chamada")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_FAZER_CHAMADA") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            registrador.warning("⚠️ Canal #fazer-chamada não configurado/encontrado.")
            return

        guild = (
            interaction.guild
            if interaction and interaction.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guild is None:
            return

        mensagem = await canal.send(view=PainelFazerChamadaLayout(guild=guild))
        session.add(
            PainelPostado(
                nome_painel="fazer_chamada",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await session.commit()
        registrador.info(f"✅ Painel Fazer Chamada postado em #{canal.name}.")
