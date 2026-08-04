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


async def destruir_print_com_aviso(mensagem_print: discord.Message, delay: int = 10):
    """Usado quando a chamada é abortada após o print já ter sido enviado —
    avisa publicamente no canal e apaga print + aviso após o delay."""
    if mensagem_print is None:
        return

    aviso = None
    try:
        aviso = await mensagem_print.reply(
            f"⚠️ Esta mensagem e o print do `/ems` serão destruídos em {delay} segundos."
        )
    except discord.HTTPException:
        pass

    async def _destruir():
        await asyncio.sleep(delay)
        for msg in (mensagem_print, aviso):
            if msg is None:
                continue
            try:
                await msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    asyncio.create_task(_destruir())


async def responder_ephemera(
    interaction: discord.Interaction,
    content: str,
    view: discord.ui.View | None = None,
    delay: int = 10,
):
    kwargs = {"content": content, "ephemeral": True}
    if view is not None:
        kwargs["view"] = view
    await interaction.response.send_message(**kwargs)

    msg = await interaction.original_response()
    
    asyncio.create_task(excluir_mensagem(msg, delay=delay))