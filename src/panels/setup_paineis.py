import discord
from sqlalchemy import select

from src.config import (
    CANAIS,
    CANAL_PAINEL_RECRUTAMENTO_ID,
    GUILD_ID,
    LOGO_PATH,
)
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.gate.evento_gate_panel import PainelEventosGate
from src.guia.boas_vindas_panel import PainelBoasVindasLayout
from src.panels.avaliacao_panel import PainelAvaliacaoLayout
from src.panels.gerenciar_cargos_panel import PainelGerenciarCargoLayout
from src.plantao.chamada.painel_chamada_persistente import PainelFazerChamadaLayout
from src.plantao.gerenciar_membros_panel import PainelGerenciarMembrosLayout
from src.plantao.plantao_panel import PainelPlantaoLayout
from src.recrutamento.recrutamento_panel import PainelRecrutamentoLayout
from src.whitelist.whitelist_panel import PainelWhitelistLayout


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
            print("❌ Canal de WhiteList não foi encontrado ou definido.")
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
            print("❌ Guild não encontrada!")
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
        print(f"✅ Painel de Whitelist postado no canal #{canal.name}.")


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
            print(
                "❌ Canal do painel de recrutamento não encontrado. Confira CANAL_PAINEL_RECRUTAMENTO_ID."
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
            print("❌ Guild não encontrada!")
            return

        arquivo = discord.File(LOGO_PATH, filename="logo.png")
        mensagem = await canal.send(view=PainelRecrutamentoLayout(guild), file=arquivo)

        novo_registro = PainelPostado(
            nome_painel="recrutamento",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        print(f"✅ Painel de Recrutamento postado no canal #{canal.name}.")


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
            print("❌ Canal de Avaliação não encontrado. Confira CANAIS['AVALIACAO'].")
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
            print("❌ Guild não encontrada!")
            return

        arquivo = discord.File(LOGO_PATH, filename="logo.png")
        mensagem = await canal.send(view=PainelAvaliacaoLayout(guild), file=arquivo)

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="avaliacao",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        print(f"✅ Painel de Avaliação postado no canal #{canal.name}.")


async def garantir_painel_gerenciar_cargos(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """
    Garante que o painel de gerenciamento de cargos está postado no canal.
    Se já existir no banco, não duplica.
    """
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "gerenciar_cargos")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["MANAGE_ROLE_CHANNEL_ID"])
        if canal is None:
            print(
                "❌ Canal de Gerenciamento de Cargos não encontrado. Confira CANAIS['MANAGE_ROLE_CHANNEL_ID']."
            )
            return

        # Caso já tenha sido postado, não duplicar
        if registro is not None:
            return

        # Obtém o guild para passar ao layout (necessário para o ícone)
        guild = bot.get_guild(int(GUILD_ID))

        # Obtém o guild do bot ou do interaction
        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            print("❌ Guild não encontrada!")
            return

        arquivo_da_logo = discord.File(LOGO_PATH, filename="logo.png")
        view_do_painel = PainelGerenciarCargoLayout(guild=guild)
        mensagem = await canal.send(view=view_do_painel, file=arquivo_da_logo)

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="gerenciar_cargos",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        print(f"✅ Painel de Gerenciamento de Cargos postado no canal #{canal.name}.")


async def garantir_painel_plantao(
    bot: discord.Client, interaction: discord.Interaction = None
):
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "plantao")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["CANAL_PAINEL_PLANTAO_ID"])
        if canal is None:
            print("Canal do painel de plantão não encontrado.")
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
            print("❌ Guild não encontrada!")
            return

        arquivo = discord.File(LOGO_PATH, filename="logo.png")
        mensagem = await canal.send(view=PainelPlantaoLayout(guild=guild), file=arquivo)

        # Salva o registro no banco
        novo_registro = PainelPostado(
            nome_painel="plantao",
            canal_id=canal.id,
            message_id=mensagem.id,
        )
        session.add(novo_registro)
        await session.commit()
        print(f"✅ Painel de Plantão Médico postado no canal #{canal.name}.")


async def garantir_painel_eventos_gate(
    bot: discord.Client, interaction: discord.Interaction = None
):
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "eventos_gate")
        )
        registro = resultado.scalar_one_or_none()

        canal = bot.get_channel(CANAIS["CRIAR_EVENTO_GATE"])
        if canal is None:
            print("Canal do painel de eventos não encontrado.")
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
            print("❌ Guild não encontrada!")
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
        print(f"✅ Painel de Eventos Gate postado no canal #{canal.name}.")


async def garantir_painel_boas_vindas(
    bot: discord.Client, interaction: discord.Interaction = None
):
    """Garante o painel de boas-vindas (Guia do Estagiário — cat. 1) no canal."""
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "boas_vindas")
        )
        registro = resultado.scalar_one_or_none()

        canal_id = CANAIS.get("PAINEL_BOAS_VINDAS") or 0
        if not canal_id:
            print(
                "⚠️ CANAIS['PAINEL_BOAS_VINDAS'] não configurado — painel não postado."
            )
            return

        canal = bot.get_channel(canal_id)
        if canal is None:
            print(f"❌ Canal de boas-vindas ({canal_id}) não encontrado.")
            return

        if registro is not None:
            return

        if interaction and interaction.guild:
            guild = interaction.guild
        else:
            guild = bot.get_guild(int(GUILD_ID))

        if guild is None:
            print("❌ Guild não encontrada!")
            return

        mensagem = await canal.send(view=PainelBoasVindasLayout(guild))

        session.add(
            PainelPostado(
                nome_painel="boas_vindas",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await session.commit()
        print(f"✅ Painel de Boas-Vindas postado no canal #{canal.name}.")


async def garantir_painel_fazer_chamada(
    bot: discord.Client, interaction: discord.Interaction = None
):
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(PainelPostado.nome_painel == "fazer_chamada")
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_FAZER_CHAMADA") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print("⚠️ Canal #fazer-chamada não configurado/encontrado.")
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
        print(f"✅ Painel Fazer Chamada postado em #{canal.name}.")


async def garantir_painel_gerenciar_membros(
    bot: discord.Client, interaction: discord.Interaction = None
):
    async with async_session() as session:
        resultado = await session.execute(
            select(PainelPostado).where(
                PainelPostado.nome_painel == "gerenciar_membros"
            )
        )
        if resultado.scalar_one_or_none() is not None:
            return

        canal_id = CANAIS.get("CANAL_GERENCIAR_MEMBROS") or 0
        canal = bot.get_channel(canal_id) if canal_id else None
        if canal is None:
            print("⚠️ Canal #gerenciar-membros não configurado/encontrado.")
            return

        guild = (
            interaction.guild
            if interaction and interaction.guild
            else bot.get_guild(int(GUILD_ID))
        )
        if guild is None:
            return

        mensagem = await canal.send(view=PainelGerenciarMembrosLayout(guild=guild))
        session.add(
            PainelPostado(
                nome_painel="gerenciar_membros",
                canal_id=canal.id,
                message_id=mensagem.id,
            )
        )
        await session.commit()
        print(f"✅ Painel Gerenciar Membros postado em #{canal.name}.")
