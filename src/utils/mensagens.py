import asyncio
import discord


async def excluir_mensagem(mensagem: discord.Message, delay: int = 120):
    """Aguarda o tempo especificado e exclui a mensagem."""
    await asyncio.sleep(delay)
    try:
        await mensagem.delete()
    except discord.NotFound:
        pass
    except discord.Forbidden:
        print("Sem permissão para excluir a mensagem")