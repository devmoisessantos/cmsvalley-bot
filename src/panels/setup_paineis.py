import discord
from sqlalchemy import select

from src.config import (
    CANAL_PAINEL_RECRUTAMENTO_ID, 
    LOGO_PATH, 
    CANAIS, 
    GUILD_ID,
)
from src.database.connection import async_session
from src.database.models import PainelPostado
from src.panels.recrutamento_panel import PainelRecrutamentoLayout
from src.panels.avaliacao_panel import PainelAvaliacaoLayout
from src.panels.whitelist_panel import PainelWhitelistLayout 
from src.panels.plantao_panel import PainelPlantaoLayout
from src.panels.gerenciar_cargos_panel import PainelGerenciarCargoLayout


async def garantir_painel_whitelist(bot: discord.Client, interaction: discord.Interaction = None):
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


async def garantir_painel_recrutamento(bot: discord.Client, interaction: discord.Interaction = None):
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
            print("❌ Canal do painel de recrutamento não encontrado. Confira CANAL_PAINEL_RECRUTAMENTO_ID.")
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


async def garantir_painel_avaliacao(bot: discord.Client, interaction: discord.Interaction = None):
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


async def garantir_painel_gerenciar_cargos(bot: discord.Client, interaction: discord.Interaction = None):
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
            print("❌ Canal de Gerenciamento de Cargos não encontrado. Confira CANAIS['MANAGE_ROLE_CHANNEL_ID'].")
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


async def garantir_painel_plantao(bot: discord.Client, interaction: discord.Interaction = None):
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
