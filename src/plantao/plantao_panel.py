import discord

from src.config import GUILD_ID, CANAIS_PLANTAO, NOMES_CANAIS_PLANTAO
from src.plantao.plantao_service import ligar_servico, desligar_servico
from src.database.connection import async_session
from src.database.models import EstadoPlantao
from sqlalchemy import select


def _botao_link_canal(canal_id: int) -> discord.ui.Button:
    return discord.ui.Button(
        label=NOMES_CANAIS_PLANTAO.get(canal_id, "Canal"),
        style=discord.ButtonStyle.link,
        url=f"https://discord.com/channels/{GUILD_ID}/{canal_id}",
    )


class PainelPlantaoLayout(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

        # Linha 0: toggle (única linha reservada pra ação real do bot)
        self.add_item(self._botao_toggle())

        # Linhas seguintes: um grupo de botões de link por categoria,
        # respeitando o limite de 5 botões por linha do Discord
        grupos = [
            [
                CANAIS_PLANTAO["CALL_INTERNA"], 
                CANAIS_PLANTAO["CALL_EXTERNA"]
            ],
            [
                CANAIS_PLANTAO["BATE_PAPO_1"], 
                CANAIS_PLANTAO["BATE_PAPO_2"], 
                CANAIS_PLANTAO["BATE_PAPO_3"]
            ],
            CANAIS_PLANTAO["CONSULTORIOS"],
            CANAIS_PLANTAO["SALA_CURSOS"],
            [
                CANAIS_PLANTAO["DIRETORIA"], 
                CANAIS_PLANTAO["DIRETORIA_GERAL"]
            ],
            CANAIS_PLANTAO["RECRUTAMENTO"],
        ]

        linha_atual = 1
        for grupo in grupos:
            for canal_id in grupo:
                botao = _botao_link_canal(canal_id)
                botao.row = linha_atual
                self.add_item(botao)
            linha_atual += 1

    def _botao_toggle(self) -> discord.ui.Button:
        botao = discord.ui.Button(
            label="🟢 Ligar / Desligar Serviço",
            style=discord.ButtonStyle.primary,
            custom_id="plantao:toggle",
            row=0,
        )
        botao.callback = self._callback_toggle
        return botao

    async def _callback_toggle(self, interaction: discord.Interaction):
        with_estado = None  # placeholder, ver nota abaixo
        resultado = await self._alternar(interaction.user)
        await interaction.response.send_message(resultado, ephemeral=True)

    async def _alternar(self, membro: discord.Member) -> str:

        async with async_session() as session:
            resultado = await session.execute(
                select(EstadoPlantao).where(EstadoPlantao.discord_id == membro.id)
            )
            estado = resultado.scalar_one_or_none()
            ja_ligado = estado is not None and estado.toggle_ligado

        if ja_ligado:
            return await desligar_servico(membro)
        return await ligar_servico(membro)